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
    roc_zh_match: re.Match[str] | None = re.search(
        r"(?:中華民國|民國)\s*(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text
    )
    if roc_zh_match:
        try:
            y, m, d = (int(part) for part in roc_zh_match.groups())
            return datetime(y + 1911, m, d).date().isoformat()
        except ValueError:
            raise WlhError("document_date_unparseable") from None
    zh_match: re.Match[str] | None = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if zh_match:
        try:
            y, m, d = (int(part) for part in zh_match.groups())
            return datetime(y, m, d).date().isoformat()
        except ValueError:
            raise WlhError("document_date_unparseable") from None
    roc_num_match: re.Match[str] | None = re.match(
        r"^(\d{2,3})[-./](\d{1,2})[-./](\d{1,2})(?:\b|$)", text
    )
    if roc_num_match:
        y, m, d = (int(part) for part in roc_num_match.groups())
        if 1 <= y <= 300:
            try:
                return datetime(y + 1911, m, d).date().isoformat()
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


_CN_DIGITS: dict[str, int] = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _parse_roc_or_int(raw: str) -> int | None:
    s = raw.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if all(ch in _CN_DIGITS for ch in s):
        return int("".join(str(_CN_DIGITS[ch]) for ch in s))
    total = 0
    current = 0
    for ch in s:
        if ch in _CN_DIGITS:
            current = _CN_DIGITS[ch]
        elif ch == "十":
            total += (current if current > 0 else 1) * 10
            current = 0
        elif ch == "百":
            total += (current if current > 0 else 1) * 100
            current = 0
        else:
            return None
    total += current
    return total if total > 0 else None


def _infer_fallback_effective_date(
    tree: Any | None, body_text: str | None, published_at: str | None
) -> str | None:
    chunks: list[str] = []
    if tree is not None:
        scoped_nodes = tree.xpath(
            '//div[contains(@class, "law-reg-content") or contains(@class, "law-agree") or contains(@class, "table-title")]'
        )
        if scoped_nodes:
            chunks.extend(node.text_content() for node in scoped_nodes)
        else:
            chunks.append(tree.text_content())
    if body_text:
        chunks.append(body_text)
    combined = re.sub(
        r"\s+",
        " ",
        " ".join(re.sub(r"\r?\n\s*", "", c) for c in chunks),
    ).strip()
    if not combined:
        return None
    if re.search(r"尚待生效|尚未生效|尚待互換批准|尚未互換批准|尚待通知生效", combined):
        return None

    explicit_dates: list[str] = []
    for m_roc in re.finditer(
        r"(?:自|於|，|；|\s)(?:(?:中華)?民國\s*)?([0-9〇零一二三四五六七八九十百]{1,5})\s*年\s*([0-9〇零一二三四五六七八九十]{1,3})\s*月\s*([0-9〇零一二三四五六七八九十]{1,3})\s*日(?:起)?(?:施行|生效)",
        " " + combined,
    ):
        y = _parse_roc_or_int(m_roc.group(1))
        m = _parse_roc_or_int(m_roc.group(2))
        d = _parse_roc_or_int(m_roc.group(3))
        if y and m and d:
            greg_y = y + 1911 if 1 <= y <= 250 else (y if 1800 <= y <= 2100 else None)
            if greg_y:
                try:
                    explicit_dates.append(datetime(greg_y, m, d).date().isoformat())
                except ValueError:
                    continue

    for m_greg in re.finditer(
        r"(?:自|於)?\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日(?:起)?(?:施行|生效)",
        combined,
    ):
        try:
            explicit_dates.append(
                datetime(
                    int(m_greg.group(1)), int(m_greg.group(2)), int(m_greg.group(3))
                ).date().isoformat()
            )
        except ValueError:
            continue

    for m_en in re.finditer(
        r"(?:Entered\s+into\s+force|Effective(?:\s+date)?(?:\s+on|\s+since|\s+from|\s*:)?|comes?\s+into\s+force(?:\s+on)?)\s*(?:on\s+)?([A-Z][a-z]+\s+\d{1,2},\s*\d{4}|\d{1,2}\s+[A-Z][a-z]+\s+\d{4}|\d{4}[./-]\d{1,2}[./-]\d{1,2})",
        combined,
        re.IGNORECASE,
    ):
        try:
            parsed_en = parse_date(m_en.group(1).replace(".", "-"))
            if parsed_en:
                explicit_dates.append(parsed_en)
        except WlhError:
            continue

    has_same_day_clause = bool(
        re.search(
            r"(?:本法|本條例|本通則|本規則|本辦法|本細則|本準則|本標準|本綱要|本規程|本編制表|本協定|本協議|本條約|本約定書|本備忘錄|修正條文)[^\n。；]{0,30}?自(?:公布|發布|簽署|簽字|換文)之?日(?:起)?(?:施行|生效)|自(?:公布|發布|簽署|簽字|換文)之?日(?:起)?(?:施行|生效)|enter(?:s|ed)?\s+into\s+force\s+(?:(?:at|on)\s+(?:the\s+)?same\s+date|(?:on|from)\s+the\s+date\s+of\s+sign(?:ature|ing)|upon\s+sign(?:ature|ing))|effective\s+(?:from\s+the\s+date\s+of\s+sign(?:ature|ing)|upon\s+sign(?:ature|ing))",
            combined,
            re.IGNORECASE,
        )
    )

    if explicit_dates:
        latest_explicit = max(explicit_dates)
        if not has_same_day_clause or not published_at or latest_explicit >= published_at:
            return latest_explicit

    if has_same_day_clause and published_at:
        return published_at

    return None


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
        if "effective_at" in fields and not values.get("effective_at"):
            values["effective_at"] = _infer_fallback_effective_date(
                tree, text, values.get("published_at")
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
