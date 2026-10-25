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
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import urljoin, urlsplit, urlunsplit

import lxml.etree as etree
from lxml import html as lxml_html  # type: ignore[import-untyped]

from wlh.adapters.bridges.common import RecordAdapter
from wlh.core import WlhError, public_url
from wlh.engine.scope import guard
from wlh.extraction.markdown import normalized

etree: Any
lxml_html: Any
HEADING_TAGS: set[str] = {
    "heading", "longTitle", "docTitle", "LawTitle", "LawName", "法規名稱",
    "ArticleCaption", "ChapterTitle", "titel", "enbez", "HEAD", "Heading",
    "TitleText", "MarginalNote",
}
PARAGRAPH_TAGS: set[str] = {
    "p", "paragraph", "Sentence", "LawContent", "法規內容", "條文內容",
    "ParagraphSentence", "P", "PSPACE", "FP", "Text",
}
SKIP_TAGS: set[str] = {
    "meta", "metadata", "identification", "Identification", "preface",
    "Notes", "附註", "presentation", "style", "metadaten", "fussnoten", "toc",
}
ERA_OFFSETS: dict[str, int] = {
    "Meiji": 1867, "Taisho": 1911, "Showa": 1925, "Heisei": 1988, "Reiwa": 2018,
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
        raw_bytes = data
        zipped = data.startswith(b"PK\x03\x04")
        if zipped:
            max_bytes = self.config["budget"]["response_bytes"]
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    member: zipfile.ZipInfo | None = next(
                        (
                            m
                            for m in archive.infolist()
                            if not m.is_dir() and m.filename.lower().endswith(".xml")
                        ),
                        None,
                    )
                    if member is None:
                        raise WlhError("zip_without_xml")
                    if member.file_size > max_bytes:
                        raise WlhError("zip_expanded_size_exceeded")
                    with archive.open(member) as source:
                        data = source.read(max_bytes + 1)
                    if len(data) > max_bytes:
                        raise WlhError("zip_expanded_size_exceeded")
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
            tag in {"feed", "items", "ActsRegsList"}
            or (
                tag == "DataRoot"
                and not (select(root, "LawTitle") or root.xpath(".//LawBody"))
                and bool(root.xpath(".//*[local-name()='LawId']/text()"))
            )
        ):
            return []
        if tag == "akomaNtoso" or "/akn/3.0" in namespace:
            is_judgment = tag == "judgment" or bool(root.xpath(".//*[local-name()='judgment']"))
            number: str | None = select(root, "neutralCitation", "cite", "docNumber") or select(
                root, "FRBRnumber", attribute="value"
            )
            title: str | None = select(root, "docTitle", "shortTitle", "longTitle") or select(
                root, "FRBRname", attribute="value"
            )
            key: str | None = select(root, "FRBRuri", attribute="value")
            if not key or key.rstrip("/").endswith("/id"):
                key = number or acquisition.get("actual_url")
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
        elif "uslm" in namespace.lower() or tag in {"lawDoc", "bill", "statute", "uscDoc"}:
            title = select(root, "docTitle", "longTitle", "dcTitle", "title")
            number = select(root, "docNumber", "number", "identifier")
            published = (
                select(root, "created")
                or select(root, "date", attribute="date")
                or select(root, "date")
                or ""
            )[:10] or None
            issuer = select(root, "publisher", "creator")
            entries = [
                (root, title, number, root.get("identifier") or number, published, issuer, "法律法规")
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
            entries = [(root, title, number, key, published, None, "法律法规")]
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
            entries = [(root, title, number, key, published, None, "法律法规")]
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
            entries = [
                (
                    n,
                    select(n, "法規名稱", "LawName"),
                    select(n, "法規字號", "LawNo"),
                    select(n, "法規網址", "LawURL") or select(n, "法規名稱", "LawName"),
                    select(n, "最新異動日期", "LawModifiedDate", "LawPublishedDate"),
                    None,
                    None,
                )
                for n in nodes
            ]
        else:
            raise WlhError("unsupported_legal_xml_vocabulary")
        result: list[dict[str, Any]] = []
        for node, title, number, key, published, issuer, doc_type in entries:
            if not title or not key:
                raise WlhError("legal_xml_identity_missing")
            if published and re.fullmatch(r"\d{8}", published):
                published = f"{published[:4]}-{published[4:6]}-{published[6:]}"
            result.append(
                {
                    "external_key": key,
                    "title": title,
                    "doc_number": number,
                    "published_at": published,
                    "issuer": issuer,
                    "doc_type": doc_type,
                    "text": "# " + title + "\n\n" + markdown(node),
                    "detail_url": acquisition["actual_url"],
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
            )
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
        if tag == "items":
            links = [
                str(t).strip()
                for t in root.xpath(".//*[local-name()='link']/text()")
                if str(t).strip()
            ]
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
