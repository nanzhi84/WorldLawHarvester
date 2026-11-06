"""Shared archived-record delivery for JSON, XML, and HTML record sources."""

from __future__ import annotations

import base64
import io
import json
import re
import unicodedata
import zipfile
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Literal, cast
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import lxml.etree as _etree  # type: ignore[import-untyped]

from wlh.cleaners.text_cleaner import clean_title
from wlh.core import WlhError, digest, guard, public_url
from wlh.processor.extractors.html.dom_tree import parse_html
from wlh.processor.extractors.magic import detect_real_format
from wlh.processor.extractors.registry import inline_markdown
from wlh.processor.extractors.xml import egov_law_xml_to_markdown
from wlh.processor.parsers import Base
from wlh.processor.records import RawRecord, RawUnitSource

etree: Any = _etree


def _normalize_attachment_src(raw_src: str) -> str:
    cleaned = raw_src.strip().replace("\\", "/")
    while cleaned.startswith("./") or cleaned.startswith("/"):
        cleaned = cleaned[2:] if cleaned.startswith("./") else cleaned[1:]
    return cleaned


def verify_attachment_zip(required_srcs: Sequence[str], zip_bytes: bytes | None) -> None:
    if not zip_bytes:
        raise WlhError("egov_attachments_unverified")
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            members: dict[str, bytes] = {}
            basenames: dict[str, bytes] = {}
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                norm_name = _normalize_attachment_src(name)
                member_bytes = zf.read(name)
                members[norm_name] = member_bytes
                basenames[norm_name.rsplit("/", 1)[-1]] = member_bytes
    except (zipfile.BadZipFile, OSError, KeyError):
        raise WlhError("egov_attachments_unverified") from None
    if not members or any(len(b) == 0 for b in members.values()):
        raise WlhError("egov_attachments_unverified")
    for req_src in required_srcs:
        norm_req = _normalize_attachment_src(req_src)
        matched = members.get(norm_req) or basenames.get(norm_req.rsplit("/", 1)[-1])
        if not matched:
            raise WlhError("egov_attachments_unverified")


def scalar(value: Any) -> str | None:
    if isinstance(value, dict):
        mapping = cast(Mapping[str, Any], value)
        return str(mapping.get("name") or mapping.get("title") or mapping.get("id") or "")
    if isinstance(value, list):
        return "; ".join(scalar(v) or "" for v in cast(Sequence[Any], value))
    return str(value).strip() if value is not None else None


