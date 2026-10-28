"""Lossless native PDF lines with conservative, coordinate-backed table recovery.

Ruled cells come from PyMuPDF. Borderless tables additionally need a repeated
short numeric key and stable cell starts: ordinary aligned prose is insufficient.
Every source line is either emitted unchanged or assigned once to a table cell.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from contextlib import redirect_stdout
from dataclasses import dataclass
from statistics import median
from typing import Any

from wlh.core import WlhError
from wlh.extraction.markdown import normalized

_KEY = re.compile(r"\d[\w.()–/−-]{0,24}\Z")
_PAGE_FOLIO = re.compile(r"[\s\d\-–—―Page頁ページ()/]+\Z", re.I)


def clean_soft_hyphens(text: str) -> str:
    """A discretionary hyphen is layout; ordinary hyphens remain authoritative."""
    return re.sub("\u00ad[ \t]*\n[ \t]*", "", text).replace("\u00ad", "")


@dataclass(frozen=True)
class Line:
    index: int
    box: tuple[float, float, float, float]
    text: str

    @property
    def middle(self) -> float:
        return (self.box[1] + self.box[3]) / 2

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]


def _vertical_rules(page: Any) -> list[tuple[float, float, float]]:
    rules: list[tuple[float, float, float]] = []
    for drawing in page.get_drawings():
        for item in drawing.get("items", []):
            kind = item[0]
            if kind == "re":
                r = tuple(item[1])
                if r[3] - r[1] >= 8:
                    if r[2] - r[0] <= 3:
                        rules.append(((r[0] + r[2]) / 2, r[1], r[3]))
                    else:
                        rules.append((r[0], r[1], r[3]))
                        rules.append((r[2], r[1], r[3]))
            elif kind == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.x - p2.x) <= 2 and abs(p1.y - p2.y) >= 8:
                    rules.append(((p1.x + p2.x) / 2, min(p1.y, p2.y), max(p1.y, p2.y)))
    return rules


def _has_vertical_rule_between(
    rules: list[tuple[float, float, float]], x0: float, x1: float, y0: float, y1: float
) -> bool:
    mid = (y0 + y1) / 2
    return any(x0 - 1 <= rx <= x1 + 1 and ry0 - 2 <= mid <= ry1 + 2 for rx, ry0, ry1 in rules)


def native_lines(page: Any) -> list[Line]:
    rules = _vertical_rules(page)
    raw: list[Line] = []
    for block in page.get_text("dict", flags=0)["blocks"]:
        for line in block.get("lines", []):
            spans = [span for span in line.get("spans", []) if span.get("text")]
            if not spans:
                continue
            groups: list[list[dict[str, Any]]] = [[spans[0]]]
            for span in spans[1:]:
                prev_span = groups[-1][-1]
                sb = tuple(span["bbox"])
                pb = tuple(prev_span["bbox"])
                if (
                    span["text"].strip()
                    and prev_span["text"].strip()
                    and sb[0] >= pb[2] - 1
                    and _has_vertical_rule_between(rules, pb[2], sb[0], min(pb[1], sb[1]), max(pb[3], sb[3]))
                ):
                    groups.append([span])
                elif not span["text"].strip() and len(groups[-1]) == 1 and prev_span["text"].strip():
                    groups[-1].append(span)
                else:
                    stripped_prev = next((s for s in reversed(groups[-1]) if s["text"].strip()), None)
                    if (
                        span["text"].strip()
                        and stripped_prev is not None
                        and sb[0] >= stripped_prev["bbox"][2] - 1
                        and _has_vertical_rule_between(
                            rules,
                            stripped_prev["bbox"][2],
                            sb[0],
                            min(stripped_prev["bbox"][1], sb[1]),
                            max(stripped_prev["bbox"][3], sb[3]),
                        )
                    ):
                        groups.append([span])
                    else:
                        groups[-1].append(span)
            for grp in groups:
                text = "".join(s["text"] for s in grp)
                if text.strip():
                    bbox = (
                        min(float(s["bbox"][0]) for s in grp),
                        min(float(s["bbox"][1]) for s in grp),
                        max(float(s["bbox"][2]) for s in grp),
                        max(float(s["bbox"][3]) for s in grp),
                    )
                    raw.append(Line(len(raw), bbox, text))
    if not raw:
        return []
    merged: list[Line] = [raw[0]]
    for curr in raw[1:]:
        prev = merged[-1]
        h = min(prev.height, curr.height)
        gap = curr.box[0] - prev.box[2]
        if (
            h > 0
            and abs(curr.middle - prev.middle) <= h * 0.35
            and -1.0 <= gap <= h * 6.0
            and not _has_vertical_rule_between(
                rules, prev.box[2], curr.box[0], min(prev.box[1], curr.box[1]), max(prev.box[3], curr.box[3])
            )
            and (len(prev.text.strip()) <= 4 or len(curr.text.strip()) <= 4)
        ):
            sep = " " if gap >= h * 0.8 and not prev.text.endswith(" ") and not curr.text.startswith(" ") else ""
            box = (
                min(prev.box[0], curr.box[0]),
                min(prev.box[1], curr.box[1]),
                max(prev.box[2], curr.box[2]),
                max(prev.box[3], curr.box[3]),
            )
            merged[-1] = Line(prev.index, box, prev.text + sep + curr.text)
        else:
            merged.append(Line(len(merged), curr.box, curr.text))
    return merged


def _cell(lines: list[Line]) -> str:
    text = "\n".join(line.text for line in sorted(lines, key=lambda line: (line.middle, line.box[0])))
    text = clean_soft_hyphens(text).replace("|", r"\|")
    return re.sub(r"\s+", " ", text).strip()


def _table(rows: list[list[list[Line]]]) -> tuple[set[int], str]:
    used = {line.index for row in rows for cell in row for line in cell}
    output = [[_cell(cell) for cell in row] for row in rows]
    output.insert(1, ["---"] * len(output[0]))
    return used, "\n\n" + "\n".join("| " + " | ".join(row) + " |" for row in output) + "\n\n"


def _inside(line: Line, box: tuple[float, ...], tolerance: float = 2) -> bool:
    return (
        line.box[0] >= box[0] - tolerance
        and line.box[2] <= box[2] + tolerance
        and line.box[1] >= box[1] - tolerance
        and line.box[3] <= box[3] + tolerance
    )


def _ruled(table: Any, lines: list[Line]) -> tuple[set[int], str] | None:
    if table.row_count < 2 or table.col_count < 2:
        return None
    if any(box is None for row in table.rows for box in row.cells):
        # A merged or incomplete cell cannot be represented as an empty column:
        # that would falsely attach its text to just one of the covered headers.
        raise WlhError("pdf_layout_unverified")
    rows: list[list[list[Line]]] = [[[] for _ in row.cells] for row in table.rows]
    relevant = [line for line in lines if _inside(line, table.bbox)]
    for line in relevant:
        cells = [
            (i, j)
            for i, row in enumerate(table.rows)
            for j, box in enumerate(row.cells)
            if box is not None and _inside(line, box)
        ]
        if len(cells) != 1:
            # Merged/rotated or overlapping text needs a richer representation;
            # preserve it as source text instead of guessing cell ownership.
            return None
        i, j = cells[0]
        rows[i][j].append(line)
    return _table(rows) if relevant else None


def _same_row(key: Line, lines: list[Line]) -> list[Line]:
    return sorted(
        [line for line in lines if abs(line.middle - key.middle) < key.height * 0.6],
        key=lambda line: line.box[0],
    )


def _find_tables(page: Any, strategy: str) -> Any:
    # Some PyMuPDF releases print a layout-package advisory on first use. The
    # extraction worker reserves stdout for its JSON protocol, including errors.
    with redirect_stdout(sys.stderr):
        return page.find_tables(strategy=strategy, min_words_vertical=8)


def _borderless(table: Any, lines: list[Line]) -> tuple[set[int], str] | None:
    # The detector may split a long header into spurious columns. Its left edge
    # and repeated keys locate the table; actual native lines recover its cells.
    in_box = [line for line in lines if _inside(line, table.bbox, 3)]
    groups: list[list[Line]] = []
    for line in sorted(in_box, key=lambda item: item.box[0]):
        if not _KEY.fullmatch(line.text.strip()):
            continue
        if not groups or line.box[0] - groups[-1][0].box[0] > 3:
            groups.append([])
        groups[-1].append(line)
    keys: list[Line] = []
    for group in groups:
        if len(group) >= 5:
            keys = group
            break
    if len(keys) < 5 or len({line.text for line in keys}) < 5:
        return None
    if keys[0].box[0] - table.bbox[0] > (table.bbox[2] - table.bbox[0]) * 0.2:
        return None
    keys.sort(key=lambda line: line.middle)
    aligned = [_same_row(key, in_box) for key in keys]
    width, frequency = Counter(len(row) for row in aligned).most_common(1)[0]
    if not 3 <= width <= 8 or frequency < len(keys) * 0.8:
        return None
    regular = [row for row in aligned if len(row) == width]
    starts = [median(row[column].box[0] for row in regular) for column in range(width)]
    if any(right - left < 8 for left, right in zip(starts, starts[1:])):
        return None
    height = median(line.height for line in keys)
    # Cell starts must repeat; text fragments accidentally aligned in prose do not.
    if any(
        abs(line.box[0] - starts[column]) > height * 2
        for row in regular for column, line in enumerate(row)
    ):
        return None

    def column(line: Line) -> int:
        return min(range(width), key=lambda n: abs(line.box[0] - starts[n]))

    first, last = keys[0], keys[-1]
    header = [
        line for line in lines
        if first.box[1] - height * 5 <= line.box[1] < first.box[1] - height * 0.5
        and table.bbox[0] - 3 <= line.box[0] <= table.bbox[2]
        and len(line.text) <= 120
    ]
    header_cells: list[list[Line]] = [[] for _ in range(width)]
    for line in header:
        header_cells[column(line)].append(line)
    # A real header must label every column. Do not consume preceding prose.
    if not all(header_cells):
        header_cells = [[] for _ in range(width)]

    bottom = float(table.bbox[3])
    for line in sorted(lines, key=lambda item: item.middle):
        if line.middle <= last.middle + height * 0.6:
            continue
        if line.box[1] > bottom + 3:
            break
        if column(line) == 0 or line.box[1] - last.box[3] > height * 3:
            bottom = min(bottom, line.box[1] - 1)
            break
    body = [
        line for line in lines
        if first.middle - height * 0.6 <= line.middle <= bottom
        and table.bbox[0] - 3 <= line.box[0] <= table.bbox[2]
    ]
    rows: list[list[list[Line]]] = [header_cells] + [[[] for _ in range(width)] for _ in keys]
    for line in body:
        row = max((n for n, key in enumerate(keys) if key.middle <= line.middle + height * 0.6), default=0)
        cell = column(line)
        if cell == 0 and line.index != keys[row].index:
            return None
        if cell < width - 1 and line.box[2] > starts[cell + 1] + height:
            return None
        rows[row + 1][cell].append(line)
    if any(not all(row) for row in rows[1:]):
        return None
    return _table(rows)


def _leader_table(lines: list[Line]) -> tuple[set[int], str] | None:
    """Dotted leaders explicitly pair a left label with an aligned right value."""
    leader = re.compile(r"[.·・･…]{4,}\s*$")
    keys = sorted([line for line in lines if leader.search(line.text)], key=lambda line: line.middle)
    if len(keys) < 2:
        return None
    partners: list[Line] = []
    for key in keys:
        right = [line for line in _same_row(key, lines) if line.box[0] > key.box[2]]
        if len(right) != 1:
            raise WlhError("pdf_layout_unverified")
        partners.append(right[0])
    starts = [line.box[0] for line in partners]
    if max(starts) - min(starts) > 3:
        raise WlhError("pdf_layout_unverified")
    right_start = median(starts)
    rows: list[list[list[Line]]] = []
    for i, key in enumerate(keys):
        left = [line for line in _same_row(key, lines) if line.box[2] <= right_start]
        left = [Line(line.index, line.box, leader.sub("", line.text)) for line in left]
        following = [line.middle for line in lines
                     if line.middle > key.middle + key.height * 0.6
                     and line.box[0] < right_start - 3]
        end = keys[i + 1].middle if i + 1 < len(keys) else min(following, default=float("inf"))
        right = [line for line in lines
                 if abs(line.box[0] - right_start) <= 3
                 and key.middle - key.height * 0.5 <= line.middle < end - key.height * 0.5]
        right.sort(key=lambda line: line.middle)
        if not left or not right or any(
            b.middle - a.middle > key.height * 2.5 for a, b in zip(right, right[1:])
        ):
            raise WlhError("pdf_layout_unverified")
        rows.append([left, right])
    return _table(rows)


def _check_box_order(page: Any, lines: list[Line]) -> list[Line]:
    """A full-width framed clause cannot be moved across the prose below it.

    A native stream may emit all ordinary paragraphs and then all text boxes.
    When a full-width framed box has no side-by-side prose overlapping its
    vertical band, restore its vertical position; otherwise reject the layout.
    """
    prose = [
        line
        for line in lines
        if len(line.text.strip()) >= 5 and not _PAGE_FOLIO.fullmatch(line.text.strip())
    ]
    if not prose:
        return lines
    height = median(line.height for line in prose)
    current = list(lines)
    for drawing in page.get_drawings():
        if "s" not in drawing["type"]:
            continue
        for item in drawing["items"]:
            if item[0] != "re":
                continue
            box = tuple(item[1])
            if box[2] - box[0] < page.rect.width * 0.6 or box[3] - box[1] < height * 3:
                continue
            framed_prose = [line for line in prose if _inside(line, box)]
            if len(framed_prose) < 2:
                continue
            framed_all = [line for line in current if _inside(line, box)]
            framed_ids = {line.index for line in framed_all}
            first, last = min(line.index for line in framed_prose), max(line.index for line in framed_prose)
            out_of_order = False
            for line in prose:
                if line.index in framed_ids or line.box[2] <= box[0] or line.box[0] >= box[2]:
                    continue
                if (line.box[1] > box[3] and line.index < first) or (
                    line.box[3] < box[1] and line.index > last
                ):
                    out_of_order = True
                    break
            if not out_of_order:
                continue
            # If any unframed prose shares the vertical band of the box (multi-column), reject.
            if any(
                line.index not in framed_ids and box[1] + 2 < line.middle < box[3] - 2
                for line in prose
            ):
                raise WlhError("pdf_layout_unverified")
            unframed = [line for line in current if line.index not in framed_ids]
            ordered_framed = sorted(framed_all, key=lambda line: (line.middle, line.box[0]))
            insert_at = len(unframed)
            for idx, line in enumerate(unframed):
                if _PAGE_FOLIO.fullmatch(line.text.strip()):
                    continue
                if line.box[1] >= box[3] - 2:
                    insert_at = idx
                    break
            reordered = unframed[:insert_at] + ordered_framed + unframed[insert_at:]
            current = [Line(i, line.box, line.text) for i, line in enumerate(reordered)]
            prose = [
                line
                for line in current
                if len(line.text.strip()) >= 5 and not _PAGE_FOLIO.fullmatch(line.text.strip())
            ]
    return current


def page_markdown(page: Any, text: str) -> str:
    lines = _check_box_order(page, native_lines(page))
    # Text dictionaries and plain extraction can differ for unusual fonts. In
    # that case, keep the authoritative native string rather than lose glyphs.
    def compact(value: str) -> Counter[str]:
        return Counter(char for char in value if not char.isspace())

    if compact("".join(line.text for line in lines)) != compact(text):
        return normalized(clean_soft_hyphens(text))
    tables: list[tuple[set[int], str]] = []
    if len(page.get_drawings()) >= 4:
        for table in _find_tables(page, "lines_strict"):
            parsed = _ruled(table, lines)
            if parsed:
                tables.append(parsed)
            elif table.row_count >= 2 and table.col_count >= 2:
                raise WlhError("pdf_layout_unverified")
    leaders = _leader_table(lines)
    if leaders and not any(leaders[0] & used for used, _ in tables):
        tables.append(leaders)
    keys = [line for line in lines if _KEY.fullmatch(line.text.strip())]
    if len(keys) >= 5 and sum(len(_same_row(key, lines)) >= 3 for key in keys) >= 5:
        for table in _find_tables(page, "text"):
            parsed = _borderless(table, lines)
            if parsed and not any(parsed[0] & used for used, _ in tables):
                tables.append(parsed)
    if not tables:
        return normalized(clean_soft_hyphens("\n".join(line.text for line in lines)))
    starts = {min(used): rendered for used, rendered in tables}
    consumed = {index for used, _ in tables for index in used}
    output = [starts.get(line.index, "") if line.index in consumed else line.text for line in lines]
    return normalized(clean_soft_hyphens("\n".join(output)))
