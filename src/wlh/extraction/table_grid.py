"""A bounded cell grid shared by HTML and legal XML renderers."""

from __future__ import annotations

from collections.abc import Sequence

from wlh.core import WlhError


def span(value: str | None) -> int:
    raw = value or "1"
    if not raw.isdigit() or not 1 <= int(raw) <= 500:
        raise WlhError("table_span_invalid")
    return int(raw)


def expand_rows(rows: Sequence[Sequence[tuple[str, int, int]]]) -> list[list[str]]:
    """Put a spanning cell's text at its anchor; retain other covered positions."""
    output: list[list[str]] = []
    pending: dict[int, int] = {}
    for row in rows:
        if not row:
            continue
        values: dict[int, str] = {column: "" for column in pending}
        occupied = set(pending)
        next_pending = {column: count - 1 for column, count in pending.items() if count > 1}
        column = 0
        for text, colspan, rowspan in row:
            while column in occupied:
                column += 1
            if any(c in occupied for c in range(column, column + colspan)):
                raise WlhError("table_span_overlap")
            for offset in range(colspan):
                values[column + offset] = text if offset == 0 else ""
                occupied.add(column + offset)
                if rowspan > 1:
                    next_pending[column + offset] = rowspan - 1
            column += colspan
        if max(values, default=0) >= 500:
            raise WlhError("table_width_exceeded")
        output.append([values.get(c, "") for c in range(max(values) + 1)])
        pending = next_pending
    if pending:
        raise WlhError("table_rowspan_incomplete")
    width = max(map(len, output), default=0)
    return [row + [""] * (width - len(row)) for row in output]
