"""wlh.inputs.v1 / wlh.document.v1 contracts, four fingerprints and the 16-column rows.

Adapters return plain candidates; only this boundary turns them into immutable CAS
objects (Markdown bytes, input manifest, unified document) and delivery rows.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any, cast

from wlh.config import DOCUMENT_TYPES
from wlh.core import WlhError, canonical, digest
from wlh.extraction.manifest import effective, fingerprint
from wlh.extraction.markdown import normalized

DOCUMENT_SCHEMA = "wlh.document.v1"
INPUTS_SCHEMA = "wlh.inputs.v1"
SOURCE_RECORD_ALGORITHM = "wlh.source-record.v1"
INPUT_ROLES = {
    "primary",
    "volume",
    "independent_attachment",
    "source_response",
    "derived",
    "context",
}
DELIVERABLE_ROLES = ("primary", "volume", "independent_attachment")
EXTENSIONS = {
    "pdf": ("pdf", "application/pdf"),
    "doc": ("doc", "application/msword"),
    "docx": (
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "rtf": ("rtf", "application/rtf"),
    "xls": ("xls", "application/vnd.ms-excel"),
    "xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "zip": ("zip", "application/zip"),
    "html": ("html", "text/html"),
    "txt": ("txt", "text/plain"),
    "json": ("json", "application/json"),
    "xml": ("xml", "application/xml"),
}
METADATA_FIELDS = (
    "title",
    "document_type",
    "document_number",
    "issuer",
    "status",
    "source_name",
)
DATE_FIELDS = ("published_at", "effective_at", "expired_at")
MAX_TEXT = 4000


def source_record_hash(source_id: str, namespace: str, external_key: str) -> str:
    return digest(
        {
            "algorithm": SOURCE_RECORD_ALGORITHM,
            "source_id": source_id,
            "namespace": namespace,
            "external_key": external_key,
        }
    )


def safe_name(title: str) -> str:
    value = unicodedata.normalize("NFC", title)
    value = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]+', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    while len(value.encode("utf-8")) > 150:
        value = value[:-1]
    return value.rstrip(" ._") or "document"


def file_names(
    title: str,
    record_hash: str,
    markdown_hash: str,
    original_hash: str | None,
    extension: str | None,
    volume_order: int | None,
) -> tuple[str, str | None]:
    suffix = "_%02d" % volume_order if volume_order else ""
    base = safe_name(title)
    markdown_file = f"{base}_{record_hash[:8]}_{markdown_hash[:8]}{suffix}.md"
    original_file: str | None = (
        f"{base}_{record_hash[:8]}_{original_hash[:8]}{suffix}.{extension}"
        if original_hash and extension
        else None
    )
    return markdown_file, original_file


def text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WlhError("invalid_document_" + field)
    # Preserve ideographic spaces in source metadata.
    value = unicodedata.normalize("NFC", re.sub(r"[^\S\u3000]+", " ", value)).strip()
    if len(value) > MAX_TEXT:
        raise WlhError("invalid_document_" + field)
    return value or None


def iso_date(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise WlhError("invalid_document_" + field)
    try:
        date.fromisoformat(value)
    except ValueError:
        raise WlhError("invalid_document_" + field) from None
    return value


def timestamp(value: str | datetime) -> str:
    """采集时间: UTC, YYYY-MM-DD HH:MM:SS."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def sha(value: Any, code: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise WlhError(code)
    return value


def normalize_metadata(candidate: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str | None]]:
    meta: Mapping[str, Any] = candidate.get("metadata") or {}
    if set(meta) - set(METADATA_FIELDS):
        raise WlhError("invalid_document_metadata")
    metadata = {k: text(meta.get(k), k) for k in METADATA_FIELDS}
    if not metadata["title"]:
        raise WlhError("document_title_missing")
    if metadata["document_type"] not in DOCUMENT_TYPES:
        raise WlhError("invalid_document_type")
    dates: Mapping[str, Any] = candidate.get("dates") or {}
    if set(dates) - set(DATE_FIELDS):
        raise WlhError("invalid_document_dates")
    return metadata, {k: iso_date(dates.get(k), k) for k in DATE_FIELDS}


