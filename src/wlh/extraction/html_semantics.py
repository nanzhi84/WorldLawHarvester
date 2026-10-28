"""Explicit list labels and safe HTML for table relations Markdown cannot encode."""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from wlh.extraction.table_grid import span


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
    """Preserve original scope without duplicating text or copying unsafe attributes.

    Callers validate the bounded occupancy grid before entering this serializer.
    Raw HTML tables are part of Markdown and retain actual row/column spans;
    an empty continuation cell in a pipe table does not convey that relation.
    """
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
            tag = "div"  # Each item carries its explicit, source-derived label.
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
