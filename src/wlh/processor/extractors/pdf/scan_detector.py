"""Page-level PyMuPDF analysis and automatic service-to-local OCR fallback.

Thresholds follow production_document_parse.py. Native pages remain authoritative
when a document also contains scans. Quality failures are never successful Versions.
"""

from __future__ import annotations

import math
import os
import re
import sys
import time
import unicodedata
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from pathlib import Path
from typing import Any, cast

import pymupdf as _fitz  # type: ignore[import-untyped]

from wlh.cleaners.extraction_quality import block_text, json_value, quality_check
from wlh.cleaners.text_cleaner import normalized
from wlh.core import WlhError
from wlh.processor.extractors.base import (
    EngineAdapter,
    ExtractionInput,
    ExtractionOptions,
    QuirkHookRunner,
)
from wlh.processor.extractors.html.html_to_md import text_to_markdown
from wlh.processor.extractors.ocr.br_ocr import br_ocr
from wlh.processor.extractors.ocr.tesseract import (
    tesseract_image_bytes,
    tesseract_pages,
    vision_pages,
)
from wlh.processor.extractors.pdf.native_md import (
    clean_soft_hyphens,
    find_tables,
    page_markdown,
)
from wlh.processor.extractors.quirks.ja_kanpo_pua import decode_pua_text
from wlh.processor.extractors.quirks.vi_tcvn3 import normalize_detected_tcvn3

fitz: Any = _fitz

PDF_CONTRACT = "wlh.pdf-scan.v16"
SCAN_TEXT_CHARS = 30
SCAN_INK_RATIO = 0.003
INK_THRESHOLD = 210
_INK_LUT = bytes(1 if b < INK_THRESHOLD else 0 for b in range(256))
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]+")
ANALYSIS_SCALE = 0.5
NORMAL_PAGE_TEXT_CHARS = 350
SIGNATURE_DOC_MAX_TEXT_CHARS = 500
SIGNATURE_PAGE_MIN_COVERAGE = 0.85
SIGNATURE_PAGE_MIN_INK = 0.01


def _has_unverified_vertical_fragments(page_text: str) -> bool:
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    fragments = sum(bool(re.fullmatch(r"[\u3040-\u30ff\u3400-\u9fff]", line)) for line in lines)
    long_lines = sum(len(line) >= 5 for line in lines)
    if long_lines >= 8 and long_lines * 2 >= fragments:
        return False
    return fragments >= 12 and fragments / max(len(lines), 1) >= 0.25


def _rect_has_ink(pix: Any, rect: Any, page_rect: Any) -> bool:
    sx = float(pix.width) / max(float(page_rect.width), 1.0)
    sy = float(pix.height) / max(float(page_rect.height), 1.0)
    x0 = max(0, min(int(pix.width) - 1, int((float(rect.x0) - float(page_rect.x0)) * sx)))
    y0 = max(0, min(int(pix.height) - 1, int((float(rect.y0) - float(page_rect.y0)) * sy)))
    x1 = max(x0 + 1, min(int(pix.width), math.ceil((float(rect.x1) - float(page_rect.x0)) * sx)))
    y1 = max(y0 + 1, min(int(pix.height), math.ceil((float(rect.y1) - float(page_rect.y0)) * sy)))
    mv = memoryview(pix.samples)
    stride = pix.stride
    for row_y in range(y0, y1):
        row_slice = mv[row_y * stride + x0 : row_y * stride + x1]
        if any(b < INK_THRESHOLD for b in row_slice):
            return True
    return False


def _inspect_native_encoding(page: Any, text: str, pix: Any) -> tuple[str, bool]:
    has_cc = any(unicodedata.category(c) == "Cc" and not c.isspace() for c in text)
    non_space = [c for c in text if not c.isspace()]
    co_count = sum(1 for c in non_space if unicodedata.category(c) == "Co")
    has_heavy_pua = bool(non_space) and (co_count / len(non_space) > 0.05)
    if not has_cc and not has_heavy_pua:
        return text, False
    rect = page.rect
    body_cc_with_ink = has_heavy_pua
    if not body_cc_with_ink:
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    st = str(span.get("text") or "")
                    if not any(unicodedata.category(c) == "Cc" and not c.isspace() for c in st):
                        continue
                    sb = span.get("bbox")
                    if not sb:
                        continue
                    is_margin_folio = (
                        float(sb[1]) >= rect.height * 0.90 or float(sb[3]) <= rect.height * 0.06
                    ) and len(st.strip()) <= 16
                    if is_margin_folio:
                        continue
                    if _rect_has_ink(pix, fitz.Rect(sb), rect):
                        body_cc_with_ink = True
                        break
                if body_cc_with_ink:
                    break
            if body_cc_with_ink:
                break
    if not body_cc_with_ink:
        return _CTRL_RE.sub(" ", text), False
    if len(page.get_drawings()) >= 4 and any(
        t.row_count >= 2 and t.col_count >= 2 for t in find_tables(page, "lines_strict")
    ):
        return text, False
    return _CTRL_RE.sub(" ", text), True


