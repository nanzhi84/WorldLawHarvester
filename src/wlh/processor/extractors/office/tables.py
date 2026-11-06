"""Shared Office table -> Markdown helpers."""

from __future__ import annotations

from typing import Any

from wlh.cleaners.text_cleaner import compact


def table_markdown(rows: list[list[str]]) -> str:
    """One Markdown table block; the first row is the header (source geometry unknown)."""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines: list[str] = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(lines)


def excel_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return compact(str(value))


def trim_trailing_empty_cols(rows: list[list[str]]) -> list[list[str]]:
    """Slice every row to the last column holding a value anywhere in the sheet.

    Government spreadsheets often format whole rows, which makes readers report thousands
    of empty trailing cells; they must not become empty Markdown columns.
    """
    width = 0
    for row in rows:
        for index in range(len(row) - 1, width - 1, -1):
            if row[index]:
                width = index + 1
                break
    return [row[:width] for row in rows]


def sheet_markdown(sheets: list[tuple[Any, list[list[str]]]]) -> str:
    """Workbook sheets as ``# name`` + compact Markdown table blocks."""
    parts: list[str] = []
    for name, rows in sheets:
        nonempty = trim_trailing_empty_cols([row for row in rows if any(cell for cell in row)])
        if not nonempty:
            continue
        width = max(len(row) for row in nonempty)
        aligned = [row + [""] * (width - len(row)) for row in nonempty]
        parts.append("# " + compact(str(name)))
        parts.append(table_markdown([[c.replace("|", "\\|") for c in row] for row in aligned]))
    return "\n\n".join(parts).strip()


__all__ = ["excel_value", "sheet_markdown", "table_markdown", "trim_trailing_empty_cols"]
