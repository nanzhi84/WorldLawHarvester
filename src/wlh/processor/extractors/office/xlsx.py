"""OOXML Excel (``.xlsx``): openpyxl read-only streaming with trailing empty column trimming."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wlh.core import WlhError
from wlh.processor.extractors.base import (
    EngineAdapter,
    ExtractionInput,
    ExtractionOptions,
    QuirkHookRunner,
)
from wlh.processor.extractors.office.ooxml_zip import normalized_office_archive
from wlh.processor.extractors.office.tables import excel_value, sheet_markdown

MIN_SHEET_CHARS = 8


def extract_xlsx(source: bytes | str | Path) -> tuple[str, str]:
    """Workbook sheets as Markdown tables (including spreadsheets disguised as DOCX)."""
    from openpyxl import load_workbook  # type: ignore[import-untyped]

    sheets: list[tuple[Any, list[list[str]]]] = []
    try:
        with normalized_office_archive(source) as handle:
            workbook: Any = load_workbook(handle, read_only=True, data_only=True)
            try:
                for sheet in workbook.worksheets:
                    rows = [
                        [excel_value(v) for v in row] for row in sheet.iter_rows(values_only=True)
                    ]
                    sheets.append((sheet.title, rows))
            finally:
                workbook.close()
    except Exception:
        raise WlhError("spreadsheet_invalid") from None
    value = sheet_markdown(sheets)
    if len(value) < MIN_SHEET_CHARS:
        raise WlhError("spreadsheet_empty")
    return value + "\n", "openpyxl"


class OpenpyxlEngine(EngineAdapter):
    kind = "xlsx"
    engine_name = "openpyxl"
    parser_tag = "office"

    def extract(
        self, inp: ExtractionInput, opts: ExtractionOptions, hooks: QuirkHookRunner
    ) -> tuple[str, dict[str, Any]]:
        return extract_xlsx(inp.data)[0], {}


__all__ = ["OpenpyxlEngine", "extract_xlsx"]
