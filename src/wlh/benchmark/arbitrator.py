"""Compare independent labels and stage explicit resolutions for review and freezing.

Agreement is evidence, not publication approval. This module never picks one
annotator automatically and never writes the canonical frozen dataset.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Mapping, cast

from wlh.benchmark.annotator import read_rows, write_json
from wlh.benchmark.quality import validate_ground_truth_integrity as validate_ground_truth_integrity
from wlh.core import public_url

DEFAULT_ANNOTATOR_A_PATH = Path("work/benchmark/annotator_a.jsonl")
DEFAULT_ANNOTATOR_B_PATH = Path("work/benchmark/annotator_b.jsonl")
DEFAULT_DISPUTES_PATH = Path("work/benchmark/disputes.jsonl")
DEFAULT_BENCHMARK_PATH = Path("work/benchmark/resolved.jsonl")
BENCHMARK_FIELDS = ("title", "doc_number", "published_at", "effective_at", "issuer", "doc_type")


def normalize_semantic_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).lower()


def evaluate_field_agreement(field: str, val_a: Any, val_b: Any) -> tuple[bool, bool]:
    return val_a == val_b, normalize_semantic_text(val_a) == normalize_semantic_text(val_b)


def indexed(path: Path | None) -> dict[str, dict[str, Any]]:
    rows = read_rows(path) if path and path.is_file() else []
    result = {r["id"]: r for r in rows}
    if len(result) != len(rows):
        raise ValueError("duplicate_annotation_identity")
    return result


def staging_output(path: Path) -> None:
    if path.resolve() == Path("data/benchmark/ground_truth_benchmark.jsonl").resolve():
        raise ValueError("canonical_publication_requires_review_and_freeze")


def evaluate_task_agreement(
    task_type: str,
    gt_a: Mapping[str, Any],
    gt_b: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    """Evaluate agreement between Annotator A and B for list_page or content_page tasks."""
    if task_type == "list_page":
        raw_u_a = gt_a.get("target_urls")
        u_list_a: list[object] = cast(list[object], raw_u_a) if isinstance(raw_u_a, list) else []
        urls_a: set[str] = {public_url(str(u)).rstrip("/") for u in u_list_a if u}
        raw_u_b = gt_b.get("target_urls")
        u_list_b: list[object] = cast(list[object], raw_u_b) if isinstance(raw_u_b, list) else []
        urls_b: set[str] = {public_url(str(u)).rstrip("/") for u in u_list_b if u}
        if urls_a == urls_b:
            return True, []
        diffs: list[str] = []
        if urls_a - urls_b:
            diffs.append(f"URLs only in A: {sorted(urls_a - urls_b)}")
        if urls_b - urls_a:
            diffs.append(f"URLs only in B: {sorted(urls_b - urls_a)}")
        return False, diffs

    raw_recs_a = gt_a.get("records")
    r_list_a: list[object] = cast(list[object], raw_recs_a) if isinstance(raw_recs_a, list) else []
    recs_a: list[Mapping[str, Any]] = (
        [cast(Mapping[str, Any], r) for r in r_list_a if isinstance(r, Mapping)]
        if r_list_a
        else [gt_a]
    )
    raw_recs_b = gt_b.get("records")
    r_list_b: list[object] = cast(list[object], raw_recs_b) if isinstance(raw_recs_b, list) else []
    recs_b: list[Mapping[str, Any]] = (
        [cast(Mapping[str, Any], r) for r in r_list_b if isinstance(r, Mapping)]
        if r_list_b
        else [gt_b]
    )

    if len(recs_a) != len(recs_b):
        return False, [f"Record count split mismatch: len(A)={len(recs_a)} vs len(B)={len(recs_b)}"]

    all_agreed = True
    diffs: list[str] = []
    for idx, (ra, rb) in enumerate(zip(recs_a, recs_b, strict=False)):
        for f in BENCHMARK_FIELDS:
            va, vb = ra.get(f), rb.get(f)
            exact_match, semantic_match = evaluate_field_agreement(f, va, vb)
            if not (exact_match or semantic_match):
                all_agreed = False
                diffs.append(f"Record {idx} field {f} mismatch: {va!r} != {vb!r}")

        raw_att_a = ra.get("attachment_urls")
        att_list_a: list[object] = (
            cast(list[object], raw_att_a) if isinstance(raw_att_a, list) else []
        )
        att_a: set[str] = {public_url(str(u)) for u in att_list_a if u}
        raw_att_b = rb.get("attachment_urls")
        att_list_b: list[object] = (
            cast(list[object], raw_att_b) if isinstance(raw_att_b, list) else []
        )
        att_b: set[str] = {public_url(str(u)) for u in att_list_b if u}
        if att_a != att_b:
            all_agreed = False
            diffs.append(f"Record {idx} attachment_urls mismatch")

        # Markdown body text similarity
        ma = normalize_semantic_text(ra.get("markdown", ""))
        mb = normalize_semantic_text(rb.get("markdown", ""))
        if ma != mb:
            all_agreed = False
            diffs.append(f"Record {idx} markdown content differs")

    return all_agreed, diffs


def export_disputes(
    annotator_a_path: Path = DEFAULT_ANNOTATOR_A_PATH,
    annotator_b_path: Path = DEFAULT_ANNOTATOR_B_PATH,
    output_path: Path = DEFAULT_DISPUTES_PATH,
    bundle_path: Path | None = None,
    candidates_path: Path | None = None,
) -> list[dict[str, Any]]:
    items_a, items_b = indexed(annotator_a_path), indexed(annotator_b_path)
    tasks = indexed(bundle_path or candidates_path)
    disputes: list[dict[str, Any]] = []
    for key in sorted(set(items_a) | set(items_b)):
        a, b = items_a.get(key, {}), items_b.get(key, {})
        task = tasks.get(key, {})
        task_type = (
            task.get("task_type") or a.get("task_type") or b.get("task_type") or "content_page"
        )
        ga, gb = a.get("ground_truth", {}), b.get("ground_truth", {})
        agreed, differences = (
            evaluate_task_agreement(task_type, ga, gb)
            if a and b
            else (False, ["missing_independent_annotation"])
        )
        if not agreed:
            disputes.append(
                dict(
                    task,
                    id=key,
                    task_type=task_type,
                    annotator_a=ga,
                    annotator_b=gb,
                    discrepancies=differences,
                )
            )
    write_json(output_path, disputes, jsonl=True)
    return disputes


def merge_resolutions(
    annotator_a_path: Path = DEFAULT_ANNOTATOR_A_PATH,
    annotator_b_path: Path = DEFAULT_ANNOTATOR_B_PATH,
    resolutions_path: Path | None = None,
    output_path: Path = DEFAULT_BENCHMARK_PATH,
) -> list[dict[str, Any]]:
    """Stage explicit decisions; unresolved or single annotations remain pending."""
    staging_output(output_path)
    items_a, items_b = indexed(annotator_a_path), indexed(annotator_b_path)
    resolutions = indexed(resolutions_path)
    merged: list[dict[str, Any]] = []
    for key, resolution in sorted(resolutions.items()):
        row = {**items_a.get(key, items_b.get(key, {})), **resolution}
        truth = row.get("ground_truth", {})
        raw = row.get("raw_payload")
        valid, reason = validate_ground_truth_integrity(
            truth,
            row.get("task_type", "content_page"),
            raw,
            row.get("request_url"),
            mime_type=row.get("mime_type", "text/html"),
            jurisdiction=row.get("jurisdiction"),
        )
        if not valid:
            raise ValueError(f"{key}:{reason}")
        payload_hash = hashlib.sha256(raw.encode()).hexdigest() if isinstance(raw, str) else None
        if raw is not None and row.get("payload_sha256", payload_hash) != payload_hash:
            raise ValueError("resolution_payload_hash_mismatch")
        row.update(
            verification_status="ARBITRATED",
            arbitration_notes=resolution.get(
                "arbitration_notes", "Explicit resolution; completeness review required"
            ),
            adjudication_metadata={
                "pipeline_version": "3.0",
                "content_hash": hashlib.sha256(
                    json.dumps(truth, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest(),
                "payload_sha256": payload_hash,
            },
        )
        merged.append(row)
    write_json(output_path, merged, jsonl=True)
    return merged
