"""Pure date normalization and effective-date inference utilities (Stage 4 Cleaners).

Supports Gregorian ISO/localized dates, Japanese imperial era dates (Meiji, Taisho, Showa,
Heisei, Reiwa), and Republic of China (Minguo) calendar dates, plus legislative history
effective/repeal date extraction.
"""

from __future__ import annotations

import calendar as _calendar
import re
from collections.abc import Sequence as _Sequence
from datetime import date, datetime
from typing import Any

from wlh.core import WlhError

ERAS = {
    "明治": 1868,
    "大正": 1912,
    "昭和": 1926,
    "平成": 1989,
    "令和": 2019,
}
_ERA_BOUNDS = {
    "明": (1867, date(1868, 1, 25), date(1912, 7, 29)),
    "大": (1911, date(1912, 7, 30), date(1926, 12, 24)),
    "昭": (1925, date(1926, 12, 25), date(1989, 1, 7)),
    "平": (1988, date(1989, 1, 8), date(2019, 4, 30)),
    "令": (2018, date(2019, 5, 1), date.max),
}
ERA_DATE_RE = re.compile(
    r"(明治|大正|昭和|平成|令和|明|大|昭|平|令)\s*(元|[0-9０-９一二三四五六七八九十百]+)\s*"
    r"(?:年|[./-])\s*([0-9０-９一二三四五六七八九十]+)\s*(?:月|[./-])\s*([0-9０-９一二三四五六七八九十]+)\s*日?"
)
ISO_DATE_RE = re.compile(r"\b(\d{4})[./年-](\d{1,2})[./月-](\d{1,2})日?")
KANJI_DIGITS = {
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

_CN_DIGITS: dict[str, int] = dict(KANJI_DIGITS)

_TW_NUM_MAP = str.maketrans("０１２３４５６７８９", "0123456789")

_HISTORY_LINE_RE = re.compile(
    r"(?:中華民國)?\s*"
    r"([0-9０-９〇零一二三四五六七八九十百廿卅]+)\s*年\s*"
    r"([0-9０-９〇零一二三四五六七八九十]+)\s*月\s*"
    r"([0-9０-９〇零一二三四五六七八九十]+)\s*日"
    r"([^\n；;。]*)"
)

_SUBCLAUSE_DATE_RE = re.compile(
    r"(?:自|於|定自)\s*(?:中華民國)?\s*"
    r"([0-9０-９〇零一二三四五六七八九十百廿卅]+)\s*年\s*"
    r"([0-9０-９〇零一二三四五六七八九十]+)\s*月\s*"
    r"([0-9０-９〇零一二三四五六七八九十]+)\s*日\s*(?:起)?\s*(施行|生效)"
)

_ROC_DATE_RE = re.compile(
    r"(?:中華民國|民國)?\s*"
    r"([0-9０-９〇零一二三四五六七八九十百廿卅]{1,5})\s*年\s*"
    r"([0-9０-９〇零一二三四五六七八九十]{1,3})\s*月\s*"
    r"([0-9０-９〇零一二三四五六七八九十]{1,3})\s*日"
)

_SLASH_DOT_DATE_RE = re.compile(r"\b(\d{2,4})[./-](\d{1,2})[./-](\d{1,2})\b")

_ISSUER_AGENCIES = (
    "司法院",
    "行政院",
    "立法院",
    "考試院",
    "監察院",
    "總統府",
    "國家安全會議",
    "內政部",
    "外交部",
    "國防部",
    "財政部",
    "教育部",
    "法務部",
    "經濟部",
    "交通部",
    "勞動部",
    "農業部",
    "衛生福利部",
    "環境部",
    "文化部",
    "數位發展部",
    "國家發展委員會",
    "國家科學及技術委員會",
    "大陸委員會",
    "金融監督管理委員會",
    "海洋委員會",
    "僑務委員會",
    "國軍退除役官兵輔導委員會",
    "原住民族委員會",
    "客家委員會",
    "行政院公共工程委員會",
    "行政院主計總處",
    "行政院人事行政總處",
    "中央銀行",
    "國立故宮博物院",
    "中央選舉委員會",
    "公平交易委員會",
    "國家通訊傳播委員會",
    "核能安全委員會",
)


def japanese_number(token: str) -> int:
    text = token.translate(str.maketrans("０１２３４５６７８９", "0123456789")).strip()
    if text == "元":
        return 1
    if text.isdigit():
        return int(text)
    total = 0
    current = 0
    for char in text:
        if char in KANJI_DIGITS:
            current = KANJI_DIGITS[char]
        elif char == "十":
            total += (current or 1) * 10
            current = 0
        elif char == "百":
            total += (current or 1) * 100
            current = 0
        else:
            raise ValueError(token)
    return total + current


def era_date(value: str) -> str | None:
    match = ERA_DATE_RE.search(value)
    if match:
        era, year_token, month_token, day_token = match.groups()
        offset, start, end = _ERA_BOUNDS[era[0]]
        try:
            year = offset + japanese_number(year_token)
            month = japanese_number(month_token)
            day = japanese_number(day_token)
            parsed = date(year, month, day)
            if not start <= parsed <= end:
                raise ValueError
            return parsed.isoformat()
        except ValueError:
            raise WlhError("document_date_unparseable") from None
    return None


parse_japanese_date = era_date


def _parse_cn_int(token: str) -> int | None:
    s = token.translate(_TW_NUM_MAP).strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    s = s.replace("廿", "二十").replace("卅", "三十")
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


def _to_iso_date(y_raw: str, m_raw: str, d_raw: str) -> str | None:
    y = _parse_cn_int(y_raw)
    m = _parse_cn_int(m_raw)
    d = _parse_cn_int(d_raw)
    if not y or not m or not d:
        return None
    greg_y = y + 1911 if 1 <= y <= 300 else (y if 1800 <= y <= 2100 else None)
    if not greg_y:
        return None
    try:
        return date(greg_y, m, d).isoformat()
    except ValueError:
        return None


def parse_taiwan_date(value: str | None) -> str | None:
    """Parse an arbitrary ROC or Gregorian date string into ISO ``YYYY-MM-DD``."""
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    m = _ROC_DATE_RE.search(text)
    if m:
        iso = _to_iso_date(m.group(1), m.group(2), m.group(3))
        if iso:
            return iso
    m2 = _SLASH_DOT_DATE_RE.search(text.translate(_TW_NUM_MAP))
    if m2:
        iso = _to_iso_date(m2.group(1), m2.group(2), m2.group(3))
        if iso:
            return iso
    if len(text) == 7 and text.isdigit():
        iso = _to_iso_date(text[:3], text[3:5], text[5:7])
        if iso:
            return iso
    if len(text) == 8 and text.isdigit():
        iso = _to_iso_date(text[:4], text[4:6], text[6:8])
        if iso:
            return iso
    return None


roc_date = parse_taiwan_date


def parse_date(value: str | None, fmt: str | None = None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        return None
    if fmt:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            raise WlhError("document_date_unparseable") from None
    japanese = era_date(text)
    if japanese is not None:
        return japanese
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
    tw_fallback = parse_taiwan_date(text)
    if tw_fallback is not None:
        return tw_fallback
    raise WlhError("document_date_unparseable")


def infer_effective_date(
    text: str | None = None,
    published_at: str | None = None,
    *,
    tree: Any | None = None,
) -> str | None:
    chunks: list[str] = []
    if tree is not None and hasattr(tree, "text_content"):
        chunks.append(tree.text_content())
    elif tree is not None and hasattr(tree, "itertext"):
        chunks.append("".join(tree.itertext()))
    if text:
        chunks.append(text)
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
        iso = _to_iso_date(m_roc.group(1), m_roc.group(2), m_roc.group(3))
        if iso:
            explicit_dates.append(iso)

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


def extract_taiwan_law_dates_and_meta(
    text: str,
    current_status: str | None = None,
) -> dict[str, str | None]:
    """Extract ``published_at``, ``effective_at``, ``expired_at``, ``status``, and ``issuer``."""
    published_candidates: list[str] = []
    effective_candidates: list[str] = []
    expired_candidates: list[str] = []
    inferred_issuer: str | None = None
    is_repealed = bool(
        current_status
        and any(k in current_status for k in ("廢止", "废止", "停止適用", "不再援用"))
    )

    for m in _HISTORY_LINE_RE.finditer(text):
        iso = _to_iso_date(m.group(1), m.group(2), m.group(3))
        if not iso:
            continue
        tail = m.group(4).strip()
        if not tail:
            continue

        if not inferred_issuer:
            for agency in _ISSUER_AGENCIES:
                if agency in tail:
                    inferred_issuer = agency
                    break

        if re.search(r"廢止|停止適用|不再援用|失效|期滿當然廢止", tail):
            expired_candidates.append(iso)
            is_repealed = True
            continue

        has_pub_action = bool(re.search(r"制定|訂定|修正|公布|發布|發佈", tail))
        if has_pub_action:
            published_candidates.append(iso)

        sub_eff_found = False
        for sm in _SUBCLAUSE_DATE_RE.finditer(tail):
            sub_iso = _to_iso_date(sm.group(1), sm.group(2), sm.group(3))
            if sub_iso:
                effective_candidates.append(sub_iso)
                sub_eff_found = True

        if not sub_eff_found:
            if re.search(r"自(?:公布|發布|發佈)日施行|並自即日施行|即日施行", tail):
                effective_candidates.append(iso)
            elif re.search(r"(?:^|[；;，,\s])施行|生效", tail) and not re.search(
                r"施行日期.{0,8}定之", tail
            ):
                effective_candidates.append(iso)

    for sm in _SUBCLAUSE_DATE_RE.finditer(text):
        sub_iso = _to_iso_date(sm.group(1), sm.group(2), sm.group(3))
        if sub_iso:
            effective_candidates.append(sub_iso)

    published_at = max(published_candidates) if published_candidates else None
    effective_at = max(effective_candidates) if effective_candidates else None
    expired_at = max(expired_candidates) if expired_candidates else None

    if not effective_at and published_at:
        if re.search(
            r"(?:本法|本條例|本通則|本規則|本辦法|本細則|本準則|本標準|本綱要|本規程|本編制表|修正條文)"
            r"[^\n。；]{0,30}?自(?:公布|發布|發佈)日(?:起)?(?:施行|生效)"
            r"|自(?:公布|發布|發佈)日(?:起)?(?:施行|生效)",
            text,
        ):
            effective_at = published_at

    status = "废止" if is_repealed else (current_status or "现行有效")
    return {
        "published_at": published_at,
        "effective_at": effective_at,
        "expired_at": expired_at,
        "status": status,
        "issuer": inferred_issuer,
    }


def infer_missing_dates(
    tree: Any,
    markdown: str,
    published_at: str | None,
    effective_at: str | None,
    expired_at: str | None,
    doc_number: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    if not effective_at:
        effective_at = infer_effective_date(markdown, published_at=published_at, tree=tree)
    return published_at, effective_at, expired_at


_ZH_NUM_MAP_MOJ: dict[str, int] = {
    "零": 0, "○": 0, "０": 0, "Ｏ": 0,
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100, "元": 1,
}
_ROC_YMD_RE_MOJ = re.compile(
    r"(?:中華民國|民國)?\s*"
    r"([一二三四五六七八九十百零○０Ｏ元\d]{1,5})\s*年\s*"
    r"([一二三四五六七八九十\d]{1,3})\s*月\s*"
    r"([一二三四五六七八九十\d]{1,3})\s*日"
)


def _select_xml_val(root: Any, *names: str) -> str | None:
    for name in names:
        nodes = root.xpath(".//*[local-name()=$name]", name=name)
        for node in nodes:
            value = re.sub(r"\s+", " ", "".join(node.itertext())).strip()
            if value:
                return value
    return None


def _parse_roc_int_moj(raw: str) -> int | None:
    s = re.sub(r"\s+", "", raw)
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if all(c in _ZH_NUM_MAP_MOJ for c in s):
        if "百" in s or "十" in s:
            total = 0
            cur = 0
            for ch in s:
                if ch == "百":
                    total += (cur or 1) * 100
                    cur = 0
                elif ch == "十":
                    total += (cur or 1) * 10
                    cur = 0
                else:
                    cur = _ZH_NUM_MAP_MOJ[ch]
            return total + cur
        return int("".join(str(_ZH_NUM_MAP_MOJ[ch]) for ch in s))
    return None


def _roc_ymd_to_iso_moj(y_str: str, m_str: str, d_str: str) -> str | None:
    y, m, d = _parse_roc_int_moj(y_str), _parse_roc_int_moj(m_str), _parse_roc_int_moj(d_str)
    if y is None or m is None or d is None:
        return None
    year = y + 1911 if y < 500 else y
    try:
        return date(year, m, d).isoformat()
    except ValueError:
        return None


def _format_yyyymmdd_moj(val: str | None) -> str | None:
    if not val:
        return None
    s = val.strip()
    if re.fullmatch(r"\d{8}", s) and s != "99991231":
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:])).isoformat()
        except ValueError:
            return None
    m = _ROC_YMD_RE_MOJ.search(s)
    if m:
        return _roc_ymd_to_iso_moj(m.group(1), m.group(2), m.group(3))
    return None