def _xref_has_ink(document: Any, xref: int) -> bool:
    """Check whether an embedded image XObject itself contains non-blank ink (>= 0.2%)."""
    if xref <= 0:
        return False
    try:
        pix = fitz.Pixmap(document, xref)
        if pix.colorspace and pix.colorspace.n > 1:
            pix = fitz.Pixmap(fitz.csGRAY, pix)
        samples = pix.samples
        if not samples:
            return False
        step = max(1, len(samples) // 10000)
        sampled = samples[::step]
        dark = sum(1 for b in sampled if b < 245)
        return (dark / len(sampled)) >= 0.002
    except Exception:
        return True


def analyze(path: str | Path | Any) -> tuple[dict[str, Any], list[str]]:
    raw_pages: list[dict[str, Any]] = []
    texts: list[str] = []
    try:
        with fitz.open(path) if isinstance(path, (str, Path)) else nullcontext(path) as document:
            if getattr(document, "is_closed", False) or document.needs_pass or not len(document):
                raise WlhError("pdf_invalid_or_encrypted")
            for number in range(1, len(document) + 1):
                page = document[number - 1]
                raw_text = str(page.get_text("text"))
                decoded_raw = decode_pua_text(raw_text)
                is_kanpo_pua_page = "\ue05a" not in raw_text and (
                    decoded_raw != raw_text
                    or (
                        "官" in raw_text
                        and "報" in raw_text
                        and ("号)" in raw_text or "号）" in raw_text)
                    )
                )
                rect = page.rect
                page_area = max(rect.width * rect.height, 1.0)
                image_rects = [
                    (int(img[0]), page.get_image_rects(int(img[0])))
                    for img in page.get_images(full=True)
                ]
                # Quality gates need measured ink even for rich native text: a text
                # layer may be invisible. Skipping the raster is not a blank page.
                pix = page.get_pixmap(
                    matrix=fitz.Matrix(ANALYSIS_SCALE, ANALYSIS_SCALE), colorspace=fitz.csGRAY
                )
                text, unreadable_body = _inspect_native_encoding(page, decoded_raw, pix)
                native = len(text.strip())
                samples = bytes(pix.samples)
                if not samples:
                    raise WlhError("pdf_ink_measurement_failed")
                ink = samples.translate(_INK_LUT).count(1) / len(samples)
                placed_rects: list[Any] = []
                overlay_xrefs: list[int] = []
                for xref, rects in image_rects:
                    if not _xref_has_ink(document, xref):
                        continue
                    for r in rects:
                        if r.width <= 1.5 or r.height <= 1.5 or not _rect_has_ink(pix, r, rect):
                            continue
                        cov = (r.width * r.height) / page_area
                        if (
                            number <= 2
                            and native >= NORMAL_PAGE_TEXT_CHARS
                            and cov <= 0.015
                            and float(r.y1) <= float(rect.height) * 0.30
                        ):
                            continue
                        if is_kanpo_pua_page and (cov < 0.35 or native >= 500):
                            continue
                        if is_kanpo_pua_page:
                            if xref not in overlay_xrefs:
                                overlay_xrefs.append(xref)
                            continue
                        placed_rects.append(r)
                img_area_total = sum(r.width * r.height for r in placed_rects)
                image_coverage = min(img_area_total / page_area, 1.0)
                raw_pages.append(
                    {
                        "page": number,
                        "native_text_chars": native,
                        "embedded_images": len(placed_rects),
                        "image_coverage": round(image_coverage, 4),
                        "ink_ratio": round(ink, 5),
                        "_unreadable_body": unreadable_body,
                        "_overlay_xrefs": overlay_xrefs,
                    }
                )
                texts.append(text)
    except WlhError:
        raise
    except Exception:
        raise WlhError("pdf_invalid_or_encrypted") from None

    total_native = sum(p["native_text_chars"] for p in raw_pages)
    has_normal_text_page = any(
        p["native_text_chars"] >= NORMAL_PAGE_TEXT_CHARS and not p.get("_unreadable_body")
        for p in raw_pages
    )
    max_sparse_doc_chars = max(SIGNATURE_DOC_MAX_TEXT_CHARS, 220 * len(raw_pages))

    pages: list[dict[str, Any]] = []
    for p in raw_pages:
        unreadable_body = bool(p.pop("_unreadable_body", False))
        overlay_xrefs = list(p.pop("_overlay_xrefs", []))
        is_pure_scan = (
            p["native_text_chars"] < SCAN_TEXT_CHARS
            and not (
                p["native_text_chars"] >= 15
                and p["embedded_images"] == 0
                and float(p["image_coverage"]) == 0.0
                and float(p["ink_ratio"]) < 0.02
            )
            and (p["ink_ratio"] > SCAN_INK_RATIO or p["embedded_images"] > 0)
            and (not has_normal_text_page or p["embedded_images"] > 0 or p["image_coverage"] > 0)
        )
        is_signature_scan = (
            not has_normal_text_page
            and total_native < max_sparse_doc_chars
            and p["native_text_chars"] < NORMAL_PAGE_TEXT_CHARS
            and float(p["image_coverage"]) >= 0.25
            and float(p["ink_ratio"]) >= SIGNATURE_PAGE_MIN_INK
        )
        is_full_page_appendix_scan = (
            has_normal_text_page
            and int(p["page"]) >= 2
            and int(p["embedded_images"]) == 1
            and float(p["image_coverage"]) >= 0.95
            and float(p["ink_ratio"]) >= SIGNATURE_PAGE_MIN_INK
        )
        suspected = (
            is_pure_scan or is_signature_scan or is_full_page_appendix_scan or unreadable_body
        )
        page_entry: dict[str, Any] = dict(p, suspected_scan=suspected)
        if overlay_xrefs:
            page_entry["overlay_xrefs"] = overlay_xrefs
        pages.append(page_entry)

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
    *,
    invalid_body_patterns: Sequence[str] = (),
) -> tuple[str, dict[str, Any], dict[str, Any], list[str]]:
    try:
        document = fitz.open(path)
    except Exception:
        raise WlhError("pdf_invalid_or_encrypted") from None
    with document:
        analysis, native = analyze(document)
        scans = analysis["suspected_scan_pages"]
        if any(
            page["embedded_images"] and not page["suspected_scan"] for page in analysis["pages"]
        ):
            raise WlhError("pdf_images_unverified")
        if any(
            unicodedata.category(char) == "Cc" and not char.isspace()
            for text in native
            for char in text
        ):
            raise WlhError("pdf_native_text_unreadable")
        texts: dict[int, str] = {}
        recovered = False
        for i, text in enumerate(native, 1):
            texts[i], changed = normalize_detected_tcvn3(text) if profile == "vi" else (text, False)
            recovered |= changed
        info: dict[str, Any] = {
            "kind": "pdf",
            "analysis": analysis,
            "method": "native",
            "ocr": None,
        }
        parsers: list[str] = ["pdf", "quality"]
        service_blocks: list[dict[str, Any]] = []
        if recovered:
            parsers.append("tcvn3")
        scan_set = set(scans)
        native_rendered: dict[int, str] = {}
        overlay_by_page = {
            int(p["page"]): list(p.get("overlay_xrefs") or [])
            for p in analysis["pages"]
            if p.get("overlay_xrefs")
        }
        if len(scan_set) < len(native):
            ocr_langs = str(ocr.get("languages") or "jpn_vert+jpn")
            active_overlays = set(sorted(overlay_by_page)[:20])
            ov_pngs_by_page: dict[int, list[bytes]] = {}
            for n in range(1, len(native) + 1):
                if n not in scan_set:
                    page_text = page_markdown(document[n - 1], native[n - 1])
                    if profile == "vi":
                        page_text, _ = normalize_detected_tcvn3(page_text)
                    if _has_unverified_vertical_fragments(page_text):
                        raise WlhError("pdf_layout_unverified")
                    native_rendered[n] = page_text
                    if n in active_overlays:
                        max_dim = 1000 if len(overlay_by_page) > 20 else 1600
                        png_list: list[bytes] = []
                        for xr in overlay_by_page[n]:
                            pix = fitz.Pixmap(document, int(xr))
                            if pix.colorspace and pix.colorspace.n > 1:
                                pix = fitz.Pixmap(fitz.csGRAY, pix)
                            while pix.width > max_dim and pix.height > max_dim:
                                pix.shrink(1)
                            png_list.append(bytes(pix.tobytes("png")))
                        if png_list:
                            ov_pngs_by_page[n] = png_list
            if ov_pngs_by_page:
                import concurrent.futures

                all_tasks: list[tuple[int, bytes]] = [
                    (page_no, png_bytes)
                    for page_no, p_list in ov_pngs_by_page.items()
                    for png_bytes in p_list
                ]

                def _ocr_overlay(item: tuple[int, bytes]) -> tuple[int, str]:
                    return (
                        item[0],
                        tesseract_image_bytes(item[1], languages=ocr_langs, timeout=120.0),
                    )

                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(4, len(all_tasks))
                ) as pool:
                    ov_results: list[tuple[int, str]] = list(pool.map(_ocr_overlay, all_tasks))
                by_p: dict[int, list[str]] = {}
                for page_no, ov_txt in ov_results:
                    if ov_txt:
                        by_p.setdefault(page_no, []).append(ov_txt)
                for page_no, parts in by_p.items():
                    extra_ov = "\n".join(parts)
                    native_rendered[page_no] = f"{native_rendered[page_no]}\n\n{extra_ov}".strip()
                    texts[page_no] = f"{texts[page_no]}\n{extra_ov}".strip()
    if scans:
        deadline = time.monotonic() + ocr["timeout_seconds"]
        recognized: dict[int, str] | None = None
        engine: dict[str, Any] = {}
        req_engine = str(ocr.get("engine") or "local")
        br_url = (os.environ.get("WLH_BR_OCR_URL") or "").strip()
        if req_engine == "br-ocr-v1" and (cached_result is not None or bool(br_url)):
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
            nonblank_scans = {
                p["page"]
                for p in analysis["pages"]
                if p["suspected_scan"] and (p["ink_ratio"] >= 0.012 or p["native_text_chars"] >= 20)
            }
            use_vision = (
                sys.platform == "darwin"
                and req_engine in {"local", "vision", "br-ocr-v1"}
                and not engine.get("fallback_from")
                and not str(ocr.get("languages") or "").startswith("jpn_vert")
            )
            local: dict[str, Any] = {}
            if use_vision:
                try:
                    recognized, local = vision_pages(
                        path,
                        scans,
                        ocr,
                        time.monotonic() + ocr["timeout_seconds"],
                        nonblank_scans=nonblank_scans,
                    )
                except WlhError:
                    recognized = None
            if recognized is None or not all(recognized.values()):
                recognized, local = tesseract_pages(
                    path,
                    scans,
                    ocr,
                    time.monotonic() + ocr["timeout_seconds"],
                    nonblank_scans=nonblank_scans,
                )
            local_blocks = local.pop("blocks", None)
            if isinstance(local_blocks, list) and local_blocks and not service_blocks:
                service_blocks = cast(list[dict[str, Any]], local_blocks)
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
        if _has_unverified_vertical_fragments(page_text):
            raise WlhError("pdf_layout_unverified")
    blocks = (
        service_blocks + page_blocks({n: t for n, t in texts.items() if n not in scans})
        if service_blocks
        else page_blocks(texts)
    )
    report: dict[str, Any] = quality_check(
        analysis,
        {"page_parse_json": blocks, "whole_text_md": markdown},
        profile,
        invalid_body_patterns=invalid_body_patterns,
    )
    return markdown, info, report, parsers


