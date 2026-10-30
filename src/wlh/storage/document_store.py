"""wlh.inputs.v1 / wlh.document.v1 contracts, four fingerprints, 16-column rows, and transactional Resource/Observation/Version timelines."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any, cast

from wlh.config import DOCUMENT_TYPES
from wlh.core import WlhError, canonical, digest, effective, fingerprint, normalized, uid
from wlh.storage.db import DbConn, connect, fenced, j, locked_run, one

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
FINGERPRINTS = ("input_fingerprint", "parser_fingerprint", "content_hash")


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


def resource(
    c: DbConn,
    frontier_id: str,
    namespace: str,
    external_key: str,
    public_url: str | None,
    recheck_locator: Mapping[str, Any],
) -> dict[str, Any]:
    if not namespace or not external_key:
        raise WlhError("document_identity_required")
    c.execute(
        """INSERT INTO wlh.resource(id,frontier_id,identity_namespace,external_key,public_url,
        recheck_locator) VALUES(%s,%s,%s,%s,%s,%s)
        ON CONFLICT(frontier_id,identity_namespace,external_key) DO NOTHING""",
        (uid(), frontier_id, namespace, external_key, public_url, j(recheck_locator)),
    )
    row: dict[str, Any] = one(
        c,
        """SELECT * FROM wlh.resource WHERE frontier_id=%s AND identity_namespace=%s
        AND external_key=%s FOR UPDATE""",
        (frontier_id, namespace, external_key),
    )
    if row["public_url"] != public_url or row["recheck_locator"] != recheck_locator:
        c.execute(
            "UPDATE wlh.resource SET public_url=%s,recheck_locator=%s WHERE id=%s",
            (public_url, j(recheck_locator), row["id"]),
        )
    return row


def check_acquisitions(
    c: DbConn, source: Mapping[str, Any], run: Mapping[str, Any], ids: Sequence[str]
) -> None:
    rows: list[dict[str, Any]] = c.execute(
        "SELECT id,source_id,run_id,state FROM wlh.acquisition WHERE id=ANY(%s)", (ids,)
    ).fetchall()
    if len(rows) != len(set(ids)):
        raise WlhError("input_acquisition_missing")
    for row in rows:
        if row["source_id"] != source["id"] or row["state"] != "archived":
            raise WlhError("input_acquisition_not_owned")
        if run["mode"] != "reparse" and row["run_id"] != run["id"]:
            raise WlhError("input_acquisition_not_owned")


def finish_target(
    c: DbConn,
    target_id: str | None,
    state: str,
    observation_id: str | None = None,
    error: str | None = None,
    epoch: int | None = None,
) -> None:
    if target_id:
        c.execute(
            """UPDATE wlh.task SET state=%s,observation_id=%s,error=%s,attempt_epoch=%s,
            updated_at=clock_timestamp() WHERE id=%s AND kind='target'""",
            (state, observation_id, error, epoch, target_id),
        )


def evidence(c: DbConn, observation_id: str, ids: Sequence[str], relation: str) -> None:
    for acquisition_id in sorted(set(ids)):
        c.execute(
            """INSERT INTO wlh.observation_evidence(observation_id,acquisition_id,relation)
            VALUES(%s,%s,%s) ON CONFLICT DO NOTHING""",
            (observation_id, acquisition_id, relation),
        )


def conflict(
    c: DbConn,
    run_id: str,
    epoch: int,
    resource_id: str,
    reason: str,
    existing: str | None,
    attempted: Mapping[str, Any],
) -> None:
    c.execute(
        """INSERT INTO wlh.delivery_conflict(id,run_id,resource_id,attempt_epoch,reason,
        existing_observation_id,attempted) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
        (uid(), run_id, resource_id, epoch, reason, existing, j(attempted)),
    )
    c.execute("UPDATE wlh.run SET errors=errors || %s WHERE id=%s", (j([reason]), run_id))


def schedule(
    c: DbConn, run: Mapping[str, Any], resource_id: str, outcome: str, success: bool
) -> None:
    cfg_raw = run.get("config")
    cfg: Mapping[str, Any] = cast(Mapping[str, Any], cfg_raw) if isinstance(cfg_raw, Mapping) else {}
    sched_raw = cfg.get("scheduler")
    sched: Mapping[str, Any] = cast(Mapping[str, Any], sched_raw) if isinstance(sched_raw, Mapping) else {}
    recheck_sec = int(sched.get("recheck_seconds") or cfg.get("recheck_seconds") or 3600)
    interval: int = recheck_sec if success else min(60, recheck_sec)
    c.execute(
        """UPDATE wlh.resource SET last_checked_at=now(),last_outcome=%s,
        next_check=now()+(%s * interval '1 second') WHERE id=%s""",
        (outcome, interval, resource_id),
    )


