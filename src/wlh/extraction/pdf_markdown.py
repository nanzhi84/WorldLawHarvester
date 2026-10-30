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
from typing import Any, cast

from wlh.core import WlhError
from wlh.extraction.markdown import normalized

_KEY = re.compile(r"\d[\w.()–/−-]{0,24}\Z")
_PAGE_FOLIO = re.compile(r"[\s\d\-–—―Page頁ページ()/]+\Z", re.I)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]+")
_VERT_CJK_CHAR = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")

_PUA_CHAR_MAP: dict[str, str] = {
    "\ue88a": "0",
    "\ue88b": "1",
    "\ue88c": "2",
    "\ue88d": "3",
    "\ue88e": "4",
    "\ue88f": "5",
    "\ue890": "6",
    "\ue891": "7",
    "\ue892": "8",
    "\ue893": "9",
    "\ue0a8": "・",
    "\ue61c": "－",
    "\uea75": ",",
    "\ue05a": "⑴",
    "\ue000": "𠮟",
    "\ue003": "𠮷",
    "\ue005": "",
    "\ue00b": "麴",
    "\ue090": "𦥑",
    "\ue7b2": "〔",
    "\ue7b3": "〕",
}
_PUA_RESIDUAL_RE = re.compile(r"[\ue000-\uf8ff]+")


def decode_pua_text(text: str) -> str:
    """Decode National Printing Bureau (Kanpo) & NTA Private Use Area and vertical font glyphs."""
    orig = text
    for src, dst in _PUA_CHAR_MAP.items():
        if src in text:
            text = text.replace(src, dst)
    if "\u617a" in text:
        text = text.replace("\u617a", "－")
    if "\u5a23" in text:
        text = text.replace("\u5a23", "")
    if text != orig and _PUA_RESIDUAL_RE.search(text):
        text = _PUA_RESIDUAL_RE.sub("", text)
    return text


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


