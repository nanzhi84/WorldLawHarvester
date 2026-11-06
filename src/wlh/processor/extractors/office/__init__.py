"""Office formats without an external office suite: one module per format, one gateway facade."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from wlh.processor.extractors.magic import OFFICE_KINDS
from wlh.processor.extractors.office.doc import (
    extract_doc,
    extract_msdoc_pure,
    extract_ole2_data_images,
    parse_ole2_streams,
)
from wlh.processor.extractors.office.docx import extract_docx
from wlh.processor.extractors.office.ooxml_zip import normalized_office_archive
from wlh.processor.extractors.office.rtf import extract_rtf
from wlh.processor.extractors.office.tables import (
    excel_value,
    table_markdown,
    trim_trailing_empty_cols,
)
from wlh.processor.extractors.office.xls import extract_xls
from wlh.processor.extractors.office.xlsx import extract_xlsx

OFFICE_CONTRACT = "wlh.office.v3"


def extract_office(
    source: str | Path | bytes,
    kind: str | None = None,
    *,
    ocr: Mapping[str, Any] | None = None,
    script_hint: str | None = None,
    preferred_engine: str | None = None,
    quirks: tuple[str, ...] = (),
) -> tuple[str, dict[str, Any]]:
    """Office document (or a file disguised with an Office suffix) through the gateway."""
    from wlh.config import OCRConfig
    from wlh.core import MAX_ORIGINAL_BYTES
    from wlh.processor.extractors.base import ExtractionInput, ExtractionOptions
    from wlh.processor.extractors.registry import default_registry

    path = None if isinstance(source, bytes) else Path(source)
    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    hint = kind or (path.suffix.lstrip(".") if path is not None else "")
    output = default_registry().dispatch(
        ExtractionInput(data=raw, kind=hint, path=path),
        ExtractionOptions(
            profile=script_hint or "generic",
            quirks=quirks,
            ocr=dict(ocr) if ocr is not None else OCRConfig().model_dump(),
            max_bytes=MAX_ORIGINAL_BYTES,
            preferred_engine=preferred_engine,
        ),
    )
    return output.markdown, output.info


def extract_excel(source: str | Path | bytes, kind: str) -> tuple[str, str]:
    """XLS/XLSX workbooks (including spreadsheets disguised as DOC/DOCX) as Markdown tables."""
    return extract_xls(source) if kind == "xls" else extract_xlsx(source)


__all__ = [
    "OFFICE_CONTRACT",
    "OFFICE_KINDS",
    "excel_value",
    "extract_doc",
    "extract_docx",
    "extract_excel",
    "extract_msdoc_pure",
    "extract_office",
    "extract_ole2_data_images",
    "extract_rtf",
    "extract_xls",
    "extract_xlsx",
    "normalized_office_archive",
    "parse_ole2_streams",
    "table_markdown",
    "trim_trailing_empty_cols",
]
