"""Deterministic legal HTML conversion; no browser, network or implicit caches."""

import re
import unicodedata
from urllib.parse import urljoin, urlsplit

import lxml.etree as etree
from lxml import html as dom

from wlh.core import WlhError

MARKDOWN_CONTRACT = "wlh.markdown.v1"


def compact(value):
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[ \t\u00a0]+", " ", value).strip()


def normalized(value):
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in value.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def text_to_markdown(value):
    return normalized(value)


def html_to_markdown(html, base_url=None, content_xpath=None):
    try:
        tree = dom.document_fromstring(html)
        nodes = (
            tree.xpath(content_xpath)
            if content_xpath
            else [tree.body if tree.body is not None else tree]
        )
    except (etree.Error, ValueError):
        raise WlhError("html_unparseable") from None
    if not nodes or any(not isinstance(n, etree._Element) for n in nodes):
        raise WlhError("content_region_missing")

    def inline(text):
        return re.sub(r"\s+", " ", text or "")

    def children(node):
        return inline(node.text) + "".join(render(c) + inline(c.tail) for c in node)

    def table(node):
        rows = []
        for tr in node.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr"):
            cells = [
                re.sub(r"\s+", " ", children(c)).strip().replace("|", "\\|")
                for c in tr.xpath("./th|./td")
            ]
            if cells:
                rows.append(cells)
        if not rows:
            return ""
        width = max(map(len, rows))
        rows = [row + [""] * (width - len(row)) for row in rows]
        rows.insert(1, ["---"] * width)
        return "\n\n" + "\n".join("| " + " | ".join(row) + " |" for row in rows) + "\n\n"

    def render(node):
        if not isinstance(node.tag, str):
            return ""
        tag = node.tag.split("}")[-1].split(":")[-1].lower()
        if tag in {"script", "style", "noscript", "template", "head", "nav", "footer", "form"}:
            return ""
        if tag == "table":
            return table(node)
        if tag == "pre":
            return "\n\n```\n" + node.text_content().strip() + "\n```\n\n"
        value = children(node)
        if tag == "a":
            href = urljoin(base_url or "", node.get("href", ""))
            return (
                f"[{value.strip()}]({href})"
                if href and urlsplit(href).scheme in {"http", "https"}
                else value
            )
        if tag in {"strong", "b"}:
            return "**" + value.strip() + "**" if value.strip() else ""
        if tag in {"em", "i"}:
            return "*" + value.strip() + "*" if value.strip() else ""
        if tag == "code":
            return "`" + value.strip() + "`"
        if tag == "br":
            return "\n"
        if re.fullmatch(r"h[1-6]", tag):
            return "\n\n" + "#" * int(tag[1]) + " " + value.strip() + "\n\n"
        if tag == "li":
            parent = node.getparent()
            marker = (
                f"{parent.index(node) + 1}. " if parent is not None and parent.tag == "ol" else "- "
            )
            return "\n" + marker + value.strip().replace("\n", "\n  ") + "\n"
        if tag == "blockquote":
            return "\n\n" + "\n".join("> " + line for line in value.strip().splitlines()) + "\n\n"
        if tag in {"p", "div", "section", "article", "main", "ul", "ol", "header", "body", "hr"}:
            return "\n\n" + value.strip() + "\n\n"
        return value

    return normalized("\n\n".join(render(node) for node in nodes))
