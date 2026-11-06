"""Acceptance of the two published benchmark files and their honest denominator."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pytest
import yaml

from wlh.benchmark.arbitrator import validate_ground_truth_integrity

ROOT = Path(__file__).resolve().parents[1]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _published() -> tuple[dict[str, Any], list[dict[str, Any]], bytes]:
    directory = ROOT / "data" / "benchmark"
    ledger = json.loads((directory / "annotation_pipeline.json").read_text(encoding="utf-8"))
    golden_bytes = (directory / "ground_truth_benchmark.jsonl").read_bytes()
    rows = [json.loads(line) for line in golden_bytes.decode("utf-8").split("\n") if line]
    return ledger, rows, golden_bytes


@pytest.fixture(scope="session")
def wlh_env() -> dict[str, str]:
    """File-only benchmark contracts need no shared database or S3 bootstrap."""
    return {}


class TestBenchmarkDataset:
    def test_published_jurisdictions_match_their_source_identity(self) -> None:
        _, rows, _ = _published()
        mismatches: list[tuple[str, str, str]] = []
        for row in rows:
            config_path = ROOT / "sources" / f"{row['source_id']}.yaml"
            if not config_path.is_file():
                continue
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            expected = config.get("storage", {}).get("metadata", {}).get("jurisdiction")
            if expected and row["jurisdiction"] != expected:
                mismatches.append((row["id"], row["jurisdiction"], expected))
        assert not mismatches, f"Source identity mismatches: {mismatches}"

    def test_published_golden_records_have_archived_inputs_and_reviewed_grounded_truth(
        self,
    ) -> None:
        ledger, rows, _ = _published()
        assert rows, "A frozen benchmark must contain reviewed original documents"
        expected_keys = {
            "id",
            "task_type",
            "source_id",
            "jurisdiction",
            "request_url",
            "mime_type",
            "payload_sha256",
            "raw_payload",
            "ground_truth",
        }
        members = {row["id"]: row for row in ledger["items"]}
        assert len(members) == len(ledger["items"])
        assert {row["id"] for row in rows} == set(members)
        assert len({row["id"] for row in rows}) == len(rows)
        for row in rows:
            assert set(row) == expected_keys
            assert row["raw_payload"].strip()
            assert row["payload_sha256"] == _sha(row["raw_payload"].encode("utf-8"))
            assert not any(
                marker in row["raw_payload"]
                for marker in ("{host}", "#unselected", "data-wlh-pages")
            )
            member = members[row["id"]]
            assert {key: member[key] for key in expected_keys} == row
            provenance = member["provenance"]
            assert provenance["kind"] == "archived_response"
            assert provenance["acquisition_id"]
            assert len(provenance["archive_sha256"]) == 64
            review = member["review"]
            assert review["status"] == "approved"
            assert review["reviewer"].strip() and review["notes"].strip()
            assert review["payload_sha256"] == row["payload_sha256"]
            assert review["ground_truth_sha256"] == _sha(
                json.dumps(row["ground_truth"], ensure_ascii=False, sort_keys=True).encode()
            )
            valid, reason = validate_ground_truth_integrity(
                row["ground_truth"],
                row["task_type"],
                raw_payload=row["raw_payload"],
                request_url=row["request_url"],
                mime_type=row["mime_type"],
                jurisdiction=row["jurisdiction"],
            )
            assert valid, f"{row['id']} is not grounded: {reason}"

    def test_candidate_denominator_and_source_gaps_are_fully_accounted_for(self) -> None:
        ledger, rows, _ = _published()
        candidates = ledger["candidates"]
        candidate_ids = {row["id"] for row in candidates}
        golden_ids = {row["id"] for row in rows}
        assert len(candidate_ids) == len(candidates)
        assert golden_ids <= candidate_ids
        assert ledger["summary"]["total_candidates"] == len(candidates)
        assert ledger["summary"]["golden_set_exported"] == len(rows)
        assert ledger["summary"]["excluded_count"] == len(candidates) - len(rows)
        candidate_counts = Counter(row["source_id"] for row in candidates)
        golden_counts = Counter(row["source_id"] for row in rows)
        coverage = {row["source_id"]: row for row in ledger["coverage"]}
        assert len(coverage) == len(ledger["coverage"])
        assert set(coverage) == set(candidate_counts)
        for candidate in candidates:
            if candidate["id"] not in golden_ids:
                assert candidate["reasons"], f"Missing exclusion evidence for {candidate['id']}"
        for source_id, record in coverage.items():
            assert record["candidate_count"] == candidate_counts[source_id]
            assert record["golden_count"] == golden_counts[source_id]
            assert (
                record["excluded_count"] == candidate_counts[source_id] - golden_counts[source_id]
            )
            if record["excluded_count"]:
                assert record["reasons"], f"Missing source coverage gap for {source_id}"
        matrix_path = ROOT / "docs/runbooks/tax-p0-delivery-matrix.csv"
        with matrix_path.open(encoding="utf-8") as handle:
            planned_sources = {row["Source ID"].strip() for row in csv.DictReader(handle)}
        assert planned_sources <= set(coverage), "Every planned source needs a visible disposition"

    def test_published_version_and_document_group_membership_match_frozen_digests(self) -> None:
        ledger, rows, golden_bytes = _published()
        assert ledger["dataset_version"].strip()
        assert ledger["dataset_sha256"] == _sha(golden_bytes)
        members = {row["id"]: row for row in ledger["items"]}
        splits = Counter(member["split"] for member in members.values())
        assert set(splits) <= {"dev", "test"}
        assert ledger["summary"]["dev_split_count"] == splits["dev"]
        assert ledger["summary"]["test_split_count"] == splits["test"]
        groups: dict[str, set[str]] = defaultdict(set)
        manifest: list[dict[str, str]] = []
        for row in sorted(rows, key=lambda item: item["id"]):
            member = members[row["id"]]
            assert member["document_group_id"]
            groups[member["document_group_id"]].add(member["split"])
            manifest.append(
                {
                    "id": row["id"],
                    "document_group_id": member["document_group_id"],
                    "split": member["split"],
                    "payload_sha256": row["payload_sha256"],
                    "ground_truth_sha256": member["review"]["ground_truth_sha256"],
                }
            )
        assert all(len(splits_for_group) == 1 for splits_for_group in groups.values())
        assert ledger["summary"]["document_groups"] == len(groups)
        assert ledger["split_manifest_sha256"] == _sha(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
        )
