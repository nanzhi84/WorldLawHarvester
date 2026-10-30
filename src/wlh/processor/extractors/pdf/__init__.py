"""PDF extractor exports."""

from __future__ import annotations

from wlh.processor.extractors.pdf.native_md import page_markdown
from wlh.processor.extractors.pdf.scan_detector import (
    INK_THRESHOLD,
    NORMAL_PAGE_TEXT_CHARS,
    PDF_CONTRACT,
    SCAN_INK_RATIO,
    SCAN_TEXT_CHARS,
    SIGNATURE_DOC_MAX_TEXT_CHARS,
    SIGNATURE_PAGE_MIN_COVERAGE,
    SIGNATURE_PAGE_MIN_INK,
    analyze,
    extract_pdf,
)

__all__ = [
    "INK_THRESHOLD",
    "NORMAL_PAGE_TEXT_CHARS",
    "PDF_CONTRACT",
    "SCAN_INK_RATIO",
    "SCAN_TEXT_CHARS",
    "SIGNATURE_DOC_MAX_TEXT_CHARS",
    "SIGNATURE_PAGE_MIN_COVERAGE",
    "SIGNATURE_PAGE_MIN_INK",
    "analyze",
    "extract_pdf",
    "page_markdown",
]
