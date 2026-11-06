"""OOXML Word (``.docx``): python-docx structured text, media scans through local PDF OCR."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

from wlh.cleaners.text_cleaner import compact
from wlh.core import WlhError
from wlh.processor.extractors.base import (
    EngineAdapter,
    ExtractionInput,
    ExtractionOptions,
    QuirkHookRunner,
)
from wlh.processor.extractors.office.ooxml_zip import normalized_office_archive
from wlh.processor.extractors.office.tables import table_markdown

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif")
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MIN_SCAN_DOCX_CHARS = 200
MIN_OCR_CHARS = 80


def _media_names(archive: zipfile.ZipFile) -> list[str]:
    return sorted(
        name
        for name in archive.namelist()
        if name.startswith("word/media/") and name.lower().endswith(_IMAGE_SUFFIXES)
    )


def _textbox_text(paragraph_element: Any) -> list[str]:
    """Paragraph texts inside ``w:txbxContent`` (document numbers / issuer headers)."""
    texts: list[str] = []
    for box_paragraph in paragraph_element.iter(_W + "txbxContent"):
        for p in box_paragraph.iter(_W + "p"):
            text = compact("".join(t.text or "" for t in p.iter(_W + "t")))
            if text:
                texts.append(text)
    return texts


def extract_docx(source: bytes | str | Path) -> str:
    from docx import Document  # type: ignore[import-untyped]
    from docx.table import Table  # type: ignore[import-untyped]
    from docx.text.paragraph import Paragraph  # type: ignore[import-untyped]

    try:
        with normalized_office_archive(source) as handle:
            with zipfile.ZipFile(handle) as zf:
                has_media_images = bool(_media_names(zf))
            handle.seek(0)
            document: Any = Document(handle)
    except Exception:
        raise WlhError("docx_invalid") from None
    parts: list[str] = []
    seen_box_text: set[str] = set()
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            paragraph: Any = Paragraph(child, document)
            text = compact(paragraph.text)
            for box_text in _textbox_text(child):
                if box_text not in seen_box_text and box_text not in text:
                    seen_box_text.add(box_text)
                    parts.append(box_text)
            if not text:
                continue
            style = ((paragraph.style.name if paragraph.style else "") or "").lower()
            level = re.match(r"heading\s*([1-6])", style)
            if style == "title":
                parts.append("# " + text)
            elif level:
                parts.append("#" * int(level[1]) + " " + text)
            else:
                parts.append(text)
        elif child.tag.endswith("}tbl"):
            rows: list[list[str]] = []
            for row in Table(child, document).rows:
                cells = [compact(cell.text).replace("|", "\\|") for cell in row.cells]
                # Word represents horizontal merges as repeated cell objects.
                cells = [c for i, c in enumerate(cells) if i == 0 or c != cells[i - 1]]
                rows.append(cells)
            if rows:
                parts.append(table_markdown(rows))
    value = "\n\n".join(parts).strip()
    min_chars = MIN_SCAN_DOCX_CHARS if has_media_images else 8
    if len(compact(value)) < min_chars:
        raise WlhError("quality_too_short" if has_media_images else "docx_text_unusable")
    return value + "\n"


class PythonDocxEngine(EngineAdapter):
    kind = "docx"
    engine_name = "python_docx"
    parser_tag = "office"

    def extract(
        self, inp: ExtractionInput, opts: ExtractionOptions, hooks: QuirkHookRunner
    ) -> tuple[str, dict[str, Any]]:
        return extract_docx(inp.data), {}


class DocxMediaOcrEngine(EngineAdapter):
    kind = "docx"
    engine_name = "docx_media_pdf_ocr"
    parser_tag = "office"

    def extract(
        self, inp: ExtractionInput, opts: ExtractionOptions, hooks: QuirkHookRunner
    ) -> tuple[str, dict[str, Any]]:
        from wlh.processor.extractors.pdf.scan_detector import ocr_image_pages

        try:
            with normalized_office_archive(inp.data) as handle, zipfile.ZipFile(handle) as zf:
                images = [zf.read(name) for name in _media_names(zf)]
        except (zipfile.BadZipFile, OSError, ValueError):
            raise WlhError("docx_invalid") from None
        if not images:
            raise WlhError("quality_too_short")
        try:
            markdown, pdf_info = ocr_image_pages(images, opts.ocr, opts.profile)
        except WlhError:
            raise WlhError("quality_too_short") from None
        if len(compact(markdown)) < MIN_OCR_CHARS:
            raise WlhError("quality_too_short")
        return markdown, {"pdf": pdf_info}


__all__ = ["DocxMediaOcrEngine", "PythonDocxEngine", "extract_docx"]
