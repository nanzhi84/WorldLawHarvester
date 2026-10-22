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
from typing import Any

import lxml.etree as etree

from wlh.adapters.bridges.common import RecordAdapter
from wlh.core import WlhError
from wlh.extraction.markdown import normalized

etree: Any


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
    headings: set[str] = {
        "heading",
        "longTitle",
        "docTitle",
        "LawTitle",
        "LawName",
        "法規名稱",
        "ArticleCaption",
        "ChapterTitle",
        "titel",
        "enbez",
        "HEAD",
        "Heading",
        "TitleText",
        "MarginalNote",
    }
    paragraphs: set[str] = {
        "p",
        "paragraph",
        "Sentence",
        "LawContent",
        "法規內容",
        "條文內容",
        "ParagraphSentence",
        "P",
        "PSPACE",
        "FP",
        "Text",
    }

    def walk(node: Any) -> None:
        if not isinstance(node.tag, str):
            return
        tag = etree.QName(node).localname
        if tag in {
            "meta",
            "metadata",
            "identification",
            "Identification",
            "preface",
            "Notes",
            "附註",
            "presentation",
            "style",
            "metadaten",
            "fussnoten",
            "toc",
        }:
            return
        if tag in headings or tag in paragraphs:
            value: str | None = text(node)
            if value:
                parts.append(("## " if tag in headings else "") + value)
            return
        if tag in {"table", "Table"}:
            rows: list[list[str]] = []
            for row in node.xpath('.//*[local-name()="tr" or local-name()="TableRow"]'):
                rows.append([text(c).replace("|", "\\|") for c in row])
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
            except (zipfile.BadZipFile, OSError, ValueError, RuntimeError, NotImplementedError):
                raise WlhError("zip_invalid") from None
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
        if tag == "akomaNtoso" or "/akn/3.0" in namespace:
            is_judgment = tag == "judgment" or bool(root.xpath(".//*[local-name()='judgment']"))
            number: str | None = select(root, "neutralCitation", "cite", "docNumber") or select(
                root, "FRBRnumber", attribute="value"
            )
            title: str | None = select(root, "docTitle", "shortTitle", "longTitle") or select(
                root, "FRBRname", attribute="value"
            )
            key: str | None = select(root, "FRBRuri", attribute="value") or number
            published: str | None = select(root, "FRBRdate", attribute="date")
            issuer: str | None = select(root, "FRBRauthor", attribute="href")
            if issuer and issuer.startswith("#"):
                matches = root.xpath(".//*[@eId=$id]", id=issuer[1:])
                issuer = matches[0].get("showAs") if matches else None
            issuer = issuer or select(root, "courtType", "court")
            doc_type: str | None = "案例" if is_judgment else "法律法规"
            entries: list[tuple[Any, str | None, str | None, str | None, str | None, str | None, str | None]] = [(root, title, number, key, published, issuer, doc_type)]
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
                (
                    root,
                    title,
                    number,
                    root.get("identifier") or number,
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
            m_date: re.Match[str] | None = re.search(r"/(\d{4}-\d{2}-\d{2})/", acquisition.get("actual_url") or "")
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
                if year and law.get("Era") in {"Meiji", "Taisho", "Showa", "Heisei", "Reiwa"}:
                    year = str(
                        int(year)
                        + {
                            "Meiji": 1867,
                            "Taisho": 1911,
                            "Showa": 1925,
                            "Heisei": 1988,
                            "Reiwa": 2018,
                        }[law.get("Era")]
                    )
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
                published = published[:4] + "-" + published[4:6] + "-" + published[6:]
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
