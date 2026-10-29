"""Deterministic legal HTML conversion; no browser, network or implicit caches."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, cast
from urllib.parse import urljoin, urlsplit

import lxml.etree as _etree  # type: ignore[import-untyped]

from wlh.core import WlhError
from wlh.extraction.html_semantics import ordered_marker, spanning_table
from wlh.extraction.html_tree import document_tree
from wlh.extraction.table_grid import expand_rows, span

etree: Any = _etree

MARKDOWN_CONTRACT = "wlh.markdown.v6"

UI_ACTION_LABELS: set[str] = {
    "【打印】",
    "【返回】",
    "【关闭】",
    "[打印]",
    "[返回]",
    "[关闭]",
    "打印本页",
    "关闭窗口",
    "print",
    "print this page",
    "back",
    "▲ to the top",
    "to the top",
    "skip to document content",
    "report a problem",
    "トップに戻る",
}


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


def clean_html_tree(node: Any) -> None:
    """In-place strip of UI chrome, hidden widgets, TOC sidebars, and inline cell scripts."""
    for sc in list(node.xpath(".//tr/script")):
        sc_text = sc.text or ""
        if "document.write" in sc_text and "_sblb" in sc_text:
            m = re.search(r"var\s+_sblb\s*=\s*['\"]([^'\"]*)['\"]", sc_text)
            val = m.group(1).strip() if m else ""
            if val == "原":
                val = ""
            td_el = etree.Element("td")
            td_el.text = val
            parent = sc.getparent()
            if parent is not None:
                parent.replace(sc, td_el)

    for toc_col in list(
        node.xpath(
            './/div[contains(@class, "col-md-4") and following-sibling::div[contains(@class, "col-md-8")]//div[contains(@class, "book")]]'
        )
    ):
        _remove_element(toc_col)

    for el in list(node.xpath(".//*[@style]")):
        st = (el.get("style") or "").lower()
        if re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", st):
            _remove_element(el)

    for bad_el in list(
        node.xpath(
            './/*[@type="RelatedNewsBox" or @data-controller="legislationContents" '
            'or @id="xgxw" or @id="history-tab" or @id="related-tab" or @id="language-switcher" '
            'or contains(@class, "extend_font") or contains(@class, "m3page_funs") '
            'or contains(@class, "share_icon") or contains(@class, "zrbj") '
            'or contains(@class, "xgxw") or contains(@class, "xg-list") '
            'or contains(@class, "hmc4ChgBtn") or contains(@class, "eyd-page-header__share") '
            'or contains(@class, "eyd-page-anchors") or contains(@class, "eyd-related-pages") '
            'or contains(@class, "eyd-breadcrumb") or contains(@class, "eyd-side-navigation") '
            'or contains(@class, "target-hidden") or contains(@class, "document-tip") '
            'or contains(@class, "tooltip-button") or contains(@class, "box-commen") '
            'or contains(@class, "text-gopy") or contains(@class, "gopy") '
            'or contains(@class, "gem-c-print-link") or contains(@class, "skip-links") '
            'or contains(@class, "to-the-top") or contains(@class, "breadcrumb")]'
        )
    ):
        _remove_element(bad_el)

    for bad_tag in list(
        node.xpath(".//script|.//style|.//noscript|.//template|.//svg|.//button|.//nav")
    ):
        _remove_element(bad_tag)


def compact(value: str) -> str:
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[ \t\u00a0]+", " ", value).strip()


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("****", "")
    value = re.sub(r"【字体：\s*大\s+中\s+小\s*】", "", value)
    value = re.sub(r"\d{4}-\d{2}-\d{2}[ \t]+字体\s*\[\s*大\s+中\s+小\s*\]", "", value)
    value = re.sub(r"字体\s*\[\s*大\s+中\s+小\s*\]", "", value)
    value = re.sub(r"\[责任编辑：[^\]]*\]", "", value)
    lines = [line.rstrip() for line in value.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


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
    data_raw = top.get("data")
    dataset_raw = top.get("dataset")
    inner: dict[str, Any] = (
        cast(dict[str, Any], data_raw)
        if isinstance(data_raw, dict)
        else cast(dict[str, Any], dataset_raw)
        if isinstance(dataset_raw, dict)
        else top
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
    html: str | bytes, base_url: str | None = None, content_xpath: str | None = None
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
        clean_html_tree(n)

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
        if len(rows) == 1 and node.xpath('ancestor::*[contains(@class, "hmc4Table")]'):
            return ""
        if (any(colspan > 1 or rowspan > 1 for row in raw_rows for _, colspan, rowspan in row)
                or node.xpath(".//table | .//th[@scope]")):
            return spanning_table(node, base_url, UI_ACTION_LABELS)
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
