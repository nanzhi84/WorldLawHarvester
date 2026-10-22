"""Page-level PyMuPDF analysis and automatic service-to-local OCR fallback.

Thresholds follow production_document_parse.py. Native pages remain authoritative
when a document also contains scans. Quality failures are never successful Versions.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pymupdf as _fitz  # type: ignore[import-untyped]

from wlh.core import WlhError
from wlh.extraction.markdown import normalized, text_to_markdown
from wlh.extraction.ocr_br import br_ocr
from wlh.extraction.ocr_tesseract import tesseract_pages
from wlh.extraction.quality import block_text, json_value, quality_check
from wlh.extraction.vi_tcvn3 import normalize_detected_tcvn3

fitz: Any = _fitz

PDF_CONTRACT = "wlh.pdf-scan.v1"
SCAN_TEXT_CHARS = 30
SCAN_INK_RATIO = 0.003
INK_THRESHOLD = 210
ANALYSIS_SCALE = 0.5


def analyze(path: str | Path) -> tuple[dict[str, Any], list[str]]:
    pages: list[dict[str, Any]] = []
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
                ink = sum(v < INK_THRESHOLD for v in pix.samples) / len(pix.samples)
                pages.append(
                    {
                        "page": number,
                        "native_text_chars": native,
                        "embedded_images": len(page.get_images(full=True)),
                        "ink_ratio": round(ink, 5),
                        "suspected_scan": native < SCAN_TEXT_CHARS and ink > SCAN_INK_RATIO,
                    }
                )
                texts.append(text)
    except WlhError:
        raise
    except Exception:
        raise WlhError("pdf_invalid_or_encrypted") from None
    return {
        "contract": PDF_CONTRACT,
        "page_count": len(pages),
        "pages": pages,
        "native_text_chars": sum(p["native_text_chars"] for p in pages),
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
    texts: dict[int, str] = {}
    recovered = False
    for i, text in enumerate(native, 1):
        texts[i], changed = normalize_detected_tcvn3(text)
        recovered |= changed
    info: dict[str, Any] = {"kind": "pdf", "analysis": analysis, "method": "native", "ocr": None}
    parsers: list[str] = ["pdf", "quality"]
    service_blocks: list[dict[str, Any]] = []
    if recovered:
        parsers.append("tcvn3")
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
            recognized, local = tesseract_pages(
                path, scans, ocr, time.monotonic() + ocr["timeout_seconds"]
            )
            engine.update(local)
        texts.update(recognized)
        info.update(method="hybrid_ocr" if len(scans) < len(native) else "ocr", ocr=engine)
        parsers.append("ocr")
    markdown: str = normalized(
        "\n".join(text_to_markdown(texts[n]) for n in sorted(texts) if texts[n].strip())
    )
    if not markdown.strip():
        raise WlhError("pdf_no_text")
    blocks = (
        service_blocks + page_blocks({n: t for n, t in texts.items() if n not in scans})
        if service_blocks
        else page_blocks(texts)
    )
    report: dict[str, Any] = quality_check(
        analysis, {"page_parse_json": blocks, "whole_text_md": markdown}, profile
    )
    return markdown, info, report, parsers
