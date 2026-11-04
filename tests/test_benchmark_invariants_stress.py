"""Independent adversarial stress verification tests for benchmark dataset invariants.

Validates:
- Strict canonical schema adherence across all 721 records (9 required top-level fields, 7 required ground_truth fields).
- Complete removal of deprecated cleanliness_ground_truth and clean_markdown_excerpt from schema and dataset.
- Candidate ID uniqueness and sequential continuity (cand_01 to cand_721).
- Strict ISO YYYY-MM-DD or None calendar date constraints.
- Non-empty ground_truth.markdown requirement (min_length=1) and cleanliness evaluation driven by ground_truth.markdown.
- Machine audit metadata invariants (onboarded_at, pipeline_version, 64-hex SHA-256).
- Defect resolutions for cand_01, cand_03, cand_04.
- Deterministic offline execution contract without socket operations.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

import wlh.benchmark as benchmark_pkg
import wlh.benchmark.models as benchmark_models
import wlh.benchmark.schema as benchmark_schema
from wlh.benchmark.evaluator import evaluate_benchmark_dataset, evaluate_sample
from wlh.benchmark.metrics import evaluate_cleanliness
from wlh.benchmark.schema import (
    AdjudicationMetadata,
    BenchmarkSampleRecord,
    GroundTruthData,
)

BENCHMARK_FILE = Path("data/benchmark/ground_truth_benchmark.jsonl")
ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HEX64_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")

REQUIRED_SCHEMA_FIELDS = {
    "id",
    "source_id",
    "jurisdiction",
    "detail_url",
    "current_extracted",
    "ground_truth",
    "verification_status",
    "arbitration_notes",
    "adjudication_metadata",
}

REQUIRED_GROUND_TRUTH_FIELDS = {
    "title",
    "doc_number",
    "published_at",
    "effective_at",
    "issuer",
    "doc_type",
    "markdown",
}


@pytest.fixture(scope="module")
def benchmark_data() -> list[dict[str, Any]]:
    assert BENCHMARK_FILE.is_file(), f"Benchmark file {BENCHMARK_FILE} does not exist"
    records: list[dict[str, Any]] = []
    with BENCHMARK_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if line_str:
                records.append(json.loads(line_str))
    return records


class TestBenchmarkDatasetSchemaContract:
    """Black-box adversarial audit of dataset schema adherence and field invariants."""

    def test_all_benchmark_records_strictly_satisfy_canonical_schema(
        self, benchmark_data: list[dict[str, Any]]
    ) -> None:
        """Every record must contain exactly all 9 canonical top-level fields and 7 ground_truth fields."""
        assert len(benchmark_data) == 721, (
            f"Expected 721 benchmark records, found {len(benchmark_data)}"
        )
        assert len({r["source_id"] for r in benchmark_data}) == 73, (
            f"Expected 73 distinct source_ids, found {len({r['source_id'] for r in benchmark_data})}"
        )
        for record in benchmark_data:
            rec_id = record.get("id", "unknown")
            record_keys = set(record.keys())
            assert "cleanliness_ground_truth" not in record_keys, (
                f"Record {rec_id} contains deprecated cleanliness_ground_truth field"
            )
            missing_fields = REQUIRED_SCHEMA_FIELDS - record_keys
            assert not missing_fields, (
                f"Record {rec_id} missing required schema fields: {missing_fields}"
            )
            extra_fields = record_keys - REQUIRED_SCHEMA_FIELDS
            assert not extra_fields, f"Record {rec_id} has unexpected extra fields: {extra_fields}"

            # Ensure top-level fields are non-None
            for field in REQUIRED_SCHEMA_FIELDS:
                assert record[field] is not None, f"Record {rec_id} has null field: {field}"

            # Ensure ground_truth has exact 7 required keys and non-empty markdown
            gt = record["ground_truth"]
            gt_keys = set(gt.keys())
            assert "clean_markdown_excerpt" not in gt_keys, (
                f"Record {rec_id} ground_truth contains deprecated clean_markdown_excerpt"
            )
            assert gt_keys == REQUIRED_GROUND_TRUTH_FIELDS, (
                f"Record {rec_id} ground_truth keys mismatch: expected {REQUIRED_GROUND_TRUTH_FIELDS}, got {gt_keys}"
            )
            assert isinstance(gt["markdown"], str) and len(gt["markdown"].strip()) > 0, (
                f"Record {rec_id} ground_truth.markdown must be a non-empty string"
            )

            # Golden Set invariant: zero unresolved '待人工确认' fields or PENDING_CONFIRMATION status
            assert record["verification_status"] != "PENDING_CONFIRMATION", (
                f"Record {rec_id} has unresolved PENDING_CONFIRMATION status"
            )
            for gt_key, gt_val in gt.items():
                assert gt_val != "待人工确认", (
                    f"Record {rec_id} ground_truth.{gt_key} has unresolved '待人工确认' placeholder"
                )

            # Strict Pydantic model validation
            validated = BenchmarkSampleRecord.model_validate(record)
            assert validated.id == rec_id

    def test_candidate_ids_are_unique_and_sequentially_numbered(
        self, benchmark_data: list[dict[str, Any]]
    ) -> None:
        """Record IDs must be exactly cand_01 through cand_721 with zero duplicates or gaps."""
        actual_ids = [str(r["id"]) for r in benchmark_data]
        expected_ids = [f"cand_{i:02d}" for i in range(1, 722)]
        assert actual_ids == expected_ids, f"ID sequence mismatch. Got: {actual_ids}"
        assert len(set(actual_ids)) == 721, "Duplicate IDs detected in benchmark dataset"

    def test_date_fields_strictly_conform_to_iso_calendar_format_or_none(
        self, benchmark_data: list[dict[str, Any]]
    ) -> None:
        """Published and effective dates must be valid calendar dates matching YYYY-MM-DD or None."""
        for record in benchmark_data:
            rec_id = str(record["id"])
            gt = record["ground_truth"]
            for date_field in ("published_at", "effective_at"):
                val = gt.get(date_field)
                assert val != "待人工确认", (
                    f"{rec_id} {date_field} in Golden Set must not be unresolved '待人工确认'"
                )
                if val is not None:
                    assert isinstance(val, str), f"{rec_id} {date_field} is not string: {type(val)}"
                    assert ISO_DATE_PATTERN.match(val), (
                        f"{rec_id} {date_field} fails ISO YYYY-MM-DD pattern: '{val}'"
                    )
                    # Verify real calendar date validity (reject 2026-02-31, 2026-13-40, etc.)
                    try:
                        datetime.date.fromisoformat(val)
                    except ValueError as exc:
                        pytest.fail(
                            f"{rec_id} {date_field} is not a valid calendar date: {val} ({exc})"
                        )

    def test_cleanliness_ground_truth_and_clean_markdown_excerpt_removed_from_models_and_schema(
        self,
    ) -> None:
        """CleanlinessGroundTruth, cleanliness_ground_truth, and clean_markdown_excerpt are removed from models."""
        assert not hasattr(benchmark_models, "CleanlinessGroundTruth")
        assert not hasattr(benchmark_schema, "CleanlinessGroundTruth")
        assert not hasattr(benchmark_pkg, "CleanlinessGroundTruth")
        assert "cleanliness_ground_truth" not in BenchmarkSampleRecord.model_fields
        assert "clean_markdown_excerpt" not in GroundTruthData.model_fields
        assert "markdown" in GroundTruthData.model_fields


class TestBenchmarkMachineAuditMetadataInvariants:
    """Black-box audit of machine audit tracking metadata and content hash invariants."""

    def test_every_record_contains_complete_audit_metadata_envelope(
        self, benchmark_data: list[dict[str, Any]]
    ) -> None:
        """Every record must contain valid onboarded_at, pipeline_version, and 64-hex hashes without Jev traces."""
        forbidden_jev_keys = {
            "step1_health_check",
            "step2_repairs",
            "candidates_evaluated",
            "pending_confirmation_fields",
        }
        for record in benchmark_data:
            rec_id = str(record["id"])
            raw_adj = record.get("adjudication_metadata")
            assert isinstance(raw_adj, dict), (
                f"{rec_id} adjudication_metadata is missing or not a dictionary"
            )
            adj: dict[str, Any] = cast(dict[str, Any], raw_adj)

            present_jev_keys = forbidden_jev_keys & set(adj.keys())
            assert not present_jev_keys, (
                f"{rec_id} adjudication_metadata contains residual Jev onboarding keys: {present_jev_keys}"
            )

            onboarded_at = str(adj.get("onboarded_at") or "")
            assert len(onboarded_at) > 0, f"{rec_id} adjudication_metadata.onboarded_at is empty"

            pipeline_version = str(adj.get("pipeline_version") or "")
            assert len(pipeline_version) > 0, (
                f"{rec_id} adjudication_metadata.pipeline_version is empty"
            )
            assert "path-a" not in pipeline_version, (
                f"{rec_id} adjudication_metadata.pipeline_version contains Jev path-a version: {pipeline_version}"
            )

            content_hash = str(adj.get("content_hash") or "")
            assert HEX64_PATTERN.match(content_hash), (
                f"{rec_id} adjudication_metadata.content_hash is not a 64-char hex string: '{content_hash}'"
            )

            payload_sha256 = str(adj.get("payload_sha256") or "")
            assert HEX64_PATTERN.match(payload_sha256), (
                f"{rec_id} adjudication_metadata.payload_sha256 is not a 64-char hex string: '{payload_sha256}'"
            )
            assert content_hash == payload_sha256, f"{rec_id} content_hash != payload_sha256"

    def test_content_hash_matches_candidate_payload_fingerprint(
        self, benchmark_data: list[dict[str, Any]]
    ) -> None:
        """Audit content_hash against raw markdown text fingerprint to ensure cryptographic fidelity."""
        for record in benchmark_data:
            rec_id = str(record["id"])
            adj = record["adjudication_metadata"]
            cur = record.get("current_extracted", {})
            md_text = str(cur.get("markdown") or cur.get("raw_payload") or "")
            expected_hash = hashlib.sha256(md_text.encode("utf-8")).hexdigest()
            assert adj["content_hash"] == expected_hash, (
                f"{rec_id} content_hash {adj['content_hash']} does not match raw markdown sha256 {expected_hash}"
            )


class TestBenchmarkDefectResolutionIntegrity:
    """Verification of specific defect resolutions required by M8 acceptance criteria."""

    def test_cand_01_resolves_effective_date_and_issuing_authority(
        self, benchmark_data: list[dict[str, Any]]
    ) -> None:
        """cand_01 (dd-501) must resolve effective_at to 2026-10-01 and issuer to 国家发展和改革委员会."""
        cand = next(r for r in benchmark_data if r["id"] == "cand_01")
        gt = cand["ground_truth"]
        assert cand["source_id"] == "dd-501"
        assert gt["effective_at"] == "2026-10-01", (
            f"cand_01 effective_at expected '2026-10-01', got '{gt.get('effective_at')}'"
        )
        assert gt["issuer"] == "国家发展和改革委员会", (
            f"cand_01 issuer expected '国家发展和改革委员会', got '{gt.get('issuer')}'"
        )
        assert gt["doc_number"] == "国家发展改革委令第45号"

    def test_cand_03_strips_file_size_and_date_artifacts_from_gazette_number(
        self, benchmark_data: list[dict[str, Any]]
    ) -> None:
        """cand_03 (dd-572) doc_number must not contain '(372.7 KB)' or publication noise."""
        cand = next(r for r in benchmark_data if r["id"] == "cand_03")
        gt = cand["ground_truth"]
        cur = cand["current_extracted"]
        assert cand["source_id"] == "dd-572"
        # Confirm candidate had the file size artifact
        assert "372.7 KB" in str(cur.get("doc_number")), (
            "Baseline candidate did not have file size artifact"
        )
        # Confirm ground truth doc_number is cleaned
        doc_num = str(gt.get("doc_number"))
        assert "372.7 KB" not in doc_num, (
            f"cand_03 ground_truth doc_number still contains file size: '{doc_num}'"
        )
        assert "KB" not in doc_num, f"cand_03 ground_truth doc_number contains 'KB': '{doc_num}'"
        assert doc_num == "Western Cape Provincial Gazette 9196"

    def test_cand_04_prioritizes_governing_revision_decree_number(
        self, benchmark_data: list[dict[str, Any]]
    ) -> None:
        """cand_04 (dd-502) doc_number must resolve to 河南省人民政府令第238号."""
        cand = next(r for r in benchmark_data if r["id"] == "cand_04")
        gt = cand["ground_truth"]
        cur = cand["current_extracted"]
        assert cand["source_id"] == "dd-502"
        # Original crawler captured repealed decree No. 128
        assert cur.get("doc_number") == "河南省人民政府令第128号"
        # Ground truth must resolve to governing decree No. 238
        assert gt.get("doc_number") == "河南省人民政府令第238号", (
            f"cand_04 doc_number expected '河南省人民政府令第238号', got '{gt.get('doc_number')}'"
        )


class TestBenchmarkAdversarialNegativeScenarios:
    """Stress-testing models and validation with adversarial inputs to confirm error detection."""

    def test_ground_truth_model_rejects_corrupted_date_formats(self) -> None:
        """GroundTruthData must raise ValidationError on non-ISO, invalid calendar, or placeholder dates."""
        bad_dates = [
            "2026/10/01",
            "2026.10.01",
            "10-01-2026",
            "2026-1-1",
            "2026-02-31",
            "2026-13-40",
            "待人工确认",
            "null",
            "",
        ]
        for bad_date in bad_dates:
            with pytest.raises(ValidationError):
                GroundTruthData(
                    title="Test Regulation",
                    doc_type="regulation",
                    published_at=bad_date,
                    markdown="# Test Regulation\n\nArticle 1.",
                )

    def test_adjudication_metadata_rejects_truncated_or_non_hex_hashes(self) -> None:
        """AdjudicationMetadata must raise ValidationError on truncated or non-hex SHA-256 hashes."""
        short_hash = "abc123"
        non_hex_hash = "g" * 64
        with pytest.raises(ValidationError):
            AdjudicationMetadata(content_hash=short_hash)
        with pytest.raises(ValidationError):
            AdjudicationMetadata(content_hash=non_hex_hash)
        with pytest.raises(ValidationError):
            AdjudicationMetadata(content_hash="a" * 64, payload_sha256=short_hash)

        # Valid model instantiation auto-populates payload_sha256
        valid_hash = "a" * 64
        meta = AdjudicationMetadata(content_hash=valid_hash)
        assert meta.payload_sha256 == valid_hash
        assert meta.pipeline_version == "1.0"
        assert len(meta.onboarded_at) > 0

    def test_ground_truth_model_requires_non_empty_markdown_field(self) -> None:
        """GroundTruthData must strictly require markdown to be a non-empty string (min_length=1)."""
        with pytest.raises(ValidationError):
            GroundTruthData.model_validate(
                {
                    "title": "Test Regulation",
                    "doc_type": "regulation",
                }
            )
        with pytest.raises(ValidationError):
            GroundTruthData.model_validate(
                {
                    "title": "Test Regulation",
                    "doc_type": "regulation",
                    "markdown": "",
                }
            )
        valid_gt = GroundTruthData.model_validate(
            {
                "title": "Test Regulation",
                "doc_type": "regulation",
                "markdown": "# Test Regulation\n\nSubstantive legal content.",
            }
        )
        assert valid_gt.markdown == "# Test Regulation\n\nSubstantive legal content."

    def test_cleanliness_evaluation_driven_strictly_by_ground_truth_markdown_and_heuristics(
        self,
    ) -> None:
        """Cleanliness evaluation penalizes portal metadata/duplicates and recovers 100.0 on clean match."""
        clean_md = (
            "# 공정거래위원회 고시 제2026-10호\n\n"
            "제1조(목적) 이 고시는 독점규제 및 공정거래에 관한 법률의 시행에 필요한 사항을 정함을 목적으로 한다.\n\n"
            "제2조(시행일) 이 고시는 2026년 10월 1일부터 시행한다."
        )
        noisy_candidate_md = (
            "담당부서 : 경쟁정책과 | 조회수 : 842 | 字体：[ 大 中 小 ] | 分享到： 微信 **X**\n"
            + clean_md
            + "\n\n[Repealed, 1997, c. 20, s. 45]\n"
            + "[Repealed, 1997, c. 20, s. 45]\n"
            + "[Repealed, 1997, c. 20, s. 45]\n"
            + "[Repealed, 1997, c. 20, s. 45]\n"
        )
        noisy_res = evaluate_cleanliness(
            noisy_candidate_md,
            title="공정거래위원회 고시 제2026-10호",
            metadata={"ground_truth": {"markdown": clean_md}},
        )
        assert noisy_res.sub_scores.boilerplate_freedom < 100.0
        assert noisy_res.sub_scores.deduplication_purity < 100.0

        clean_sample_res = evaluate_sample(
            {
                "title": "공정거래위원회 고시 제2026-10호",
                "doc_number": "제2026-10호",
                "published_at": "2026-09-15",
                "effective_at": "2026-10-01",
                "issuer": "공정거래위원회",
                "doc_type": "notice",
                "markdown": clean_md,
            },
            ground_truth={
                "title": "공정거래위원회 고시 제2026-10호",
                "doc_number": "제2026-10호",
                "published_at": "2026-09-15",
                "effective_at": "2026-10-01",
                "issuer": "공정거래위원회",
                "doc_type": "notice",
                "markdown": clean_md,
            },
            sample_id="cand_stress_clean",
            source_id="dd-510",
            jurisdiction="KR",
        )
        assert clean_sample_res.cleanliness.score == 100.0
        assert clean_sample_res.overall_score == 100.0

    def test_benchmark_sample_record_rejects_pending_confirmation_status(
        self, benchmark_data: list[dict[str, Any]]
    ) -> None:
        """BenchmarkSampleRecord must reject legacy Jev PENDING_CONFIRMATION verification_status."""
        corrupted = dict(benchmark_data[0])
        corrupted["verification_status"] = "PENDING_CONFIRMATION"
        with pytest.raises(ValidationError):
            BenchmarkSampleRecord.model_validate(corrupted)


class TestBenchmarkOfflineExecutionPurity:
    """Verification that benchmark evaluation operates 100% offline without network endpoints."""

    def test_benchmark_dataset_evaluation_produces_valid_quality_scores(
        self, benchmark_data: list[dict[str, Any]]
    ) -> None:
        """In-memory dataset evaluation produces valid quality scores exceeding all thresholds."""
        report = evaluate_benchmark_dataset(benchmark_data)
        assert report.total_samples == 721
        assert report.average_overall_score >= 65.0, (
            f"Expected overall score >= 65.0, got {report.average_overall_score}"
        )
        assert report.average_cleanliness_score >= 80.0, (
            f"Expected cleanliness >= 80.0, got {report.average_cleanliness_score}"
        )
        assert (
            report.average_accuracy_score is not None and report.average_accuracy_score >= 60.0
        ), f"Expected accuracy >= 60.0, got {report.average_accuracy_score}"

    def test_benchmark_dataset_evaluation_executes_offline_without_network_sockets(
        self, tmp_path: Path
    ) -> None:
        """Evaluation executes cleanly with zero network sockets using blackhole network proxies."""
        out_dir = tmp_path / "offline_reports"
        blackhole_env = os.environ.copy()
        blackhole_env["http_proxy"] = "http://127.0.0.1:59999"
        blackhole_env["https_proxy"] = "http://127.0.0.1:59999"

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "wlh",
                "benchmark",
                "run",
                "--input",
                str(BENCHMARK_FILE),
                "--output-dir",
                str(out_dir),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=blackhole_env,
            timeout=60,
        )
        assert proc.returncode == 0, (
            f"Benchmark run attempted network connection! stderr:\n{proc.stderr}"
        )
        report_file = out_dir / "benchmark_report.json"
        assert report_file.is_file(), f"Expected {report_file} to exist"

        with report_file.open("r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["total_samples"] == 721
        assert data["average_overall_score"] >= 65.0, (
            f"Expected overall score >= 65.0, got {data['average_overall_score']}"
        )
        assert data["average_cleanliness_score"] >= 80.0, (
            f"Expected cleanliness >= 80.0, got {data['average_cleanliness_score']}"
        )
        assert data["average_accuracy_score"] >= 60.0, (
            f"Expected accuracy >= 60.0, got {data['average_accuracy_score']}"
        )
