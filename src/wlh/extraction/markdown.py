"""Deterministic legal HTML conversion; no browser, network or implicit caches."""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import urljoin, urlsplit

import lxml.etree as _etree  # type: ignore[import-untyped]
from lxml import html as _dom  # type: ignore[import-untyped]

from wlh.core import WlhError

etree: Any = _etree
dom: Any = _dom

MARKDOWN_CONTRACT = "wlh.markdown.v1"

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


def html_to_markdown(
    html: str | bytes, base_url: str | None = None, content_xpath: str | None = None
) -> str:
    try:
        tree = dom.document_fromstring(html)
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

    def children(node: Any) -> str:
        return inline(node.text) + "".join(render(c) + inline(c.tail) for c in node)

    def table(node: Any) -> str:
        rows: list[list[str]] = []
        for tr in node.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr"):
            cells: list[str] = [
                re.sub(r"\s+", " ", children(c)).strip().replace("|", "\\|")
                for c in tr.xpath("./th|./td")
            ]
            if cells:
                rows.append(cells)
        if not rows:
            return ""
        if len(rows) == 1 and node.xpath('ancestor::*[contains(@class, "hmc4Table")]'):
            return ""
        width = max(map(len, rows))
        rows = [row + [""] * (width - len(row)) for row in rows]
        rows.insert(1, ["---"] * width)
        return "\n\n" + "\n".join("| " + " | ".join(row) + " |" for row in rows) + "\n\n"

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
            return "\n"
        if re.fullmatch(r"h[1-6]", tag):
            return "\n\n" + "#" * int(tag[1]) + " " + value.strip() + "\n\n"
        if tag == "li":
            if not value.strip():
                return ""
            parent = node.getparent()
            marker: str = (
                f"{parent.index(node) + 1}. " if parent is not None and parent.tag == "ol" else "- "
            )
            return "\n" + marker + value.strip().replace("\n", "\n  ") + "\n"
        if tag == "blockquote":
            return "\n\n" + "\n".join("> " + line for line in value.strip().splitlines()) + "\n\n"
        if tag in {"p", "div", "section", "article", "main", "ul", "ol", "header", "body", "hr"}:
            return "\n\n" + value.strip() + "\n\n"
        return value

    return normalized("\n\n".join(render(node) for node in nodes))
