"""Declarative XML schema specifications and table-driven evaluator for legal XML dialects."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urljoin

import lxml.etree as _etree  # type: ignore[import-untyped]

from wlh.cleaners.dates import resolve_moj_law_dates_and_meta
from wlh.core import WlhError
from wlh.processor.extractors.xml import (
    dokument_xml_to_markdown,
    gazette_html_to_markdown,
    markdown,
)
from wlh.processor.parsers.record_base import day as _record_day
from wlh.processor.parsers.record_base import first as _first_xpath
from wlh.processor.parsers.xml.values import select

etree: Any = _etree

ERA_OFFSETS: dict[str, int] = {
    "Meiji": 1867,
    "Taisho": 1911,
    "Showa": 1925,
    "Heisei": 1988,
    "Reiwa": 2018,
}


def _empty_fields() -> dict[str, tuple[str, ...]]:
    return {}


@dataclass(frozen=True, slots=True)
class XmlSchemaSpec:
    """Declarative specification for one legal or judicial XML dialect."""

    name: str
    root_tags: tuple[str, ...] = ()
    namespace_substrings: tuple[str, ...] = ()
    record_xpath: str | None = None
    default_namespace: str = "legislative-xml"
    default_doc_type: str | None = "法律法规"
    default_issuer: str | None = None
    fields: Mapping[str, tuple[str, ...]] = field(default_factory=_empty_fields)
    markdown_dialect: Literal["legal_xml", "dokument", "gazette"] = "legal_xml"


XML_SCHEMA_SPECS: tuple[XmlSchemaSpec, ...] = (
    XmlSchemaSpec(
        name="de_dokument",
        root_tags=("dokument",),
        default_namespace="oldp/case",
        default_doc_type="案例",
        fields={
            "external_key": (".//ecli/text()", ".//doknr/text()", "$doc_number"),
            "doc_number": (".//aktenzeichen/text()", ".//az/text()"),
            "title": (".//titelzeile", ".//titel", "$doc_number", "$external_key"),
            "published_at": (".//entsch-datum/text()",),
            "issuer": (".//gertyp/text()|.//gerort/text()|.//spruchkoerper/text()",),
        },
        markdown_dialect="dokument",
    ),
    XmlSchemaSpec(
        name="akoma_ntoso",
        root_tags=("akomaNtoso", "judgment"),
        namespace_substrings=("/akn/3.0",),
        default_doc_type="法律法规",
        fields={
            "doc_number": (
                "neutralCitation",
                "cite",
                "docNumber",
                "FRBRnumber@value",
                "docketNumber",
            ),
            "title": ("docTitle", "shortTitle", "longTitle", "FRBRname@value"),
            "external_key": ("FRBRuri@value", "$doc_number"),
            "published_at": ("FRBRdate@date",),
            "issuer": ("FRBRauthor@href", "courtType", "court"),
        },
        markdown_dialect="legal_xml",
    ),
    XmlSchemaSpec(
        name="uslm",
        root_tags=("lawDoc", "bill", "statute", "uscDoc", "pLaw"),
        namespace_substrings=("uslm",),
        default_doc_type="法律法规",
        fields={
            "title": ("docTitle", "dcTitle", "title", "officialTitle", "longTitle"),
            "doc_number": ("citableAs", "docNumber", "number", "identifier"),
            "external_key": ("@identifier", "identifier", "$doc_number"),
            "published_at": ("approvedDate", "created", "date@date", "date"),
            "issuer": ("publisher", "creator"),
        },
        markdown_dialect="legal_xml",
    ),
    XmlSchemaSpec(
        name="de_norm",
        root_tags=("dokumente", "norm"),
        default_doc_type="法律法规",
        fields={
            "title": ("langue", "titel", "jurabk"),
            "doc_number": ("jurabk", "@doknr"),
            "external_key": ("@doknr", "norm@doknr", "$doc_number"),
            "published_at": ("ausfertigung-datum",),
            "issuer": ("periodikum",),
        },
        markdown_dialect="legal_xml",
    ),
    XmlSchemaSpec(
        name="us_ecfr",
        root_tags=(
            "ECFR",
            "DLPSTEXTCLASS",
            "DIV1",
            "DIV2",
            "DIV3",
            "DIV4",
            "DIV5",
            "DIV6",
            "DIV7",
            "DIV8",
            "DIV9",
        ),
        default_doc_type="法律法规",
        default_issuer="Office of the Federal Register (NARA)",
        fields={
            "title": ("HEAD", "TITLE"),
            "doc_number": ("@hierarchy_metadata:citation", "@N", "$title"),
            "external_key": ("@hierarchy_metadata:path", "$doc_number"),
            "published_at": ("AMDDATE", "DATE"),
            "issuer": ("AGENCY",),
        },
        markdown_dialect="legal_xml",
    ),
    XmlSchemaSpec(
        name="ca_statute",
        root_tags=("Statute", "Regulation"),
        default_doc_type="法律法规",
        default_issuer="Department of Justice Canada",
        fields={
            "title": ("ShortTitle", "LongTitle", "TitleText"),
            "doc_number": ("ConsolidatedNumber", "InstrumentNumber"),
            "external_key": ("$doc_number", "$title"),
            "published_at": ("@*lastAmendedDate", "@*pit-date", "@*current-date"),
        },
        markdown_dialect="legal_xml",
    ),
    XmlSchemaSpec(
        name="jp_egov_xml",
        root_tags=("Law", "DataRoot"),
        record_xpath='.//*[local-name()="Law"]',
        default_doc_type=None,
        fields={
            "title": ("LawTitle",),
            "doc_number": ("LawNum",),
            "external_key": ("$doc_number",),
            "published_at": ("@Era+@Year+@PromulgateMonth+@PromulgateDay",),
        },
        markdown_dialect="legal_xml",
    ),
    XmlSchemaSpec(
        name="tw_moj_xml",
        root_tags=("MojLawSplit", "Laws", "法規", "法規資料"),
        record_xpath='.//*[local-name()="法規" or local-name()="Law"]',
        default_doc_type=None,
        fields={
            "title": ("法規名稱", "LawName"),
            "doc_number": ("法規字號", "LawNo"),
            "external_key": ("法規網址", "LawURL", "$title"),
            "published_at": ("最新異動日期", "LawModifiedDate", "LawPublishedDate"),
        },
        markdown_dialect="legal_xml",
    ),
    XmlSchemaSpec(
        name="tw_gazette_xml",
        root_tags=("Gazette",),
        record_xpath='.//*[local-name()="Record"]',
        default_doc_type=None,
        fields={
            "title": ("Title", "ThemeSubject", "GazetteId"),
            "doc_number": ("GazetteId", "MetaId"),
            "published_at": ("Date_Published", "Date_Created"),
            "issuer": ("PubGovName", "PubGov"),
        },
        markdown_dialect="gazette",
    ),
)

_SCHEMA_BY_NAME: dict[str, XmlSchemaSpec] = {spec.name: spec for spec in XML_SCHEMA_SPECS}


def match_xml_schema(root: Any) -> XmlSchemaSpec | None:
    """Return the matching ``XmlSchemaSpec`` for an XML element root, or ``None``."""
    tag = etree.QName(root).localname
    namespace = etree.QName(root).namespace or ""
    ns_lower = namespace.lower()

    if tag == "dokument":
        return _SCHEMA_BY_NAME["de_dokument"]
    if tag in {"akomaNtoso", "judgment"} or "/akn/3.0" in namespace:
        return _SCHEMA_BY_NAME["akoma_ntoso"]
    if "uslm" in ns_lower or tag in {"lawDoc", "bill", "statute", "uscDoc", "pLaw"}:
        return _SCHEMA_BY_NAME["uslm"]
    if tag in {"dokumente", "norm"}:
        return _SCHEMA_BY_NAME["de_norm"]
    if re.fullmatch(r"DIV[1-9]", tag) or tag in {"ECFR", "DLPSTEXTCLASS"}:
        return _SCHEMA_BY_NAME["us_ecfr"]
    if tag in {"Statute", "Regulation"}:
        return _SCHEMA_BY_NAME["ca_statute"]
    if tag in {"Law", "DataRoot"} and (
        select(root, "LawTitle") or root.xpath('.//*[local-name()="LawBody"]')
    ):
        return _SCHEMA_BY_NAME["jp_egov_xml"]
    if tag in {"MojLawSplit", "Laws", "法規", "法規資料"} or select(root, "法規名稱", "LawName"):
        return _SCHEMA_BY_NAME["tw_moj_xml"]
    if tag == "Gazette" or bool(
        root.xpath('.//*[local-name()="Record" and *[local-name()="MetaId"]]')
    ):
        return _SCHEMA_BY_NAME["tw_gazette_xml"]
    return None


def _eval_rule(node: Any, rule: str, resolved: Mapping[str, str | None]) -> str | None:
    """Evaluate a single declarative field rule against ``node``."""
    if rule.startswith("$"):
        return resolved.get(rule[1:])
    if rule.startswith("@hierarchy_metadata:"):
        subkey = rule.split(":", 1)[1]
        meta = json.loads(html.unescape(node.get("hierarchy_metadata") or "{}"))
        val = meta.get(subkey)
        return str(val).strip() if val else None
    if rule.startswith("@*"):
        suffix = rule[2:]
        for attr_k, attr_v in node.attrib.items():
            if attr_k.endswith(suffix) and attr_v:
                return str(attr_v).strip()
        return None
    if rule.startswith("@"):
        val = node.get(rule[1:])
        return str(val).strip() if val else None
    if rule.startswith(("./", ".//", "/")):
        return _first_xpath(node, rule)
    if "@" in rule:
        elem_name, attr_name = rule.split("@", 1)
        return select(node, elem_name, attribute=attr_name)
    return select(node, rule)


def _eval_fields(
    node: Any, spec: XmlSchemaSpec, field_order: Sequence[str]
) -> dict[str, str | None]:
    resolved: dict[str, str | None] = {}
    for field_name in field_order:
        rules = spec.fields.get(field_name, ())
        val: str | None = None
        for rule in rules:
            val = _eval_rule(node, rule, resolved)
            if val:
                break
        resolved[field_name] = val
    return resolved


def extract_xml_records(
    root: Any,
    acquisition: Mapping[str, Any],
    *,
    raw_bytes: bytes,
    zipped: bool = False,
    extra_xml_blobs: Sequence[bytes] = (),
    doc_budget: int = 25,
    detail_url_template: str = "",
) -> tuple[list[dict[str, Any]], str | None]:
    """Evaluate ``root`` against ``XML_SCHEMA_SPECS`` and return ``(records, truncated)``."""
    spec = match_xml_schema(root)
    if spec is None:
        raise WlhError("unsupported_legal_xml_vocabulary")

    tag = etree.QName(root).localname
    truncated: str | None = None
    total_nodes = 1
    moj_meta_by_node: dict[int, dict[str, str | None]] = {}

    if spec.name == "de_dokument":
        vals = _eval_fields(
            root, spec, ("doc_number", "external_key", "title", "published_at", "issuer")
        )
        published = vals["published_at"]
        if published and re.fullmatch(r"\d{8}", published):
            published = f"{published[:4]}-{published[4:6]}-{published[6:]}"
        return (
            [
                {
                    "namespace": spec.default_namespace,
                    "external_key": vals["external_key"] or vals["doc_number"],
                    "title": vals["title"],
                    "doc_number": vals["doc_number"],
                    "issuer": vals["issuer"],
                    "published_at": _record_day(published),
                    "doc_type": spec.default_doc_type,
                    "text": dokument_xml_to_markdown(root),
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
            ],
            None,
        )

    entries: list[
        tuple[Any, str | None, str | None, str | None, str | None, str | None, str | None]
    ] = []

    if spec.name == "akoma_ntoso":
        is_judgment = tag == "judgment" or bool(root.xpath(".//*[local-name()='judgment']"))
        cite_num = select(root, "neutralCitation", "cite", "docNumber") or select(
            root, "FRBRnumber", attribute="value"
        )
        vals = _eval_fields(
            root, spec, ("doc_number", "title", "external_key", "published_at", "issuer")
        )
        number = vals["doc_number"]
        title = vals["title"]
        key = select(root, "FRBRuri", attribute="value")
        if not key or key.rstrip("/").endswith("/id"):
            key = cite_num or acquisition.get("actual_url")
        published = vals["published_at"]
        issuer = select(root, "FRBRauthor", attribute="href")
        if issuer and issuer.startswith("#"):
            target_id = issuer[1:]
            matches = root.xpath(
                './/*[@eId=$id or @id=$id or @*[local-name()="eId"]=$id]', id=target_id
            )
            if matches:
                node_m = matches[0]
                issuer = (
                    node_m.get("showAs")
                    or next(
                        (v for k, v in node_m.attrib.items() if k.endswith("}showAs") and v),
                        None,
                    )
                    or "".join(node_m.itertext()).strip()
                    or None
                )
            else:
                issuer = None
        issuer = issuer or select(root, "courtType", "court")
        doc_type: str | None = "案例" if is_judgment else spec.default_doc_type
        entries = [(root, title, number, key, published, issuer, doc_type)]

    elif spec.name == "uslm":
        doc_title = select(root, "docTitle")
        title = (
            select(root, "dcTitle", "title", "officialTitle", "longTitle")
            if doc_title and doc_title.strip().lower() in {"an act", "joint resolution"}
            else (doc_title or select(root, "longTitle", "dcTitle", "title"))
        )
        vals = _eval_fields(root, spec, ("doc_number", "external_key", "published_at", "issuer"))
        number = vals["doc_number"]
        published = (vals["published_at"] or "")[:10] or None
        issuer = vals["issuer"] or ("United States Congress" if tag == "pLaw" else None)
        entries = [
            (root, title, number, vals["external_key"], published, issuer, spec.default_doc_type)
        ]

    elif spec.name == "de_norm":
        vals = _eval_fields(
            root, spec, ("title", "doc_number", "external_key", "published_at", "issuer")
        )
        entries = [
            (
                root,
                vals["title"],
                vals["doc_number"],
                vals["external_key"],
                vals["published_at"],
                vals["issuer"],
                spec.default_doc_type,
            )
        ]

    elif spec.name == "us_ecfr":
        vals = _eval_fields(
            root, spec, ("title", "doc_number", "external_key", "published_at", "issuer")
        )
        m_date = re.search(r"/(\d{4}-\d{2}-\d{2})/", str(acquisition.get("actual_url") or ""))
        published = vals["published_at"] or (m_date.group(1) if m_date else None)
        issuer = vals["issuer"] or spec.default_issuer
        entries = [
            (
                root,
                vals["title"],
                vals["doc_number"],
                vals["external_key"],
                published,
                issuer,
                spec.default_doc_type,
            )
        ]

    elif spec.name == "ca_statute":
        vals = _eval_fields(root, spec, ("title", "doc_number", "external_key", "published_at"))
        entries = [
            (
                root,
                vals["title"],
                vals["doc_number"],
                vals["external_key"],
                vals["published_at"],
                spec.default_issuer,
                spec.default_doc_type,
            )
        ]

    elif spec.name == "jp_egov_xml":
        laws = (
            [root] if tag == "Law" else root.xpath(spec.record_xpath or './/*[local-name()="Law"]')
        )
        for law in laws:
            vals = _eval_fields(law, spec, ("title", "doc_number", "external_key"))
            year: str | None = law.get("Year")
            era = str(law.get("Era") or "")
            if year and era in ERA_OFFSETS:
                year = str(int(year) + ERA_OFFSETS[era])
            published = (
                f"{int(year):04d}-{int(law.get('PromulgateMonth')):02d}-{int(law.get('PromulgateDay')):02d}"
                if year and law.get("PromulgateMonth") and law.get("PromulgateDay")
                else None
            )
            entries.append(
                (
                    law,
                    vals["title"],
                    vals["doc_number"],
                    vals["external_key"],
                    published,
                    spec.default_issuer,
                    spec.default_doc_type,
                )
            )

    elif spec.name == "tw_moj_xml":
        nodes = root.xpath(
            spec.record_xpath or './/*[local-name()="法規" or local-name()="Law"]'
        ) or [root]
        total_nodes = len(nodes)
        if total_nodes > doc_budget:
            truncated = "document_budget_exceeded"
            nodes = nodes[:doc_budget]
        for n in nodes:
            vals = _eval_fields(n, spec, ("title", "doc_number", "external_key", "published_at"))
            law_url = select(n, "法規網址", "LawURL")
            law_no = vals["doc_number"]
            if not law_no and law_url:
                m_pcode = re.search(r"[?&]pcode=([A-Za-z0-9]+)", law_url, re.I)
                if m_pcode:
                    law_no = m_pcode.group(1)
            moj_meta = resolve_moj_law_dates_and_meta(n)
            moj_meta_by_node[id(n)] = moj_meta
            entries.append(
                (
                    n,
                    vals["title"],
                    law_no,
                    vals["external_key"],
                    moj_meta["published_at"] or vals["published_at"],
                    moj_meta["issuer"],
                    spec.default_doc_type,
                )
            )

    elif spec.name == "tw_gazette_xml":
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
            for rec in r_node.xpath(spec.record_xpath or './/*[local-name()="Record"]')
        ]
        total_nodes = len(nodes)
        if total_nodes > doc_budget:
            truncated = "document_budget_exceeded"
            nodes = nodes[:doc_budget]
        base_acq_url = str(acquisition.get("actual_url") or acquisition.get("request_url") or "")
        for n in nodes:
            vals = _eval_fields(n, spec, ("title", "doc_number", "published_at", "issuer"))
            meta_id = select(n, "MetaId")
            if meta_id and detail_url_template:
                rec_url: str | None = detail_url_template.format(meta_id=meta_id)
            elif meta_id and base_acq_url:
                rec_url = urljoin(
                    base_acq_url, f"/egFront/detail.do?metaid={meta_id}&log=detailLog"
                )
            else:
                rec_url = select(n, "GazetteHTML")
            doc_style = select(n, "Doc_Style_LName")
            rec_title = vals["title"] or (f"行政院公報第{meta_id}號" if meta_id else None)
            if not rec_title or not (rec_url or rec_title):
                continue
            entries.append(
                (
                    n,
                    rec_title,
                    vals["doc_number"],
                    rec_url or rec_title,
                    vals["published_at"],
                    vals["issuer"],
                    "法律法规" if doc_style == "法規" else None,
                )
            )

    result: list[dict[str, Any]] = []
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
        html_content = select(node, "HTMLContent") if spec.markdown_dialect == "gazette" else None
        if html_content:
            text_body = gazette_html_to_markdown(
                html_content,
                key,
                fallback_parts=(select(node, "ThemeSubject"), select(node, "Explain")),
                title=title,
            )
        else:
            text_body = markdown(node, title=title)
        moj_extra = moj_meta_by_node.get(id(node)) or {}
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
    return result, truncated


__all__ = [
    "ERA_OFFSETS",
    "XML_SCHEMA_SPECS",
    "XmlSchemaSpec",
    "extract_xml_records",
    "match_xml_schema",
]
