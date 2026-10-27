"""Official legal XML and XML packages.

Namespaces identify vocabulary, never trigger external entity/schema downloads.
Body text stays in source order; this adapter does not infer a legal ontology.
"""

from __future__ import annotations

import calendar
import html
import io
import json
import re
import zipfile
from collections.abc import Mapping, Sequence
from datetime import date as _dt_date
from typing import Any, cast
from urllib.parse import urljoin, urlsplit, urlunsplit

import lxml.etree as etree
from lxml import html as lxml_html  # type: ignore[import-untyped]

from wlh.adapters.bridges.common import RecordAdapter
from wlh.config import MAX_ORIGINAL_BYTES
from wlh.core import WlhError, public_url
from wlh.engine.scope import guard
from wlh.extraction.markdown import html_to_markdown, normalized

etree: Any
lxml_html: Any
HEADING_TAGS: set[str] = {
    "heading", "longTitle", "docTitle", "LawTitle", "LawName", "法規名稱",
    "ArticleCaption", "ChapterTitle", "titel", "enbez", "HEAD", "Heading",
    "TitleText", "MarginalNote", "ArticleNo", "條號", "EngArticleNo",
}
PARAGRAPH_TAGS: set[str] = {
    "p", "paragraph", "Sentence", "LawContent", "法規內容", "條文內容",
    "ParagraphSentence", "P", "PSPACE", "FP", "Text",
    "ArticleContent", "ArticleConctent", "EngArticleContent",
    "LawForeword", "前言", "EngLawForeword",
    "LawHistories", "沿革內容", "EngLawHistories",
}
SKIP_TAGS: set[str] = {
    "meta", "metadata", "identification", "Identification", "preface",
    "Notes", "附註", "presentation", "style", "metadaten", "fussnoten", "toc", "TOC",
    "LawLevel", "法規位階", "LawURL", "法規網址", "EngLawURL",
    "LawCategory", "法規類別", "LawModifiedDate", "最新異動日期", "EngLawModifiedDate",
    "LawEffectiveDate", "生效日期", "LawEffectiveNote", "生效內容",
    "LawAbandonNote", "廢止註記", "EngLawAbandonNote",
    "LawHasEngVersion", "是否英譯註記", "EngLawName", "英文法規名稱",
    "LawAttachements", "EngLawAttachements", "附件",
    "ArticleType", "條文型態", "EngArticleType",
    "FileName", "檔案名稱", "FileURL", "下載網址",
}
ERA_OFFSETS: dict[str, int] = {
    "Meiji": 1867, "Taisho": 1911, "Showa": 1925, "Heisei": 1988, "Reiwa": 2018,
}
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


def _resolve_moj_law_dates_and_meta(node: Any) -> dict[str, str | None]:
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
        "issuer": raw_cat.strip() if raw_cat and raw_cat.strip() else None,
        "effective_tier": effective_tier,
    }


def text(node: Any) -> str:
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def select(root: Any, *names: str, attribute: str | None = None) -> str | None:
    for name in names:
        nodes = root.xpath(".//*[local-name()=$name]", name=name)
        for node in nodes:
            value: str | None = node.get(attribute) if attribute else text(node)
            if value:
                return value
    return None


def markdown(root: Any) -> str:
    parts: list[str] = []

    def walk(node: Any) -> None:
        if not isinstance(node.tag, str):
            return
        tag = etree.QName(node).localname
        if tag in SKIP_TAGS:
            return
        if tag in HEADING_TAGS or tag in PARAGRAPH_TAGS:
            value: str | None = text(node)
            if value:
                parts.append(("## " if tag in HEADING_TAGS else "") + value)
            return
        if tag in {"table", "Table"}:
            rows: list[list[str]] = [
                [text(c).replace("|", "\\|") for c in row]
                for row in node.xpath('.//*[local-name()="tr" or local-name()="TableRow"]')
            ]
            if rows:
                width = max(map(len, rows))
                rows = [r + [""] * (width - len(r)) for r in rows]
                rows.insert(1, ["---"] * width)
                parts.append("\n".join("| " + " | ".join(r) + " |" for r in rows))
            return
        if not len(node) and node.text and node.text.strip():
            parts.append(node.text.strip())
            return
        for child in node:
            walk(child)

    walk(root)
    return normalized("\n\n".join(parts))


