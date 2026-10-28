"""Preserve legal XML cells, explicit spans and source-defined border continuity."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

import lxml.etree as etree

from wlh.adapters.legal_xml_inline import inline_content
from wlh.core import WlhError
from wlh.extraction.table_grid import expand_rows, span

etree: Any
TABLE_TAGS = {"table", "Table", "TABLE"}
ROW_TAGS = {"tr", "TR", "TableRow", "TableHeaderRow"}
CELL_TAGS = {"td", "th", "TD", "TH", "TableColumn", "TableHeaderColumn"}


def _tag(node: Any) -> str:
    return etree.QName(node).localname if isinstance(node.tag, str) else ""


def table_markdown(node: Any) -> str | None:
    if any(_tag(child) in TABLE_TAGS for child in node.iterdescendants()):
        raise WlhError("legal_xml_nested_table_unverified")
    raw_rows: list[list[tuple[str, int, int]]] = []
    source_rows: list[Any] = []
    for row in node.iterdescendants():
        if _tag(row) not in ROW_TAGS:
            continue
        cells = [cell for cell in row if _tag(cell) in CELL_TAGS]
        if not cells:
            continue
        source_rows.append(row)
        raw_rows.append([
            (inline_content(cell).replace("|", "\\|"),
             span(cell.get("colspan") or cell.get("COLSPAN")),
             span(cell.get("rowspan") or cell.get("ROWSPAN")))
            for cell in cells
        ])
    rows = expand_rows(raw_rows)
    if not rows:
        return None

    spanning = any(colspan > 1 or rowspan > 1 for row in raw_rows for _, colspan, rowspan in row)
    borders = any(name.startswith("Border") and value != "solid"
                  for row in source_rows for cell in row for name, value in cell.attrib.items())
    # Remove only row content from a copy so caption mixed content (including
    # scientific scripts and footnote references) keeps its original structure.
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
    caption = re.sub(r"\s+", " ", inline_content(caption_node, html_output=spanning or borders)).strip()
    if spanning or borders:
        return _semantic_table(source_rows, caption)
    width = max(map(len, rows))
    rows = [row + [""] * (width - len(row)) for row in rows]
    rows.insert(1, ["---"] * width)
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows)
    return "\n\n" + ((caption + "\n\n") if caption else "") + body + "\n\n"


def _semantic_table(rows: list[Any], caption: str) -> str:
    """Use explicit HTML scope; a blank Markdown cell cannot express inheritance.

    The bounded grid was validated above. Only semantic table elements and span
    values are emitted, never source styles, event handlers or arbitrary markup.
    Text remains single-copy at its authoritative anchor, including ruby/scripts.
    """
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
                # In e-Gov, paired absent borders express a shared visible group
                # even when the XML contains separate blank cells. Preserve that
                # evidence without inventing inherited text or guessed rowspans.
                value = "none" if border == "none" else (
                    ("3px" if border == "double" else "1px") + " " + border + " currentColor"
                )
                styles.append("border-" + side.lower() + ":" + value)
            if styles:
                attributes += ' style="' + ";".join(styles) + '"'
            output.append(f"<{tag}{attributes}>" + inline_content(cell, html_output=True) + f"</{tag}>")
        output.append("</tr>")
    if group:
        output.append(f"</{group}>")
    output.append("</table>")
    return "\n\n" + "".join(output) + "\n\n"
