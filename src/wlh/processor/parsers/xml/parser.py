"""Official legal XML, e-Gov v2 catalog/revision JSON+XML, and XML packages."""

from __future__ import annotations

import base64
import html
import io
import json
import re
import unicodedata
import zipfile
from collections.abc import Mapping
from datetime import date
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import lxml.etree as _etree  # type: ignore[import-untyped]

from wlh.cleaners.dates import resolve_moj_law_dates_and_meta
from wlh.cleaners.text_cleaner import normalized
from wlh.config import MAX_ORIGINAL_BYTES
from wlh.core import WlhError, guard, public_url
from wlh.processor.extractors.html.html_to_md import html_to_markdown
from wlh.processor.parsers.record_base import (
    RecordAdapter,
    _budget_documents,
    _budget_response_bytes,
    _normalize_attachment_src,
    verify_attachment_zip,
)
from wlh.processor.parsers.record_base import (
    day as _record_day,
)
from wlh.processor.parsers.record_base import (
    first as _first_xpath,
)
from wlh.processor.parsers.xml.discovery import discover
from wlh.processor.parsers.xml.markdown import markdown
from wlh.processor.parsers.xml.values import select

etree: Any = _etree

ERA_OFFSETS: dict[str, int] = {
    "Meiji": 1867,
    "Taisho": 1911,
    "Showa": 1925,
    "Heisei": 1988,
    "Reiwa": 2018,
}
_REVISION = re.compile(r"[A-Za-z0-9]+_\d{8}_[A-Za-z0-9]+")
_DIGITS = "〇一二三四五六七八九"
_NUMBER = re.compile(r"[0-9〇零一二三四五六七八九十百千元]+")
_ERA = {"明治": 1867, "大正": 1911, "昭和": 1925, "平成": 1988, "令和": 2018}
_JAPANESE_DAY = r"(明治|大正|昭和|平成|令和)(\d+)年(\d+)月(\d+)日"


def _response(
    data: bytes, acquisition: Mapping[str, Any], config: Mapping[str, Any]
) -> tuple[str, dict[str, Any]] | None:
    url = str(acquisition.get("actual_url") or acquisition.get("request_url") or "")
    path = urlsplit(url).path.rstrip("/")
    if path != "/api/2/laws" and not path.startswith("/api/2/law_data/"):
        return None
    try:
        value = json.loads(data)
    except ValueError:
        raise WlhError("egov_response_invalid") from None
    if not isinstance(value, dict):
        raise WlhError("egov_response_invalid")
    return url, cast(dict[str, Any], value)


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WlhError("egov_response_invalid")
    return cast(dict[str, Any], value)


def _day(value: Any) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _number(match: re.Match[str]) -> str:
    value = match.group().replace("元", "一").replace("零", "〇")
    digits = "".join(str(_DIGITS.index(c)) if c in _DIGITS else c for c in value)
    if digits.isdecimal():
        return str(int(digits))
    total, current = 0, ""
    for char in digits:
        if char.isdecimal():
            current += char
        else:
            total += int(current or "1") * {"十": 10, "百": 100, "千": 1000}[char]
            current = ""
    return str(total + int(current or "0"))


def _japanese_numbers(value: str) -> str:
    return _NUMBER.sub(_number, re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)))