def day(value: Any) -> str | None:
    text = scalar(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        raise WlhError("document_date_unparseable") from None


def first(node: Any, xpath: str) -> str | None:
    values: Any = node.xpath(xpath)
    if isinstance(values, str):
        return values.strip() or None
    return (
        " ".join(
            " ".join(v.itertext()) if hasattr(v, "itertext") else str(v) for v in values
        ).strip()
        or None
    )


def payload(data: bytes) -> Any:
    try:
        return json.loads(data)
    except ValueError:
        raise WlhError("bridge_json_invalid") from None


def items(value: Any, names: Sequence[str]) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return cast(list[dict[str, Any]], value)
    if not isinstance(value, dict):
        raise WlhError("bridge_records_invalid")
    mapping = cast(dict[str, Any], value)
    for name in names:
        if isinstance(mapping.get(name), list):
            return cast(list[dict[str, Any]], mapping[name])
    return [mapping]


# e-Gov API v2 envelope: JSON catalog / revision payloads carrying Base64 law XML. Shared by
# the JSON and XML engines so neither imports the other.
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
        elif not label and str(revision.get("law_revision_id") or "").endswith("_000000000000000"):
            provisions.append(node)
    if not provisions:
        return None
    dates: set[str] = set()
    for provision in provisions:
        sentences = [
            _japanese_numbers("".join(node.itertext())) for node in provision.xpath(".//Sentence")
        ]
        if any(re.search(r"ただし|但し|但シ|ただ,|次の各号|政令で定める日", s) for s in sentences):
            return None
        for sentence in sentences:
            match = re.match(
                r"(?:この|本)(?:法律|政令|省令|府令|規則|法|令)(?:は|ハ)[、,]?"
                r"(?P<day>"
                + _JAPANESE_DAY
                + r"|公布の日)(?:から|より|ヨリ)施行(?:する|し|スル|ス)",
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
    fig_srcs = [
        norm_fig_src
        for fig in root.xpath(".//Fig[normalize-space(@src)!='']")
        if (norm_fig_src := _normalize_attachment_src(str(fig.get("src") or "")))
    ]
    required_srcs = list(dict.fromkeys([*fig_srcs, *att_srcs]))
    inline_zip_b64 = str(att_map.get("image_data") or "").strip()
    if required_srcs and inline_zip_b64:
        try:
            verify_attachment_zip(required_srcs, base64.b64decode(inline_zip_b64, validate=True))
        except ValueError:
            raise WlhError("egov_attachments_unverified") from None
    status = str(revision.get("current_revision_status") or "").strip()
    comment = str(revision.get("amendment_enforcement_comment") or "").strip()
    effective = _effective(root, revision)
    scheduled = _day(revision.get("amendment_scheduled_enforcement_date"))
    number = str(law.get("law_num") or "").strip() or None
    full_text = egov_law_xml_to_markdown(
        root,
        title=title,
        number=number,
        enforcement_comment=comment or None,
        scheduled_enforcement_date=scheduled,
        att_srcs=att_srcs,
    )
    record_out: dict[str, Any] = {
        "external_key": law_id,
        "title": title,
        "doc_number": number,
        "published_at": _day(law.get("promulgation_date")),
        "effective_at": effective,
        "expired_at": (
            _day(revision.get("repeal_date")) if revision.get("repeal_status") == "Repeal" else None
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
        target = public_url(urljoin(url, "law_data/" + revision_id + "?law_full_text_format=xml"))
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


class RecordAdapter(Base):
    NAMESPACE: str = "record"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.truncated: str | None = None

    def records(self, data: bytes, acquisition: Mapping[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def next_requests(self, data: bytes, acquisition: Mapping[str, Any]) -> list[dict[str, Any]]:
        return []

    def parse(
        self, ctx: Any, task: Mapping[str, Any], acquisition: Mapping[str, Any]
    ) -> dict[str, Any]:
        if task["payload"].get("role") == "attachment":
            return {"requests": [], "targets": []}
        self.truncated = None
        data: bytes = ctx.read(acquisition)
        records = self.records(data, acquisition)
        targets: list[dict[str, Any]] = []
        for index, record in enumerate(records):
            key = str(record["external_key"])
            url = public_url(
                urljoin(
                    acquisition["actual_url"],
                    record.get("detail_url") or acquisition["actual_url"],
                )
            )
            inputs: list[dict[str, Any]] = [
                {"request": task["payload"], "role": "record", "selector": key, "position": index}
            ]
            for unit in record.get("downloads", []):
                target = guard(self.config, urljoin(url, unit["url"]))
                inputs.append(
                    {
                        "request": {"url": target, "role": "attachment"},
                        "role": "attachment",
                        "unit": {k: v for k, v in unit.items() if k != "url"},
                    }
                )
            targets.append(
                {
                    "identity": {
                        "namespace": record.get("namespace") or self.NAMESPACE,
                        "external_key": key,
                        "public_url": url,
                        "recheck_locator": {"url": task["payload"]["url"], "role": "page"},
                    },
                    "inputs": inputs,
                }
            )
        if ctx.follow_links(task):
            requests = self.next_requests(data, acquisition)
        else:
            requests = []
            if ctx.runtime.mode == "collect":
                unfollowed = self.next_requests(data, acquisition)
                if unfollowed:
                    self.truncated = self.truncated or "max_depth_exceeded"
        return {
            "requests": requests,
            "targets": targets,
            **({"truncated": self.truncated} if self.truncated else {}),
        }

    def plan_recheck(
        self, resources: Sequence[Mapping[str, Any]], state: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "requests": [{"url": r["recheck_locator"]["url"], "role": "page"} for r in resources],
            "targets": [],
            "state": state,
        }

    def build(
        self, ctx: Any, target: Mapping[str, Any], inputs: Sequence[Mapping[str, Any]]
    ) -> list[RawRecord]:
        acquisition: Mapping[str, Any] = inputs[0]["acquisition"]
        cache_key = (
            acquisition.get("archive_ref"),
            acquisition.get("actual_url"),
            self.budget_documents,
        )
        cached: tuple[list[dict[str, Any]], str | None] | None = getattr(
            self, "_records_cache", {}
        ).get(cache_key)
        records = (
            cached[0]
            if (cached is not None and cache_key[0] is not None)
            else self.records(ctx.read(acquisition), acquisition)
        )
        key: str = inputs[0].get("selector") or target["identity"]["external_key"]
        pos = inputs[0].get("position")
        if (
            isinstance(pos, int)
            and 0 <= pos < len(records)
            and str(records[pos]["external_key"]) == key
        ):
            matches = [(pos, records[pos])]
        else:
            matches = [(i, r) for i, r in enumerate(records) if str(r["external_key"]) == key]
        if len(matches) != 1:
            raise WlhError("bridge_record_identity_ambiguous")
        index, record = matches[0]
        url: str = target["identity"]["public_url"]
        ns: str = str(
            record.get("namespace") or target["identity"].get("namespace") or self.NAMESPACE
        )
        lineage: list[dict[str, Any]] = [
            {
                "acquisition_id": acquisition["id"],
                "archive_ref": acquisition["archive_ref"],
                "role": "source_response",
                "locator": {"record_index": index},
                "semantic_locator": {"namespace": ns, "external_key": key},
                "slice_hash": digest(record),
            }
        ]
        units: list[RawUnitSource] = []
        inline_zip_inputs = [
            item
            for item in inputs[1:]
            if cast(Mapping[str, Any], item.get("unit") or {}).get("role")
            == "inline_attachment_zip"
        ]
        regular_inputs = [
            item
            for item in inputs[1:]
            if cast(Mapping[str, Any], item.get("unit") or {}).get("role")
            != "inline_attachment_zip"
        ]
        req_att_srcs = record.get("required_attachment_srcs")
        if isinstance(req_att_srcs, list) and req_att_srcs and not record.get("attachment_zip_b64"):
            if not inline_zip_inputs:
                raise WlhError("egov_attachments_unverified")
            zip_acq: Mapping[str, Any] = inline_zip_inputs[0]["acquisition"]
            zip_bytes: bytes = ctx.read(zip_acq)
            verify_attachment_zip([str(s) for s in cast(list[Any], req_att_srcs)], zip_bytes)
            lineage.append(
                {
                    "acquisition_id": zip_acq["id"],
                    "archive_ref": zip_acq["archive_ref"],
                    "role": "context",
                    "locator": {"kind": "whole_response", "url": zip_acq["actual_url"]},
                }
            )
        downloads: list[Any] = [
            d
            for d in list(record.get("downloads") or [])
            if not (
                isinstance(d, Mapping)
                and cast(Mapping[str, Any], d).get("role") == "inline_attachment_zip"
            )
        ]
        strip_pats = self.strip_text_regexes or None
        title_pats = self.title_strip_regexes
        for idx, item in enumerate(regular_inputs):
            acq: Mapping[str, Any] = item["acquisition"]
            data: bytes = ctx.read(acq)
            kind = detect_real_format(data)
            if kind == "html":
                markdown = inline_markdown(
                    data,
                    kind,
                    base_url=acq["actual_url"],
                    quirks=tuple(self.quirks),
                    strip_text_regexes=strip_pats,
                )
                result: dict[str, Any] = {
                    "quality": ctx.text_quality(markdown),
                    "info": {"kind": kind},
                    "parsers": ["markdown", "quality"],
                }
            elif kind == "xml":
                markdown = inline_markdown(data, kind, strip_text_regexes=strip_pats)
                result = {
                    "quality": ctx.text_quality(markdown),
                    "info": {"kind": kind},
                    "parsers": ["xml", "markdown", "quality"],
                }
            else:
                result = ctx.extract(data, kind)
                markdown = str(result["markdown"])
                if strip_pats:
                    markdown = inline_markdown(markdown, "txt", strip_text_regexes=strip_pats)
            unit: Mapping[str, Any] = (
                cast(Mapping[str, Any], downloads[idx])
                if idx < len(downloads) and isinstance(downloads[idx], Mapping)
                else (item.get("unit") or {})
            )
            role = cast(
                Literal["primary", "volume", "independent_attachment"],
                unit.get("role", "primary" if not units else "independent_attachment"),
            )
            lineage.append(
                {
                    "acquisition_id": acq["id"],
                    "archive_ref": acq["archive_ref"],
                    "role": role,
                    "locator": {"kind": "whole_response", "url": acq["actual_url"]},
                    "volume_order": unit.get("volume_order"),
                }
            )
            units.append(
                RawUnitSource(
                    role=role,
                    title=unit.get("title"),
                    volume_order=unit.get("volume_order"),
                    markdown=markdown,
                    input=len(lineage) - 1,
                    quality=result["quality"],
                    extraction=result["info"],
                    parsers=result["parsers"],
                    original={"ref": acq["archive_ref"], "kind": kind, "byte_size": len(data)},
                )
            )
        if not units:
            markdown = (
                inline_markdown(
                    record["html"],
                    "html",
                    base_url=url,
                    quirks=tuple(self.quirks),
                    strip_text_regexes=strip_pats,
                )
                if record.get("html")
                else inline_markdown(record.get("text") or "", "txt", strip_text_regexes=strip_pats)
            )
            if not markdown.strip():
                raise WlhError("bridge_document_text_missing")
            lineage[0]["role"] = "primary"
            units = [
                RawUnitSource(
                    markdown=markdown,
                    input=0,
                    quality=ctx.text_quality(markdown),
                    parsers=["markdown", "quality"],
                    original=record.get("original"),
                )
            ]
        accept_routes = self.quality_accept_routes
        for unit_obj in units:
            if unit_obj.quality["recommended_route"] not in accept_routes:
                raise WlhError("quality_check_failed:" + unit_obj.quality["recommended_route"])
        meta_cfg = self.metadata_spec
        final_title = str(record["title"])
        if title_pats:
            final_title = clean_title(final_title, title_pats)
        return [
            RawRecord(
                namespace=ns,
                external_key=key,
                title=final_title,
                doc_type=record.get("doc_type") or str(meta_cfg.get("document_type") or "法律法规"),
                document_number=scalar(record.get("doc_number")),
                issuer=scalar(record.get("issuer")),
                published_at=day(record.get("published_at")),
                effective_at=day(record.get("effective_at")),
                expired_at=day(record.get("expired_at")),
                status=scalar(record.get("status")),
                source_name=str(meta_cfg.get("source_name") or ""),
                detail_url=url,
                units=units,
                inputs=lineage,
                recheck_locator=target["identity"]["recheck_locator"],
            )
        ]


def html_records(
    data: bytes,
    acquisition: Mapping[str, Any],
    *,
    containers: str,
    fields: Mapping[str, str],
    namespace: str,
) -> list[dict[str, Any]]:
    tree, _, _ = parse_html(data, acquisition.get("charset"))
    nodes: list[Any] = tree.xpath(containers) or [tree]
    records: list[dict[str, Any]] = []
    for _index, node in enumerate(nodes):
        values: dict[str, Any] = {key: first(node, xpath) for key, xpath in fields.items()}
        title = values.get("title")
        if not title:
            raise WlhError("bridge_title_missing")
        identity = values.pop("external_key", None) or values.get("doc_number")
        if not identity:
            raise WlhError("bridge_identity_missing")
        body: list[Any] = node.xpath(
            './/*[contains(concat(" ",normalize-space(@class)," ")," tenor ") or contains(concat(" ",normalize-space(@class)," ")," body ")]'
        )
        fragment: str = etree.tostring(body[0] if body else node, encoding="unicode", method="html")
        records.append(
            {
                **values,
                "namespace": namespace,
                "external_key": identity,
                "html": fragment,
                "detail_url": acquisition["actual_url"],
            }
        )
    return records


__all__ = [
    "RecordAdapter",
    "_normalize_attachment_src",
    "day",
    "first",
    "html_records",
    "items",
    "japan_records",
    "japan_requests",
    "payload",
    "scalar",
    "verify_attachment_zip",
]
