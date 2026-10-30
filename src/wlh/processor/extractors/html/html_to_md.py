"""Deterministic legal HTML conversion; no browser, network or implicit caches."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, cast
from urllib.parse import urljoin, urlsplit

import lxml.etree as _etree  # type: ignore[import-untyped]

from wlh.cleaners.text_cleaner import UI_ACTION_LABELS, normalized
from wlh.core import WlhError
from wlh.processor.extractors.html.dom_tree import document_tree
from wlh.processor.extractors.html.tables_and_lists import (
    expand_rows,
    ordered_marker,
    span,
    spanning_table,
)

etree: Any = _etree

MARKDOWN_CONTRACT = "wlh.markdown.v6"


def _remove_element(el: Any) -> None:
    parent = el.getparent()
    if parent is None:
        return
    if el.tail:
        prev = el.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def clean_html_tree(node: Any, *, quirks: Sequence[str] | None = None) -> None:
    """In-place strip of standard non-content HTML tags and configured DOM cleanup rules."""
    from wlh.processor.parsers.html.dom_cleanup import clean_site_dom

    clean_site_dom(
        node,
        dom_transforms=("inline_script_cell_restore",),
        include_defaults=True,
    )

    for bad_tag in list(
        node.xpath(".//script|.//style|.//noscript|.//template|.//svg|.//button|.//nav")
    ):
        _remove_element(bad_tag)

    if quirks is None or "ja_inline_glyphs" in quirks or "ja_kanpo_pua" in quirks:
        from wlh.processor.extractors.quirks.ja_kanpo_pua import inline_glyph_text

        for img_el in list(node.xpath(".//img")):
            glyph = inline_glyph_text(img_el)
            if glyph is not None:
                parent = img_el.getparent()
                if parent is not None:
                    prev = img_el.getprevious()
                    if prev is not None:
                        prev.tail = (prev.tail or "") + glyph + (img_el.tail or "")
                    else:
                        parent.text = (parent.text or "") + glyph + (img_el.tail or "")
                    parent.remove(img_el)


def text_to_markdown(value: str) -> str:
    return normalized(value)


def json_to_markdown(value: str) -> str:
    try:
        parsed = json.loads(value)
    except ValueError:
        return text_to_markdown(value)
    if not isinstance(parsed, dict):
        return text_to_markdown(value)
    top = cast(dict[str, Any], parsed)
    inner: dict[str, Any] = (
        cast(dict[str, Any], next(iter(top.values())))
        if len(top) == 1 and isinstance(next(iter(top.values())), dict)
        else next(
            (cast(dict[str, Any], v) for v in top.values() if isinstance(v, dict)),
            top,
        )
    )
    sections: list[str] = []
    seen_values: set[str] = set()
    for key, val in inner.items():
        if val is None or isinstance(val, bool):
            continue
        if isinstance(val, (str, int, float)):
            sval = str(val).strip()
            if not sval or sval in seen_values:
                continue
            seen_values.add(sval)
            if len(sval) >= 40 or "\n" in sval:
                sections.append(f"### {key}\n\n{sval}")
            else:
                sections.append(f"- **{key}**: {sval}")
    if not sections:
        return text_to_markdown(value)
    return normalized("\n\n".join(sections))


def html_to_markdown(
    html: str | bytes,
    base_url: str | None = None,
    content_xpath: str | None = None,
    *,
    quirks: Sequence[str] | None = None,
) -> str:
    try:
        tree = document_tree(html)
        for comment in list(tree.xpath("//comment()")):
            _remove_element(comment)
        nodes: list[Any] = (
            tree.xpath(content_xpath)
            if content_xpath
            else [tree.body if tree.body is not None else tree]
        )
    except (etree.Error, ValueError):
        raise WlhError("html_unparseable") from None
    if not nodes or any(not isinstance(n, etree._Element) for n in nodes):
        raise WlhError("content_region_missing")
    for n in nodes:
        clean_html_tree(n, quirks=quirks)

    def inline(text: str | None) -> str:
        return re.sub(r"\s+", " ", text or "")

    def escape_literal_order(text: str) -> str:
        return re.sub(r"^(\s*\d{1,9})([.)])(?=\s)", r"\1\\\2", text)

    def children(node: Any) -> str:
        has_br = any(isinstance(c.tag, str) and c.tag.lower() == "br" for c in node)
        head = escape_literal_order(inline(node.text)) if has_br else inline(node.text)
        chunks: list[str] = [head]
        for c in node:
            chunks.append(render(c))
            tail = inline(c.tail)
            if isinstance(c.tag, str) and c.tag.lower() == "br":
                tail = escape_literal_order(tail)
            chunks.append(tail)
        return "".join(chunks)

    def table(node: Any) -> str:
        raw_rows: list[list[tuple[str, int, int]]] = []
        for tr in node.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr"):
            cells = [
                (re.sub(r"\s+", " ", children(c)).strip().replace("|", "\\|"),
                 span(c.get("colspan")), span(c.get("rowspan")))
                for c in tr.xpath("./th|./td")
            ]
            if cells:
                raw_rows.append(cells)
        raw_rows = [
            [
                (text, colspan, min(rowspan, len(raw_rows) - r_idx))
                for text, colspan, rowspan in row
            ]
            for r_idx, row in enumerate(raw_rows)
        ]
        rows = expand_rows(raw_rows)
        if not rows:
            return ""
        if (any(colspan > 1 or rowspan > 1 for row in raw_rows for _, colspan, rowspan in row)
                or node.xpath(".//table | .//th[@scope]")):
            return spanning_table(node, base_url, set(UI_ACTION_LABELS))
        caption = " ".join(children(c).strip() for c in node.xpath("./caption")).strip()
        width = max(map(len, rows))
        rows = [row + [""] * (width - len(row)) for row in rows]
        rows.insert(1, ["---"] * width)
        return ("\n\n" + (caption + "\n\n" if caption else "")
                + "\n".join("| " + " | ".join(row) + " |" for row in rows) + "\n\n")

    def render(node: Any) -> str:
        if not isinstance(node.tag, str):
            return ""
        tag = node.tag.split("}")[-1].split(":")[-1].lower()
        if tag in {
            "script",
            "style",
            "noscript",
            "template",
            "head",
            "nav",
            "footer",
            "form",
            "button",
            "svg",
        }:
            return ""
        if tag == "table":
            return table(node)
        if tag == "pre":
            return "\n\n```\n" + node.text_content().strip() + "\n```\n\n"
        value = children(node)
        if tag == "a":
            label = value.strip()
            if not label:
                return ""
            raw_href = (node.get("href") or "").strip()
            onclick = (node.get("onclick") or "").lower()
            if label.lower() in UI_ACTION_LABELS and (
                not raw_href
                or raw_href.startswith(("#", "javascript:"))
                or raw_href.endswith("#")
                or any(
                    k in onclick
                    for k in ("print", "gospecialurl", "history.back", "window.close", "fontzoom")
                )
            ):
                return ""
            if (
                not raw_href
                or raw_href.lower().startswith(("#", "javascript:"))
                or raw_href.endswith("#")
            ):
                return value
            try:
                href = urljoin(base_url or "", raw_href)
                scheme = urlsplit(href).scheme if href else ""
            except ValueError:
                href, scheme = "", ""
            if href and scheme in {"http", "https"}:
                lead = " " if value[:1].isspace() else ""
                trail = " " if value[-1:].isspace() else ""
                return f"{lead}[{label}]({href}){trail}"
            return value
        if tag in {"strong", "b"}:
            stripped = value.strip()
            if not stripped:
                return ""
            lead = " " if value[:1].isspace() else ""
            trail = " " if value[-1:].isspace() else ""
            return f"{lead}**{stripped}**{trail}"
        if tag in {"em", "i"}:
            stripped = value.strip()
            if not stripped:
                return ""
            lead = " " if value[:1].isspace() else ""
            trail = " " if value[-1:].isspace() else ""
            return f"{lead}*{stripped}*{trail}"
        if tag == "code":
            return "`" + value.strip() + "`"
        if tag == "br":
            return "\n\n"
        if re.fullmatch(r"h[1-6]", tag):
            heading = re.sub(r"\s+", " ", value).strip()
            return "\n\n" + "#" * int(tag[1]) + " " + heading + "\n\n" if heading else ""
        if tag == "li":
            if not value.strip():
                return ""
            marker = ordered_marker(node)
            if not marker:
                literal = re.sub(r"(?m)^(\s*\d{1,9})([.)])(?=\s)", r"\1\\\2", value.strip())
                return "\n\n" + literal + "\n\n"
            return "\n" + marker + re.sub(r"\n+", "\n  ", value.strip()) + "\n"
        if tag == "blockquote":
            return "\n\n" + "\n".join("> " + line for line in value.strip().splitlines()) + "\n\n"
        if tag in {"p", "div", "section", "article", "main", "ul", "ol", "header", "body", "hr"}:
            body_text = value.strip()
            if tag == "p" and "\n" in body_text:
                body_text = re.sub(r"(?m)^(\s*\d{1,9})([.)])(?=\s)", r"\1\\\2", body_text)
            return "\n\n" + body_text + "\n\n"
        return value

    selected = {id(n) for n in nodes}
    parts: list[str] = []
    seen_parents: set[int] = set()
    for node in nodes:
        parent = node.getparent()
        if parent is not None and id(parent) not in selected and id(parent) not in seen_parents:
            seen_parents.add(id(parent))
            if node.getprevious() is None and inline(parent.text).strip():
                parts.append(escape_literal_order(inline(parent.text).strip()))
        parts.append(render(node))
        if parent is not None and id(parent) not in selected and inline(node.tail).strip():
            parts.append(escape_literal_order(inline(node.tail).strip()))
    return normalized("\n\n".join(parts))
