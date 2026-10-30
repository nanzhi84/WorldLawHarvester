"""Bounded cell grid, literal Markdown escaping, explicit list labels, and spanning HTML tables."""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urljoin, urlsplit

from wlh.core import WlhError


def literal_text(value: str | None, *, html_output: bool = False) -> str:
    text = html.escape(re.sub(r"\s+", " ", value or ""), quote=False)
    if html_output:
        return text
    text = re.sub(r"([\\`*_\[\]~])", r"\\\1", text)
    text = re.sub(r"^(\s*\d{1,9})([.)])(?=\s)", r"\1\\\2", text)
    return re.sub(r"^(\s*)([#>+\-])(?=\s|$)", r"\1\\\2", text)


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


def ordered_marker(node: Any) -> str:
    parent = node.getparent()
    if any(re.search(r"(?:^|;)\s*list-style(?:-type)?\s*:\s*none\s*(?:!important\s*)?(?:;|$)",
                     element.get("style") or "", re.I)
           for element in (node, parent) if element is not None):
        return ""
    if parent is None or str(parent.tag).lower() != "ol":
        return "- "
    items = [child for child in parent if str(child.tag).lower() == "li"]
    step = -1 if "reversed" in parent.attrib else 1

    def integer(value: str | None, default: int) -> int:
        try:
            return int(value) if value is not None else default
        except ValueError:
            return default

    number = integer(parent.get("start"), len(items) if step == -1 else 1)
    for item in items:
        number = integer(item.get("value"), number)
        if item is node:
            break
        number += step
    kind = node.get("type") or parent.get("type") or "1"
    label = str(number)
    if kind in {"i", "I"} and 0 < number < 4000:
        label = ""
        for value, digits in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
                              (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
                              (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
            count, number = divmod(number, value)
            label += digits * count
        if kind == "i":
            label = label.lower()
    elif kind in {"a", "A"} and number > 0:
        label = ""
        while number:
            number, digit = divmod(number - 1, 26)
            label = chr(ord("A") + digit) + label
        if kind == "a":
            label = label.lower()
    return label + ". "


def spanning_table(node: Any, base_url: str | None, ui_labels: set[str]) -> str:
    """Preserve original scope without duplicating text or copying unsafe attributes."""
    allowed = {"table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td",
               "p", "div", "br", "strong", "b", "em", "i", "sup", "sub", "u", "del",
               "s", "ul", "li", "a"}

    def text(value: str | None) -> str:
        return html.escape(re.sub(r"\s+", " ", value or ""))

    def render(element: Any) -> str:
        if not isinstance(element.tag, str):
            return ""
        tag = element.tag.lower()
        if tag in {"script", "style", "noscript", "template", "button", "nav", "svg"}:
            return ""
        body = text(element.text) + "".join(render(c) + text(c.tail) for c in element)
        attributes = ""
        if tag in {"td", "th"}:
            for name in ("rowspan", "colspan"):
                value = span(element.get(name))
                if value > 1:
                    attributes += f' {name}="{value}"'
            if element.get("scope") in {"row", "col", "rowgroup", "colgroup"}:
                attributes += ' scope="' + element.get("scope") + '"'
        if tag == "ol":
            tag = "div"
        elif tag == "li" and (
            str(element.getparent().tag).lower() == "ol" or not ordered_marker(element)
        ):
            tag, body = "p", html.escape(ordered_marker(element)) + body
        elif tag == "a":
            raw = (element.get("href") or "").strip()
            label = "".join(element.itertext()).strip().lower()
            if label in ui_labels and (not raw or raw.startswith(("#", "javascript:"))):
                return ""
            try:
                href = urljoin(base_url or "", raw)
                if urlsplit(href).scheme in {"http", "https"} and not raw.startswith("#"):
                    attributes = ' href="' + html.escape(href, quote=True) + '"'
            except ValueError:
                pass
        if tag not in allowed:
            return body
        if tag == "br":
            return "<br>"
        return f"<{tag}{attributes}>{body}</{tag}>"

    return "\n\n" + render(node) + "\n\n"
