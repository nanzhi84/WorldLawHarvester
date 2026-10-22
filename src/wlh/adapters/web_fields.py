"""HTML decoding, URL cleaning, date normalization and declarative field extraction for WebAdapter."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, cast
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

import lxml.etree as etree
from lxml import html  # type: ignore[import-untyped]

from wlh.core import WlhError, public_url

etree: Any
html: Any
DATE_FIELDS: set[str] = {"published_at", "effective_at", "expired_at"}


def decode(data: bytes, charset: str | None) -> tuple[str, str]:
    detected: str | None = charset
    if not detected:
        match: re.Match[bytes] | None = re.search(
            br'<meta\b[^>]*charset=["\']?\s*([a-zA-Z0-9._-]+)', data[:4096], re.I
        )
        if match:
            detected = match.group(1).decode("ascii", errors="ignore")
    for encoding in (detected, "utf-8"):
        if encoding:
            try:
                return data.decode(encoding), encoding
            except (LookupError, UnicodeDecodeError):
                continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def parse_html(data: bytes, charset: str | None) -> tuple[Any, str, str]:
    text, encoding = decode(data, charset)
    try:
        tree: Any = html.document_fromstring(text)
    except (etree.ParserError, ValueError):
        raise WlhError("html_unparseable") from None
    return tree, text, encoding


def clean_url(base: str, href: str | None) -> str | None:
    if not href:
        return None
    cleaned: str = href.strip()
    if cleaned.lower().startswith("javascript:") or re.match(r"^[A-Za-z_]\w*\s*\(", cleaned):
        js_match: re.Match[str] | None = re.search(
            r"""['"]((?:https?://|/)[^'"]+)['"]""", cleaned
        )
        if not js_match:
            return None
        cleaned = str(js_match.group(1) or "").strip()
    if not cleaned or cleaned.lower().startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
        return None
    try:
        base_parts: Any = urlsplit(base)
        parts: Any = urlsplit(urljoin(base, cleaned))
        if parts.scheme not in ("http", "https"):
            return None
        scheme: str = (
            "https"
            if parts.scheme == "http"
            and base_parts.scheme == "https"
            and parts.netloc == base_parts.netloc
            else parts.scheme
        )
        return public_url(
            urlunsplit((scheme, parts.netloc, parts.path or "/", parts.query, ""))
        )
    except (WlhError, ValueError):
        return None


def file_name(url: str) -> str:
    parts: Any = urlsplit(url)
    query_name = parse_qs(parts.query).get("file_name", [""])[0]
    name = PurePosixPath(query_name or unquote(parts.path)).name
    return name or "file"


def parse_date(value: str, fmt: str | None = None) -> str | None:
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        return None
    if fmt:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            raise WlhError("document_date_unparseable") from None
    zh_match: re.Match[str] | None = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if zh_match:
        try:
            y, m, d = (int(part) for part in zh_match.groups())
            return datetime(y, m, d).date().isoformat()
        except ValueError:
            raise WlhError("document_date_unparseable") from None
    for candidate in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text[:10], candidate).date().isoformat()
        except ValueError:
            continue
    for candidate in ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, candidate).date().isoformat()
        except ValueError:
            continue
    raise WlhError("document_date_unparseable")


def field_value(
    tree: Any, text: str | None, rule: Mapping[str, Any], *, is_date: bool = False
) -> Any:
    if rule.get("value") is not None:
        return rule["value"]
    value: Any = text
    if rule.get("xpath"):
        if tree is None:
            return None
        found: Any = tree.xpath(rule["xpath"])
        if isinstance(found, list):
            if not found:
                return None
            first: Any = cast(list[Any], found)[0]
            value = first if isinstance(first, str) else first.text_content()
        elif isinstance(found, str):
            value = found
        elif found is None or isinstance(found, bool):
            return None
        else:
            value = str(found)
    if value is None:
        return None
    # Preserve ideographic spaces in source metadata.
    value = re.sub(r"[^\S\u3000]+", " ", str(value)).strip()
    if rule.get("regex"):
        match: re.Match[str] | None = re.search(cast(str, rule["regex"]), value)
        if not match:
            return None
        value = match.groupdict().get("value") or (
            match.group(1) if match.groups() else match[0]
        )
    if not value.strip():
        return None
    if rule.get("date_format") or is_date:
        return parse_date(value, rule.get("date_format"))
    return value.strip() or None


def extract_detail_fields(
    fields: Mapping[str, Any],
    data: bytes,
    kind: str,
    acquisition: Mapping[str, Any],
    url: str,
    text: str | None = None,
) -> tuple[dict[str, Any], str | None, str | None]:
    values: dict[str, Any] = {}
    if kind == "html":
        tree, _, encoding = parse_html(data, acquisition["charset"])
        for name, rule in fields.items():
            values[name] = field_value(
                tree, tree.text_content(), rule, is_date=(name in DATE_FIELDS)
            )
        h1: str | None = next(
            (
                re.sub(r"\s+", " ", node.text_content()).strip()
                for node in tree.xpath("//h1")
                if re.sub(r"\s+", " ", node.text_content()).strip()
            ),
            None,
        )
        page_title: str | None = next(
            (
                re.sub(r"\s+", " ", node.text_content()).strip()
                for node in tree.xpath("//title")
                if re.sub(r"\s+", " ", node.text_content()).strip()
            ),
            None,
        )
        return values, values.get("title") or h1 or page_title, encoding
    source_text = f"{url}\n{text}" if text else url
    for name, rule in fields.items():
        if rule.get("value") is not None:
            values[name] = rule["value"]
        elif not rule.get("xpath") and rule.get("regex"):
            values[name] = field_value(None, source_text, rule, is_date=(name in DATE_FIELDS))
    return values, values.get("title") or file_name(url), acquisition["charset"]
