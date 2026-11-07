"""Declarative XML schema specifications and table-driven evaluator for legal XML dialects."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable, Mapping, Sequence
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


def _empty_str_map() -> dict[str, str]:
    return {}


@dataclass(frozen=True, slots=True)
class XmlSchemaSpec:
    """Declarative specification for one legal or judicial XML dialect."""

    name: str
    root_tags: tuple[str, ...] = ()
    namespace_substrings: tuple[str, ...] = ()
    required_xpath: str | None = None
    fallback_xpath: str | None = None
    record_xpath: str | None = None
    fallback_root_as_record: bool = True
    accept_extra_blobs: bool = False
    enforce_budget_limit: bool = False
    skip_incomplete_records: bool = False
    default_namespace: str = "legislative-xml"
    include_namespace: bool = False
    include_detail_url: bool = True
    default_doc_type: str | None = "法律法规"
    doc_type_xpaths: Mapping[str, str] = field(default_factory=_empty_str_map)
    default_issuer: str | None = None
    field_order: tuple[str, ...] = (
        "doc_number",
        "title",
        "external_key",
        "published_at",
        "issuer",
    )
    fields: Mapping[str, tuple[str, ...]] = field(default_factory=_empty_fields)
    node_enricher: Callable[[Any], Mapping[str, str | None]] | None = None
    markdown_dialect: Literal["legal_xml", "dokument", "gazette"] = "legal_xml"


XML_SCHEMA_SPECS: tuple[XmlSchemaSpec, ...] = (
    XmlSchemaSpec(
        name="de_dokument",
        root_tags=("dokument",),
        default_namespace="oldp/case",
        include_namespace=True,
        include_detail_url=False,
        default_doc_type="案例",
        field_order=("doc_number", "external_key", "title", "published_at", "issuer"),
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
        doc_type_xpaths={
            'self::*[local-name()="judgment"] | .//*[local-name()="judgment"]': "案例"
        },
        fields={
            "doc_number": (
                "neutralCitation",
                "cite",
                "docNumber",
                "FRBRnumber@value",
                "docketNumber",
            ),
            "title": ("docTitle", "shortTitle", "longTitle", "FRBRname@value"),
            "external_key": (
                "FRBRuri@value!/id",
                "neutralCitation",
                "cite",
                "docNumber",
                "FRBRnumber@value",
                "$actual_url",
                "$doc_number",
            ),
            "published_at": ("FRBRdate@date",),
            "issuer": ("FRBRauthor@href->showAs", "courtType", "court"),
        },
        markdown_dialect="legal_xml",
    ),
    XmlSchemaSpec(
        name="uslm",
        root_tags=("lawDoc", "bill", "statute", "uscDoc", "pLaw"),
        namespace_substrings=("uslm",),
        default_doc_type="法律法规",
        fields={
            "title": (
                "docTitle!=an act|joint resolution",
                "docTitle=an act|joint resolution->dcTitle|title|officialTitle|longTitle",
                "longTitle",
                "dcTitle",
                "title",
            ),
            "doc_number": ("citableAs", "docNumber", "number", "identifier"),
            "external_key": ("@identifier", "identifier", "$doc_number"),
            "published_at": ("approvedDate", "created", "date@date", "date"),
            "issuer": ("publisher", "creator", "@tag=pLaw:United States Congress"),
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
        field_order=("title", "doc_number", "external_key", "published_at", "issuer"),
        fields={
            "title": ("HEAD", "TITLE"),
            "doc_number": ("@hierarchy_metadata:citation", "@N", "$title"),
            "external_key": ("@hierarchy_metadata:path", "$doc_number"),
            "published_at": ("AMDDATE", "DATE", "$url_date"),
            "issuer": ("AGENCY",),
        },
        markdown_dialect="legal_xml",
    ),
    XmlSchemaSpec(
        name="ca_statute",
        root_tags=("Statute", "Regulation"),
        default_doc_type="法律法规",
        default_issuer="Department of Justice Canada",
        field_order=("title", "doc_number", "external_key", "published_at", "issuer"),
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
        required_xpath=(
            'self::*[local-name()="LawTitle" and normalize-space()]'
            ' | .//*[local-name()="LawTitle" and normalize-space()]'
            ' | .//*[local-name()="LawBody"]'
        ),
        record_xpath='self::*[local-name()="Law"] | .//*[local-name()="Law"]',
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
        fallback_xpath=(
            'self::*[local-name()="法規名稱" or local-name()="LawName"][normalize-space()]'
            ' | .//*[local-name()="法規名稱" or local-name()="LawName"][normalize-space()]'
        ),
        record_xpath='.//*[local-name()="法規" or local-name()="Law"]',
        fallback_root_as_record=True,
        enforce_budget_limit=True,
        default_doc_type=None,
        field_order=("title", "doc_number", "external_key", "published_at", "issuer"),
        fields={
            "title": ("法規名稱", "LawName"),
            "doc_number": ("法規字號", "LawNo", "法規網址?pcode", "LawURL?pcode"),
            "external_key": ("法規網址", "LawURL", "$title"),
            "published_at": ("最新異動日期", "LawModifiedDate", "LawPublishedDate"),
        },
        node_enricher=resolve_moj_law_dates_and_meta,
        markdown_dialect="legal_xml",
    ),
    XmlSchemaSpec(
        name="tw_gazette_xml",
        root_tags=("Gazette",),
        fallback_xpath='.//*[local-name()="Record" and *[local-name()="MetaId"]]',
        record_xpath='.//*[local-name()="Record"]',
        fallback_root_as_record=False,
        accept_extra_blobs=True,
        enforce_budget_limit=True,
        skip_incomplete_records=True,
        default_doc_type=None,
        doc_type_xpaths={
            './/*[local-name()="Doc_Style_LName" and normalize-space()="法規"]': "法律法规"
        },
        field_order=("title", "doc_number", "external_key", "published_at", "issuer"),
        fields={
            "title": (
                "Title",
                "ThemeSubject",
                "GazetteId",
                "MetaId#template:行政院公報第{value}號",
            ),
            "doc_number": ("GazetteId", "MetaId"),
            "external_key": ("MetaId#gazette_url", "GazetteHTML", "$title"),
            "published_at": ("Date_Published", "Date_Created"),
            "issuer": ("PubGovName", "PubGov"),
        },
        markdown_dialect="gazette",
    ),
)


def match_xml_schema(
    root: Any,
    *,
    specs: Sequence[XmlSchemaSpec] = XML_SCHEMA_SPECS,
) -> XmlSchemaSpec | None:
    """Return the matching ``XmlSchemaSpec`` for an XML element root, or ``None``."""
    qname = etree.QName(root)
    tag = qname.localname
    ns_lower = (qname.namespace or "").lower()
    for spec in specs:
        matched = tag in spec.root_tags or any(
            sub.lower() in ns_lower for sub in spec.namespace_substrings
        )
        if matched and spec.required_xpath:
            matched = bool(root.xpath(spec.required_xpath))
        if not matched and spec.fallback_xpath:
            matched = bool(root.xpath(spec.fallback_xpath))
        if matched:
            return spec
    return None


def _deref_author_show_as(node: Any, ref: str | None) -> str | None:
    if not ref:
        return None
    if not ref.startswith("#"):
        return ref
    target_id = ref[1:]
    matches = node.xpath('.//*[@eId=$id or @id=$id or @*[local-name()="eId"]=$id]', id=target_id)
    if not matches:
        return None
    target = matches[0]
    return (
        target.get("showAs")
        or next((v for k, v in target.attrib.items() if k.endswith("}showAs") and v), None)
        or "".join(target.itertext()).strip()
        or None
    )


def _eval_era_promulgate_date(node: Any) -> str | None:
    year_str: str | None = node.get("Year")
    era = str(node.get("Era") or "")
    month = node.get("PromulgateMonth")
    day = node.get("PromulgateDay")
    if not (year_str and month and day):
        return None
    year = int(year_str) + ERA_OFFSETS.get(era, 0)
    return f"{year:04d}-{int(month):02d}-{int(day):02d}"


def _eval_rule(
    node: Any,
    rule: str,
    resolved: Mapping[str, str | None],
    context: Mapping[str, str | None],
) -> str | None:
    """Evaluate a single declarative field rule against ``node``."""
    if rule == "@Era+@Year+@PromulgateMonth+@PromulgateDay":
        return _eval_era_promulgate_date(node)
    if rule.startswith("$"):
        key = rule[1:]
        return resolved.get(key) or context.get(key)
    if rule.startswith("@tag="):
        tag_cond, literal = rule[5:].split(":", 1)
        return literal if etree.QName(node).localname == tag_cond else None
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
    if "->showAs" in rule and "@" in rule:
        elem_attr = rule.removesuffix("->showAs")
        elem_name, attr_name = elem_attr.split("@", 1)
        return _deref_author_show_as(node, select(node, elem_name, attribute=attr_name))
    if "@" in rule and "!" in rule:
        elem_attr, bad_suffix = rule.split("!", 1)
        elem_name, attr_name = elem_attr.split("@", 1)
        val = select(node, elem_name, attribute=attr_name)
        return val if val and not val.rstrip("/").endswith(bad_suffix) else None
    if "@" in rule:
        elem_name, attr_name = rule.split("@", 1)
        return select(node, elem_name, attribute=attr_name)
    if "!=" in rule:
        elem_name, excluded_raw = rule.split("!=", 1)
        excluded = {part.strip().lower() for part in excluded_raw.split("|") if part.strip()}
        val = select(node, elem_name)
        return val if val and val.strip().lower() not in excluded else None
    if "=" in rule and "->" in rule:
        cond_part, fallback_raw = rule.split("->", 1)
        elem_name, matched_raw = cond_part.split("=", 1)
        matched = {part.strip().lower() for part in matched_raw.split("|") if part.strip()}
        val = select(node, elem_name)
        if not val or val.strip().lower() not in matched:
            return None
        fallback_names = [part.strip() for part in fallback_raw.split("|") if part.strip()]
        return select(node, *fallback_names)
    if "?" in rule:
        elem_name, param_name = rule.split("?", 1)
        val = select(node, elem_name)
        if not val:
            return None
        m_param = re.search(rf"[?&]{re.escape(param_name)}=([A-Za-z0-9]+)", val, re.I)
        return m_param.group(1) if m_param else None
    if "#gazette_url" in rule:
        elem_name = rule.removesuffix("#gazette_url")
        meta_id = select(node, elem_name)
        if not meta_id:
            return None
        tpl = context.get("detail_url_template")
        if tpl:
            return tpl.format(meta_id=meta_id)
        base_url = context.get("base_acq_url")
        if base_url:
            return urljoin(base_url, f"/egFront/detail.do?metaid={meta_id}&log=detailLog")
        return None
    if "#template:" in rule:
        elem_name, tpl_str = rule.split("#template:", 1)
        val = select(node, elem_name)
        return tpl_str.format(value=val) if val else None
    return select(node, rule)


def _eval_fields(
    node: Any,
    spec: XmlSchemaSpec,
    field_order: Sequence[str],
    context: Mapping[str, str | None],
) -> dict[str, str | None]:
    resolved: dict[str, str | None] = {}
    ordered_names = list(dict.fromkeys([*field_order, *spec.fields.keys()]))
    for field_name in ordered_names:
        rules = spec.fields.get(field_name, ())
        val: str | None = None
        for rule in rules:
            val = _eval_rule(node, rule, resolved, context)
            if val:
                break
        resolved[field_name] = val
    return resolved


def _resolve_doc_type(node: Any, spec: XmlSchemaSpec) -> str | None:
    for xpath_expr, mapped_type in spec.doc_type_xpaths.items():
        if node.xpath(xpath_expr):
            return mapped_type
    return spec.default_doc_type


def _normalize_xml_date(published: str | None, *, strict: bool) -> str | None:
    if not published:
        return None
    m_roc = re.search(
        r"(?:中華)?民國\s*(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
        published,
    )
    if m_roc:
        published = (
            f"{int(m_roc.group(1)) + 1911:04d}-{int(m_roc.group(2)):02d}-{int(m_roc.group(3)):02d}"
        )
    elif re.fullmatch(r"\d{8}", published):
        published = f"{published[:4]}-{published[4:6]}-{published[6:]}"
    elif re.match(r"^\d{4}-\d{2}-\d{2}", published):
        published = published[:10]
    return _record_day(published) if strict else published


def _render_xml_markdown(node: Any, spec: XmlSchemaSpec, *, key: str, title: str) -> str:
    if spec.markdown_dialect == "dokument":
        return dokument_xml_to_markdown(node)
    if spec.markdown_dialect == "gazette":
        html_content = select(node, "HTMLContent")
        if html_content:
            return gazette_html_to_markdown(
                html_content,
                key,
                fallback_parts=(select(node, "ThemeSubject"), select(node, "Explain")),
                title=title,
            )
    return markdown(node, title=title)


def extract_xml_records(
    root: Any,
    acquisition: Mapping[str, Any],
    *,
    raw_bytes: bytes,
    zipped: bool = False,
    extra_xml_blobs: Sequence[bytes] = (),
    doc_budget: int = 25,
    detail_url_template: str = "",
    specs: Sequence[XmlSchemaSpec] = XML_SCHEMA_SPECS,
) -> tuple[list[dict[str, Any]], str | None]:
    """Evaluate ``root`` against ``XML_SCHEMA_SPECS`` and return ``(records, truncated)``."""
    spec = match_xml_schema(root, specs=specs)
    if spec is None:
        raise WlhError("unsupported_legal_xml_vocabulary")

    roots: list[Any] = [root]
    if spec.accept_extra_blobs and extra_xml_blobs:
        xml_parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        for extra_blob in extra_xml_blobs:
            if b"<!ENTITY" in extra_blob.upper():
                raise WlhError("legal_xml_doctype_rejected")
            try:
                roots.append(etree.fromstring(extra_blob, xml_parser))
            except etree.XMLSyntaxError:
                raise WlhError("legal_xml_invalid") from None

    if spec.record_xpath:
        nodes = [rec for r_node in roots for rec in r_node.xpath(spec.record_xpath)]
        if not nodes and spec.fallback_root_as_record:
            nodes = [root]
    else:
        nodes = [root]

    truncated: str | None = None
    total_nodes = len(nodes)
    if spec.enforce_budget_limit and total_nodes > doc_budget:
        truncated = "document_budget_exceeded"
        nodes = nodes[:doc_budget]

    actual_url = acquisition.get("actual_url")
    base_acq_url = str(actual_url or acquisition.get("request_url") or "")
    m_url_date = re.search(r"/(\d{4}-\d{2}-\d{2})/", str(actual_url or ""))
    context: dict[str, str | None] = {
        "actual_url": str(actual_url) if actual_url else None,
        "base_acq_url": base_acq_url or None,
        "detail_url_template": detail_url_template or None,
        "url_date": m_url_date.group(1) if m_url_date else None,
    }

    result: list[dict[str, Any]] = []
    for node in nodes:
        vals = _eval_fields(node, spec, spec.field_order, context)
        extra = dict(spec.node_enricher(node)) if spec.node_enricher is not None else {}

        title = vals.get("title")
        number = vals.get("doc_number")
        key = vals.get("external_key") or number
        published = extra.get("published_at") or vals.get("published_at")
        issuer = extra.get("issuer") or vals.get("issuer") or spec.default_issuer
        doc_type = _resolve_doc_type(node, spec)

        if not title or not key:
            if spec.skip_incomplete_records:
                continue
            raise WlhError("legal_xml_identity_missing")

        published = _normalize_xml_date(published, strict=not spec.include_detail_url)
        text_body = _render_xml_markdown(node, spec, key=key, title=title)
        original = (
            {
                "ref": acquisition["archive_ref"],
                "kind": "zip",
                "byte_size": len(raw_bytes),
            }
            if zipped and total_nodes == 1
            else None
        )
        record: dict[str, Any] = {
            **({"namespace": spec.default_namespace} if spec.include_namespace else {}),
            "external_key": key,
            "title": title,
            "doc_number": number,
            "published_at": published,
            **(
                {
                    "effective_at": extra.get("effective_at"),
                    "expired_at": extra.get("expired_at"),
                    "status": extra.get("status"),
                }
                if spec.include_detail_url
                else {}
            ),
            "issuer": issuer,
            "doc_type": doc_type,
            "text": text_body,
            **({"detail_url": actual_url} if spec.include_detail_url else {}),
            "original": original,
        }
        result.append(record)
    return result, truncated


__all__ = [
    "ERA_OFFSETS",
    "XML_SCHEMA_SPECS",
    "XmlSchemaSpec",
    "extract_xml_records",
    "match_xml_schema",
]
