"""Rebuild source-faithful tasks and freeze reviewed benchmark membership."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml

from wlh.benchmark.annotator import read_rows, write_json
from wlh.benchmark.models import ProcessorBenchmarkTask
from wlh.benchmark.quality import (
    POLLUTION,
    markdown_text,
    normalize,
    validate_ground_truth_integrity,
    visible_text,
)

etree: Any = importlib.import_module("lxml.etree")

TASK_FIELDS = (
    "id",
    "task_type",
    "source_id",
    "jurisdiction",
    "request_url",
    "mime_type",
    "payload_sha256",
    "raw_payload",
)


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def truth_hash(value: Any) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True))


def unique_rows(path: Path) -> list[dict[str, Any]]:
    rows = read_rows(path)
    ids = [r.get("id") for r in rows]
    if (
        not rows
        or any(not isinstance(i, str) or not i.strip() for i in ids)
        or len(ids) != len(set(ids))
    ):
        raise ValueError("empty_or_duplicate_dataset_identity")
    return rows


def validate_payload(raw: str, mime: str) -> None:
    if not raw.strip() or "\ufffd" in raw:
        raise ValueError("empty_or_lossy_payload")
    if POLLUTION.search(raw):
        raise ValueError("modified_replay_fixture")
    if "…(truncated)" in raw or "...(truncated)" in raw:
        raise ValueError("truncated_payload")
    if mime == "application/json":
        json.loads(raw)
    elif mime == "application/xml":
        etree.fromstring(raw.encode(), etree.XMLParser(resolve_entities=False, no_network=True))
    elif mime == "text/html":
        if not raw.lstrip().startswith("<") or not __import__("re").search(
            r"<[a-zA-Z][\w:-]*(?:\s|>)", raw
        ):
            raise ValueError("not_original_html")
    else:
        raise ValueError("unsupported_original_media")


def rebuild(args: argparse.Namespace) -> dict[str, Any]:
    rows = unique_rows(args.input)
    selected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        try:
            if row.get("recovery_error"):
                raise ValueError(str(row["recovery_error"]))
            provenance: dict[str, Any] = row.get("provenance") or {}
            if provenance.get("kind") != "archived_response" or not provenance.get(
                "acquisition_id"
            ):
                raise ValueError("missing_archive_provenance")
            path = Path(provenance["archive_path"])
            if not path.is_absolute():
                path = args.input.parent / path
            body = path.read_bytes()
            if digest(body) != provenance.get("archive_sha256"):
                raise ValueError("archive_hash_mismatch")
            if provenance.get("byte_size") is not None and len(body) != provenance["byte_size"]:
                raise ValueError("archive_size_mismatch")
            raw = body.decode(provenance.get("charset") or "utf-8", errors="strict")
            validate_payload(raw, row["mime_type"])
            task = dict(row, raw_payload=raw, payload_sha256=digest(raw))
            ProcessorBenchmarkTask.model_validate(task)
            public_provenance = {k: v for k, v in provenance.items() if k != "archive_path"}
            selected.append({**{k: task[k] for k in TASK_FIELDS}, "provenance": public_provenance})
        except (KeyError, OSError, LookupError, ValueError, etree.XMLSyntaxError) as exc:
            reason = (
                str(exc)
                if isinstance(exc, ValueError) and len(str(exc)) < 100
                else type(exc).__name__
            )
            rejected.append(
                {"id": row["id"], "source_id": row.get("source_id"), "reasons": [reason]}
            )
    report = {
        "command": "rebuild",
        "status": "prepared_with_exclusions" if rejected else "prepared",
        "total": len(rows),
        "selected": len(selected),
        "rejected": len(rejected),
        "coverage": {
            k: dict(Counter(r[k] for r in selected))
            for k in ("source_id", "jurisdiction", "task_type")
        },
        "reasons": dict(Counter(reason for r in rejected for reason in r["reasons"])),
    }
    write_json(args.output_dir / "selected.jsonl", selected, jsonl=True)
    write_json(args.output_dir / "rejected.jsonl", rejected, jsonl=True)
    write_json(args.output_dir / "report.json", report)
    if not selected:
        raise ValueError("no_verified_archived_inputs")
    return report


def canonical_url(value: str) -> str:
    url = urlsplit(value)
    path = url.path.rstrip("/")
    if path.endswith("/data.xml"):
        path = path[:-9]
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(url.query, keep_blank_values=True)
            if not k.lower().startswith("utm_")
        )
    )
    return urlunsplit(("https", url.netloc.lower(), path, query, ""))


def document_groups(rows: list[dict[str, Any]]) -> dict[str, str]:
    parents = {r["id"]: r["id"] for r in rows}

    def root(key: str) -> str:
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    seen: dict[str, str] = {}
    for row in sorted(rows, key=lambda r: r["id"]):
        keys = ["url:" + canonical_url(row["request_url"]), "raw:" + row["payload_sha256"]]
        if row["task_type"] == "content_page":
            text = normalize(visible_text(row["raw_payload"]))
            md = markdown_text("\n".join(r["markdown"] for r in row["ground_truth"]["records"]))
            if len(text) >= 24:
                keys.append("visible:" + digest(text))
            if len(md) >= 24:
                keys.append("body:" + digest(md))
            for rec in row["ground_truth"]["records"]:
                if rec.get("doc_number") and rec.get("title"):
                    keys.append(
                        "document:"
                        + truth_hash(
                            [
                                row["jurisdiction"],
                                rec.get("issuer") or row["source_id"],
                                normalize(rec["doc_number"]),
                                normalize(rec["title"]),
                            ]
                        )
                    )
        for key in keys:
            if key in seen:
                a, b = root(row["id"]), root(seen[key])
                parents[max(a, b)] = min(a, b)
            else:
                seen[key] = row["id"]
    members: dict[str, list[str]] = {}
    for row in rows:
        members.setdefault(root(row["id"]), []).append(row["id"])
    return {tid: "doc-" + truth_hash(sorted(ids))[:20] for ids in members.values() for tid in ids}


def split_manifest(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            k: r[k]
            for k in ("id", "document_group_id", "split", "payload_sha256", "ground_truth_sha256")
        }
        for r in sorted(items, key=lambda row: row["id"])
    ]


def freeze(args: argparse.Namespace) -> dict[str, Any]:
    rows = unique_rows(args.input)
    for row in rows:
        ProcessorBenchmarkTask.model_validate(row)
        source_path = Path("sources") / (row["source_id"] + ".yaml")
        if source_path.is_file():
            source: dict[str, Any] = yaml.safe_load(source_path.read_text())
            jurisdiction = source.get("storage", {}).get("metadata", {}).get("jurisdiction")
            if jurisdiction and row["jurisdiction"] != jurisdiction:
                raise ValueError("source_jurisdiction_mismatch:" + row["id"])
        provenance: dict[str, Any] = row.get("provenance") or {}
        if (
            provenance.get("kind") != "archived_response"
            or not provenance.get("acquisition_id")
            or len(str(provenance.get("archive_sha256", ""))) != 64
        ):
            raise ValueError("missing_archive_provenance:" + row["id"])
        if digest(row["raw_payload"]) != row["payload_sha256"]:
            raise ValueError("payload_hash_mismatch:" + row["id"])
        validate_payload(row["raw_payload"], row["mime_type"])
        review: dict[str, Any] = row.get("review") or {}
        if (
            review.get("status") != "approved"
            or not review.get("reviewer")
            or not review.get("notes")
            or review.get("payload_sha256") != row["payload_sha256"]
            or review.get("ground_truth_sha256") != truth_hash(row["ground_truth"])
        ):
            raise ValueError("missing_or_stale_review:" + row["id"])
        valid, reason = validate_ground_truth_integrity(
            row["ground_truth"],
            row["task_type"],
            row["raw_payload"],
            row["request_url"],
            mime_type=row["mime_type"],
            jurisdiction=row["jurisdiction"],
        )
        if not valid:
            raise ValueError(row["id"] + ":" + reason)
    groups = document_groups(rows)
    # Stable assignment of whole document groups, fixed by the frozen manifest.
    group_ids = sorted(set(groups.values()))
    dev_count = (
        max(1, min(len(group_ids) - 1, round(len(group_ids) * 0.6))) if len(group_ids) > 1 else 1
    )
    dev = set(group_ids[:dev_count])
    golden = [
        {k: r[k] for k in (*TASK_FIELDS, "ground_truth")}
        for r in sorted(rows, key=lambda r: r["id"])
    ]
    golden_text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in golden)
    items = [
        dict(
            r,
            document_group_id=groups[r["id"]],
            split="dev" if groups[r["id"]] in dev else "test",
            ground_truth_sha256=truth_hash(r["ground_truth"]),
        )
        for r in sorted(rows, key=lambda r: r["id"])
    ]
    candidates: list[dict[str, Any]] = (
        unique_rows(args.candidate_ledger)
        if args.candidate_ledger
        else [{"id": r["id"], "source_id": r["source_id"], "reasons": []} for r in items]
    )
    members = {r["id"]: r for r in items}
    candidate_ids = {r["id"] for r in candidates}
    if not set(members) <= candidate_ids:
        raise ValueError("candidate_ledger_missing_golden_members")
    for candidate in candidates:
        member = members.get(candidate["id"])
        if member and member["source_id"] != candidate.get("source_id"):
            raise ValueError("candidate_source_conflict")
        if not candidate.get("source_id") or (not member and not candidate.get("reasons")):
            raise ValueError("candidate_missing_source_or_exclusion_reason")
    coverage: list[dict[str, Any]] = []
    for source in sorted({r["source_id"] for r in candidates}):
        source_candidates = [r for r in candidates if r["source_id"] == source]
        excluded = [r for r in source_candidates if r["id"] not in members]
        coverage.append(
            {
                "source_id": source,
                "candidate_count": len(source_candidates),
                "golden_count": len(source_candidates) - len(excluded),
                "excluded_count": len(excluded),
                "reasons": dict(Counter(reason for r in excluded for reason in r["reasons"])),
            }
        )
    ledger: dict[str, Any] = {
        "schema_version": "3.0",
        "dataset_version": args.version,
        "dataset_sha256": digest(golden_text),
        "split_manifest_sha256": truth_hash(split_manifest(items)),
        "split_policy": "document_groups_v1",
        "items": items,
        "candidates": sorted(candidates, key=lambda r: r["id"]),
        "coverage": coverage,
        "summary": {
            "total_candidates": len(candidates),
            "excluded_count": len(candidates) - len(items),
            "golden_set_exported": len(items),
            "document_groups": len(group_ids),
            "dev_split_count": sum(r["split"] == "dev" for r in items),
            "test_split_count": sum(r["split"] == "test" for r in items),
        },
    }
    out = args.output_dir
    existing = out / "annotation_pipeline.json"
    if existing.exists():
        previous = json.loads(existing.read_text())
        if (
            previous != ledger
            or not (out / "ground_truth_benchmark.jsonl").exists()
            or digest((out / "ground_truth_benchmark.jsonl").read_bytes())
            != ledger["dataset_sha256"]
        ):
            raise ValueError("frozen_dataset_cannot_be_overwritten:use_a_new_version_directory")
        return {
            "command": "freeze",
            "status": "unchanged",
            "dataset_version": args.version,
            **ledger["summary"],
        }
    if out.exists() and any(out.iterdir()):
        raise ValueError("freeze_requires_empty_output_directory")
    out.mkdir(parents=True, exist_ok=True)
    # The ledger is the commit marker: readers require both files and verify them.
    write_json(out / "ground_truth_benchmark.jsonl", golden, jsonl=True)
    write_json(existing, ledger)
    return {
        "command": "freeze",
        "status": "frozen",
        "dataset_version": args.version,
        **ledger["summary"],
    }


def frozen_splits(
    path: Path, records: list[dict[str, Any]], golden_path: Path | None = None
) -> dict[str, str]:
    ledger: dict[str, Any] = json.loads(path.read_text())
    items = ledger["items"]
    if not ledger.get("dataset_version") or truth_hash(split_manifest(items)) != ledger.get(
        "split_manifest_sha256"
    ):
        raise ValueError("invalid_frozen_split_manifest")
    bundled = path.parent / "ground_truth_benchmark.jsonl"
    if not bundled.is_file() or digest(bundled.read_bytes()) != ledger.get("dataset_sha256"):
        raise ValueError("frozen_golden_hash_mismatch")
    if golden_path and digest(golden_path.read_bytes()) != ledger["dataset_sha256"]:
        raise ValueError("ground_truth_does_not_match_frozen_version")
    by_id = {r["id"]: r for r in items}
    if len(by_id) != len(items):
        raise ValueError("duplicate_frozen_identity")
    for row in records:
        item = by_id.get(row.get("id"))
        if not item:
            raise ValueError("unknown_frozen_sample:" + str(row.get("id")))
        if item["split"] not in {"dev", "test"} or row.get("split", item["split"]) != item["split"]:
            raise ValueError("frozen_split_conflict")
        if row.get("payload_sha256", item["payload_sha256"]) != item["payload_sha256"]:
            raise ValueError("frozen_payload_conflict")
        if (
            row.get("raw_payload") is not None
            and digest(row["raw_payload"]) != item["payload_sha256"]
        ):
            raise ValueError("frozen_payload_changed")
        if (
            row.get("ground_truth") is not None
            and truth_hash(row["ground_truth"]) != item["ground_truth_sha256"]
        ):
            raise ValueError("frozen_ground_truth_changed")
    return {key: item["split"] for key, item in by_id.items()}


def register_commands(subparsers: Any) -> None:
    for name in ("rebuild", "freeze"):
        parser = subparsers.add_parser(
            name,
            help="Rebuild archived inputs"
            if name == "rebuild"
            else "Freeze reviewed document groups",
        )
        parser.add_argument("--input", required=True, type=Path)
        parser.add_argument("--output-dir", required=True, type=Path)
        if name == "freeze":
            parser.add_argument("--version", required=True)
            parser.add_argument("--candidate-ledger", type=Path)
