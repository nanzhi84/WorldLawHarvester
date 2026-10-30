"""HTML5 tree construction with Lexbor and lxml XPath compatibility."""

from __future__ import annotations

import re
from typing import Any, cast

import lxml.etree as _etree  # type: ignore[import-untyped]
import lxml.html as lxml_html  # type: ignore[import-untyped]
from selectolax.lexbor import LexborHTMLParser

from wlh.core import WlhError

etree: Any = _etree

FRAGMENT = re.compile(r"^\s*<(?:!doctype|html|head|body)\b", re.I)


def document_tree(text: str | bytes) -> Any:
    try:
        if isinstance(text, bytes):
            parser = LexborHTMLParser(text, encoding=True, scripting=False)
            normalized = parser.html
        else:
            raw = text if FRAGMENT.match(text) else f"<!DOCTYPE html><html><body>{text}</body></html>"
            normalized = LexborHTMLParser(raw, scripting=False).html or raw
        if not normalized:
            raise ValueError
        tree: Any = cast(Any, lxml_html).document_fromstring(normalized)
        for comment in list(cast(list[Any], tree.xpath("//comment()"))):
            parent = comment.getparent()
            if parent is None:
                continue
            if comment.tail:
                prev = comment.getprevious()
                if prev is not None:
                    prev.tail = (prev.tail or "") + comment.tail
                else:
                    parent.text = (parent.text or "") + comment.tail
            parent.remove(comment)
        return tree
    except (ValueError, TypeError, RuntimeError, etree.ParserError):
        raise WlhError("html_unparseable") from None


def decode(data: bytes, charset: str | None = None) -> tuple[str, str]:
    detected: str | None = charset
    if not detected:
        match: re.Match[bytes] | None = re.search(
            br'<meta\b[^>]*charset=["\']?\s*([a-zA-Z0-9._-]+)', data[:4096], re.I
        )
        if match:
            detected = match.group(1).decode("ascii", errors="ignore")
    if detected and detected.lower().replace("-", "_") in {"shift_jis", "sjis", "ms_kanji"}:
        detected = "cp932"
    for encoding in (detected, "utf-8", "cp932"):
        if encoding:
            try:
                return data.decode(encoding), encoding
            except (LookupError, UnicodeDecodeError):
                continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def parse_html(data: bytes, charset: str | None = None) -> tuple[Any, str, str]:
    text, encoding = decode(data, charset)
    try:
        tree: Any = document_tree(text)
    except (etree.ParserError, ValueError):
        raise WlhError("html_unparseable") from None
    return tree, text, encoding


def first_text(node: Any, xpath_expr: str) -> str | None:
    try:
        found = node.xpath(xpath_expr)
    except Exception:
        return None
    if not isinstance(found, list) or not found:
        return None
    item: Any = cast(list[Any], found)[0]
    text = item if isinstance(item, str) else "".join(cast(list[str], item.itertext()))
    cleaned = re.sub(r"[ \t\r\n\u00a0]+", " ", text).strip()
    return cleaned or None
