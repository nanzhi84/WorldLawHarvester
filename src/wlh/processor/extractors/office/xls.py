"""Legacy binary Excel (``.xls``): xlrd on-demand BIFF parsing with trailing empty column trimming."""

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
from wlh.processor.extractors.office.tables import excel_value, sheet_markdown

MIN_SHEET_CHARS = 8


def extract_xls(source: bytes | str | Path) -> tuple[str, str]:
    """BIFF workbook sheets straight from memory as Markdown tables."""
    import xlrd  # type: ignore[import-untyped]

    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    sheets: list[tuple[Any, list[list[str]]]] = []
    try:
        workbook: Any = xlrd.open_workbook(file_contents=raw, formatting_info=False, on_demand=True)
        try:
            for sheet in workbook.sheets():
                rows: list[list[str]] = [
                    [excel_value(sheet.cell_value(r, c)) for c in range(sheet.ncols)]
                    for r in range(sheet.nrows)
                ]
                sheets.append((sheet.name, rows))
        finally:
            workbook.release_resources()
    except Exception:
        raise WlhError("spreadsheet_invalid") from None
    value = sheet_markdown(sheets)
    if len(value) < MIN_SHEET_CHARS:
        raise WlhError("spreadsheet_empty")
    return value + "\n", "xlrd"


class XlrdEngine(EngineAdapter):
    kind = "xls"
    engine_name = "xlrd"
    parser_tag = "office"

    def extract(
        self, inp: ExtractionInput, opts: ExtractionOptions, hooks: QuirkHookRunner
    ) -> tuple[str, dict[str, Any]]:
        return extract_xls(inp.data)[0], {}


__all__ = ["XlrdEngine", "extract_xls"]