def _add_months_moj(iso_date: str, months: int) -> str:
    d = date.fromisoformat(iso_date)
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    max_day = _calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, max_day)).isoformat()


_MOJ_EXPLICIT_EFF_RE = re.compile(
    r"(?:中華民國|民國)?\s*"
    r"([一二三四五六七八九十百零○０Ｏ元\d]{1,5})\s*年\s*"
    r"([一二三四五六七八九十\d]{1,3})\s*月\s*"
    r"([一二三四五六七八九十\d]{1,3})\s*"
    r"(?:日\s*(?:）|\))?\s*(?:零時)?(?:起)?(?:自配偶)?|(?=[施行]))"
    r"(?:[^。；，\n]{0,15}?(?:令|指令|訓令|核准|核定|發布|公布|修正))?"
    r"(?:施行|生效|實施|解嚴)"
)
_MOJ_PERIOD_EFF_RE = re.compile(
    r"(?:施行期間|施行日期|有效期間|實施期間)[，、：:\s]*自\s*" + _ROC_YMD_RE_MOJ.pattern
)
_MOJ_SAME_YEAR_EFF_RE = re.compile(
    r"同年\s*([一二三四五六七八九十\d]{1,3})\s*月\s*([一二三四五六七八九十\d]{1,3})\s*日\s*(?:起)?(?:施行|生效|實施)"
)
_MOJ_SAME_DAY_CLAUSE_RE = re.compile(
    r"自(?:[^。；\n]{0,15}?)(?:公布|發布|公告|下達|通過|核定|核准|奉准|核備|備查|簽署|頒行|頒布)之?(?:日|時)?(?:後)?(?:起)?(?:施行|斿行|生效|實施|執行)"
    r"|施行期間自(?:公布|發布)之?日起"
    r"|(?:經|由|呈奉|簽奉|奉|報請|報|提報)[^。；\n]{0,25}?(?:通過|核定|核准|核備|備查|公布|發布|院會|大會)[^。；\n]{0,12}?(?:後|即)?(?:施行|生效|實施)"
)
_MOJ_OFFSET_CLAUSE_RE = re.compile(
    r"(?:自|於)(?:公布|發布)(?:之?日)?後?\s*(一年|六個月|半年|三個月|[一二三四五六七八九十\d]+\s*個月)\s*後?(?:施行|生效|實施)"
)