def native_lines(page: Any, tables: list[Any] | None = None) -> list[Line]:
    rules = _vertical_rules(page)
    for tbl in tables or []:
        for row in tbl.rows:
            for cell_box in row.cells:
                if cell_box is not None:
                    rules.append((float(cell_box[0]), float(cell_box[1]), float(cell_box[3])))
                    rules.append((float(cell_box[2]), float(cell_box[1]), float(cell_box[3])))
    raw: list[Line] = []
    raw_dict: dict[str, Any] = cast(dict[str, Any], page.get_text("rawdict", flags=0))
    has_replacement_char = False
    for b_item in cast(list[Any], raw_dict.get("blocks") or []):
        if not isinstance(b_item, dict):
            continue
        for ln_item in cast(list[Any], cast(dict[str, Any], b_item).get("lines") or []):
            if not isinstance(ln_item, dict):
                continue
            for s_item in cast(list[Any], cast(dict[str, Any], ln_item).get("spans") or []):
                if not isinstance(s_item, dict):
                    continue
                for ch_item in cast(list[Any], cast(dict[str, Any], s_item).get("chars") or []):
                    if isinstance(ch_item, dict) and cast(dict[str, Any], ch_item).get("c") == "\ufffd":
                        has_replacement_char = True
                        break
    if has_replacement_char:
        raw_dict = cast(dict[str, Any], page.get_text("rawdict"))
    for block in raw_dict["blocks"]:
        for line in block.get("lines", []):
            raw_spans_val = line.get("spans")
            raw_spans: list[dict[str, Any]] = (
                [
                    cast(dict[str, Any], s)
                    for s in cast(list[object], raw_spans_val)
                    if isinstance(s, dict)
                ]
                if isinstance(raw_spans_val, list)
                else []
            )
            spans: list[dict[str, Any]] = []
            for s in raw_spans:
                raw_chars = s.get("chars")
                chars: list[dict[str, Any]] = (
                    [
                        cast(dict[str, Any], ch)
                        for ch in cast(list[object], raw_chars)
                        if isinstance(ch, dict)
                    ]
                    if isinstance(raw_chars, list)
                    else []
                )
                if not chars:
                    t = decode_pua_text(_CTRL_RE.sub(" ", str(s.get("text") or "")))
                    if t:
                        spans.append({"text": t, "bbox": s["bbox"]})
                    continue
                line_dir: Any = line.get("dir", (1.0, 0.0))
                if (
                    isinstance(line_dir, (list, tuple))
                    and len(cast(list[Any], line_dir)) >= 2
                    and abs(float(cast(list[Any], line_dir)[0]))
                    >= abs(float(cast(list[Any], line_dir)[1]))
                    and len(chars) >= 2
                    and float(chars[0]["bbox"][0]) > float(chars[-1]["bbox"][0]) + 1.0
                ):
                    chars = sorted(chars, key=lambda ch: round(float(ch["bbox"][0]), 1))
                sb = tuple(float(v) for v in s["bbox"])
                if _has_vertical_rule_between(rules, sb[0] + 1.0, sb[2] - 1.0, sb[1], sb[3]):
                    cur_chars: list[dict[str, Any]] = [chars[0]]
                    for c in chars[1:]:
                        prev_c = cur_chars[-1]
                        if _has_vertical_rule_between(
                            rules,
                            float(prev_c["bbox"][0]),
                            float(c["bbox"][2]),
                            sb[1],
                            sb[3],
                        ):
                            ctext = decode_pua_text(
                                _CTRL_RE.sub(
                                    " ", "".join(str(ch["c"]) for ch in cur_chars if "c" in ch)
                                )
                            )
                            if ctext:
                                spans.append(
                                    {
                                        "text": ctext,
                                        "bbox": (
                                            min(float(ch["bbox"][0]) for ch in cur_chars),
                                            min(float(ch["bbox"][1]) for ch in cur_chars),
                                            max(float(ch["bbox"][2]) for ch in cur_chars),
                                            max(float(ch["bbox"][3]) for ch in cur_chars),
                                        ),
                                    }
                                )
                            cur_chars = [c]
                        else:
                            cur_chars.append(c)
                    ctext = decode_pua_text(
                        _CTRL_RE.sub(" ", "".join(str(ch["c"]) for ch in cur_chars if "c" in ch))
                    )
                    if ctext:
                        spans.append(
                            {
                                "text": ctext,
                                "bbox": (
                                    min(float(ch["bbox"][0]) for ch in cur_chars),
                                    min(float(ch["bbox"][1]) for ch in cur_chars),
                                    max(float(ch["bbox"][2]) for ch in cur_chars),
                                    max(float(ch["bbox"][3]) for ch in cur_chars),
                                ),
                            }
                        )
                else:
                    ctext = decode_pua_text(
                        _CTRL_RE.sub(" ", "".join(str(ch["c"]) for ch in chars if "c" in ch))
                    )
                    if ctext:
                        spans.append({"text": ctext, "bbox": s["bbox"]})
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
                    and _has_vertical_rule_between(
                        rules, pb[2], sb[0], min(pb[1], sb[1]), max(pb[3], sb[3])
                    )
                ):
                    groups.append([span])
                elif (
                    not span["text"].strip() and len(groups[-1]) == 1 and prev_span["text"].strip()
                ):
                    groups[-1].append(span)
                else:
                    stripped_prev = next(
                        (s for s in reversed(groups[-1]) if s["text"].strip()), None
                    )
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
            and -1.0 <= gap <= h * 9.0
            and not _has_vertical_rule_between(
                rules,
                prev.box[2],
                curr.box[0],
                min(prev.box[1], curr.box[1]),
                max(prev.box[3], curr.box[3]),
            )
            and (len(prev.text.strip()) <= 4 or len(curr.text.strip()) <= 4)
        ):
            sep = (
                " "
                if gap >= h * 0.8 and not prev.text.endswith(" ") and not curr.text.startswith(" ")
                else ""
            )
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
    text = "\n".join(
        line.text for line in sorted(lines, key=lambda line: (line.middle, line.box[0]))
    )
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
    col_xs: list[list[float]] = [[] for _ in range(table.col_count)]
    row_ys: list[list[float]] = [[] for _ in range(table.row_count)]
    for r_idx, row in enumerate(table.rows):
        for c_idx, cell_box in enumerate(row.cells):
            if cell_box is not None:
                col_xs[c_idx].append(float(cell_box[0] + cell_box[2]) / 2.0)
                row_ys[r_idx].append(float(cell_box[1] + cell_box[3]) / 2.0)
    if any(not xs for xs in col_xs) or any(not ys for ys in row_ys):
        if any(cell_box is None for row in table.rows for cell_box in row.cells):
            raise WlhError("pdf_layout_unverified")
        return None
    col_x = [median(xs) for xs in col_xs]
    row_y = [median(ys) for ys in row_ys]
    owner: dict[tuple[int, int], tuple[int, int]] = {}
    owner_box: dict[tuple[int, int], tuple[float, float, float, float]] = {}
    for r_idx, row in enumerate(table.rows):
        for c_idx, cell_box in enumerate(row.cells):
            if cell_box is None:
                continue
            bx0, by0, bx1, by1 = (
                float(cell_box[0]),
                float(cell_box[1]),
                float(cell_box[2]),
                float(cell_box[3]),
            )
            owner_box[(r_idx, c_idx)] = (bx0, by0, bx1, by1)
            for rr in range(table.row_count):
                if by0 - 2.0 <= row_y[rr] <= by1 + 2.0:
                    for cc in range(table.col_count):
                        if bx0 - 2.0 <= col_x[cc] <= bx1 + 2.0:
                            owner.setdefault((rr, cc), (r_idx, c_idx))
    if len(owner) < table.row_count * table.col_count:
        raise WlhError("pdf_layout_unverified")
    relevant = [line for line in lines if _inside(line, table.bbox)]
    if not relevant:
        return None
    cell_lines: dict[tuple[int, int], list[Line]] = {pos: [] for pos in owner_box}
    for line in relevant:
        matched = [pos for pos, box in owner_box.items() if _inside(line, box, 3.0)]
        if len(matched) != 1:
            return None
        cell_lines[matched[0]].append(line)
    for clines in cell_lines.values():
        vert_frags = sum(bool(_VERT_CJK_CHAR.fullmatch(ln.text.strip())) for ln in clines)
        if vert_frags >= 4 and vert_frags / max(len(clines), 1) >= 0.5:
            raise WlhError("pdf_layout_unverified")
    rows: list[list[list[Line]]] = [
        [list(cell_lines[owner[(r_idx, c_idx)]]) for c_idx in range(table.col_count)]
        for r_idx in range(table.row_count)
    ]
    expanded_rows: list[list[list[Line]]] = []
    for r_idx, row in enumerate(rows):
        if r_idx >= 1 and len(row) >= 2:
            k = len(row[0])
            if k >= 2 and all(len(cell) == k for cell in row):
                sorted_cols = [
                    sorted(cell, key=lambda ln: (ln.middle, ln.box[0])) for cell in row
                ]
                if all(
                    max(col[i].middle for col in sorted_cols)
                    - min(col[i].middle for col in sorted_cols)
                    <= 3.0
                    for i in range(k)
                ):
                    for i in range(k):
                        expanded_rows.append([[col[i]] for col in sorted_cols])
                    continue
        expanded_rows.append(row)
    return _table(expanded_rows)


