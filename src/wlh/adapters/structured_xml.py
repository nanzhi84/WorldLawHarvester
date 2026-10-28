"""Official legal XML and XML packages.

Namespaces identify vocabulary, never trigger external entity/schema downloads.
Body text stays in source order; this adapter does not infer a legal ontology.
"""

from __future__ import annotations

import html
import io
import json
import re
import zipfile
from collections.abc import Mapping
from typing import Any, cast

import lxml.etree as etree

from wlh.adapters.bridges.common import RecordAdapter
from wlh.adapters.japan_law import next_requests as japan_requests
from wlh.adapters.japan_law import records as japan_records
from wlh.adapters.legal_xml_discovery import discover
from wlh.adapters.legal_xml_markdown import markdown
from wlh.adapters.legal_xml_values import select
from wlh.adapters.taiwan_law_dates import resolve_moj_law_dates_and_meta
from wlh.config import MAX_ORIGINAL_BYTES
from wlh.core import WlhError
from wlh.extraction.markdown import html_to_markdown, normalized

etree: Any
ERA_OFFSETS: dict[str, int] = {
    "Meiji": 1867, "Taisho": 1911, "Showa": 1925, "Heisei": 1988, "Reiwa": 2018,
}
class Adapter(RecordAdapter):
    CONTRACT: str = "wlh.adapter.structured-xml.v9"
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
        official_records = japan_records(data, acquisition, self.config)
        if official_records is not None:
            return official_records
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
            # The full source can contain thousands of large parts. This cache
            # only avoids immediate parse/build duplication; CAS remains truth.
            if sum(len(str(record.get("text") or "")) for record in result) <= 1000000:
                while len(cache_dict) >= 16:
                    del cache_dict[next(iter(cache_dict))]
                cache_dict[cache_key] = (result, self.truncated)
            setattr(self, "_records_cache", cache_dict)
        return result

    def next_requests(
        self, data: bytes, acquisition: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        official_requests = japan_requests(data, acquisition, self.config)
        if official_requests is not None:
            return official_requests
        requests, truncated = discover(data, acquisition, self.config)
        self.truncated = truncated or self.truncated
        return requests