OCR_ACCEPT_ROUTES = frozenset({"accept", "accept_ocr", "accept_with_warning"})


def ocr_image_pages(
    images: Sequence[bytes], ocr: Mapping[str, Any], profile: str
) -> tuple[str, dict[str, Any]]:
    """Embedded page images (one per page) as an in-memory PDF through local scan OCR."""
    import tempfile

    if not images:
        raise WlhError("ocr_images_missing")
    if not ocr:
        from wlh.config import OCRConfig

        ocr = OCRConfig().model_dump()
    pdf_doc = fitz.open()
    try:
        for image in images:
            page = pdf_doc.new_page(width=595, height=842)
            page.insert_image(fitz.Rect(18, 18, 577, 824), stream=image)
        data = bytes(pdf_doc.tobytes())
    finally:
        pdf_doc.close()
    with tempfile.TemporaryDirectory(prefix="wlh-image-pdf-") as folder:
        pdf_path = Path(folder) / "input.pdf"
        pdf_path.write_bytes(data)
        markdown, info, quality, _ = extract_pdf(pdf_path, ocr, profile)
    if quality["recommended_route"] not in OCR_ACCEPT_ROUTES:
        raise WlhError("quality_check_failed")
    return markdown, info


class PyMuPdfHybridEngine(EngineAdapter):
    """Native page text + per-page scan OCR (br-ocr / Vision / Tesseract) with quality gate."""

    kind = "pdf"
    engine_name = "pymupdf_hybrid"
    parser_tag = "pdf"
    # PUA decoding and profile-gated TCVN3 recovery run per page inside the engine.
    native_stages = frozenset({"text_span", "post_markdown"})

    def extract(
        self, inp: ExtractionInput, opts: ExtractionOptions, hooks: QuirkHookRunner
    ) -> tuple[str, dict[str, Any]]:
        import tempfile

        def run(path: Path) -> tuple[str, dict[str, Any]]:
            markdown, info, report, parsers = extract_pdf(
                path,
                opts.ocr,
                opts.profile,
                inp.ocr_cache,
                invalid_body_patterns=opts.invalid_body_patterns,
            )
            return markdown, {**info, "_quality": report, "_parsers": parsers}

        if inp.path is not None:
            return run(inp.path)
        with tempfile.TemporaryDirectory(prefix="wlh-pdf-") as folder:
            path = Path(folder) / "input.pdf"
            path.write_bytes(inp.data)
            return run(path)