def _same_row(key: Line, lines: list[Line]) -> list[Line]:
    return sorted(
        [line for line in lines if abs(line.middle - key.middle) < key.height * 0.6],
        key=lambda line: line.box[0],
    )


def find_tables(page: Any, strategy: str) -> Any:
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
        for row in regular
        for column, line in enumerate(row)
    ):
        return None

    def column(line: Line) -> int:
        return min(range(width), key=lambda n: abs(line.box[0] - starts[n]))

    first, last = keys[0], keys[-1]
    header = [
        line
        for line in lines
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
        line
        for line in lines
        if first.middle - height * 0.6 <= line.middle <= bottom
        and table.bbox[0] - 3 <= line.box[0] <= table.bbox[2]
    ]
    rows: list[list[list[Line]]] = [header_cells] + [[[] for _ in range(width)] for _ in keys]
    for line in body:
        row = max(
            (n for n, key in enumerate(keys) if key.middle <= line.middle + height * 0.6), default=0
        )
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
    keys = sorted(
        [line for line in lines if leader.search(line.text)], key=lambda line: line.middle
    )
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
        following = [
            line.middle
            for line in lines
            if line.middle > key.middle + key.height * 0.6 and line.box[0] < right_start - 3
        ]
        end = keys[i + 1].middle if i + 1 < len(keys) else min(following, default=float("inf"))
        right = [
            line
            for line in lines
            if abs(line.box[0] - right_start) <= 3
            and key.middle - key.height * 0.5 <= line.middle < end - key.height * 0.5
        ]
        right.sort(key=lambda line: line.middle)
        if (
            not left
            or not right
            or any(b.middle - a.middle > key.height * 2.5 for a, b in zip(right, right[1:]))
        ):
            raise WlhError("pdf_layout_unverified")
        rows.append([left, right])
    return _table(rows)


