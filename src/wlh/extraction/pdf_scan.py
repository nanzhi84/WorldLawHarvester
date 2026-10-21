"""Page-level PyMuPDF analysis and automatic service-to-local OCR fallback.

Thresholds follow production_document_parse.py. Native pages remain authoritative
when a document also contains scans. Quality failures are never successful Versions.
"""

import time

import pymupdf as fitz

from wlh.core import WlhError
from wlh.extraction.markdown import normalized, text_to_markdown
from wlh.extraction.ocr_br import br_ocr
from wlh.extraction.ocr_tesseract import tesseract_pages
from wlh.extraction.quality import block_text, json_value, quality_check
from wlh.extraction.vi_tcvn3 import normalize_detected_tcvn3

PDF_CONTRACT = "wlh.pdf-scan.v1"
SCAN_TEXT_CHARS = 30
SCAN_INK_RATIO = 0.003
INK_THRESHOLD = 210
ANALYSIS_SCALE = 0.5


def analyze(path):
    pages, texts = [], []
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


def page_blocks(texts):
    return [
        {"type": "text", "page_idx": n, "text": text}
        for n, text in sorted(texts.items())
        if text.strip()
    ]


def extract_pdf(path, ocr, profile, cached_result=None):
    analysis, native = analyze(path)
    scans = analysis["suspected_scan_pages"]
    texts, recovered = {}, False
    for i, text in enumerate(native, 1):
        texts[i], changed = normalize_detected_tcvn3(text)
        recovered |= changed
    info = {"kind": "pdf", "analysis": analysis, "method": "native", "ocr": None}
    parsers, service_blocks = ["pdf", "quality"], []
    if recovered:
        parsers.append("tcvn3")
    if scans:
        deadline = time.monotonic() + ocr["timeout_seconds"]
        recognized = None
        engine = {}
        if ocr["engine"] == "br-ocr-v1":
            try:
                result = cached_result or br_ocr(path, ocr, deadline, page_count=len(native))
                blocks = json_value(result.get("page_parse_json"), [])
                indices = [b.get("page_idx") for b in blocks if isinstance(b, dict)]
                offset = 1 if 0 in indices else 0
                service_blocks = [
                    dict(b, page_idx=b["page_idx"] + offset)
                    for b in blocks
                    if isinstance(b, dict) and isinstance(b.get("page_idx"), int)
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
    markdown = normalized(
        "\n".join(text_to_markdown(texts[n]) for n in sorted(texts) if texts[n].strip())
    )
    if not markdown.strip():
        raise WlhError("pdf_no_text")
    blocks = (
        service_blocks + page_blocks({n: t for n, t in texts.items() if n not in scans})
        if service_blocks
        else page_blocks(texts)
    )
    report = quality_check(
        analysis, {"page_parse_json": blocks, "whole_text_md": markdown}, profile
    )
    return markdown, info, report, parsers
