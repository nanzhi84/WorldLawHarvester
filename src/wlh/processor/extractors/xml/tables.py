"""Preserve legal XML cells, explicit spans and source-defined border continuity."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

import lxml.etree as _etree  # type: ignore[import-untyped]

from wlh.core import WlhError
from wlh.processor.extractors.html.tables_and_lists import expand_rows, span
from wlh.processor.extractors.xml.inline import inline_content

etree: Any = _etree
TABLE_TAGS = {"table", "Table", "TABLE"}
ROW_TAGS = {"tr", "TR", "TableRow", "TableHeaderRow"}
CELL_TAGS = {"td", "th", "TD", "TH", "TableColumn", "TableHeaderColumn"}


def _tag(node: Any) -> str:
    return etree.QName(node).localname if isinstance(node.tag, str) else ""


def _is_borderless_table(tbl: Any) -> bool:
    cells = [
        cell
        for row in tbl
        if _tag(row) in ROW_TAGS
        for cell in row
        if _tag(cell) in CELL_TAGS
    ]
    return bool(cells) and all(
        cell.get("Border" + side) == "none"
        for cell in cells
        for side in ("Top", "Bottom", "Left", "Right")
    )


def table_markdown(node: Any) -> str | None:
    if _is_borderless_table(node):
        return None
    if any(
        _tag(child) in TABLE_TAGS and not _is_borderless_table(child)
        for child in node.iterdescendants()
    ):
        raise WlhError("legal_xml_nested_table_unverified")
    raw_rows: list[list[tuple[str, int, int]]] = []
    source_rows: list[Any] = []
    for row in node.iterdescendants():
        if _tag(row) not in ROW_TAGS:
            continue
        owner_tbl = next((anc for anc in row.iterancestors() if _tag(anc) in TABLE_TAGS), None)
        if owner_tbl is not node:
            continue
        cells = [cell for cell in row if _tag(cell) in CELL_TAGS]
        if not cells:
            continue
        source_rows.append(row)
        raw_rows.append(
            [
                (
                    inline_content(cell).replace("|", "\\|"),
                    span(cell.get("colspan") or cell.get("COLSPAN")),
                    span(cell.get("rowspan") or cell.get("ROWSPAN")),
                )
                for cell in cells
            ]
        )
    raw_rows = [
        [
            (text, colspan, min(rowspan, len(raw_rows) - r_idx))
            for text, colspan, rowspan in row
        ]
        for r_idx, row in enumerate(raw_rows)
    ]
    rows = expand_rows(raw_rows)
    if not rows:
        return None

    spanning = any(colspan > 1 or rowspan > 1 for row in raw_rows for _, colspan, rowspan in row)
    borders = any(
        name.startswith("Border") and value != "solid"
        for row in source_rows
        for cell in row
        for name, value in cell.attrib.items()
    )
    caption_node = deepcopy(node)
    for row in list(caption_node.iterdescendants()):
        if _tag(row) not in ROW_TAGS:
            continue
        parent = row.getparent()
        previous = row.getprevious()
        if row.tail:
            if previous is None:
                parent.text = (parent.text or "") + row.tail
            else:
                previous.tail = (previous.tail or "") + row.tail
        parent.remove(row)
    caption = re.sub(
        r"\s+", " ", inline_content(caption_node, html_output=spanning or borders)
    ).strip()
    if spanning or borders:
        return _semantic_table(source_rows, caption)
    width = max(map(len, rows))
    rows = [row + [""] * (width - len(row)) for row in rows]
    rows.insert(1, ["---"] * width)
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows)
    return "\n\n" + ((caption + "\n\n") if caption else "") + body + "\n\n"


def _semantic_table(rows: list[Any], caption: str) -> str:
    output = ['<table style="border-collapse:collapse">']
    if caption:
        output.append("<caption>" + caption + "</caption>")
    group = ""
    for row in rows:
        parent_tag = _tag(row.getparent()).lower()
        current = parent_tag if parent_tag in {"thead", "tbody", "tfoot"} else ""
        if current != group:
            if group:
                output.append(f"</{group}>")
            if current:
                output.append(f"<{current}>")
            group = current
        output.append("<tr>")
        for cell in row:
            if _tag(cell) not in CELL_TAGS:
                continue
            tag = "th" if _tag(cell) in {"th", "TH", "TableHeaderColumn"} else "td"
            attributes = ""
            for name in ("rowspan", "colspan"):
                value = span(cell.get(name) or cell.get(name.upper()))
                if value > 1:
                    attributes += f' {name}="{value}"'
            scope = cell.get("scope") or cell.get("SCOPE")
            if scope in {"row", "col", "rowgroup", "colgroup"}:
                attributes += f' scope="{scope}"'
            styles: list[str] = []
            for side in ("Top", "Bottom", "Left", "Right"):
                border = cell.get("Border" + side)
                if border is None:
                    continue
                if border not in {"none", "solid", "dotted", "dashed", "double"}:
                    raise WlhError("legal_xml_table_border_unsupported")
                value = (
                    "none"
                    if border == "none"
                    else (("3px" if border == "double" else "1px") + " " + border + " currentColor")
                )
                styles.append("border-" + side.lower() + ":" + value)
            if styles:
                attributes += ' style="' + ";".join(styles) + '"'
            output.append(
                f"<{tag}{attributes}>" + inline_content(cell, html_output=True) + f"</{tag}>"
            )
        output.append("</tr>")
    if group:
        output.append(f"</{group}>")
    output.append("</table>")
    return "\n\n" + "".join(output) + "\n\n"


__all__ = [
    "CELL_TAGS",
    "ROW_TAGS",
    "TABLE_TAGS",
    "table_markdown",
]
