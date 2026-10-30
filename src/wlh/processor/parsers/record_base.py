"""Shared archived-record delivery for JSON, XML, and HTML record sources."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Literal, cast
from urllib.parse import urljoin

import lxml.etree as _etree  # type: ignore[import-untyped]

from wlh.cleaners.text_cleaner import apply_text_cleaners, clean_title, normalized
from wlh.core import WlhError, digest, guard, public_url
from wlh.processor.extractors.html.dom_tree import parse_html
from wlh.processor.extractors.html.html_to_md import html_to_markdown
from wlh.processor.extractors.word.office import detect_real_format
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


def _budget_documents(config: Mapping[str, Any]) -> int:
    sched = config.get("scheduler")
    if isinstance(sched, Mapping):
        sched_map = cast(Mapping[str, Any], sched)
        if isinstance(sched_map.get("budget"), Mapping):
            return int(cast(Mapping[str, Any], sched_map["budget"]).get("documents", 25))
    budget = config.get("budget")
    if isinstance(budget, Mapping):
        return int(cast(Mapping[str, Any], budget).get("documents", 25))
    return 25


def _budget_response_bytes(config: Mapping[str, Any]) -> int:
    sched = config.get("scheduler")
    if isinstance(sched, Mapping):
        sched_map = cast(Mapping[str, Any], sched)
        if isinstance(sched_map.get("budget"), Mapping):
            return int(
                cast(Mapping[str, Any], sched_map["budget"]).get("response_bytes", 15000000)
            )
    budget = config.get("budget")
    if isinstance(budget, Mapping):
        return int(cast(Mapping[str, Any], budget).get("response_bytes", 15000000))
    return 15000000


def _quality_routes(config: Mapping[str, Any]) -> Sequence[str]:
    cleaners = config.get("cleaners")
    if isinstance(cleaners, Mapping):
        clean_map = cast(Mapping[str, Any], cleaners)
        if isinstance(clean_map.get("quality_accept_routes"), Sequence):
            return [str(x) for x in cast(Sequence[Any], clean_map["quality_accept_routes"])]
    parser_cfg = config.get("parser")
    if isinstance(parser_cfg, Mapping):
        parser_map = cast(Mapping[str, Any], parser_cfg)
        if isinstance(parser_map.get("quality_accept_routes"), Sequence):
            return [str(x) for x in cast(Sequence[Any], parser_map["quality_accept_routes"])]
    return ("accept", "accept_ocr", "accept_with_warning")


def _metadata_cfg(config: Mapping[str, Any]) -> Mapping[str, Any]:
    storage = config.get("storage")
    if isinstance(storage, Mapping):
        storage_map = cast(Mapping[str, Any], storage)
        if isinstance(storage_map.get("metadata"), Mapping):
            return cast(Mapping[str, Any], storage_map["metadata"])
    meta = config.get("metadata")
    return cast(Mapping[str, Any], meta) if isinstance(meta, Mapping) else {}


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
            _budget_documents(self.config),
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
        ns: str = str(record.get("namespace") or target["identity"].get("namespace") or self.NAMESPACE)
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
        if (
            isinstance(req_att_srcs, list)
            and req_att_srcs
            and not record.get("attachment_zip_b64")
        ):
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
        cleaners_cfg = self.config.get("cleaners")
        cleaners_map: Mapping[str, Any] = (
            cast(Mapping[str, Any], cleaners_cfg) if isinstance(cleaners_cfg, Mapping) else {}
        )
        raw_strip = cleaners_map.get("strip_text_regexes") or self.config.get(
            "strip_text_regexes"
        )
        strip_pats: Sequence[str] = (
            [str(x) for x in cast(Sequence[Any], raw_strip) if x]
            if isinstance(raw_strip, Sequence) and not isinstance(raw_strip, (str, bytes))
            else ()
        )
        raw_title_strip = cleaners_map.get("title_strip_regexes") or self.config.get(
            "title_strip_regexes"
        )
        title_pats: Sequence[str] = (
            [str(x) for x in cast(Sequence[Any], raw_title_strip) if x]
            if isinstance(raw_title_strip, Sequence)
            and not isinstance(raw_title_strip, (str, bytes))
            else ()
        )
        for idx, item in enumerate(regular_inputs):
            acq: Mapping[str, Any] = item["acquisition"]
            data: bytes = ctx.read(acq)
            kind = detect_real_format(data)
            if kind == "html":
                markdown = html_to_markdown(data, acq["actual_url"], quirks=tuple(self.quirks))
                if strip_pats:
                    markdown = apply_text_cleaners(markdown, strip_pats)
                result: dict[str, Any] = {
                    "quality": ctx.text_quality(markdown),
                    "info": {"kind": kind},
                    "parsers": ["markdown", "quality"],
                }
            else:
                result = ctx.extract(data, kind)
                markdown = str(result["markdown"])
                if strip_pats:
                    markdown = apply_text_cleaners(markdown, strip_pats)
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
                html_to_markdown(record["html"], url, quirks=tuple(self.quirks))
                if record.get("html")
                else normalized(record.get("text") or "")
            )
            if strip_pats:
                markdown = apply_text_cleaners(markdown, strip_pats)
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
        accept_routes = _quality_routes(self.config)
        for unit_obj in units:
            if unit_obj.quality["recommended_route"] not in accept_routes:
                raise WlhError("quality_check_failed:" + unit_obj.quality["recommended_route"])
        meta_cfg = _metadata_cfg(self.config)
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
        fragment: str = etree.tostring(
            body[0] if body else node, encoding="unicode", method="html"
        )
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
    "_budget_documents",
    "_budget_response_bytes",
    "_metadata_cfg",
    "_normalize_attachment_src",
    "_quality_routes",
    "day",
    "first",
    "html_records",
    "items",
    "payload",
    "scalar",
    "verify_attachment_zip",
]