def _extract_moj_explicit_dates(text_block: str, items: _Sequence[str]) -> list[str]:
    dates: list[str] = []
    for m in _MOJ_EXPLICIT_EFF_RE.finditer(text_block):
        iso_d = _roc_ymd_to_iso_moj(m.group(1), m.group(2), m.group(3))
        if iso_d:
            dates.append(iso_d)
    for m in _MOJ_PERIOD_EFF_RE.finditer(text_block):
        iso_d = _roc_ymd_to_iso_moj(m.group(1), m.group(2), m.group(3))
        if iso_d:
            dates.append(iso_d)
    for item in items:
        m_y = _ROC_YMD_RE_MOJ.search(item)
        m_same = _MOJ_SAME_YEAR_EFF_RE.search(item)
        if m_y and m_same:
            iso_d = _roc_ymd_to_iso_moj(m_y.group(1), m_same.group(1), m_same.group(2))
            if iso_d:
                dates.append(iso_d)
    return dates


def _extract_moj_offset_months(text_block: str) -> int:
    m = _MOJ_OFFSET_CLAUSE_RE.search(text_block)
    if not m:
        return 0
    dur = re.sub(r"\s+", "", m.group(1))
    if dur == "一年":
        return 12
    if dur in ("六個月", "半年"):
        return 6
    if dur == "三個月":
        return 3
    if dur.endswith("個月"):
        return _parse_roc_int_moj(dur[:-2]) or 0
    return 0


