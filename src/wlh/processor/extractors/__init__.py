"""Stage 3b Format Extractors (PDF, Word/Office, OCR, HTML->Markdown, and Quirks)."""

from __future__ import annotations

from wlh.processor.extractors.html import (
    MARKDOWN_CONTRACT,
    clean_html_tree,
    document_tree,
    html_to_markdown,
    json_to_markdown,
    require_textual_images,
    text_to_markdown,
)
from wlh.processor.extractors.ocr import br_ocr, tesseract_image_bytes, tesseract_pages
from wlh.processor.extractors.pdf import PDF_CONTRACT, analyze, extract_pdf, page_markdown
from wlh.processor.extractors.quirks import QUIRKS
from wlh.processor.extractors.router import run_extraction
from wlh.processor.extractors.slots import PDFSlots
from wlh.processor.extractors.word import (
    OFFICE_CONTRACT,
    OFFICE_KINDS,
    detect_real_format,
    extract_docx,
    extract_excel,
    extract_office,
    libreoffice_identity,
    salvage_zip_prefix,
    unpack_zip,
)

__all__ = [
    "MARKDOWN_CONTRACT",
    "OFFICE_CONTRACT",
    "OFFICE_KINDS",
    "PDFSlots",
    "PDF_CONTRACT",
    "QUIRKS",
    "analyze",
    "br_ocr",
    "clean_html_tree",
    "detect_real_format",
    "document_tree",
    "extract_docx",
    "extract_excel",
    "extract_office",
    "extract_pdf",
    "html_to_markdown",
    "json_to_markdown",
    "libreoffice_identity",
    "page_markdown",
    "require_textual_images",
    "run_extraction",
    "salvage_zip_prefix",
    "tesseract_image_bytes",
    "tesseract_pages",
    "text_to_markdown",
    "unpack_zip",
]