def normalize_inputs(
    candidate: Mapping[str, Any], acquisitions: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    inputs: list[dict[str, Any]] = []
    for item in cast(list[dict[str, Any]], candidate.get("inputs") or []):
        if item.get("role") not in INPUT_ROLES:
            raise WlhError("invalid_input_role")
        acquisition = acquisitions.get(cast(Any, item.get("acquisition_id")))
        if acquisition is None or acquisition["state"] != "archived":
            raise WlhError("input_acquisition_not_archived")
        if item.get("archive_ref") != acquisition["archive_ref"] and item.get("role") != "derived":
            raise WlhError("input_archive_mismatch")
        locator = item.get("locator")
        if not isinstance(locator, dict) or not locator:
            raise WlhError("input_locator_required")
        inputs.append(
            {
                "acquisition_id": item["acquisition_id"],
                "archive_ref": sha(item["archive_ref"], "invalid_input_archive_ref"),
                "role": item["role"],
                "locator": locator,
                "semantic_locator": item.get("semantic_locator") or locator,
                "slice_hash": sha(item.get("slice_hash") or item["archive_ref"], "invalid_slice"),
                "charset": item.get("charset"),
                "base_url": item.get("base_url"),
                "volume_order": item.get("volume_order"),
                "context": item.get("context") or {},
                "acquired_at": timestamp(acquisition["acquired_at"]),
            }
        )
    if not inputs:
        raise WlhError("document_inputs_required")
    return inputs


def check_volumes(deliverables: Sequence[Mapping[str, Any]]) -> None:
    volumes = [d for d in deliverables if d["role"] == "volume"]
    if any(type(d.get("volume_order")) is not int for d in volumes):
        raise WlhError("volume_order_ambiguous")
    orders = sorted(d["volume_order"] for d in volumes)
    if orders != list(range(1, len(orders) + 1)):
        raise WlhError("volume_order_ambiguous")
    if volumes and any(d["role"] == "primary" for d in deliverables):
        raise WlhError("volume_and_primary_conflict")
    if sum(d["role"] == "primary" for d in deliverables) > 1:
        raise WlhError("multiple_primary_deliverables")


def build(
    objects: Any, *, source_id: str, run_id: str, manifest: Mapping[str, Any] | None,
    candidate: Mapping[str, Any], acquisitions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate a candidate, write Markdown/manifest/document to CAS, return rows."""
    namespace = text(candidate.get("namespace"), "namespace")
    external_key = candidate.get("external_key")
    if not namespace or not isinstance(external_key, str) or not external_key.strip():
        raise WlhError("document_identity_required")
    metadata, dates = normalize_metadata(candidate)
    inputs = normalize_inputs(candidate, acquisitions)
    record = source_record_hash(source_id, namespace, external_key)
    raw: list[dict[str, Any]] = candidate.get("deliverables") or []
    if not raw:
        raise WlhError("document_deliverables_required")
    for item in raw:
        if item.get("role") not in DELIVERABLE_ROLES:
            raise WlhError("invalid_deliverable_role")
        if item["role"] != "volume" and item.get("volume_order") is not None:
            raise WlhError("volume_order_without_volume")
    check_volumes(raw)
    ordered: list[dict[str, Any]] = sorted(
        raw,
        key=lambda d: (
            DELIVERABLE_ROLES.index(d["role"]),
            d.get("volume_order") or 0,
            text(d.get("title"), "title") or "",
            cast(dict[str, Any], d.get("original") or {}).get("ref") or "",
            normalized(d.get("markdown") or ""),
        ),
    )
    deliverables: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, item in enumerate(ordered, 1):
        body = normalized(item.get("markdown") or "")
        if not body.strip():
            raise WlhError("deliverable_markdown_empty")
        data = body.encode("utf-8")
        markdown_ref = objects.put(data)
        objects.get(markdown_ref)
        original = item.get("original")
        if original is not None:
            sha(original.get("ref"), "invalid_original_ref")
            if original.get("kind") not in EXTENSIONS:
                raise WlhError("invalid_original_kind")
        position = item.get("input")
        source = inputs[position] if type(position) is int and 0 <= position < len(inputs) else None
        if source is None:
            raise WlhError("deliverable_input_required")
        if original and original["ref"] != source["archive_ref"]:
            raise WlhError("deliverable_original_input_mismatch")
        title = text(item.get("title"), "title") or metadata["title"]
        if item["role"] == "independent_attachment" and not text(item.get("title"), "title"):
            raise WlhError("attachment_title_missing")
        original_hash = original["ref"] if original else None
        extension, media_type = EXTENSIONS[original["kind"]] if original else (None, None)
        markdown_file, original_file = file_names(
            title, record, markdown_ref, original_hash, extension, item.get("volume_order")
        )
        if markdown_file in names:
            raise WlhError("duplicate_deliverable_file")
        names.add(markdown_file)
        shared = item["role"] != "independent_attachment"
        overrides: Mapping[str, Any] = item.get("metadata") or {}
        row_meta: dict[str, str | None] = {
            k: text(overrides.get(k), k) if k in overrides else (metadata[k] if shared else None)
            for k in ("document_number", "issuer", "status")
        }
        row_dates: dict[str, str | None] = {
            k: iso_date(overrides.get(k), k) if k in overrides else (dates[k] if shared else None)
            for k in DATE_FIELDS
        }
        deliverable: dict[str, Any] = {
            "index": index,
            "role": item["role"],
            "title": title,
            "volume_order": item.get("volume_order"),
            "markdown_ref": markdown_ref,
            "original_file_ref": original_hash,
            "file_name": original_file,
            "markdown_file_name": markdown_file,
            "media_type": media_type,
            "extension": extension,
            "quality_report": item.get("quality") or {},
            "extraction": item.get("extraction") or {},
            "locator": source["locator"],
            "acquisition_id": source["acquisition_id"],
            "metadata": {**row_meta, **row_dates},
        }
        deliverables.append(deliverable)
        if original:
            files.append(
                {
                    "ref": original["ref"],
                    "role": item["role"],
                    "original_name": original.get("original_name"),
                    "media_type": media_type,
                    "byte_size": original.get("byte_size"),
                    "source_url": original.get("source_url"),
                    "volume_order": item.get("volume_order"),
                    "locator": source["locator"],
                }
            )
        rows.append(
            {
                "row_index": index,
                "title": title,
                "markdown_file": markdown_file,
                "doc_type": metadata["document_type"],
                "acquired_at": source["acquired_at"],
                "original_file": original_file,
                "volume_order": item.get("volume_order"),
                "doc_number": row_meta["document_number"],
                "issuer": row_meta["issuer"],
                "published_at": row_dates["published_at"],
                "effective_at": row_dates["effective_at"],
                "expired_at": row_dates["expired_at"],
                "status": row_meta["status"],
                "source_name": metadata["source_name"],
                "detail_url": candidate.get("public_url"),
                "source_record_hash": record,
                "markdown_hash": markdown_ref,
                "original_hash": original_hash,
                "original_media_type": media_type,
                "role": item["role"],
                "locator": source["locator"],
                "extensions": candidate.get("extensions") or {},
            }
        )
    for item in inputs:
        objects.get(item["archive_ref"])
    used = set(candidate.get("parsers") or [])
    for item in ordered:
        used.update(item.get("parsers") or [])
    parser_config = effective(manifest, used)
    if "ocr" in parser_config:
        engines: list[str] = sorted(
            {
                cast(dict[str, Any], d.get("extraction", {}).get("ocr") or {}).get("engine", "unknown")
                for d in deliverables
                if (d.get("extraction", {}).get("ocr"))
            }
        )
        parser_config["ocr"] = {**parser_config["ocr"], "effective_engines": engines}
    semantic_inputs: list[dict[str, Any]] = sorted(
        (
            {
                k: item[k]
                for k in (
                    "role",
                    "semantic_locator",
                    "slice_hash",
                    "charset",
                    "base_url",
                    "volume_order",
                    "context",
                )
            }
            for item in inputs
        ),
        key=canonical,
    )
    input_fp: str = fingerprint(
        "input",
        {"namespace": namespace, "external_key": external_key, "inputs": semantic_inputs},
    )
    parser_fp = fingerprint("parser", parser_config)
    version_fp = fingerprint("version", {"input": input_fp, "parser": parser_fp})
    business: dict[str, Any] = {
        "metadata": metadata,
        "dates": dates,
        "deliverables": [
            {
                k: d[k]
                for k in (
                    "role",
                    "title",
                    "volume_order",
                    "markdown_ref",
                    "original_file_ref",
                    "metadata",
                )
            }
            for d in deliverables
        ],
        "extensions": candidate.get("extensions") or {},
    }
    content_hash = fingerprint("content", business)
    manifest_doc: dict[str, Any] = {
        "schema": INPUTS_SCHEMA,
        "source_id": source_id,
        "run_id": run_id,
        "document": {"namespace": namespace, "external_key": external_key},
        "inputs": inputs,
        "adapter_inputs": candidate.get("adapter_inputs") or [],
        "input_reuse": candidate.get("input_reuse"),
    }
    inputs_ref = objects.put_json(manifest_doc)
    acquired = max(item["acquired_at"] for item in inputs)
    document: dict[str, Any] = {
        "schema": DOCUMENT_SCHEMA,
        "identity": {
            "source_id": source_id,
            "namespace": namespace,
            "external_key": external_key,
            "public_url": candidate.get("public_url"),
            "source_record_hash": record,
        },
        "metadata": metadata,
        "dates": {**dates, "acquired_at": acquired},
        "content": {
            "markdown": normalized(ordered[0]["markdown"]),
            "markdown_hash": deliverables[0]["markdown_ref"],
        },
        "deliverables": deliverables,
        "extraction": {
            "adapter": parser_config["adapter"],
            "markdown_contract": parser_config["markdown"]["contract"],
            "parsers": sorted(used),
            "deliverables": [
                {"index": d["index"], **d["extraction"], "quality_report": d["quality_report"]}
                for d in deliverables
            ],
        },
        "files": files,
        "lineage": {"inputs_ref": inputs_ref},
        "extensions": business["extensions"],
    }
    result_ref = objects.put_json(document)
    objects.get(result_ref)
    objects.get(inputs_ref)
    return {
        "namespace": namespace,
        "external_key": external_key,
        "public_url": candidate.get("public_url"),
        "recheck_locator": candidate.get("recheck_locator") or {"url": candidate.get("public_url")},
        "result_ref": result_ref,
        "inputs_ref": inputs_ref,
        "input_fingerprint": input_fp,
        "parser_fingerprint": parser_fp,
        "version_fingerprint": version_fp,
        "content_hash": content_hash,
        "parser_config": parser_config,
        "rows": rows,
        "acquisition_ids": sorted({item["acquisition_id"] for item in inputs}),
    }


def verify_document(
    objects: Any, result_ref: str, *, max_bytes: int | None = None,
    deadline: float | None = None, sizes: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Read-side validator: unified document plus its complete object closure."""
    try:
        import json

        value = json.loads(objects.get(result_ref, max_bytes=max_bytes, deadline=deadline))
        if not isinstance(value, dict) or cast(dict[str, Any], value).get("schema") != DOCUMENT_SCHEMA:
            raise ValueError
        deliverables = cast(dict[str, Any], value).get("deliverables")
        if not isinstance(deliverables, list) or not deliverables:
            raise ValueError
        for item in cast(list[Any], deliverables):
            if not isinstance(item, dict):
                raise ValueError
            md_sha = sha(cast(dict[str, Any], item).get("markdown_ref"), "document_invalid")
            md_bytes = objects.get(md_sha, deadline=deadline)
            if sizes is not None:
                sizes[md_sha] = len(md_bytes)
            if cast(dict[str, Any], item).get("original_file_ref"):
                orig_sha = sha(item["original_file_ref"], "document_invalid")
                orig_bytes = objects.get(orig_sha, deadline=deadline)
                if sizes is not None:
                    sizes[orig_sha] = len(orig_bytes)
        lineage: Any = cast(dict[str, Any], value).get("lineage") or {}
        if not isinstance(lineage, dict):
            raise ValueError
        manifest_raw: bytes = objects.get(
            sha(cast(dict[str, Any], lineage).get("inputs_ref"), "document_invalid"), deadline=deadline
        )
        manifest = json.loads(manifest_raw)
        if not isinstance(manifest, dict) or cast(dict[str, Any], manifest).get("schema") != INPUTS_SCHEMA:
            raise ValueError
        inputs = cast(dict[str, Any], manifest)["inputs"]
        if not isinstance(inputs, list) or not inputs:
            raise ValueError
        for item in cast(list[Any], inputs):
            if not isinstance(item, dict):
                raise ValueError
            objects.get(sha(cast(dict[str, Any], item).get("archive_ref"), "document_invalid"), deadline=deadline)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise WlhError("document_invalid") from None
    return cast(dict[str, Any], value)
