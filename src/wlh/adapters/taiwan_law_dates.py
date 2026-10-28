"""Deterministic date and status fields in Taiwan MOJ law packages."""

from __future__ import annotations

import calendar
import re
from collections.abc import Sequence
from datetime import date as _dt_date
from typing import Any

from wlh.adapters.legal_xml_values import select

_ZH_NUM_MAP: dict[str, int] = {
    "零": 0, "○": 0, "０": 0, "Ｏ": 0,
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100, "元": 1,
}
_ROC_YMD_RE = re.compile(
    r"(?:中華民國|民國)?\s*"
    r"([一二三四五六七八九十百零○０Ｏ元\d]{1,5})\s*年\s*"
    r"([一二三四五六七八九十\d]{1,3})\s*月\s*"
    r"([一二三四五六七八九十\d]{1,3})\s*日"
)


def _parse_roc_int(raw: str) -> int | None:
    s = re.sub(r"\s+", "", raw)
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if all(c in _ZH_NUM_MAP for c in s):
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
                    cur = _ZH_NUM_MAP[ch]
            return total + cur
        return int("".join(str(_ZH_NUM_MAP[ch]) for ch in s))
    return None


def _roc_ymd_to_iso(y_str: str, m_str: str, d_str: str) -> str | None:
    y, m, d = _parse_roc_int(y_str), _parse_roc_int(m_str), _parse_roc_int(d_str)
    if y is None or m is None or d is None:
        return None
    year = y + 1911 if y < 500 else y
    try:
        return _dt_date(year, m, d).isoformat()
    except ValueError:
        return None


def _format_yyyymmdd(val: str | None) -> str | None:
    if not val:
        return None
    s = val.strip()
    if re.fullmatch(r"\d{8}", s) and s != "99991231":
        try:
            return _dt_date(int(s[:4]), int(s[4:6]), int(s[6:])).isoformat()
        except ValueError:
            return None
    m = _ROC_YMD_RE.search(s)
    if m:
        return _roc_ymd_to_iso(m.group(1), m.group(2), m.group(3))
    return None


def _add_months(iso_date: str, months: int) -> str:
    d = _dt_date.fromisoformat(iso_date)
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    max_day = calendar.monthrange(year, month)[1]
    return _dt_date(year, month, min(d.day, max_day)).isoformat()


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
    r"(?:施行期間|施行日期|有效期間|實施期間)[，、：:\s]*自\s*" + _ROC_YMD_RE.pattern
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


def _extract_moj_explicit_dates(text_block: str, items: Sequence[str]) -> list[str]:
    dates: list[str] = []
    for m in _MOJ_EXPLICIT_EFF_RE.finditer(text_block):
        iso_d = _roc_ymd_to_iso(m.group(1), m.group(2), m.group(3))
        if iso_d:
            dates.append(iso_d)
    for m in _MOJ_PERIOD_EFF_RE.finditer(text_block):
        iso_d = _roc_ymd_to_iso(m.group(1), m.group(2), m.group(3))
        if iso_d:
            dates.append(iso_d)
    for item in items:
        m_y = _ROC_YMD_RE.search(item)
        m_same = _MOJ_SAME_YEAR_EFF_RE.search(item)
        if m_y and m_same:
            iso_d = _roc_ymd_to_iso(m_y.group(1), m_same.group(1), m_same.group(2))
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
        return _parse_roc_int(dur[:-2]) or 0
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
    raw_mod = select(node, "最新異動日期", "LawModifiedDate", "LawPublishedDate")
    raw_eff = select(node, "生效日期", "LawEffectiveDate")
    raw_aban = select(node, "廢止註記", "LawAbandonNote")
    raw_cat = select(node, "法規類別", "LawCategory")
    raw_hist = "".join(
        "".join(h.itertext())
        for h in node.xpath('.//*[local-name()="沿革內容" or local-name()="LawHistories"]')
    )
    hist = re.sub(r"\s+", " ", re.sub(r"\r?\n\s*(?!\d+\.)", "", raw_hist)).strip()
    raw_arts = " ".join(
        "".join(a.itertext()) for a in node.xpath('.//*[local-name()="LawArticles"]')
    )
    arts = re.sub(r"\s+", " ", re.sub(r"\r?\n\s*", "", raw_arts)).strip()

    published = _format_yyyymmdd(raw_mod)
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
        m_d = _ROC_YMD_RE.search(item)
        iso_d = _roc_ymd_to_iso(m_d.group(1), m_d.group(2), m_d.group(3)) if m_d else None
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

    # Strictly deterministic 4-tier effective_at resolution (never guess when no clause matches)
    effective_at: str | None = None
    effective_tier: str = "abstained_no_deterministic_clause"

    if is_pending:
        effective_tier = "pending_99991231"
    elif re.search(r"尚未命令施行|暫緩施行", f"{hist} {arts}"):
        effective_tier = "abstained_no_deterministic_clause"
    else:
        # Rule 1: Explicit <LawEffectiveDate> (non-99991231)
        if raw_eff and raw_eff.strip() != "99991231":
            effective_at = _format_yyyymmdd(raw_eff)
            if effective_at:
                effective_tier = "tier1_xml_effective_date"

        # Inspect latest legislative history item first
        if not effective_at and non_abolish_items:
            latest_item = non_abolish_items[-1]
            latest_exp = _extract_moj_explicit_dates(latest_item, (latest_item,))
            if latest_exp:
                effective_at = latest_exp[-1]
                effective_tier = "tier2_explicit_effective_date"
            else:
                latest_off = _extract_moj_offset_months(latest_item)
                if latest_off > 0 and base_pub:
                    effective_at = _add_months(base_pub, latest_off)
                    effective_tier = "tier4_relative_offset_clause"
                elif _MOJ_SAME_DAY_CLAUSE_RE.search(latest_item) and base_pub:
                    effective_at = base_pub
                    effective_tier = "tier3_same_day_statutory_clause"

        # Inspect statute articles + full non-abolish history
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
                effective_at = _add_months(base_pub, off_months)
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


