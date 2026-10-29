"""Page-level PyMuPDF analysis and automatic service-to-local OCR fallback.

Thresholds follow production_document_parse.py. Native pages remain authoritative
when a document also contains scans. Quality failures are never successful Versions.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pymupdf as _fitz  # type: ignore[import-untyped]

from wlh.core import WlhError
from wlh.extraction.markdown import normalized, text_to_markdown
from wlh.extraction.ocr_br import br_ocr
from wlh.extraction.ocr_tesseract import tesseract_pages
from wlh.extraction.pdf_markdown import clean_soft_hyphens, page_markdown
from wlh.extraction.quality import block_text, json_value, quality_check
from wlh.extraction.vi_tcvn3 import normalize_detected_tcvn3

fitz: Any = _fitz

PDF_CONTRACT = "wlh.pdf-scan.v12"
SCAN_TEXT_CHARS = 30
SCAN_INK_RATIO = 0.003
INK_THRESHOLD = 210
_INK_LUT = bytes(1 if b < INK_THRESHOLD else 0 for b in range(256))
ANALYSIS_SCALE = 0.5
NORMAL_PAGE_TEXT_CHARS = 350
SIGNATURE_DOC_MAX_TEXT_CHARS = 500
SIGNATURE_PAGE_MIN_COVERAGE = 0.85
SIGNATURE_PAGE_MIN_INK = 0.01


def _has_unverified_vertical_fragments(page_text: str) -> bool:
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    fragments = sum(bool(re.fullmatch(r"[\u3040-\u30ff\u3400-\u9fff]", line)) for line in lines)
    return fragments >= 12 and fragments / max(len(lines), 1) >= 0.25


def analyze(path: str | Path) -> tuple[dict[str, Any], list[str]]:
    raw_pages: list[dict[str, Any]] = []
    texts: list[str] = []
    try:
        with fitz.open(path) as document:
            if document.needs_pass or not len(document):
                raise WlhError("pdf_invalid_or_encrypted")
            for number in range(1, len(document) + 1):
                page = document[number - 1]
                text = str(page.get_text("text"))
                native = len(text.strip())
                pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), colorspace=fitz.csGRAY)
                samples = bytes(pix.samples)
                ink = samples.translate(_INK_LUT).count(1) / len(samples)
                rect = page.rect
                page_area = max(rect.width * rect.height, 1.0)
                img_area_total = sum(
                    r.width * r.height
                    for img in page.get_images(full=True)
                    for r in page.get_image_rects(img[0])
                )
                image_coverage = min(img_area_total / page_area, 1.0)
                raw_pages.append(
                    {
                        "page": number,
                        "native_text_chars": native,
                        "embedded_images": len(page.get_images(full=True)),
                        "image_coverage": round(image_coverage, 4),
                        "ink_ratio": round(ink, 5),
                    }
                )
                texts.append(text)
    except WlhError:
        raise
    except Exception:
        raise WlhError("pdf_invalid_or_encrypted") from None

    total_native = sum(p["native_text_chars"] for p in raw_pages)
    has_normal_text_page = any(p["native_text_chars"] >= NORMAL_PAGE_TEXT_CHARS for p in raw_pages)
    max_sparse_doc_chars = max(SIGNATURE_DOC_MAX_TEXT_CHARS, 220 * len(raw_pages))

    pages: list[dict[str, Any]] = []
    for p in raw_pages:
        is_pure_scan = (
            p["native_text_chars"] < SCAN_TEXT_CHARS
            and p["ink_ratio"] > SCAN_INK_RATIO
            and (not has_normal_text_page or p["embedded_images"] > 0 or p["image_coverage"] > 0)
        )
        is_signature_scan = (
            not has_normal_text_page
            and total_native < max_sparse_doc_chars
            and p["native_text_chars"] < NORMAL_PAGE_TEXT_CHARS
            and float(p["image_coverage"]) >= 0.25
            and float(p["ink_ratio"]) >= SIGNATURE_PAGE_MIN_INK
        )
        suspected = is_pure_scan or is_signature_scan
        pages.append(dict(p, suspected_scan=suspected))

    return {
        "contract": PDF_CONTRACT,
        "page_count": len(pages),
        "pages": pages,
        "native_text_chars": total_native,
        "suspected_scan_pages": [p["page"] for p in pages if p["suspected_scan"]],
    }, texts


def page_blocks(texts: Mapping[int, str]) -> list[dict[str, Any]]:
    return [
        {"type": "text", "page_idx": n, "text": text}
        for n, text in sorted(texts.items())
        if text.strip()
    ]


def extract_pdf(
    path: str | Path,
    ocr: Mapping[str, Any],
    profile: str,
    cached_result: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any], list[str]]:
    analysis, native = analyze(path)
    scans = analysis["suspected_scan_pages"]
    if any(
        page["embedded_images"] and not page["suspected_scan"] for page in analysis["pages"]
    ):
        # A native text layer can coexist with image-only diagrams/forms. Until
        # those assets are archived and presented in the rendered document, a
        # text-length score cannot establish complete delivery. No size-based
        # guess that an image is decorative is safe for a legal document.
        raise WlhError("pdf_images_unverified")
    if any(unicodedata.category(char) == "Cc" and not char.isspace()
           for text in native for char in text):
        # Broken font mappings can yield plenty of native characters, including
        # invisible C0/C1 values. Deleting those bytes would conceal lost words.
        raise WlhError("pdf_native_text_unreadable")
    texts: dict[int, str] = {}
    recovered = False
    for i, text in enumerate(native, 1):
        # Section signs, currencies and diacritics are ordinary legal text in
        # other languages; their presence is not evidence of Vietnamese fonts.
        texts[i], changed = normalize_detected_tcvn3(text) if profile == "vi" else (text, False)
        recovered |= changed
    info: dict[str, Any] = {"kind": "pdf", "analysis": analysis, "method": "native", "ocr": None}
    parsers: list[str] = ["pdf", "quality"]
    service_blocks: list[dict[str, Any]] = []
    if recovered:
        parsers.append("tcvn3")
    scan_set = set(scans)
    native_rendered: dict[int, str] = {}
    if len(scan_set) < len(native):
        with fitz.open(path) as document:
            for n in range(1, len(native) + 1):
                if n not in scan_set:
                    page_text = page_markdown(document[n - 1], native[n - 1])
                    if profile == "vi":
                        page_text, _ = normalize_detected_tcvn3(page_text)
                    if _has_unverified_vertical_fragments(page_text):
                        raise WlhError("pdf_layout_unverified")
                    native_rendered[n] = page_text
    if scans:
        deadline = time.monotonic() + ocr["timeout_seconds"]
        recognized: dict[int, str] | None = None
        engine: dict[str, Any] = {}
        if ocr["engine"] == "br-ocr-v1":
            try:
                result: dict[str, Any] = cached_result or br_ocr(
                    path, ocr, deadline, page_count=len(native)
                )
                blocks = json_value(result.get("page_parse_json"), [])
                indices: list[Any] = [
                    cast(dict[str, Any], b).get("page_idx") for b in blocks if isinstance(b, dict)
                ]
                offset = 1 if 0 in indices else 0
                service_blocks = [
                    dict(
                        cast(dict[str, Any], b),
                        page_idx=cast(dict[str, Any], b)["page_idx"] + offset,
                    )
                    for b in blocks
                    if isinstance(b, dict)
                    and isinstance(cast(dict[str, Any], b).get("page_idx"), int)
                ]
                recognized = {
                    n: "\n".join(block_text(b) for b in service_blocks if b["page_idx"] == n)
                    for n in scans
                }
                if any(not recognized[n].strip() for n in scans):
                    raise WlhError("br_ocr_page_mapping_missing")
                engine = {"engine": "br-ocr-v1", "model": ocr["model"], "result": result}
            except WlhError as exc:
                engine = {"fallback_from": "br-ocr-v1", "fallback_reason": exc.code}
                service_blocks = []
        if recognized is None or not all(recognized.values()):
            # Local fallback receives its own bounded budget after a failed service job.
            nonblank_scans = {
                p["page"]
                for p in analysis["pages"]
                if p["suspected_scan"] and (p["ink_ratio"] >= 0.012 or p["native_text_chars"] >= 20)
            }
            recognized, local = tesseract_pages(
                path,
                scans,
                ocr,
                time.monotonic() + ocr["timeout_seconds"],
                nonblank_scans=nonblank_scans,
            )
            engine.update(local)
        texts.update(recognized)
        info.update(method="hybrid_ocr" if len(scans) < len(native) else "ocr", ocr=engine)
        parsers.append("ocr")
    rendered: list[str] = []
    for n in sorted(texts):
        if n in scan_set:
            rendered.append(text_to_markdown(clean_soft_hyphens(texts[n])))
        else:
            rendered.append(native_rendered[n])
    markdown: str = normalized("\n".join(rendered))
    if not markdown.strip():
        raise WlhError("pdf_no_text")
    for page_text in rendered:
        # Native glyph availability does not prove reading order. Vertical CJK
        # forms can become many isolated characters while passing text counts.
        # Do not guess how to join rows or columns whose layout was not recovered.
        if _has_unverified_vertical_fragments(page_text):
            raise WlhError("pdf_layout_unverified")
    blocks = (
        service_blocks + page_blocks({n: t for n, t in texts.items() if n not in scans})
        if service_blocks
        else page_blocks(texts)
    )
    report: dict[str, Any] = quality_check(
        analysis, {"page_parse_json": blocks, "whole_text_md": markdown}, profile
    )
    return markdown, info, report, parsers
