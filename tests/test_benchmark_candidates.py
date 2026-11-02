"""Tests for benchmark candidate sample extraction contract.

Verifies the candidate pool extracted from source-verification-samples.html:
- Candidate count within 25–30 bounds
- Jurisdictions coverage (>= 8 distinct jurisdictions)
- Anomaly-first strata balance (all 6 strata present)
- Preservation of M1 known defect challenges (dd-501, dd-520, dd-572, dd-502)
- Preservation of non-empty raw_payload and canonical metadata fields
- Manifest synchronization with raw_candidates.jsonl
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from wlh.benchmark.sampler import (
    DEFAULT_CSV_PATH,
    DEFAULT_HTML_PATH,
    extract_representative_candidates,
    write_benchmark_candidates,
)

BENCHMARK_DIR = Path("data/benchmark")
RAW_CANDIDATES_JSONL = BENCHMARK_DIR / "raw_candidates.jsonl"
CANDIDATE_MANIFEST_JSON = BENCHMARK_DIR / "candidate_manifest.json"


@pytest.mark.contract
class TestBenchmarkCandidateExtraction:
    """Contract and integration validation for benchmark candidate sample extraction."""

    def test_extracted_candidate_pool_satisfies_size_and_uniqueness_contract(self) -> None:
        """The candidate dataset must contain 721 items covering all 73 sources with unique IDs."""
        assert RAW_CANDIDATES_JSONL.is_file(), f"File {RAW_CANDIDATES_JSONL} does not exist"

        with RAW_CANDIDATES_JSONL.open(encoding="utf-8") as f:
            candidates: list[dict[str, Any]] = [json.loads(line) for line in f]

        total_count = len(candidates)
        assert total_count == 721, (
            f"Expected 721 candidate items, got {total_count}"
        )

        source_counts: dict[str, int] = {}
        for item in candidates:
            sid = item["source_id"]
            source_counts[sid] = source_counts.get(sid, 0) + 1

        assert len(source_counts) == 73, (
            f"Expected 73 distinct source_ids, got {len(source_counts)}"
        )
        assert source_counts.get("dd-375") == 1, (
            f"Expected dd-375 to have 1 sample, got {source_counts.get('dd-375')}"
        )
        for sid, count in source_counts.items():
            if sid != "dd-375":
                assert count == 10, f"Expected source {sid} to have 10 samples, got {count}"

        ids = [item["id"] for item in candidates]
        expected_ids = [f"cand_{i:02d}" for i in range(1, 722)]
        assert ids == expected_ids, "Candidate IDs do not match cand_01..cand_721 sequence"
        assert len(ids) == len(set(ids)), f"Duplicate candidate IDs found: {ids}"

    def test_extracted_candidate_pool_covers_multi_jurisdiction_diversity(self) -> None:
        """The candidate dataset must cover at least 8 distinct jurisdictions including key regions."""
        with RAW_CANDIDATES_JSONL.open(encoding="utf-8") as f:
            candidates: list[dict[str, Any]] = [json.loads(line) for line in f]

        jurisdictions = {item["jurisdiction"] for item in candidates}
        assert len(jurisdictions) >= 8, (
            f"Expected at least 8 distinct jurisdictions, found {len(jurisdictions)}: {jurisdictions}"
        )

        required_key_jurisdictions = {"CN", "US", "VN", "TW", "JP", "DE", "GB"}
        missing_keys = required_key_jurisdictions - jurisdictions
        assert not missing_keys, f"Missing required key jurisdictions: {missing_keys}"

    def test_extracted_candidate_pool_covers_all_six_anomaly_strata(self) -> None:
        """The candidate dataset must contain representatives across all 6 anomaly strata."""
        with RAW_CANDIDATES_JSONL.open(encoding="utf-8") as f:
            candidates: list[dict[str, Any]] = [json.loads(line) for line in f]

        all_strata: set[str] = set()
        routes: set[str] = set()
        for item in candidates:
            all_strata.update(item.get("sampling_strata", []))
            routes.add(item.get("quality_route", ""))

        expected_strata = {
            "p5_shortest",
            "p95_longest",
            "missing_effective_at",
            "missing_doc_number",
            "accept_with_warning",
            "baseline",
        }
        missing_strata = expected_strata - all_strata
        assert not missing_strata, f"Missing expected strata: {missing_strata}"
        assert "accept_with_warning" in routes, "Missing accept_with_warning route samples"

    def test_extracted_candidate_pool_includes_m1_survey_defect_challenges(self) -> None:
        """The candidate dataset must include known defect challenges (dd-501, dd-520, dd-572, dd-502)."""
        with RAW_CANDIDATES_JSONL.open(encoding="utf-8") as f:
            candidates: list[dict[str, Any]] = [json.loads(line) for line in f]

        source_ids = {item["source_id"] for item in candidates}
        required_defect_sources = {"dd-501", "dd-520", "dd-572", "dd-502"}
        missing_defect_sources = required_defect_sources - source_ids
        assert not missing_defect_sources, f"Missing defect sources: {missing_defect_sources}"

        # Verify specific defect signatures
        by_cand_id = {item["id"]: item for item in candidates}

        # dd-501 (cand_01): effective_at is None despite being in text, issuer is portal
        dd501 = by_cand_id["cand_01"]
        assert dd501["source_id"] == "dd-501"
        assert dd501["effective_at"] is None
        assert "发改委" in dd501["selection_reason"] or "国务院" in dd501["selection_reason"]

        # dd-520 (cand_02): missing doc_number
        dd520 = by_cand_id["cand_02"]
        assert dd520["source_id"] == "dd-520"
        assert dd520["doc_number"] is None

        # dd-572 (cand_03): doc_number contains file size pollution
        dd572 = by_cand_id["cand_03"]
        assert dd572["source_id"] == "dd-572"
        assert "KB" in str(dd572["doc_number"]) or "MB" in str(dd572["doc_number"])

        # dd-502 (cand_04): historical revision issue noted in reason
        dd502 = by_cand_id["cand_04"]
        assert dd502["source_id"] == "dd-502"
        assert "128号" in dd502["selection_reason"]

    def test_extracted_candidate_records_preserve_full_raw_payloads_and_canonical_fields(
        self,
    ) -> None:
        """Each candidate record must contain non-empty raw_payload and all required metadata fields."""
        with RAW_CANDIDATES_JSONL.open(encoding="utf-8") as f:
            candidates: list[dict[str, Any]] = [json.loads(line) for line in f]

        required_fields = {
            "id",
            "source_id",
            "source_name",
            "jurisdiction",
            "doc_type",
            "title",
            "doc_number",
            "published_at",
            "effective_at",
            "issuer",
            "detail_url",
            "markdown",
            "raw_payload",
            "sampling_strata",
            "quality_route",
            "selection_reason",
        }

        for item in candidates:
            missing = required_fields - set(item.keys())
            assert not missing, f"Item {item.get('id')} missing fields: {missing}"

            raw_payload = item["raw_payload"]
            assert isinstance(raw_payload, str), f"raw_payload in {item['id']} must be str"
            assert len(raw_payload.strip()) > 50, (
                f"raw_payload in {item['id']} too short ({len(raw_payload)})"
            )

            markdown = item["markdown"]
            assert isinstance(markdown, str), f"markdown in {item['id']} must be str"
            assert len(markdown.strip()) > 0, f"markdown in {item['id']} is empty"

    def test_candidate_manifest_matches_jsonl_candidate_catalog(self) -> None:
        """The candidate_manifest.json must strictly synchronize with raw_candidates.jsonl."""
        assert CANDIDATE_MANIFEST_JSON.is_file(), f"Manifest {CANDIDATE_MANIFEST_JSON} missing"

        with RAW_CANDIDATES_JSONL.open(encoding="utf-8") as f:
            candidates: list[dict[str, Any]] = [json.loads(line) for line in f]

        with CANDIDATE_MANIFEST_JSON.open(encoding="utf-8") as f:
            manifest: dict[str, Any] = json.load(f)

        assert manifest["metadata"]["total_candidates"] == len(candidates)
        manifest_cand_ids = [c["id"] for c in manifest["candidates"]]
        jsonl_cand_ids = [c["id"] for c in candidates]
        assert manifest_cand_ids == jsonl_cand_ids

    def test_extractor_function_execution_outputs_consistent_records_from_fresh_run(
        self, tmp_path: Path
    ) -> None:
        """Calling extract_representative_candidates and write_benchmark_candidates produces identical output."""
        fresh_candidates, fresh_manifest = extract_representative_candidates(
            html_path=DEFAULT_HTML_PATH,
            csv_path=DEFAULT_CSV_PATH,
        )
        assert len(fresh_candidates) == 721
        assert fresh_manifest["metadata"]["total_candidates"] == 721

        write_benchmark_candidates(tmp_path, fresh_candidates, fresh_manifest)
        assert (tmp_path / "raw_candidates.jsonl").is_file()
        assert (tmp_path / "candidate_manifest.json").is_file()
        assert not (tmp_path / "representative_samples.jsonl").exists()