def commit(
    run_id: str,
    epoch: int,
    built: Mapping[str, Any],
    *,
    target_id: str | None = None,
    check_active: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """One run/resource delivery commits once; replay checks input, parser and content."""
    with connect() as c:
        source, run = locked_run(c, run_id)
        fenced(source, run, epoch)
        frontier = one(c, "SELECT * FROM wlh.frontier WHERE source_id=%s", (source["id"],))
        check_acquisitions(c, source, run, built["acquisition_ids"])
        r: dict[str, Any] = resource(
            c,
            frontier["id"],
            built["namespace"],
            built["external_key"],
            built["public_url"],
            built["recheck_locator"],
        )
        attempted = {k: built[k] for k in (*FINGERPRINTS, "version_fingerprint", "result_ref")}
        key = run_id + ":" + r["id"]
        existing: dict[str, Any] | None = c.execute(
            "SELECT * FROM wlh.observation WHERE delivery_key=%s", (key,)
        ).fetchone()
        if existing:
            if existing["outcome"] != "failed" and all(
                existing[k] == built[k] for k in FINGERPRINTS
            ):
                evidence(c, existing["id"], built["acquisition_ids"], "replay")
                finish_target(c, target_id, "done", existing["id"], None, epoch)
                if check_active is not None:
                    check_active()
                return {
                    "status": "replayed",
                    "observation_id": existing["id"],
                    "resource_id": r["id"],
                }
            conflict(c, run_id, epoch, r["id"], "delivery_key_conflict", existing["id"], attempted)
            finish_target(c, target_id, "failed", existing["id"], "delivery_key_conflict", epoch)
            return {"status": "conflict", "observation_id": existing["id"], "resource_id": r["id"]}
        current: dict[str, Any] | None = c.execute(
            "SELECT * FROM wlh.version WHERE id=%s", (r["current_version_id"],)
        ).fetchone()
        oid = uid()
        if current and current["version_fingerprint"] == built["version_fingerprint"]:
            if current["content_hash"] != built["content_hash"]:
                c.execute(
                    """INSERT INTO wlh.observation(id,resource_id,run_id,attempt_epoch,
                    delivery_key,outcome,change_reason,error,inputs_ref,input_fingerprint,
                    parser_fingerprint,version_fingerprint,content_hash)
                    VALUES(%s,%s,%s,%s,%s,'failed','nondeterministic','nondeterministic_parser_output',
                    %s,%s,%s,%s,%s)""",
                    (
                        oid,
                        r["id"],
                        run_id,
                        epoch,
                        key,
                        built["inputs_ref"],
                        built["input_fingerprint"],
                        built["parser_fingerprint"],
                        built["version_fingerprint"],
                        built["content_hash"],
                    ),
                )
                evidence(c, oid, built["acquisition_ids"], "input")
                conflict(
                    c, run_id, epoch, r["id"], "nondeterministic_parser_output", None, attempted
                )
                schedule(c, run, r["id"], "failed", False)
                finish_target(c, target_id, "failed", oid, "nondeterministic_parser_output", epoch)
                return {"status": "conflict", "observation_id": oid, "resource_id": r["id"]}
            outcome, reason, version_id = "unchanged", "unchanged", current["id"]
        else:
            if current is None:
                outcome, reason = "created", "first_success"
            else:
                outcome = "changed"
                source_changed = current["input_fingerprint"] != built["input_fingerprint"]
                parser_changed = current["parser_fingerprint"] != built["parser_fingerprint"]
                reason = (
                    "both_changed"
                    if source_changed and parser_changed
                    else "source_changed"
                    if source_changed
                    else "parser_recomputed"
                    if run["mode"] == "reparse"
                    else "parser_changed"
                )
            version_id = uid()
        c.execute(
            """INSERT INTO wlh.observation(id,resource_id,run_id,attempt_epoch,delivery_key,
            outcome,change_reason,inputs_ref,result_ref,input_fingerprint,parser_fingerprint,
            version_fingerprint,content_hash,version_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                oid,
                r["id"],
                run_id,
                epoch,
                key,
                outcome,
                reason,
                built["inputs_ref"],
                built["result_ref"],
                built["input_fingerprint"],
                built["parser_fingerprint"],
                built["version_fingerprint"],
                built["content_hash"],
                version_id,
            ),
        )
        if outcome != "unchanged":
            seq_row = one(
                c,
                "SELECT COALESCE(max(sequence), 0) + 1 AS seq FROM wlh.version WHERE resource_id=%s",
                (r["id"],),
            )
            next_seq = int(seq_row["seq"]) if seq_row else 1
            c.execute(
                """INSERT INTO wlh.version(id,resource_id,sequence,observation_id,result_ref,
                inputs_ref,input_fingerprint,parser_fingerprint,version_fingerprint,content_hash,
                parser_config,change_reason) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    version_id,
                    r["id"],
                    next_seq,
                    oid,
                    built["result_ref"],
                    built["inputs_ref"],
                    built["input_fingerprint"],
                    built["parser_fingerprint"],
                    built["version_fingerprint"],
                    built["content_hash"],
                    j(built["parser_config"]),
                    reason,
                ),
            )
            for row in built["rows"]:
                c.execute(
                    """INSERT INTO wlh.document_row(version_id,row_index,title,markdown_file,
                    doc_type,acquired_at,original_file,volume_order,
                    doc_number,issuer,published_at,effective_at,expired_at,status,
                    source_name,detail_url,source_record_hash,markdown_hash,original_hash,
                    original_media_type,role,locator,extensions) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        version_id,
                        row["row_index"],
                        row["title"],
                        row["markdown_file"],
                        row["doc_type"],
                        row["acquired_at"] + "+00:00",
                        row["original_file"],
                        row["volume_order"],
                        row["doc_number"],
                        row["issuer"],
                        row["published_at"],
                        row["effective_at"],
                        row["expired_at"],
                        row["status"],
                        row["source_name"],
                        row["detail_url"],
                        row["source_record_hash"],
                        row["markdown_hash"],
                        row["original_hash"],
                        row["original_media_type"],
                        row["role"],
                        j(row["locator"]),
                        j(row.get("extensions") or {}),
                    ),
                )
            c.execute(
                "UPDATE wlh.resource SET current_version_id=%s WHERE id=%s", (version_id, r["id"])
            )
        evidence(c, oid, built["acquisition_ids"], "input")
        schedule(c, run, r["id"], outcome, True)
        finish_target(c, target_id, "done", oid, None, epoch)
        if check_active is not None:
            check_active()
        return {
            "status": outcome,
            "observation_id": oid,
            "resource_id": r["id"],
            "version_id": version_id,
        }


def fail(
    run_id: str,
    epoch: int,
    identity: Mapping[str, Any] | None,
    error: str,
    *,
    acquisition_ids: Sequence[str] = (),
    target_id: str | None = None,
) -> str | None:
    """Record a failed delivery; never overwrites an existing success or current Version."""
    with connect() as c:
        source, run = locked_run(c, run_id)
        fenced(source, run, epoch)
        observation_id = None
        if identity and identity.get("namespace") and identity.get("external_key"):
            frontier = one(c, "SELECT * FROM wlh.frontier WHERE source_id=%s", (source["id"],))
            r: dict[str, Any] = resource(
                c,
                frontier["id"],
                identity["namespace"],
                identity["external_key"],
                identity.get("public_url"),
                identity.get("recheck_locator") or {"url": identity.get("public_url")},
            )
            key = run_id + ":" + r["id"]
            existing: dict[str, Any] | None = c.execute(
                "SELECT id FROM wlh.observation WHERE delivery_key=%s", (key,)
            ).fetchone()
            if existing:
                observation_id = existing["id"]
            else:
                observation_id = uid()
                c.execute(
                    """INSERT INTO wlh.observation(id,resource_id,run_id,attempt_epoch,
                    delivery_key,outcome,change_reason,error) VALUES(%s,%s,%s,%s,%s,'failed',
                    'failed',%s)""",
                    (observation_id, r["id"], run_id, epoch, key, error),
                )
                ids: list[str] = [
                    row["id"]
                    for row in c.execute(
                        "SELECT id FROM wlh.acquisition WHERE id=ANY(%s) AND source_id=%s",
                        (list(acquisition_ids), source["id"]),
                    )
                ]
                evidence(c, observation_id, ids, "input")
                schedule(c, run, r["id"], "failed", False)
        c.execute("UPDATE wlh.run SET errors=errors || %s WHERE id=%s", (j([error]), run_id))
        finish_target(c, target_id, "failed", observation_id, error, epoch)
        return observation_id


def save_checkpoint(run_id: str, epoch: int, checkpoint: Mapping[str, Any]) -> None:
    with connect() as c:
        source, run = locked_run(c, run_id)
        fenced(source, run, epoch)
        c.execute(
            "UPDATE wlh.frontier SET checkpoint=%s,updated_at=now() WHERE source_id=%s AND run_id=%s",
            (j(checkpoint), source["id"], run_id),
        )


def load_source_delivery_rows(source_id: str) -> tuple[Mapping[str, Any] | None, list[dict[str, Any]]]:
    """Load current delivered rows and source config for a source_id from PostgreSQL."""
    with connect() as c:
        source_row = c.execute(
            "SELECT config FROM wlh.source WHERE id = %s", (source_id,)
        ).fetchone()
        source_cfg: Mapping[str, Any] | None = source_row["config"] if source_row else None
        rows: list[dict[str, Any]] = c.execute(
            """
            SELECT f.source_id, r.id AS resource_id, d.*
            FROM wlh.document_row d
            JOIN wlh.version v ON v.id = d.version_id
            JOIN wlh.resource r ON r.id = v.resource_id AND r.current_version_id = v.id
            JOIN wlh.frontier f ON f.id = r.frontier_id
            WHERE f.source_id = %s
            ORDER BY d.row_index, r.id
            """,
            (source_id,),
        ).fetchall()
    return source_cfg, rows
