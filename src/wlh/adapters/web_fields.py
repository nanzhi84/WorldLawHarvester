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
    if cleaned.lower().startswith("javascript:") or re.match(r"^[A-Za-z_][\w.]*\s*\(", cleaned):
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


def _split_top_level_union(xpath_expr: str) -> list[str]:
    """Split top-level `|` union branches (outside brackets/parens/quotes) to preserve left-to-right priority."""
    branches: list[str] = []
    cur: list[str] = []
    paren_depth = 0
    bracket_depth = 0
    quote_char: str | None = None
    for ch in xpath_expr:
        if quote_char is not None:
            cur.append(ch)
            if ch == quote_char:
                quote_char = None
        elif ch in ("'", '"'):
            quote_char = ch
            cur.append(ch)
        elif ch == "(":
            paren_depth += 1
            cur.append(ch)
        elif ch == ")":
            paren_depth = max(0, paren_depth - 1)
            cur.append(ch)
        elif ch == "[":
            bracket_depth += 1
            cur.append(ch)
        elif ch == "]":
            bracket_depth = max(0, bracket_depth - 1)
            cur.append(ch)
        elif ch == "|" and paren_depth == 0 and bracket_depth == 0:
            part = "".join(cur).strip()
            if part:
                branches.append(part)
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        branches.append(tail)
    return branches if len(branches) > 1 else [xpath_expr]


def field_value(
    tree: Any, text: str | None, rule: Mapping[str, Any], *, is_date: bool = False
) -> Any:
    if rule.get("value") is not None:
        return rule["value"]
    if rule.get("xpath"):
        if tree is None:
            return None
        branches = _split_top_level_union(str(rule["xpath"]))
        if len(branches) > 1:
            for branch in branches:
                res = field_value(tree, text, {**rule, "xpath": branch}, is_date=is_date)
                if res is not None:
                    return res
            return None
        found: Any = tree.xpath(rule["xpath"])
        if isinstance(found, list):
            if not found:
                return None
            if rule.get("regex"):
                pat = re.compile(cast(str, rule["regex"]))
                for item in cast(list[Any], found):
                    val_str: Any = item if isinstance(item, str) else item.text_content()
                    cleaned = re.sub(r"[^\S\u3000]+", " ", str(val_str)).strip()
                    match = pat.search(cleaned)
                    if match:
                        val = match.groupdict().get("value") or (
                            match.group(1) if match.groups() else match[0]
                        )
                        if not val.strip():
                            continue
                        if rule.get("date_format") or is_date:
                            return parse_date(val, rule.get("date_format"))
                        return val.strip() or None
                return None
            first: Any = cast(list[Any], found)[0]
            value: Any = first if isinstance(first, str) else first.text_content()
        elif isinstance(found, str):
            value = found
        elif found is None or isinstance(found, bool):
            return None
        else:
            value = str(found)
    else:
        value = text
    if value is None:
        return None
    # Preserve ideographic spaces in source metadata.
    value = re.sub(r"[^\S\u3000]+", " ", str(value)).strip()
    if rule.get("regex"):
        match = re.search(cast(str, rule["regex"]), value)
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


def enrich_bare_title(
    title: str | None,
    doc_number: str | None,
    tree: Any | None = None,
    body_text: str | None = None,
) -> str | None:
    if not title:
        return title
    clean_title = re.sub(r"[^\S\u3000]+", " ", title).strip()
    doc_num = re.sub(r"[^\S\u3000]+", " ", doc_number or "").strip()
    is_bare = False
    if doc_num:
        if clean_title.casefold() == doc_num.casefold():
            is_bare = True
        elif re.match(
            rf"^(?:luật|pháp\s+lệnh|nghị\s+quyết|nghị\s+định|thông\s+tư|quyết\s+định|chỉ\s+thị)(?:\s+số)?\s+{re.escape(doc_num)}$",
            clean_title,
            re.IGNORECASE,
        ):
            is_bare = True
    if not is_bare:
        return clean_title
    if tree is not None:
        for node in tree.xpath('//div[contains(@class, "toanvan")]'):
            txt = re.sub(r"\s+", " ", node.text_content()).strip()
            if 15 <= len(txt) <= 260 and txt.casefold() != clean_title.casefold():
                return txt
            m = re.search(
                r"(?:LUẬT|PHÁP LỆNH|NGHỊ QUYẾT|NGHỊ ĐỊNH|THÔNG TƯ|QUYẾT ĐỊNH)\s+((?:Về|Sửa đổi|Ban hành|Quy định|Phê chuẩn|Bãi bỏ)[^.;\n]{10,200})",
                txt,
                re.IGNORECASE,
            )
            if m:
                subj = re.sub(r"\s+", " ", m.group(1)).strip()
                return f"{clean_title} - {subj}"
    if body_text:
        m = re.search(
            r"(?:LU[ẬA]T|PH[ÁA]P\s+L[ỆE]NH|NGH[ỊI]\s+QUY[ẾÉEe]T|NGH[ỊI]\s+[ĐD][ỊI]NH|TH[ÔO]NG\s+T[ƯU]|QUY[ẾÉEe]T\s+[ĐD][ỊI]NH)(?!\s+s[ốo]\b)[^\n]*\n+\s*((?:Về|Sửa đổi|Ban hành|Quy định|Phê chuẩn|Phê duyệt|Bãi bỏ|Thông qua|Chương trình|Kế hoạch)[\s\S]{10,280}?)(?=(?:\n|\s+)(?:QU[ỐÓO]C\s+H[ỘỘO]I|U[ỶY]\s+BAN|CH[ÍI]NH\s+PH[ỦU]|TH[ỦU]\s+T[ƯU][ỚƠO]NG|C[ăa]n\s+c[ứư]|ĐI[ỀE]U\s+1)\b|\n\s*\n|$)",
            body_text,
            re.IGNORECASE,
        )
        if m:
            subj = re.sub(r"\s+", " ", m.group(1)).strip(" .;-_")
            if subj and subj.casefold() not in clean_title.casefold():
                return f"{clean_title} - {subj}"
    return clean_title


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
        wlh_trunc = tree.xpath("//*[@data-wlh-truncated]/@data-wlh-truncated")
        if wlh_trunc:
            raise WlhError(str(wlh_trunc[0]))
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
        resolved_title = enrich_bare_title(
            values.get("title") or h1 or page_title,
            values.get("document_number"),
            tree=tree,
            body_text=text,
        )
        return values, resolved_title, encoding
    source_text = f"{url}\n{text}" if text else url
    for name, rule in fields.items():
        if rule.get("value") is not None:
            values[name] = rule["value"]
        elif not rule.get("xpath") and rule.get("regex"):
            values[name] = field_value(None, source_text, rule, is_date=(name in DATE_FIELDS))
    resolved_title = enrich_bare_title(
        values.get("title") or file_name(url),
        values.get("document_number"),
        tree=None,
        body_text=text,
    )
    return values, resolved_title, acquisition["charset"]
