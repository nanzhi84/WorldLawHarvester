"""Stage 3b Format Extractors (PDF, Word/Office, OCR, HTML->Markdown, XML->Markdown, and Quirks)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
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
    from wlh.processor.extractors.xml import (
        INLINE_TAGS,
        TABLE_TAGS,
        dokument_xml_to_markdown,
        gazette_html_to_markdown,
        gpo_xml_to_markdown,
        inline_content,
        table_markdown,
        xml_to_markdown,
    )
    from wlh.processor.extractors.xml import (
        markdown as xml_markdown,
    )

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "MARKDOWN_CONTRACT": ("wlh.processor.extractors.html", "MARKDOWN_CONTRACT"),
    "clean_html_tree": ("wlh.processor.extractors.html", "clean_html_tree"),
    "document_tree": ("wlh.processor.extractors.html", "document_tree"),
    "html_to_markdown": ("wlh.processor.extractors.html", "html_to_markdown"),
    "json_to_markdown": ("wlh.processor.extractors.html", "json_to_markdown"),
    "require_textual_images": ("wlh.processor.extractors.html", "require_textual_images"),
    "text_to_markdown": ("wlh.processor.extractors.html", "text_to_markdown"),
    "br_ocr": ("wlh.processor.extractors.ocr", "br_ocr"),
    "tesseract_image_bytes": ("wlh.processor.extractors.ocr", "tesseract_image_bytes"),
    "tesseract_pages": ("wlh.processor.extractors.ocr", "tesseract_pages"),
    "PDF_CONTRACT": ("wlh.processor.extractors.pdf", "PDF_CONTRACT"),
    "analyze": ("wlh.processor.extractors.pdf", "analyze"),
    "extract_pdf": ("wlh.processor.extractors.pdf", "extract_pdf"),
    "page_markdown": ("wlh.processor.extractors.pdf", "page_markdown"),
    "QUIRKS": ("wlh.processor.extractors.quirks", "QUIRKS"),
    "run_extraction": ("wlh.processor.extractors.router", "run_extraction"),
    "PDFSlots": ("wlh.processor.extractors.slots", "PDFSlots"),
    "OFFICE_CONTRACT": ("wlh.processor.extractors.word", "OFFICE_CONTRACT"),
    "OFFICE_KINDS": ("wlh.processor.extractors.word", "OFFICE_KINDS"),
    "detect_real_format": ("wlh.processor.extractors.word", "detect_real_format"),
    "extract_docx": ("wlh.processor.extractors.word", "extract_docx"),
    "extract_excel": ("wlh.processor.extractors.word", "extract_excel"),
    "extract_office": ("wlh.processor.extractors.word", "extract_office"),
    "libreoffice_identity": ("wlh.processor.extractors.word", "libreoffice_identity"),
    "salvage_zip_prefix": ("wlh.processor.extractors.word", "salvage_zip_prefix"),
    "unpack_zip": ("wlh.processor.extractors.word", "unpack_zip"),
    "INLINE_TAGS": ("wlh.processor.extractors.xml", "INLINE_TAGS"),
    "TABLE_TAGS": ("wlh.processor.extractors.xml", "TABLE_TAGS"),
    "dokument_xml_to_markdown": ("wlh.processor.extractors.xml", "dokument_xml_to_markdown"),
    "gazette_html_to_markdown": ("wlh.processor.extractors.xml", "gazette_html_to_markdown"),
    "gpo_xml_to_markdown": ("wlh.processor.extractors.xml", "gpo_xml_to_markdown"),
    "inline_content": ("wlh.processor.extractors.xml", "inline_content"),
    "table_markdown": ("wlh.processor.extractors.xml", "table_markdown"),
    "xml_markdown": ("wlh.processor.extractors.xml", "markdown"),
    "xml_to_markdown": ("wlh.processor.extractors.xml", "xml_to_markdown"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is not None:
        mod_name, attr_name = target
        mod = importlib.import_module(mod_name)
        val = getattr(mod, attr_name)
        globals()[name] = val
        return val
    try:
        submod = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = submod
        return submod
    except ImportError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc


__all__ = [
    "INLINE_TAGS",
    "MARKDOWN_CONTRACT",
    "OFFICE_CONTRACT",
    "OFFICE_KINDS",
    "PDFSlots",
    "PDF_CONTRACT",
    "QUIRKS",
    "TABLE_TAGS",
    "analyze",
    "br_ocr",
    "clean_html_tree",
    "detect_real_format",
    "document_tree",
    "dokument_xml_to_markdown",
    "extract_docx",
    "extract_excel",
    "extract_office",
    "extract_pdf",
    "gazette_html_to_markdown",
    "gpo_xml_to_markdown",
    "html_to_markdown",
    "inline_content",
    "json_to_markdown",
    "libreoffice_identity",
    "page_markdown",
    "require_textual_images",
    "run_extraction",
    "salvage_zip_prefix",
    "table_markdown",
    "tesseract_image_bytes",
    "tesseract_pages",
    "text_to_markdown",
    "unpack_zip",
    "xml_markdown",
    "xml_to_markdown",
]