def _japanese_day(value: str) -> str | None:
    match = re.fullmatch(_JAPANESE_DAY, value)
    if match is None:
        return None
    era, year, month, day = match.groups()
    try:
        return date(_ERA[era] + int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def _effective(root: Any, revision: Mapping[str, Any]) -> str | None:
    candidate = _day(revision.get("amendment_enforcement_date"))
    if candidate is None or revision.get("amendment_enforcement_comment"):
        return None
    amendment = str(revision.get("amendment_law_num") or "")
    promulgated = _day(revision.get("amendment_promulgate_date"))
    provisions: list[Any] = []
    for node in root.findall("LawBody/SupplProvision"):
        label = _japanese_numbers(str(node.get("AmendLawNum") or ""))
        if revision.get("amendment_law_id"):
            match = re.match(_JAPANESE_DAY, label)
            if (
                amendment
                and match is not None
                and _japanese_day(match.group()) == promulgated
                and re.sub(r"\d+月\d+日", "", label, count=1) == _japanese_numbers(amendment)
            ):
                provisions.append(node)
        elif not label and str(revision.get("law_revision_id") or "").endswith(
            "_000000000000000"
        ):
            provisions.append(node)
    if not provisions:
        return None
    dates: set[str] = set()
    for provision in provisions:
        sentences = [
            _japanese_numbers("".join(node.itertext()))
            for node in provision.xpath(".//Sentence")
        ]
        if any(re.search(r"ただし|但し|但シ|ただ,|次の各号|政令で定める日", s) for s in sentences):
            return None
        for sentence in sentences:
            match = re.match(
                r"(?:この|本)(?:法律|政令|省令|府令|規則|法|令)(?:は|ハ)[、,]?"
                r"(?P<day>" + _JAPANESE_DAY + r"|公布の日)(?:から|より|ヨリ)施行(?:する|し|スル|ス)",
                sentence,
            )
            if match is not None:
                evidence = (
                    promulgated if match["day"] == "公布の日" else _japanese_day(match["day"])
                )
                if evidence is not None:
                    dates.add(evidence)
    return candidate if dates == {candidate} else None


def _xml(value: Any) -> Any:
    if not isinstance(value, str):
        raise WlhError("egov_law_xml_missing")
    try:
        data = base64.b64decode(value, validate=True)
    except ValueError:
        raise WlhError("egov_law_xml_invalid") from None
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise WlhError("legal_xml_doctype_rejected")
    try:
        root = etree.fromstring(
            data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        )
    except etree.XMLSyntaxError:
        raise WlhError("egov_law_xml_invalid") from None
    if root.tag != "Law" or root.find("LawBody") is None:
        raise WlhError("egov_law_xml_invalid")
    return root


def japan_records(
    data: bytes, acquisition: Mapping[str, Any], config: Mapping[str, Any]
) -> list[dict[str, Any]] | None:
    response = _response(data, acquisition, config)
    if response is None:
        return None
    url, obj = response
    if urlsplit(url).path.rstrip("/") == "/api/2/laws":
        return []
    law = _object(obj.get("law_info"))
    revision = _object(obj.get("revision_info"))
    law_id = str(law.get("law_id") or "")
    revision_id = str(revision.get("law_revision_id") or "")
    requested_revision = urlsplit(url).path.rsplit("/", 1)[-1]
    if (
        not law_id
        or not _REVISION.fullmatch(revision_id)
        or not revision_id.startswith(law_id + "_")
        or requested_revision != revision_id
    ):
        raise WlhError("egov_revision_mismatch")
    title = str(revision.get("law_title") or "").strip()
    if not title:
        raise WlhError("egov_title_missing")
    root = _xml(obj.get("law_full_text"))
    attachments = obj.get("attached_files_info")
    att_map = cast(dict[str, Any], attachments) if isinstance(attachments, dict) else {}
    att_list_raw = att_map.get("attached_files")
    att_srcs: list[str] = []
    if isinstance(att_list_raw, list):
        for item in cast(list[Any], att_list_raw):
            if isinstance(item, dict):
                raw_s = str(cast(dict[str, Any], item).get("src") or "").strip()
                if raw_s:
                    att_srcs.append(_normalize_attachment_src(raw_s))
    fig_srcs: list[str] = []
    fig_placeholders: list[tuple[str, str]] = []
    for fig in root.xpath(".//Fig[normalize-space(@src)!='']"):
        norm_fig_src = _normalize_attachment_src(str(fig.get("src") or ""))
        if norm_fig_src:
            token = f"@@WLHFIG{len(fig_placeholders)}@@"
            fig_srcs.append(norm_fig_src)
            fig_placeholders.append((token, f"[添付ファイル: {norm_fig_src}]"))
            fig.text = token
            fig.attrib.pop("src", None)
    required_srcs = list(dict.fromkeys([*fig_srcs, *att_srcs]))
    inline_zip_b64 = str(att_map.get("image_data") or "").strip()
    if required_srcs and inline_zip_b64:
        try:
            verify_attachment_zip(
                required_srcs, base64.b64decode(inline_zip_b64, validate=True)
            )
        except ValueError:
            raise WlhError("egov_attachments_unverified") from None
    body = root.find("LawBody")
    for heading in list(body.findall("LawTitle")):
        body.remove(heading)
    text = markdown(body)
    for token, label in fig_placeholders:
        text = text.replace(token, label)
    extra_att_notes = [
        f"[添付ファイル: {src}]" for src in att_srcs if src not in set(fig_srcs)
    ]
    if extra_att_notes:
        text = text.rstrip() + "\n\n" + "\n\n".join(extra_att_notes)
    if not text.strip():
        raise WlhError("egov_law_text_missing")
    status = str(revision.get("current_revision_status") or "").strip()
    comment = str(revision.get("amendment_enforcement_comment") or "").strip()
    effective = _effective(root, revision)
    date_notes: list[str] = []
    if comment:
        date_notes.append("施行期日参考情報：" + comment)
    scheduled = _day(revision.get("amendment_scheduled_enforcement_date"))
    if scheduled:
        date_notes.append("施行期日参考情報（予定）：" + scheduled)
    number = str(law.get("law_num") or "").strip() or None
    full_text = normalized(
        "\n\n".join(
            [
                "# " + title,
                *([number] if number else []),
                *date_notes,
                text,
            ]
        )
    )
    record_out: dict[str, Any] = {
        "external_key": law_id,
        "title": title,
        "doc_number": number,
        "published_at": _day(law.get("promulgation_date")),
        "effective_at": effective,
        "expired_at": (
            _day(revision.get("repeal_date"))
            if revision.get("repeal_status") == "Repeal"
            else None
        ),
        "status": status or None,
        "issuer": None,
        "doc_type": "法律法规",
        "text": full_text,
        "detail_url": url,
    }
    if required_srcs:
        record_out["required_attachment_srcs"] = required_srcs
        if inline_zip_b64:
            record_out["attachment_zip_b64"] = inline_zip_b64
        else:
            zip_url = public_url(urljoin(url, f"/api/2/attachment/{revision_id}"))
            record_out["downloads"] = [{"url": zip_url, "role": "inline_attachment_zip"}]
    return [record_out]


def japan_requests(
    data: bytes, acquisition: Mapping[str, Any], config: Mapping[str, Any]
) -> list[dict[str, Any]] | None:
    response = _response(data, acquisition, config)
    if response is None:
        return None
    url, obj = response
    if urlsplit(url).path.rstrip("/") != "/api/2/laws":
        return []
    laws = obj.get("laws")
    count, total = obj.get("count"), obj.get("total_count")
    if (
        not isinstance(laws, list)
        or type(count) is not int
        or type(total) is not int
        or count != len(cast(list[Any], laws))
        or count < 0
        or total < 0
    ):
        raise WlhError("egov_catalog_invalid")
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    try:
        offset = int(query.get("offset", "0"))
    except ValueError:
        raise WlhError("egov_catalog_invalid") from None
    end = offset + count
    if offset < 0 or end > total:
        raise WlhError("egov_catalog_invalid")
    next_offset = obj.get("next_offset")
    if next_offset is None:
        if end != total:
            raise WlhError("egov_catalog_truncated")
    elif type(next_offset) is not int or not offset < next_offset == end < total:
        raise WlhError("egov_catalog_pagination_invalid")
    requests: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in cast(list[Any], laws):
        row = _object(item)
        law = _object(row.get("law_info"))
        revision = _object(row.get("revision_info"))
        revision_id = str(revision.get("law_revision_id") or "")
        law_id = str(law.get("law_id") or "")
        if (
            not law_id
            or law_id in seen
            or not _REVISION.fullmatch(revision_id)
            or not revision_id.startswith(law_id + "_")
        ):
            raise WlhError("egov_catalog_identity_invalid")
        seen.add(law_id)
        target = public_url(
            urljoin(url, "law_data/" + revision_id + "?law_full_text_format=xml")
        )
        guard(config, target)
        requests.append({"url": target, "role": "page"})
    if next_offset is not None:
        query["offset"] = str(next_offset)
        target = public_url(
            urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
        )
        guard(config, target)
        requests.append({"url": target, "role": "page"})
    return requests


class XmlParser(RecordAdapter):
    CONTRACT: str = "wlh.parser.xml.v9"
    NAMESPACE: str = "legislative-xml"

    def records(self, data: bytes, acquisition: Mapping[str, Any]) -> list[dict[str, Any]]:
        doc_budget = _budget_documents(self.config)
        cache_key = (
            acquisition.get("archive_ref"),
            acquisition.get("actual_url"),
            doc_budget,
        )
        cached: tuple[list[dict[str, Any]], str | None] | None = getattr(
            self, "_records_cache", {}
        ).get(cache_key)
        if cached is not None and cache_key[0] is not None:
            self.truncated = cached[1] or self.truncated
            return cached[0]
        official_records = japan_records(data, acquisition, self.config)
        if official_records is not None:
            return official_records
        raw_bytes = data
        zipped = data.startswith(b"PK\x03\x04")
        total_nodes = 1
        extra_xml_blobs: list[bytes] = []
        if zipped:
            max_bytes = max(_budget_response_bytes(self.config), MAX_ORIGINAL_BYTES)
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
        if tag == "dokument":
            key: str | None = _first_xpath(root, ".//ecli/text()") or _first_xpath(
                root, ".//doknr/text()"
            )
            number: str | None = _first_xpath(root, ".//aktenzeichen/text()") or _first_xpath(
                root, ".//az/text()"
            )
            title: str | None = (
                _first_xpath(root, ".//titelzeile")
                or _first_xpath(root, ".//titel")
                or number
                or key
            )
            published: str | None = _first_xpath(root, ".//entsch-datum/text()")
            if published and re.fullmatch(r"\d{8}", published):
                published = f"{published[:4]}-{published[4:6]}-{published[6:]}"
            return [
                {
                    "namespace": "oldp/case",
                    "external_key": key or number,
                    "title": title,
                    "doc_number": number,
                    "issuer": _first_xpath(
                        root, ".//gertyp/text()|.//gerort/text()|.//spruchkoerper/text()"
                    ),
                    "published_at": _record_day(published),
                    "doc_type": "案例",
                    "text": "\n\n".join(
                        " ".join(n.itertext())
                        for n in root.xpath(
                            ".//leitsatz|.//tenor|.//tatbestand|.//entscheidungsgruende|.//gruende|.//gründe"
                        )
                    ),
                    "original": (
                        {
                            "ref": acquisition["archive_ref"],
                            "kind": "zip",
                            "byte_size": len(raw_bytes),
                        }
                        if zipped
                        else None
                    ),
                }
            ]
        if tag == "akomaNtoso" or "/akn/3.0" in namespace:
            is_judgment = tag == "judgment" or bool(root.xpath(".//*[local-name()='judgment']"))
            cite_num: str | None = select(root, "neutralCitation", "cite", "docNumber") or select(
                root, "FRBRnumber", attribute="value"
            )
            number = cite_num or select(root, "docketNumber")
            title = select(root, "docTitle", "shortTitle", "longTitle") or select(
                root, "FRBRname", attribute="value"
            )
            key = select(root, "FRBRuri", attribute="value")
            if not key or key.rstrip("/").endswith("/id"):
                key = cite_num or acquisition.get("actual_url")
            published = select(root, "FRBRdate", attribute="date")
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
            if total_nodes > doc_budget:
                self.truncated = "document_budget_exceeded"
                nodes = nodes[:doc_budget]
            entries = []
            moj_meta_by_node: dict[int, dict[str, str | None]] = {}
            for n in nodes:
                law_url = select(n, "法規網址", "LawURL")
                law_no = select(n, "法規字號", "LawNo")
                if not law_no and law_url:
                    m_pcode = re.search(r"[?&]pcode=([A-Za-z0-9]+)", law_url, re.I)
                    if m_pcode:
                        law_no = m_pcode.group(1)
                moj_meta = resolve_moj_law_dates_and_meta(n)
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
            if total_nodes > doc_budget:
                self.truncated = "document_budget_exceeded"
                nodes = nodes[:doc_budget]
            entries = []
            base_acq_url = str(
                acquisition.get("actual_url") or acquisition.get("request_url") or ""
            )
            detail_tpl = str(
                cast(
                    dict[str, Any],
                    cast(dict[str, Any], self.config.get("processor") or {}).get("parser") or {},
                )
                .get("options", {})
                .get("detail_url_template")
                or ""
            )
            for n in nodes:
                meta_id = select(n, "MetaId")
                if meta_id and detail_tpl:
                    rec_url: str | None = detail_tpl.format(meta_id=meta_id)
                elif meta_id and base_acq_url:
                    rec_url = urljoin(
                        base_acq_url, f"/egFront/detail.do?metaid={meta_id}&log=detailLog"
                    )
                else:
                    rec_url = select(n, "GazetteHTML")
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
            if sum(len(str(record.get("text") or "")) for record in result) <= 1000000:
                while len(cache_dict) >= 16:
                    del cache_dict[next(iter(cache_dict))]
                cache_dict[cache_key] = (result, self.truncated)
            setattr(self, "_records_cache", cache_dict)
        return result

    def next_requests(
        self,
        data: bytes,
        acquisition: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        official_requests = japan_requests(data, acquisition, self.config)
        if official_requests is not None:
            return official_requests
        requests, truncated = discover(data, acquisition, self.config)
        self.truncated = truncated or self.truncated
        return requests


Adapter = XmlParser

__all__ = [
    "Adapter",
    "ERA_OFFSETS",
    "XmlParser",
    "japan_records",
    "japan_requests",
    "verify_attachment_zip",
]
