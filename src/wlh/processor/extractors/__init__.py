"""Stage 3b gateway: magic sniffing, engine registry, Office/PDF/OCR/HTML/XML engines, quirks."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wlh.processor.extractors.archive import unpack_zip
    from wlh.processor.extractors.base import (
        EngineAdapter,
        ExtractionInput,
        ExtractionOptions,
        ExtractionOutput,
        QuirkHook,
        QuirkHookRunner,
    )
    from wlh.processor.extractors.html import (
        MARKDOWN_CONTRACT,
        clean_html_tree,
        document_tree,
        html_to_markdown,
        json_to_markdown,
        require_textual_images,
        text_to_markdown,
    )
    from wlh.processor.extractors.magic import detect_real_format
    from wlh.processor.extractors.ocr import br_ocr, tesseract_image_bytes, tesseract_pages
    from wlh.processor.extractors.office import (
        OFFICE_CONTRACT,
        OFFICE_KINDS,
        extract_doc,
        extract_docx,
        extract_excel,
        extract_office,
        extract_rtf,
        extract_xls,
        extract_xlsx,
    )
    from wlh.processor.extractors.pdf import PDF_CONTRACT, analyze, extract_pdf, page_markdown
    from wlh.processor.extractors.quirks import QUIRK_HOOKS, QUIRKS, salvage_zip_prefix
    from wlh.processor.extractors.registry import ExtractorRegistry, default_registry
    from wlh.processor.extractors.router import run_extraction
    from wlh.processor.extractors.slots import PDFSlots
    from wlh.processor.extractors.xml import (
        INLINE_TAGS,
        TABLE_TAGS,
        dokument_xml_to_markdown,
        egov_law_xml_to_markdown,
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
    "EngineAdapter": ("wlh.processor.extractors.base", "EngineAdapter"),
    "ExtractionInput": ("wlh.processor.extractors.base", "ExtractionInput"),
    "ExtractionOptions": ("wlh.processor.extractors.base", "ExtractionOptions"),
    "ExtractionOutput": ("wlh.processor.extractors.base", "ExtractionOutput"),
    "QuirkHook": ("wlh.processor.extractors.base", "QuirkHook"),
    "QuirkHookRunner": ("wlh.processor.extractors.base", "QuirkHookRunner"),
    "ExtractorRegistry": ("wlh.processor.extractors.registry", "ExtractorRegistry"),
    "default_registry": ("wlh.processor.extractors.registry", "default_registry"),
    "detect_real_format": ("wlh.processor.extractors.magic", "detect_real_format"),
    "unpack_zip": ("wlh.processor.extractors.archive", "unpack_zip"),
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
    "QUIRK_HOOKS": ("wlh.processor.extractors.quirks", "QUIRK_HOOKS"),
    "salvage_zip_prefix": ("wlh.processor.extractors.quirks", "salvage_zip_prefix"),
    "run_extraction": ("wlh.processor.extractors.router", "run_extraction"),
    "PDFSlots": ("wlh.processor.extractors.slots", "PDFSlots"),
    "OFFICE_CONTRACT": ("wlh.processor.extractors.office", "OFFICE_CONTRACT"),
    "OFFICE_KINDS": ("wlh.processor.extractors.office", "OFFICE_KINDS"),
    "extract_doc": ("wlh.processor.extractors.office", "extract_doc"),
    "extract_docx": ("wlh.processor.extractors.office", "extract_docx"),
    "extract_excel": ("wlh.processor.extractors.office", "extract_excel"),
    "extract_office": ("wlh.processor.extractors.office", "extract_office"),
    "extract_rtf": ("wlh.processor.extractors.office", "extract_rtf"),
    "extract_xls": ("wlh.processor.extractors.office", "extract_xls"),
    "extract_xlsx": ("wlh.processor.extractors.office", "extract_xlsx"),
    "INLINE_TAGS": ("wlh.processor.extractors.xml", "INLINE_TAGS"),
    "TABLE_TAGS": ("wlh.processor.extractors.xml", "TABLE_TAGS"),
    "dokument_xml_to_markdown": ("wlh.processor.extractors.xml", "dokument_xml_to_markdown"),
    "egov_law_xml_to_markdown": ("wlh.processor.extractors.xml", "egov_law_xml_to_markdown"),
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
    "QUIRK_HOOKS",
    "TABLE_TAGS",
    "EngineAdapter",
    "ExtractionInput",
    "ExtractionOptions",
    "ExtractionOutput",
    "ExtractorRegistry",
    "QuirkHook",
    "QuirkHookRunner",
    "analyze",
    "br_ocr",
    "clean_html_tree",
    "default_registry",
    "detect_real_format",
    "document_tree",
    "dokument_xml_to_markdown",
    "egov_law_xml_to_markdown",
    "extract_doc",
    "extract_docx",
    "extract_excel",
    "extract_office",
    "extract_pdf",
    "extract_rtf",
    "extract_xls",
    "extract_xlsx",
    "gazette_html_to_markdown",
    "gpo_xml_to_markdown",
    "html_to_markdown",
    "inline_content",
    "json_to_markdown",
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
