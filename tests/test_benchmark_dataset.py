"""High-value black-box and contract tests for the authoritative benchmark dataset.

Validates:
- ground_truth_benchmark.jsonl schema, field presence, and data integrity
- cross_verification_log.json structure and agreement rate thresholds (>= 80%)
- Cleanliness ground truth validity and clean excerpt completeness
- Status transitions (CONSENSUS vs ARBITRATED) and arbitration note justifications
- Known M1 defect challenge resolutions against raw payloads
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from wlh.benchmark.arbitrator import (
    DEFAULT_ANNOTATOR_A_PATH,
    DEFAULT_ANNOTATOR_B_PATH,
    DEFAULT_BENCHMARK_PATH,
    DEFAULT_CANDIDATES_PATH,
    DEFAULT_LOG_PATH,
    run_arbitration,
)

ISO_DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@pytest.fixture(scope="module")
def benchmark_records() -> list[dict[str, Any]]:
    assert DEFAULT_BENCHMARK_PATH.is_file(), f"Benchmark file {DEFAULT_BENCHMARK_PATH} missing"
    with DEFAULT_BENCHMARK_PATH.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


@pytest.fixture(scope="module")
def verification_log() -> dict[str, Any]:
    assert DEFAULT_LOG_PATH.is_file(), f"Verification log {DEFAULT_LOG_PATH} missing"
    with DEFAULT_LOG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def raw_candidates() -> dict[str, dict[str, Any]]:
    assert DEFAULT_CANDIDATES_PATH.is_file(), f"Candidates file {DEFAULT_CANDIDATES_PATH} missing"
    with DEFAULT_CANDIDATES_PATH.open(encoding="utf-8") as f:
        return {item["id"]: item for item in (json.loads(line) for line in f)}


class TestBenchmarkDatasetContract:
    """Contract tests verifying the structure and integrity of ground_truth_benchmark.jsonl."""

    def test_benchmark_dataset_contains_exact_sample_count_matching_candidates(
        self, benchmark_records: list[dict[str, Any]], raw_candidates: dict[str, dict[str, Any]]
    ) -> None:
        """Benchmark dataset must contain all 721 candidate items covering 73 sources with unique identifiers."""
        assert len(benchmark_records) == 721, f"Expected 721 records, found {len(benchmark_records)}"
        assert len({rec["source_id"] for rec in benchmark_records}) == 73, (
            f"Expected 73 distinct source_ids, found {len({rec['source_id'] for rec in benchmark_records})}"
        )
        benchmark_ids = [rec["id"] for rec in benchmark_records]
        assert benchmark_ids == [f"cand_{i:02d}" for i in range(1, 722)], (
            "Benchmark IDs do not match sequential cand_01..cand_721"
        )
        assert len(benchmark_ids) == len(set(benchmark_ids)), "Duplicate IDs found in benchmark dataset"
        assert set(benchmark_ids) == set(raw_candidates.keys()), "Benchmark IDs do not match raw candidates"

    def test_benchmark_records_conform_to_canonical_schema_contract(
        self, benchmark_records: list[dict[str, Any]]
    ) -> None:
        """Every record must contain all required top-level dictionary keys."""
        required_top_keys = {
            "id",
            "source_id",
            "jurisdiction",
            "detail_url",
            "current_extracted",
            "ground_truth",
            "cleanliness_ground_truth",
            "verification_status",
            "arbitration_notes",
        }
        for rec in benchmark_records:
            missing = required_top_keys - set(rec.keys())
            assert not missing, f"Record {rec.get('id')} missing keys: {missing}"

    def test_benchmark_metadata_identifiers_are_non_empty_and_valid(
        self, benchmark_records: list[dict[str, Any]]
    ) -> None:
        """Identifiers (id, source_id, jurisdiction, detail_url) must be non-empty strings."""
        for rec in benchmark_records:
            rec_id = rec["id"]
            assert rec["source_id"].startswith("dd-"), f"{rec_id}: invalid source_id format"
            assert len(rec["jurisdiction"]) in (2, 3), f"{rec_id}: jurisdiction code should be 2-3 chars"
            assert rec["detail_url"].startswith("http"), f"{rec_id}: detail_url must start with http"

    def test_benchmark_ground_truth_fields_conform_to_contract_types(
        self, benchmark_records: list[dict[str, Any]]
    ) -> None:
        """Ground truth must contain title, doc_type, clean_markdown_excerpt, and optional dates/numbers."""
        required_gt_keys = {
            "title",
            "doc_number",
            "published_at",
            "effective_at",
            "issuer",
            "doc_type",
            "clean_markdown_excerpt",
        }
        for rec in benchmark_records:
            rec_id = rec["id"]
            gt = rec["ground_truth"]
            missing = required_gt_keys - set(gt.keys())
            assert not missing, f"{rec_id} ground_truth missing keys: {missing}"
            for field_name, field_val in gt.items():
                assert field_val != "待人工确认", (
                    f"{rec_id}: Golden Set ground_truth.{field_name} contains unresolved placeholder '待人工确认'"
                )
            assert isinstance(gt["title"], str) and len(gt["title"]) > 0, f"{rec_id}: title must be non-empty"
            assert isinstance(gt["doc_type"], str) and len(gt["doc_type"]) > 0, f"{rec_id}: doc_type non-empty"
            assert isinstance(gt["clean_markdown_excerpt"], str) and len(gt["clean_markdown_excerpt"]) >= 30, (
                f"{rec_id}: clean excerpt must be at least 30 chars"
            )

    def test_benchmark_date_fields_conform_to_iso_or_null_contract(
        self, benchmark_records: list[dict[str, Any]]
    ) -> None:
        """Date fields (published_at, effective_at) must strictly match YYYY-MM-DD or be null in the Golden Set."""
        for rec in benchmark_records:
            rec_id = rec["id"]
            gt = rec["ground_truth"]
            for date_key in ("published_at", "effective_at"):
                val = gt[date_key]
                assert val != "待人工确认", (
                    f"{rec_id}: {date_key} in Golden Set must not be unresolved '待人工确认'"
                )
                if val is not None:
                    assert isinstance(val, str), f"{rec_id}: {date_key} must be string or None"
                    assert ISO_DATE_REGEX.match(val), f"{rec_id}: {date_key} '{val}' does not match YYYY-MM-DD"

    def test_benchmark_cleanliness_flags_are_strictly_boolean(
        self, benchmark_records: list[dict[str, Any]]
    ) -> None:
        """Cleanliness ground truth must include boolean flags for all major noise dimensions."""
        required_flags = {
            "has_html_artifacts",
            "has_navigation_boilerplate",
            "has_garbled_encoding",
            "has_duplicated_body",
        }
        for rec in benchmark_records:
            rec_id = rec["id"]
            clean_gt = rec["cleanliness_ground_truth"]
            for flag in required_flags:
                assert flag in clean_gt, f"{rec_id} missing cleanliness flag: {flag}"
                assert isinstance(clean_gt[flag], bool), f"{rec_id} flag {flag} is not boolean: {clean_gt[flag]}"

    def test_benchmark_verification_status_is_valid_enum_with_notes_when_arbitrated(
        self, benchmark_records: list[dict[str, Any]]
    ) -> None:
        """Golden Set records must have resolved verification_status (no PENDING_CONFIRMATION) and descriptive notes."""
        for rec in benchmark_records:
            rec_id = rec["id"]
            status = rec["verification_status"]
            assert status != "PENDING_CONFIRMATION", (
                f"{rec_id}: Golden Set record must not remain in PENDING_CONFIRMATION"
            )
            assert status in (
                "CONSENSUS",
                "ARBITRATED",
            ), f"{rec_id}: invalid status {status}"
            notes = rec["arbitration_notes"]
            assert isinstance(notes, str) and len(notes) > 0, f"{rec_id}: notes must be non-empty"
            if status == "ARBITRATED":
                assert "Arbitrated" in notes or "standard" in notes.lower(), (
                    f"{rec_id}: arbitrated note should describe ruling rationale"
                )


class TestCrossVerificationLogContract:
    """Contract tests verifying the structure and statistical consistency of cross_verification_log.json."""

    def test_verification_log_structure_satisfies_contract(
        self, verification_log: dict[str, Any]
    ) -> None:
        """Log must contain total_samples, agreement rates, arbitration summary, and item comparisons."""
        required_keys = {
            "total_samples",
            "overall_agreement_rate",
            "overall_semantic_agreement_rate",
            "field_agreement_rates",
            "arbitration_summary",
            "per_item_comparisons",
        }
        missing = required_keys - set(verification_log.keys())
        assert not missing, f"Verification log missing keys: {missing}"
        assert verification_log["total_samples"] == 28

    def test_inter_annotator_overall_agreement_rate_exceeds_threshold(
        self, verification_log: dict[str, Any]
    ) -> None:
        """Overall agreement rate between Annotator A and B must be >= 80% (0.80)."""
        exact_rate = verification_log["overall_agreement_rate"]
        assert exact_rate >= 0.80, f"Expected exact agreement rate >= 0.80, got {exact_rate}"
        assert 0.80 <= exact_rate <= 1.0

    def test_inter_annotator_semantic_agreement_rate_exceeds_threshold(
        self, verification_log: dict[str, Any]
    ) -> None:
        """Overall semantic agreement rate between Annotator A and B must be >= 90% (0.90)."""
        semantic_rate = verification_log["overall_semantic_agreement_rate"]
        assert semantic_rate >= 0.90, f"Expected semantic agreement rate >= 0.90, got {semantic_rate}"
        exact_rate = verification_log["overall_agreement_rate"]
        assert exact_rate <= semantic_rate

    def test_per_field_agreement_rates_satisfy_contract_bounds(
        self, verification_log: dict[str, Any]
    ) -> None:
        """Per-field agreement rates must be present for all 6 core benchmark fields."""
        fields = ("title", "doc_number", "published_at", "effective_at", "issuer", "doc_type")
        field_rates = verification_log["field_agreement_rates"]
        for f in fields:
            assert f in field_rates, f"Field {f} missing from agreement rates"
            rate_info = field_rates[f]
            assert 0.0 <= rate_info["exact_rate"] <= 1.0
            assert 0.0 <= rate_info["semantic_rate"] <= 1.0
            assert rate_info["semantic_rate"] >= rate_info["exact_rate"]
            assert rate_info["semantic_rate"] >= 0.85, (
                f"Field {f} semantic rate {rate_info['semantic_rate']} below expected 85%"
            )

    def test_arbitration_summary_counts_reconcile_with_item_statuses(
        self, verification_log: dict[str, Any]
    ) -> None:
        """Arbitration summary totals must match the sum of per-item consensus and arbitrated counts."""
        summary = verification_log["arbitration_summary"]
        items = verification_log["per_item_comparisons"]
        assert len(items) == 28

        consensus_in_items = sum(1 for it in items if it["verification_status"] == "CONSENSUS")
        arbitrated_in_items = sum(1 for it in items if it["verification_status"] == "ARBITRATED")

        assert consensus_in_items == summary["consensus_items_count"]
        assert arbitrated_in_items == summary["arbitrated_items_count"]
        assert consensus_in_items + arbitrated_in_items == 28


class TestArbitrationIntegrityAndDefectResolutions:
    """Verifies that known M1 defect challenges and payload-specific rulings are faithfully resolved."""

    def test_cand_01_resolves_national_development_reform_commission_order(
        self, benchmark_records: list[dict[str, Any]]
    ) -> None:
        """cand_01 (dd-501) must resolve the true order number and title in consensus."""
        cand = next(r for r in benchmark_records if r["id"] == "cand_01")
        gt = cand["ground_truth"]
        assert gt["doc_number"] == "国家发展改革委令第45号"
        assert "废止一批规章" in gt["title"]
        assert gt["published_at"] == "2026-07-23"
        assert gt["effective_at"] == "2026-10-01"
        assert cand["verification_status"] == "CONSENSUS"

    def test_cand_04_arbitrates_revision_decree_number_and_effective_date(
        self, benchmark_records: list[dict[str, Any]]
    ) -> None:
        """cand_04 (dd-502) must capture governing decree 238 and promulgation-grounded effective date."""
        cand = next(r for r in benchmark_records if r["id"] == "cand_04")
        gt = cand["ground_truth"]
        assert gt["doc_number"] == "河南省人民政府令第238号"
        assert gt["published_at"] == "2026-09-03"
        assert gt["effective_at"] == "2026-09-03"
        assert cand["verification_status"] == "ARBITRATED"
        assert "238号" in cand["arbitration_notes"]

    def test_cand_15_arbitrates_german_norm_identifier_and_effective_footnote(
        self, benchmark_records: list[dict[str, Any]]
    ) -> None:
        """cand_15 (dd-550) must establish BImSchV 1 2010 and footnote effective date 2010-03-22."""
        cand = next(r for r in benchmark_records if r["id"] == "cand_15")
        gt = cand["ground_truth"]
        assert gt["doc_number"] == "BImSchV 1 2010"
        assert gt["published_at"] == "2010-01-26"
        assert gt["effective_at"] == "2010-03-22"
        assert cand["verification_status"] == "ARBITRATED"

    def test_cand_19_arbitrates_omitted_statutory_number_with_fidelity(
        self, benchmark_records: list[dict[str, Any]]
    ) -> None:
        """cand_19 (dd-525) must retain doc_number as None adhering to raw payload fidelity."""
        cand = next(r for r in benchmark_records if r["id"] == "cand_19")
        gt = cand["ground_truth"]
        assert gt["doc_number"] is None
        assert gt["effective_at"] == "2007-11-01"
        assert cand["verification_status"] == "ARBITRATED"

    def test_reproducible_arbitration_pipeline_execution(self) -> None:
        """The run_arbitration function must execute idempotently and produce identical results."""
        verification_log, records = run_arbitration(
            candidates_path=DEFAULT_CANDIDATES_PATH,
            annotator_a_path=DEFAULT_ANNOTATOR_A_PATH,
            annotator_b_path=DEFAULT_ANNOTATOR_B_PATH,
        )
        assert len(records) == 28
        assert verification_log["overall_agreement_rate"] >= 0.80
        assert verification_log["arbitration_summary"]["consensus_items_count"] == 8
        assert verification_log["arbitration_summary"]["arbitrated_items_count"] == 20

    def test_benchmark_ground_truth_markdown_is_free_of_raw_binary_zip_headers_and_replacement_chars(
        self, benchmark_records: list[dict[str, Any]]
    ) -> None:
        """Every record's ground_truth.markdown must be clean text without raw ZIP/DOCX/XML or \\ufffd."""
        for rec in benchmark_records:
            rec_id = rec["id"]
            gt_md = rec["ground_truth"].get("markdown")
            assert isinstance(gt_md, str) and len(gt_md.strip()) >= 30, (
                f"{rec_id}: ground_truth.markdown must be at least 30 chars"
            )
            assert "PK\x03\x04" not in gt_md and "[Content_Types].xml" not in gt_md, (
                f"{rec_id}: ground_truth.markdown contains unextracted raw ZIP/DOCX binary bytes"
            )
            assert "<akomaNtoso" not in gt_md, (
                f"{rec_id}: ground_truth.markdown contains unextracted raw Akoma Ntoso XML tags"
            )
            assert "| ... |" not in gt_md, (
                f"{rec_id}: ground_truth.markdown contains abbreviated table placeholder '| ... |'"
            )
            assert "\ufffd" not in gt_md, (
                f"{rec_id}: ground_truth.markdown contains corrupted Unicode replacement character \\ufffd"
            )

    def test_first_28_records_align_with_cross_verified_arbitration_rulings(
        self, benchmark_records: list[dict[str, Any]]
    ) -> None:
        """Records cand_01..cand_28 in ground_truth_benchmark.jsonl must match cross-verified arbitration metadata."""
        _, arb_records = run_arbitration(
            candidates_path=DEFAULT_CANDIDATES_PATH,
            annotator_a_path=DEFAULT_ANNOTATOR_A_PATH,
            annotator_b_path=DEFAULT_ANNOTATOR_B_PATH,
        )
        arb_by_id = {r["id"]: r for r in arb_records}
        core_fields = ("title", "doc_number", "published_at", "effective_at", "issuer", "doc_type")
        for cid, arb_rec in arb_by_id.items():
            bench_rec = next(r for r in benchmark_records if r["id"] == cid)
            for field in core_fields:
                assert bench_rec["ground_truth"][field] == arb_rec["ground_truth"][field], (
                    f"{cid}.{field}: expected {arb_rec['ground_truth'][field]!r}, "
                    f"got {bench_rec['ground_truth'][field]!r}"
                )