def _detect_composite_boxes(page: Any) -> list[tuple[float, float, float, float]]:
    """Detect rectangular frames drawn with 4 thin filled border rectangles ('f')."""
    horiz_edges: list[tuple[float, float, float, float]] = []
    vert_edges: list[tuple[float, float, float, float]] = []
    for d in page.get_drawings():
        rect = d.get("rect")
        if not rect:
            continue
        x0, y0, x1, y1 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
        w, h = x1 - x0, y1 - y0
        if w > 180.0 and h <= 3.5:
            horiz_edges.append((x0, y0, x1, y1))
        elif h > 45.0 and w <= 3.5:
            vert_edges.append((x0, y0, x1, y1))
    horiz_edges.sort(key=lambda r: r[1])
    used_h: set[int] = set()
    frames: list[tuple[float, float, float, float]] = []
    for i, top in enumerate(horiz_edges):
        if i in used_h:
            continue
        for j in range(i + 1, len(horiz_edges)):
            if j in used_h:
                continue
            bot = horiz_edges[j]
            if (
                abs(top[0] - bot[0]) < 4.0
                and abs(top[2] - bot[2]) < 4.0
                and (bot[1] - top[3]) > 45.0
            ):
                has_left = any(
                    abs(v[0] - top[0]) < 4.0 and v[1] <= top[3] + 4.0 and v[3] >= bot[1] - 4.0
                    for v in vert_edges
                )
                has_right = any(
                    abs(v[2] - top[2]) < 4.0 and v[1] <= top[3] + 4.0 and v[3] >= bot[1] - 4.0
                    for v in vert_edges
                )
                if has_left and has_right:
                    frames.append((top[0], top[1], top[2], bot[3]))
                    used_h.add(i)
                    used_h.add(j)
                    break
    return frames


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
    candidate_boxes: list[tuple[float, float, float, float]] = []
    for drawing in page.get_drawings():
        if "s" not in drawing["type"]:
            continue
        for item in drawing["items"]:
            if item[0] == "re":
                r = tuple(item[1])
                candidate_boxes.append((float(r[0]), float(r[1]), float(r[2]), float(r[3])))
    composite_boxes = _detect_composite_boxes(page)
    candidate_boxes.extend(composite_boxes)
    for box in candidate_boxes:
        if box[2] - box[0] < page.rect.width * 0.6 or box[3] - box[1] < height * 3:
            continue
        framed_prose = [line for line in prose if _inside(line, box, 3.0)]
        if len(framed_prose) < 2:
            continue
        framed_all = [line for line in current if _inside(line, box, 3.0)]
        framed_ids = {line.index for line in framed_all}
        first, last = (
            min(line.index for line in framed_prose),
            max(line.index for line in framed_prose),
        )
        out_of_order = False
        for line in prose:
            if line.index in framed_ids or line.box[2] <= box[0] or line.box[0] >= box[2]:
                continue
            if (line.box[1] > box[3] and line.index < last) or (
                line.box[3] < box[1] and line.index > first
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
    # If a bottom-margin standalone numeric folio was emitted as the first stream line on a
    # continuation page (where the first prose line starts near the top and is indented),
    # move the bottom folio to the bottom of the page so cross-page sentences remain continuous.
    if (
        not composite_boxes
        and len(current) >= 2
        and re.fullmatch(r"\d+", current[0].text.strip())
        and current[0].box[1] >= page.rect.height * 0.90
        and current[1].box[1] <= page.rect.height * 0.12
        and current[1].box[0] >= 58.0
    ):
        reordered_folio = current[1:] + [current[0]]
        current = [Line(i, line.box, line.text) for i, line in enumerate(reordered_folio)]
    return current


def _join_vertical_columns(lines: list[Line], *, min_ratio: float = 0.25) -> list[Line]:
    vert_count = sum(bool(_VERT_CJK_CHAR.fullmatch(ln.text.strip())) for ln in lines)
    if vert_count < 12 or vert_count / max(len(lines), 1) < min_ratio:
        return lines
    vert: list[Line] = []
    horiz: list[Line] = []
    for ln in lines:
        w = ln.box[2] - ln.box[0]
        if len(ln.text.strip()) <= 2 and w <= max(ln.height * 1.8, 24.0):
            vert.append(ln)
        else:
            horiz.append(ln)
    if len(vert) < 12:
        return lines
    med_h = max(median(ln.height for ln in vert), 8.0)
    x_tol = max(med_h * 0.65, 6.0)
    vert_by_x = sorted(vert, key=lambda ln: -((ln.box[0] + ln.box[2]) / 2.0))
    x_cols: list[list[Line]] = []
    col_cxs: list[float] = []
    for ln in vert_by_x:
        cx = (ln.box[0] + ln.box[2]) / 2.0
        if x_cols and abs(cx - col_cxs[-1]) <= x_tol:
            x_cols[-1].append(ln)
            col_cxs[-1] = sum((item.box[0] + item.box[2]) / 2.0 for item in x_cols[-1]) / len(
                x_cols[-1]
            )
        else:
            x_cols.append([ln])
            col_cxs.append(cx)
    segments: list[tuple[float, float, float, Line]] = []
    y_gap_max = max(med_h * 3.0, 36.0)
    for col in x_cols:
        col_sorted = sorted(col, key=lambda ln: (ln.box[1], -ln.box[0]))
        cur_seg = [col_sorted[0]]
        for ln in col_sorted[1:]:
            if ln.box[1] - cur_seg[-1].box[3] > y_gap_max:
                cx = sum((item.box[0] + item.box[2]) / 2.0 for item in cur_seg) / len(cur_seg)
                box = (
                    min(item.box[0] for item in cur_seg),
                    min(item.box[1] for item in cur_seg),
                    max(item.box[2] for item in cur_seg),
                    max(item.box[3] for item in cur_seg),
                )
                segments.append(
                    (
                        box[1],
                        box[3],
                        cx,
                        Line(0, box, "".join(item.text.strip() for item in cur_seg)),
                    )
                )
                cur_seg = [ln]
            else:
                cur_seg.append(ln)
        cx = sum((item.box[0] + item.box[2]) / 2.0 for item in cur_seg) / len(cur_seg)
        box = (
            min(item.box[0] for item in cur_seg),
            min(item.box[1] for item in cur_seg),
            max(item.box[2] for item in cur_seg),
            max(item.box[3] for item in cur_seg),
        )
        segments.append(
            (box[1], box[3], cx, Line(0, box, "".join(item.text.strip() for item in cur_seg)))
        )
    segments.sort(key=lambda item: item[0])
    tiers: list[list[tuple[float, float, float, Line]]] = []
    for seg in segments:
        if not tiers:
            tiers.append([seg])
            continue
        tier_y0 = min(s[0] for s in tiers[-1])
        tier_y1 = max(s[1] for s in tiers[-1])
        overlap = min(tier_y1, seg[1]) - max(tier_y0, seg[0])
        if overlap > -med_h * 1.5:
            tiers[-1].append(seg)
        else:
            tiers.append([seg])
    ordered_vert: list[Line] = []
    for tier in tiers:
        tier.sort(key=lambda item: -item[2])
        ordered_vert.extend(item[3] for item in tier)
    min_vy = min(ln.box[1] for ln in ordered_vert)
    top_horiz = sorted(
        [ln for ln in horiz if ln.middle < min_vy], key=lambda ln: (ln.middle, ln.box[0])
    )
    bot_horiz = sorted(
        [ln for ln in horiz if ln.middle >= min_vy], key=lambda ln: (ln.middle, ln.box[0])
    )
    combined = top_horiz + ordered_vert + bot_horiz
    return [Line(idx, ln.box, ln.text) for idx, ln in enumerate(combined)]


def page_markdown(page: Any, text: str) -> str:
    raw_page_text = _CTRL_RE.sub(" ", str(page.get_text("text")))
    clean_text = decode_pua_text(_CTRL_RE.sub(" ", text))
    is_kanpo_page = "\ue05a" not in raw_page_text and (
        decode_pua_text(raw_page_text) != raw_page_text
        or (
            "官" in raw_page_text
            and "報" in raw_page_text
            and ("号)" in raw_page_text or "号）" in raw_page_text)
        )
    )
    strict_tables = (
        list(find_tables(page, "lines_strict"))
        if (not is_kanpo_page and len(page.get_drawings()) >= 4)
        else []
    )
    lines = _check_box_order(page, native_lines(page, strict_tables)) if not is_kanpo_page else native_lines(page, [])
    if sum(bool(_VERT_CJK_CHAR.fullmatch(ln.text.strip())) for ln in lines) >= 80:
        strict_tables = []
    if not strict_tables:
        lines = _join_vertical_columns(lines, min_ratio=(0.05 if is_kanpo_page else 0.25))
    if is_kanpo_page:
        cleaned_lines = [
            Line(ln.index, ln.box, _PUA_RESIDUAL_RE.sub("", ln.text))
            for ln in lines
            if _PUA_RESIDUAL_RE.sub("", ln.text).strip()
        ]
        return normalized(clean_soft_hyphens("\n".join(ln.text for ln in cleaned_lines)))

    # Text dictionaries and plain extraction can differ for unusual fonts. In
    # that case, keep the authoritative native string rather than lose glyphs.
    def compact(value: str) -> Counter[str]:
        return Counter(char for char in value if not char.isspace())

    if compact("".join(line.text for line in lines)) != compact(clean_text):
        return normalized(clean_soft_hyphens(clean_text))
    tables: list[tuple[set[int], str]] = []
    for table in strict_tables:
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
        for table in find_tables(page, "text"):
            parsed = _borderless(table, lines)
            if parsed and not any(parsed[0] & used for used, _ in tables):
                tables.append(parsed)
    if not tables:
        return normalized(clean_soft_hyphens("\n".join(line.text for line in lines)))
    starts = {min(used): rendered for used, rendered in tables}
    consumed = {index for used, _ in tables for index in used}
    output = [starts.get(line.index, "") if line.index in consumed else line.text for line in lines]
    return normalized(clean_soft_hyphens("\n".join(output)))