_YUAN_HEAD_ISSUER_MAP: dict[str, str] = {
    "行政": "行政院",
    "司法": "司法院",
    "考試": "考試院",
    "監察": "監察院",
    "廢止法規": "行政院",
}


def normalize_moj_law_category_issuer(raw_cat: str | None) -> str | None:
    """Normalize MOJ `<LawCategory>` hierarchy strings into pure issuing agency names."""
    if not raw_cat or not raw_cat.strip():
        return None
    parts = [p.strip() for p in re.split(r"\s*[＞>]\s*", raw_cat.strip()) if p.strip()]
    if not parts:
        return None
    if len(parts) == 1:
        p0 = parts[0]
        if p0 == "立法":
            return "立法院"
        if p0.startswith("國民大會"):
            return "國民大會"
        return p0
    top, second = parts[0], parts[1]
    if second == "院本部":
        return _YUAN_HEAD_ISSUER_MAP.get(top, "行政院")
    if second == "立法":
        return "立法院"
    if second.startswith("國民大會"):
        return "國民大會"
    return second


def resolve_moj_law_dates_and_meta(node: Any) -> dict[str, str | None]:
    raw_mod = _select_xml_val(node, "最新異動日期", "LawModifiedDate", "LawPublishedDate")
    raw_eff = _select_xml_val(node, "生效日期", "LawEffectiveDate")
    raw_aban = _select_xml_val(node, "廢止註記", "LawAbandonNote")
    raw_cat = _select_xml_val(node, "法規類別", "LawCategory")
    raw_hist = "".join(
        "".join(h.itertext())
        for h in node.xpath('.//*[local-name()="沿革內容" or local-name()="LawHistories"]')
    )
    hist = re.sub(r"\s+", " ", re.sub(r"\r?\n\s*(?!\d+\.)", "", raw_hist)).strip()
    raw_arts = " ".join(
        "".join(a.itertext()) for a in node.xpath('.//*[local-name()="LawArticles"]')
    )
    arts = re.sub(r"\s+", " ", re.sub(r"\r?\n\s*", "", raw_arts)).strip()

    published = _format_yyyymmdd_moj(raw_mod)
    is_abolished = bool(raw_aban and raw_aban.strip())
    is_pending = bool(raw_eff and raw_eff.strip() == "99991231")

    status: str | None = (
        "已廢止" if is_abolished else ("尚未生效" if is_pending else "現行有效")
    )

    hist_items = [
        part.strip()
        for part in re.split(r"(?:^|\s)\d+\.", hist)
        if part.strip()
    ]
    non_abolish_items: list[str] = []
    non_abolish_dates: list[str] = []
    abolish_dates: list[str] = []
    for item in hist_items:
        m_d = _ROC_YMD_RE_MOJ.search(item)
        iso_d = _roc_ymd_to_iso_moj(m_d.group(1), m_d.group(2), m_d.group(3)) if m_d else None
        if re.search(r"廢止|停止適用|暫緩施行", item):
            if iso_d and re.search(r"廢止|停止適用", item):
                abolish_dates.append(iso_d)
        else:
            non_abolish_items.append(item)
            if iso_d:
                non_abolish_dates.append(iso_d)

    expired_at: str | None = (
        (abolish_dates[-1] if abolish_dates else published) if is_abolished else None
    )
    base_pub: str | None = (
        (non_abolish_dates[-1] if non_abolish_dates else published)
        if is_abolished
        else (published or (non_abolish_dates[-1] if non_abolish_dates else None))
    )

    effective_at: str | None = None
    effective_tier: str = "abstained_no_deterministic_clause"

    if is_pending:
        effective_tier = "pending_99991231"
    elif re.search(r"尚未命令施行|暫緩施行", f"{hist} {arts}"):
        effective_tier = "abstained_no_deterministic_clause"
    else:
        if raw_eff and raw_eff.strip() != "99991231":
            effective_at = _format_yyyymmdd_moj(raw_eff)
            if effective_at:
                effective_tier = "tier1_xml_effective_date"

        if not effective_at and non_abolish_items:
            latest_item = non_abolish_items[-1]
            latest_exp = _extract_moj_explicit_dates(latest_item, (latest_item,))
            if latest_exp:
                effective_at = latest_exp[-1]
                effective_tier = "tier2_explicit_effective_date"
            else:
                latest_off = _extract_moj_offset_months(latest_item)
                if latest_off > 0 and base_pub:
                    effective_at = _add_months_moj(base_pub, latest_off)
                    effective_tier = "tier4_relative_offset_clause"
                elif _MOJ_SAME_DAY_CLAUSE_RE.search(latest_item) and base_pub:
                    effective_at = base_pub
                    effective_tier = "tier3_same_day_statutory_clause"

        if not effective_at:
            active_text = f"{' '.join(non_abolish_items)} {arts}".strip()
            all_exp = _extract_moj_explicit_dates(active_text, non_abolish_items)
            has_same = bool(_MOJ_SAME_DAY_CLAUSE_RE.search(active_text))
            off_months = _extract_moj_offset_months(active_text)
            if all_exp:
                max_exp = max(all_exp)
                if (not has_same and off_months == 0) or not base_pub or max_exp >= base_pub:
                    effective_at = max_exp
                    effective_tier = "tier2_explicit_effective_date"
            if not effective_at and has_same and base_pub:
                effective_at = base_pub
                effective_tier = "tier3_same_day_statutory_clause"
            if not effective_at and off_months > 0 and base_pub:
                effective_at = _add_months_moj(base_pub, off_months)
                effective_tier = "tier4_relative_offset_clause"
            if not effective_at and all_exp:
                effective_at = all_exp[-1]
                effective_tier = "tier2_explicit_effective_date"

    return {
        "published_at": published,
        "effective_at": effective_at,
        "expired_at": expired_at,
        "status": status,
        "issuer": normalize_moj_law_category_issuer(raw_cat),
        "effective_tier": effective_tier,
    }