def _normalize_candidate_urls(
    base: str,
    raw_urls: Sequence[str],
    config: Mapping[str, Any],
    patterns: Sequence[re.Pattern[str]] = (),
) -> list[str]:
    base_parts = urlsplit(base)
    urls: list[str] = []
    seen: set[str] = set()
    for href in raw_urls:
        if not href or href.lower().startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
            continue
        parts = urlsplit(urljoin(base, href))
        if parts.scheme not in ("http", "https"):
            continue
        scheme = (
            "https"
            if parts.scheme == "http"
            and base_parts.scheme == "https"
            and parts.netloc == base_parts.netloc
            else parts.scheme
        )
        try:
            candidate = public_url(
                urlunsplit((scheme, parts.netloc, parts.path or "/", parts.query, ""))
            )
            if patterns and not any(p.search(candidate) for p in patterns):
                continue
            guard(config, candidate)
        except (WlhError, ValueError):
            continue
        if candidate not in seen:
            seen.add(candidate)
            urls.append(candidate)
    return urls


class Adapter(RecordAdapter):
    CONTRACT: str = "wlh.adapter.structured-xml.v1"
    NAMESPACE: str = "legislative-xml"

    def records(self, data: bytes, acquisition: Mapping[str, Any]) -> list[dict[str, Any]]:
        cache_key = (
            acquisition.get("archive_ref"),
            acquisition.get("actual_url"),
            int(self.config["budget"]["documents"]),
        )
        cached: tuple[list[dict[str, Any]], str | None] | None = getattr(
            self, "_records_cache", {}
        ).get(cache_key)
        if cached is not None and cache_key[0] is not None:
            self.truncated = cached[1] or self.truncated
            return cached[0]
        raw_bytes = data
        zipped = data.startswith(b"PK\x03\x04")
        total_nodes = 1
        extra_xml_blobs: list[bytes] = []
        if zipped:
            max_bytes = max(int(self.config["budget"]["response_bytes"]), MAX_ORIGINAL_BYTES)
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    members = [
                        m
                        for m in sorted(archive.infolist(), key=lambda item: item.filename)
                        if not m.is_dir() and m.filename.lower().endswith(".xml")
                    ]
                    if not members:
                        raise WlhError("zip_without_xml")
                    total_expanded = 0
                    xml_blobs: list[bytes] = []
                    for member in members:
                        if member.file_size > max_bytes:
                            raise WlhError("zip_expanded_size_exceeded")
                        with archive.open(member) as source:
                            blob = source.read(max_bytes - total_expanded + 1)
                        total_expanded += len(blob)
                        if total_expanded > max_bytes:
                            raise WlhError("zip_expanded_size_exceeded")
                        xml_blobs.append(blob)
                    data = xml_blobs[0]
                    extra_xml_blobs = xml_blobs[1:]
            except (zipfile.BadZipFile, OSError, ValueError, RuntimeError):
                raise WlhError("zip_invalid") from None
        if not zipped:
            if data.lstrip().startswith(b"{"):
                try:
                    obj = json.loads(data)
                except ValueError:
                    raise WlhError("legal_xml_invalid") from None
                if isinstance(obj, dict) and (
                    isinstance(cast(dict[str, Any], obj).get("children"), list)
                    or isinstance(cast(dict[str, Any], obj).get("titles"), list)
                ):
                    return []
                raise WlhError("legal_xml_invalid")
            if re.search(br"<!DOCTYPE\s+html\b|<html(?:\s|>)", data[:2048], re.I):
                return []
        if b"<!ENTITY" in data.upper():
            raise WlhError("legal_xml_doctype_rejected")
        try:
            root: Any = etree.fromstring(
                data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
            )
        except etree.XMLSyntaxError:
            raise WlhError("legal_xml_invalid") from None
        tag = etree.QName(root).localname
        namespace = etree.QName(root).namespace or ""
        if not zipped and (
            tag in {"feed", "items", "ActsRegsList", "rss"}
            or (
                tag == "DataRoot"
                and not (select(root, "LawTitle") or root.xpath(".//LawBody"))
                and bool(root.xpath(".//*[local-name()='LawId']/text()"))
            )
        ):
            return []
        if tag == "akomaNtoso" or "/akn/3.0" in namespace:
            is_judgment = tag == "judgment" or bool(root.xpath(".//*[local-name()='judgment']"))
            cite_num: str | None = select(root, "neutralCitation", "cite", "docNumber") or select(
                root, "FRBRnumber", attribute="value"
            )
            number: str | None = cite_num or select(root, "docketNumber")
            title: str | None = select(root, "docTitle", "shortTitle", "longTitle") or select(
                root, "FRBRname", attribute="value"
            )
            key: str | None = select(root, "FRBRuri", attribute="value")
            if not key or key.rstrip("/").endswith("/id"):
                key = cite_num or acquisition.get("actual_url")
            published: str | None = select(root, "FRBRdate", attribute="date")
            issuer: str | None = select(root, "FRBRauthor", attribute="href")
            if issuer and issuer.startswith("#"):
                matches = root.xpath(".//*[@eId=$id]", id=issuer[1:])
                issuer = matches[0].get("showAs") if matches else None
            issuer = issuer or select(root, "courtType", "court")
            doc_type: str | None = "案例" if is_judgment else "法律法规"
            entries: list[
                tuple[Any, str | None, str | None, str | None, str | None, str | None, str | None]
            ] = [(root, title, number, key, published, issuer, doc_type)]
        elif "uslm" in namespace.lower() or tag in {"lawDoc", "bill", "statute", "uscDoc", "pLaw"}:
            doc_title = select(root, "docTitle")
            title = (
                select(root, "dcTitle", "title", "officialTitle", "longTitle")
                if doc_title and doc_title.strip().lower() in {"an act", "joint resolution"}
                else (doc_title or select(root, "longTitle", "dcTitle", "title"))
            )
            number = select(root, "citableAs", "docNumber", "number", "identifier")
            published = (
                select(root, "approvedDate")
                or select(root, "created")
                or select(root, "date", attribute="date")
                or select(root, "date")
                or ""
            )[:10] or None
            issuer = select(root, "publisher", "creator") or (
                "United States Congress" if tag == "pLaw" else None
            )
            entries = [
                (
                    root,
                    title,
                    number,
                    root.get("identifier") or select(root, "identifier") or number,
                    published,
                    issuer,
                    "法律法规",
                )
            ]
        elif tag in {"dokumente", "norm"}:
            title = select(root, "langue", "titel", "jurabk")
            number = select(root, "jurabk") or root.get("doknr")
            key = root.get("doknr") or select(root, "norm", attribute="doknr") or number
            published = select(root, "ausfertigung-datum")
            issuer = select(root, "periodikum")
            entries = [(root, title, number, key, published, issuer, "法律法规")]
        elif re.fullmatch(r"DIV[1-9]", tag) or tag in {"ECFR", "DLPSTEXTCLASS"}:
            meta = json.loads(html.unescape(root.get("hierarchy_metadata") or "{}"))
            title = select(root, "HEAD", "TITLE")
            number = meta.get("citation") or root.get("N") or title
            key = meta.get("path") or number
            m_date: re.Match[str] | None = re.search(
                r"/(\d{4}-\d{2}-\d{2})/", acquisition.get("actual_url") or ""
            )
            published = select(root, "AMDDATE", "DATE") or (m_date.group(1) if m_date else None)
            issuer = select(root, "AGENCY") or "Office of the Federal Register (NARA)"
            entries = [(root, title, number, key, published, issuer, "法律法规")]
        elif tag in {"Statute", "Regulation"}:
            title = select(root, "ShortTitle", "LongTitle", "TitleText")
            number = select(root, "ConsolidatedNumber", "InstrumentNumber")
            key = number or title
            published = next(
                (
                    v
                    for k, v in root.attrib.items()
                    if k.endswith(("lastAmendedDate", "pit-date", "current-date"))
                ),
                None,
            )
            entries = [
                (
                    root,
                    title,
                    number,
                    key,
                    published,
                    "Department of Justice Canada",
                    "法律法规",
                )
            ]
        elif tag in {"Law", "DataRoot"} and (select(root, "LawTitle") or root.xpath(".//LawBody")):
            laws = [root] if tag == "Law" else root.xpath(".//Law")
            entries = []
            for law in laws:
                title, number = select(law, "LawTitle"), select(law, "LawNum")
                year: str | None = law.get("Year")
                era = str(law.get("Era") or "")
                if year and era in ERA_OFFSETS:
                    year = str(int(year) + ERA_OFFSETS[era])
                published = (
                    f"{int(year):04d}-{int(law.get('PromulgateMonth')):02d}-{int(law.get('PromulgateDay')):02d}"
                    if year and law.get("PromulgateMonth") and law.get("PromulgateDay")
                    else None
                )
                entries.append((law, title, number, number, published, None, None))
        elif tag in {"MojLawSplit", "Laws", "法規", "法規資料"} or select(
            root, "法規名稱", "LawName"
        ):
            nodes = root.xpath('.//*[local-name()="法規" or local-name()="Law"]') or [root]
            total_nodes = len(nodes)
            limit = int(self.config["budget"]["documents"])
            if total_nodes > limit:
                self.truncated = "document_budget_exceeded"
                nodes = nodes[:limit]
            entries = []
            moj_meta_by_node: dict[int, dict[str, str | None]] = {}
            for n in nodes:
                law_url = select(n, "法規網址", "LawURL")
                law_no = select(n, "法規字號", "LawNo")
                if not law_no and law_url:
                    m_pcode = re.search(r"[?&]pcode=([A-Za-z0-9]+)", law_url, re.I)
                    if m_pcode:
                        law_no = m_pcode.group(1)
                moj_meta = _resolve_moj_law_dates_and_meta(n)
                moj_meta_by_node[id(n)] = moj_meta
                entries.append(
                    (
                        n,
                        select(n, "法規名稱", "LawName"),
                        law_no,
                        law_url or select(n, "法規名稱", "LawName"),
                        moj_meta["published_at"]
                        or select(n, "最新異動日期", "LawModifiedDate", "LawPublishedDate"),
                        moj_meta["issuer"],
                        None,
                    )
                )
        elif tag == "Gazette" or (
            root.xpath('.//*[local-name()="Record" and *[local-name()="MetaId"]]')
        ):
            xml_parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
            roots: list[Any] = [root]
            for extra_blob in extra_xml_blobs:
                if b"<!ENTITY" in extra_blob.upper():
                    raise WlhError("legal_xml_doctype_rejected")
                try:
                    roots.append(etree.fromstring(extra_blob, xml_parser))
                except etree.XMLSyntaxError:
                    raise WlhError("legal_xml_invalid") from None
            nodes = [
                rec
                for r_node in roots
                for rec in r_node.xpath('.//*[local-name()="Record"]')
            ]
            total_nodes = len(nodes)
            limit = int(self.config["budget"]["documents"])
            if total_nodes > limit:
                self.truncated = "document_budget_exceeded"
                nodes = nodes[:limit]
            entries = []
            for n in nodes:
                meta_id = select(n, "MetaId")
                rec_url = (
                    f"https://gazette.nat.gov.tw/egFront/detail.do?metaid={meta_id}&log=detailLog"
                    if meta_id
                    else select(n, "GazetteHTML")
                )
                doc_style = select(n, "Doc_Style_LName")
                rec_title = (
                    select(n, "Title")
                    or select(n, "ThemeSubject")
                    or select(n, "GazetteId")
                    or (f"行政院公報第{meta_id}號" if meta_id else None)
                )
                if not rec_title or not (rec_url or rec_title):
                    continue
                entries.append(
                    (
                        n,
                        rec_title,
                        select(n, "GazetteId") or meta_id,
                        rec_url or rec_title,
                        select(n, "Date_Published", "Date_Created"),
                        select(n, "PubGovName", "PubGov"),
                        "法律法规" if doc_style == "法規" else None,
                    )
                )
        else:
            raise WlhError("unsupported_legal_xml_vocabulary")
        result: list[dict[str, Any]] = []
        moj_meta_lookup: dict[int, dict[str, str | None]] = locals().get("moj_meta_by_node", {})
        for node, title, number, key, published, issuer, doc_type in entries:
            if not title or not key:
                raise WlhError("legal_xml_identity_missing")
            if published:
                m_roc = re.search(
                    r"(?:中華)?民國\s*(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
                    published,
                )
                if m_roc:
                    published = (
                        f"{int(m_roc.group(1)) + 1911:04d}-"
                        f"{int(m_roc.group(2)):02d}-"
                        f"{int(m_roc.group(3)):02d}"
                    )
                elif re.fullmatch(r"\d{8}", published):
                    published = f"{published[:4]}-{published[4:6]}-{published[6:]}"
            html_content = select(node, "HTMLContent") if tag == "Gazette" else None
            if html_content:
                cleaned_html = re.sub(r"<!--.*?-->", "", html_content, flags=re.S)
                cleaned_html = re.sub(r"<\s*$", "", cleaned_html.strip())
                cleaned_html = re.sub(
                    r"</?(?:table|tbody|thead|tr|td|th)\b[^>]*>",
                    "\n<p>",
                    cleaned_html,
                    flags=re.I,
                )
                body_md = html_to_markdown(
                    b"<meta charset='utf-8'><div>" + cleaned_html.encode("utf-8") + b"</div>",
                    key,
                )
                if not body_md.strip():
                    body_md = normalized(
                        "\n\n".join(
                            part
                            for part in (select(node, "ThemeSubject"), select(node, "Explain"))
                            if part
                        )
                    )
                text_body = "# " + title + "\n\n" + body_md
            else:
                text_body = "# " + title + "\n\n" + markdown(node)
            moj_extra = moj_meta_lookup.get(id(node)) or {}
            result.append(
                {
                    "external_key": key,
                    "title": title,
                    "doc_number": number,
                    "published_at": published,
                    "effective_at": moj_extra.get("effective_at"),
                    "expired_at": moj_extra.get("expired_at"),
                    "status": moj_extra.get("status"),
                    "issuer": issuer,
                    "doc_type": doc_type,
                    "text": text_body,
                    "detail_url": acquisition["actual_url"],
                    "original": (
                        {
                            "ref": acquisition["archive_ref"],
                            "kind": "zip",
                            "byte_size": len(raw_bytes),
                        }
                        if zipped and total_nodes == 1
                        else None
                    ),
                }
            )
        if cache_key[0] is not None:
            cache_dict: dict[Any, Any] = getattr(self, "_records_cache", {})
            cache_dict[cache_key] = (result, self.truncated)
            setattr(self, "_records_cache", cache_dict)
        return result

    def _filter_requests(
        self, base: str, raw_urls: list[str], pagination_urls: Sequence[str] = ()
    ) -> list[dict[str, Any]]:
        patterns = [
            re.compile(p)
            for p in self.config.get("documents", {}).get("detail_url_patterns", [])
        ]
        urls = _normalize_candidate_urls(base, raw_urls, self.config, patterns)
        next_pages = _normalize_candidate_urls(base, pagination_urls, self.config)
        seen = set(urls)
        limit: int = int(self.config["budget"]["documents"])
        if len(urls) > limit or (len(urls) == limit and bool(next_pages)):
            self.truncated = "document_budget_exceeded"
        selected = list(urls[:limit])
        if len(selected) < limit:
            selected.extend([u for u in next_pages if u not in seen])
        return [{"url": u, "role": "page"} for u in selected]

    def next_requests(
        self, data: bytes, acquisition: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        if data.startswith(b"PK\x03\x04"):
            return []
        base = str(acquisition.get("actual_url") or acquisition.get("request_url") or "")
        if data.lstrip().startswith(b"{"):
            try:
                obj = json.loads(data)
            except ValueError:
                return []
            if not isinstance(obj, dict):
                return []
            typed_obj = cast(dict[str, Any], obj)
            children_root = typed_obj.get("children")
            if not isinstance(children_root, list):
                titles_list = typed_obj.get("titles")
                if isinstance(titles_list, list):
                    raw_urls: list[str] = []
                    for raw_item in cast(list[Any], titles_list):
                        if not isinstance(raw_item, dict):
                            continue
                        typed_item = cast(dict[str, Any], raw_item)
                        if typed_item.get("reserved"):
                            continue
                        num: Any = typed_item.get("number")
                        raw_date: Any = (
                            typed_item.get("up_to_date_as_of")
                            or typed_item.get("latest_issue_date")
                            or ""
                        )
                        date = str(raw_date).strip()
                        if num is not None and date:
                            raw_urls.append(urljoin(base, f"structure/{date}/title-{num}.json"))
                    urls = _normalize_candidate_urls(base, raw_urls, self.config)
                    return [{"url": u, "role": "page"} for u in urls[:5]]
                return []
            m = re.search(r"/structure/(\d{4}-\d{2}-\d{2})/title-(\d+)\.json", base)
            date = m.group(1) if m else str(typed_obj.get("up_to_date_as_of") or "").strip()
            title_num = m.group(2) if m else str(typed_obj.get("identifier") or "").strip()
            if not date or not title_num:
                return []
            raw_urls: list[str] = []

            def walk_parts(nodes: list[Any]) -> None:
                for node in nodes:
                    if not isinstance(node, dict):
                        continue
                    typed_node = cast(dict[str, Any], node)
                    if typed_node.get("type") == "part" and not typed_node.get("reserved"):
                        ident = typed_node.get("identifier")
                        if ident:
                            raw_urls.append(
                                urljoin(base, f"../../full/{date}/title-{title_num}.xml?part={ident}")
                            )
                    children = typed_node.get("children")
                    if isinstance(children, list):
                        walk_parts(cast(list[Any], children))

            walk_parts(cast(list[Any], children_root))
            return self._filter_requests(base, raw_urls)
        if re.search(br"<!DOCTYPE\s+html\b|<html(?:\s|>)", data[:2048], re.I):
            try:
                doc = lxml_html.document_fromstring(data.decode("utf-8", errors="replace"))
            except (etree.ParserError, ValueError):
                return []
            raw_urls = [
                str(href).strip()
                for href in doc.xpath("//a[@href]/@href")
                if str(href).strip()
            ]
            return self._filter_requests(base, raw_urls)
        if b"<!ENTITY" in data.upper():
            return []
        try:
            root: Any = etree.fromstring(
                data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
            )
        except etree.XMLSyntaxError:
            return []
        tag = etree.QName(root).localname
        if tag == "feed":
            next_links = [
                str(h).strip()
                for h in root.xpath(".//*[local-name()='link' and @rel='next']/@href")
                if str(h).strip()
            ]
            next_set = set(next_links)
            links = [
                str(h).strip()
                for h in root.xpath(".//*[local-name()='link']/@href")
                if str(h).strip().endswith("/data.xml") and str(h).strip() not in next_set
            ]
            if not links:
                for h in root.xpath(".//*[local-name()='entry']/*[local-name()='link']/@href"):
                    href = str(h).strip()
                    if href and href not in next_set:
                        links.append(
                            href if href.endswith(".xml") else href.rstrip("/") + "/data.xml"
                        )
            return self._filter_requests(base, links, next_links)
        if tag in {"items", "rss"}:
            links = [
                str(t).strip()
                for t in root.xpath(".//*[local-name()='link']/text()")
                if str(t).strip()
            ]
            for desc in root.xpath(".//*[local-name()='description']/text()"):
                for href in re.findall(r"""href=["']([^"']+)["']""", html.unescape(str(desc))):
                    if href.strip():
                        links.append(href.strip())
            return self._filter_requests(base, links)
        if tag == "ActsRegsList":
            links = [
                str(t).strip()
                for t in root.xpath(".//*[local-name()='LinkToXML']/text()")
                if str(t).strip()
            ]
            return self._filter_requests(base, links)
        if (
            tag == "DataRoot"
            and not (select(root, "LawTitle") or root.xpath(".//LawBody"))
            and root.xpath(".//*[local-name()='LawId']/text()")
        ):
            links = [
                urljoin(base, f"../lawdata/{str(law_id).strip()}")
                for law_id in root.xpath(".//*[local-name()='LawId']/text()")
                if str(law_id).strip()
            ]
            return self._filter_requests(base, links)
        return []
