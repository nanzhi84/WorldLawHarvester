"""High-value black-box test suite for Issue #8 Processor Benchmark Suite.

Verifies the 5 evaluation pillars, batch bundle export, static preflight checks,
arbitration subcommands, and zero-dependency GPU annotator dry-run execution.

Adheres strictly to Universal Harness guidelines:
- Domain-named test file: tests/test_benchmark_processor_suite.py
- class Test<Subject>:
- def test_<scenario_or_condition>_<expected_behavior>():
- Zero mocks: uses real domain models, evaluation logic, and subprocess invocation.
- Strict type checking compliance for pyright.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from wlh.benchmark.arbitrator import merge_resolutions
from wlh.benchmark.evaluator import extract_with_processor
from wlh.benchmark.metrics import (
    evaluate_accuracy,
    evaluate_attachment_urls,
    evaluate_cleanliness,
    evaluate_content_fidelity,
    evaluate_list_discovery,
    evaluate_record_split,
)
from wlh.benchmark.models import (
    AccuracyResult,
    BenchmarkSummaryReport,
    ContentFidelityResult,
    DiscoveryResult,
    RecordSplitResult,
)
from wlh.benchmark.reporter import render_benchmark_markdown_report
from wlh.benchmark.sampler import run_preflight_checks
from wlh.benchmark.schema import (
    get_content_page_json_schema,
    get_list_page_json_schema,
    validate_xgrammar_schema,
)


def run_wlh_cli(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Execute wlh CLI as a clean separate subprocess."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    cmd = [sys.executable, "-m", "wlh", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False, env=merged_env)


class TestBenchmarkPillars:
    """Test suite covering the 5 quantitative evaluation pillars."""

    def test_list_discovery_metric_calculates_precision_recall_and_f1(self) -> None:
        """Pillar 1: List discovery evaluates URL precision, recall, and harmonic F1."""
        gt_urls = [
            "https://example.gov/doc/1",
            "https://example.gov/doc/2",
            "https://example.gov/doc/3",
            "https://example.gov/doc/4",
        ]
        # Predicted matches 3 of 4, plus 1 false positive
        pred_urls = [
            "https://example.gov/doc/1",
            "https://example.gov/doc/2",
            "https://example.gov/doc/3",
            "https://example.gov/doc/extra",
        ]

        res = evaluate_list_discovery(pred_urls, gt_urls)
        assert isinstance(res, DiscoveryResult)
        # Precision: 3 / 4 = 75.0, Recall: 3 / 4 = 75.0, F1: 75.0
        assert res.target_url_precision == 75.0
        assert res.target_url_recall == 75.0
        assert res.target_url_f1 == 75.0

    def test_record_split_metric_calculates_boundary_alignment(self) -> None:
        """Pillar 2: Record split evaluates boundary alignment for multi-record containers."""
        gt_records = [{"title": "Notice A"}, {"title": "Notice B"}, {"title": "Notice C"}]
        pred_records = [{"title": "Notice A"}, {"title": "Notice B"}, {"title": "Notice D"}]

        res, pairs = evaluate_record_split(pred_records, gt_records)
        assert isinstance(res, RecordSplitResult)
        assert len(pairs) == 2
        assert res.exact_count_match is True
        # Precision: 2/3 = 66.67, Recall: 2/3 = 66.67, F1 = 66.67
        assert round(res.record_split_f1, 2) == 66.67

    def test_attachment_urls_metric_evaluates_exact_url_overlap(self) -> None:
        """Pillar 3 Sub-metric: Attachment URL F1 correctly scores empty and non-empty URLs."""
        # Both empty evaluates to perfect 100.0
        _p, _r, f1 = evaluate_attachment_urls([], [])
        assert f1 == 100.0

        # Disjoint URLs evaluate to 0.0
        _p, _r, f1 = evaluate_attachment_urls(["https://a.pdf"], ["https://b.pdf"])
        assert f1 == 0.0

        # Partial overlap calculates F1
        gt_urls = ["https://site.gov/doc.pdf", "https://site.gov/annex.xlsx"]
        pred_urls = ["https://site.gov/doc.pdf"]
        # Prec: 1/1 = 1.0, Rec: 1/2 = 0.5 -> F1 = 2 * (1.0 * 0.5) / 1.5 = 66.67
        _p, _r, f1 = evaluate_attachment_urls(pred_urls, gt_urls)
        assert round(f1, 2) == 66.67

    def test_accuracy_metric_incorporates_attachment_url_f1(self) -> None:
        """Pillar 3: Metadata evaluation incorporates attachment_url_f1 alongside 6 core fields."""
        prediction = {
            "title": "Clean Law",
            "doc_number": "No. 1",
            "published_at": "2026-01-01",
            "effective_at": "2026-01-01",
            "issuer": "Department of Justice",
            "doc_type": "law",
            "attachment_urls": ["https://site.gov/file.pdf"],
        }
        gt = {
            "title": "Clean Law",
            "doc_number": "No. 1",
            "published_at": "2026-01-01",
            "effective_at": "2026-01-01",
            "issuer": "Department of Justice",
            "doc_type": "law",
            "attachment_urls": ["https://site.gov/file.pdf"],
        }
        acc = evaluate_accuracy(prediction, gt)
        assert isinstance(acc, AccuracyResult)
        assert acc.sub_scores.attachment_url_f1 == 100.0
        assert acc.score == 100.0

    def test_content_fidelity_metric_evaluates_exact_dom_char_recall_and_precision(self) -> None:
        """Pillar 4: Content DOM boundary fidelity scores character recall, precision, and F1."""
        gt_content = "Substantive law text here."

        # Case 1: Extracted text matches substantive content exactly
        pred_content = "Substantive law text here."
        fid_perfect = evaluate_content_fidelity(pred_content, gt_content)
        assert isinstance(fid_perfect, ContentFidelityResult)
        assert fid_perfect.content_char_recall == 100.0
        assert fid_perfect.content_char_precision == 100.0
        assert fid_perfect.content_f1 == 100.0

        # Case 2: Extracted text includes footer boilerplate (hurts precision)
        pred_polluted = "Substantive law text here. Footer"
        fid_polluted = evaluate_content_fidelity(pred_polluted, gt_content)
        assert fid_polluted.content_char_recall == 100.0
        assert fid_polluted.content_char_precision < 100.0
        assert fid_polluted.content_f1 < 100.0

    def test_cleanliness_metric_evaluates_five_dimensions(self) -> None:
        """Pillar 5: Text cleanliness evaluates 5 independent dimensions."""
        md = "# Pure Heading\n\nPure clean Markdown paragraph without tags or noise."
        clean = evaluate_cleanliness(md)
        assert clean.score == 100.0
        assert clean.sub_scores.noise_purity == 100.0
        assert clean.sub_scores.encoding_purity == 100.0
        assert clean.sub_scores.structural_integrity == 100.0
        assert clean.sub_scores.boilerplate_freedom == 100.0
        assert clean.sub_scores.deduplication_purity == 100.0


class TestProcessorBatchBundle:
    """Test suite covering batch bundle generation, schemas, and preflight verification."""

    def test_json_schemas_comply_with_xgrammar_and_vllm_constraints(self) -> None:
        """JSON schemas for list_page and content_page validate under strict xgrammar schema compiler."""
        list_schema = get_list_page_json_schema()
        assert list_schema["type"] == "object"
        assert "target_urls" in list_schema["properties"]
        assert validate_xgrammar_schema(list_schema) is True

        content_schema = get_content_page_json_schema()
        assert content_schema["type"] == "object"
        assert "records" in content_schema["properties"]
        assert validate_xgrammar_schema(content_schema) is True

    def test_preflight_checks_on_existing_batch_bundle_verifies_all_invariants(self) -> None:
        """Preflight verification passes on the canonical benchmark dataset."""
        preflight_res = run_preflight_checks()
        assert preflight_res["status"] == "passed"
        assert preflight_res["total_tasks"] > 0
        assert preflight_res["list_tasks"] > 0
        assert preflight_res["content_tasks"] > 0

    def test_benchmark_directory_contains_only_two_canonical_files(self) -> None:
        """data/benchmark/ contains strictly annotation_pipeline.json and ground_truth_benchmark.jsonl."""
        benchmark_dir = Path("data/benchmark")
        assert benchmark_dir.is_dir()
        entries = {p.name for p in benchmark_dir.iterdir() if not p.name.startswith(".")}
        assert entries == {"annotation_pipeline.json", "ground_truth_benchmark.jsonl"}

    def test_annotation_pipeline_ledger_validates_progressive_four_stage_schema(self) -> None:
        """annotation_pipeline.json validates progressive 4-stage lifecycle schema and exact counts."""
        ledger_path = Path("data/benchmark/annotation_pipeline.json")
        assert ledger_path.is_file()
        data: dict[str, Any] = json.loads(ledger_path.read_text(encoding="utf-8"))

        required_top_keys = {"schema_version", "policy", "budgets", "tokenizers", "summary", "items"}
        assert required_top_keys.issubset(set(data.keys()))

        summary = cast(dict[str, Any], data["summary"])
        assert summary["total_candidates"] == 668
        assert summary["stage1_admitted"] == 430
        assert summary["stage1_excluded"] == 238
        assert summary["stage2_paired_valid"] == 417
        assert summary["stage3_consensus"] == 101
        assert summary["stage3_arbitrated"] == 316
        assert summary["golden_set_exported"] == 417

        items = cast(list[dict[str, Any]], data["items"])
        assert len(items) == 668

        expected_item_keys = {
            "id",
            "task_type",
            "source_id",
            "jurisdiction",
            "request_url",
            "mime_type",
            "payload_sha256",
            "raw_payload",
            "lifecycle_status",
            "admission",
            "annotations",
            "arbitration",
        }

        counts_by_status: dict[str, int] = {
            "EXCLUDED_STAGE1": 0,
            "REJECTED_STAGE2": 0,
            "CONSENSUS": 0,
            "ARBITRATED": 0,
        }

        for item in items:
            assert expected_item_keys.issubset(set(item.keys()))
            status = str(item["lifecycle_status"])
            assert status in counts_by_status
            counts_by_status[status] += 1

            admission = cast(dict[str, Any], item["admission"])
            annotations = cast(dict[str, Any] | None, item["annotations"])
            arbitration = cast(dict[str, Any] | None, item["arbitration"])

            if status == "EXCLUDED_STAGE1":
                assert admission["selected"] is False
                assert annotations is None
                assert arbitration is None
            elif status == "REJECTED_STAGE2":
                assert admission["selected"] is True
                assert annotations is not None
                assert annotations["paired_valid"] is False
                assert arbitration is None
            elif status == "CONSENSUS":
                assert admission["selected"] is True
                assert annotations is not None
                assert annotations["paired_valid"] is True
                assert arbitration is not None
                assert arbitration["status"] == "CONSENSUS"
                assert "authoritative_ground_truth" in arbitration
            elif status == "ARBITRATED":
                assert admission["selected"] is True
                assert annotations is not None
                assert annotations["paired_valid"] is True
                assert arbitration is not None
                assert arbitration["status"] == "ARBITRATED"
                assert "authoritative_ground_truth" in arbitration

        assert counts_by_status["EXCLUDED_STAGE1"] == 238
        assert counts_by_status["REJECTED_STAGE2"] == 13
        assert counts_by_status["CONSENSUS"] == 101
        assert counts_by_status["ARBITRATED"] == 316

    def test_ground_truth_benchmark_golden_set_contains_pure_fields_without_process_metadata(self) -> None:
        """ground_truth_benchmark.jsonl contains 417 pure golden-set items with zero process metadata."""
        gt_path = Path("data/benchmark/ground_truth_benchmark.jsonl")
        assert gt_path.is_file()

        rows: list[dict[str, Any]] = []
        with gt_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(cast(dict[str, Any], json.loads(line)))

        assert len(rows) == 417
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
        for row in rows:
            assert "verification_status" not in row
            assert "arbitration_notes" not in row
            assert "adjudication_metadata" not in row
            assert set(row.keys()) == expected_keys


class TestBenchmarkCliSubcommands:
    """Subprocess black-box verification of benchmark CLI subcommands."""

    def test_preflight_subcommand_via_cli_executes_successfully(self) -> None:
        """CLI: `wlh benchmark preflight` runs and reports passed status."""
        proc = run_wlh_cli(["benchmark", "preflight"])
        assert proc.returncode == 0
        assert "passed" in proc.stdout

    def test_dry_run_annotator_script_executes_successfully(self, tmp_path: Path) -> None:
        """Zero-dependency RTX PRO 6000 annotator script executes dry-run smoke test and batching."""
        out_dir = tmp_path / "isolated_annotations"
        cmd = [
            sys.executable,
            "scripts/run_rtx_pro6000_annotator.py",
            "--dry-run",
            "--output-dir",
            str(out_dir),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert proc.returncode == 0
        assert "Smoke Test: PASSED" in proc.stdout
        assert "Dual-blind batch annotation completed successfully" in proc.stdout
        assert (out_dir / "annotator_a.jsonl").is_file()
        assert (out_dir / "annotator_b.jsonl").is_file()


class TestProcessorOfflineExtraction:
    """Black-box verification of offline processor extraction."""

    def test_extract_with_processor_on_real_list_and_content_tasks(self) -> None:
        """extract_with_processor executes offline extraction on real tasks without crashes."""
        bundle_path = Path("data/benchmark/ground_truth_benchmark.jsonl")
        assert bundle_path.is_file()

        list_task: dict[str, Any] | None = None
        content_task: dict[str, Any] | None = None
        with bundle_path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                if item.get("task_type") == "list_page" and list_task is None:
                    list_task = item
                elif item.get("task_type") == "content_page" and content_task is None:
                    content_task = item
                if list_task and content_task:
                    break

        assert list_task is not None
        assert content_task is not None

        # Test list_page extraction
        list_pred = extract_with_processor(list_task)
        assert isinstance(list_pred, dict)
        assert "target_urls" in list_pred
        target_urls = cast(list[object], list_pred["target_urls"])
        assert len(target_urls) > 0

        # Test content_page extraction
        content_pred = extract_with_processor(content_task)
        assert isinstance(content_pred, dict)
        assert "records" in content_pred
        records = cast(list[dict[str, Any]], content_pred["records"])
        assert len(records) > 0
        first_record = records[0]
        assert "markdown" in first_record
        assert len(str(first_record["markdown"])) > 0
        assert first_record.get("title") is not None


class TestBenchmarkArbitrationAndReporting:
    """Black-box verification of arbitration resolution merging and report rendering."""

    def test_merge_resolutions_produces_valid_adjudication_metadata(self, tmp_path: Path) -> None:
        """merge_resolutions builds records containing valid SHA-256 adjudication_metadata."""
        annotator_a_path = tmp_path / "annotator_a.jsonl"
        annotator_b_path = tmp_path / "annotator_b.jsonl"
        res_path = tmp_path / "resolutions.jsonl"
        out_path = tmp_path / "ground_truth.jsonl"

        task_a = {
            "id": "content_test_01",
            "task_type": "content_page",
            "source_id": "dd-017",
            "jurisdiction": "CN",
            "request_url": "https://example.gov/doc/1",
            "ground_truth": {
                "records": [
                    {
                        "doc_type": "案例",
                        "title": "测试案例",
                        "markdown": "# 测试案例\n\n正文",
                    }
                ]
            },
        }
        task_b = {
            "id": "content_test_01",
            "task_type": "content_page",
            "source_id": "dd-017",
            "jurisdiction": "CN",
            "request_url": "https://example.gov/doc/1",
            "ground_truth": {
                "records": [
                    {
                        "doc_type": "案例",
                        "title": "测试案例（不同标题）",
                        "markdown": "# 测试案例\n\n正文",
                    }
                ]
            },
        }
        resolution = {
            "id": "content_test_01",
            "task_type": "content_page",
            "source_id": "dd-017",
            "jurisdiction": "CN",
            "request_url": "https://example.gov/doc/1",
            "ground_truth": {
                "records": [
                    {
                        "doc_type": "案例",
                        "title": "测试案例",
                        "markdown": "# 测试案例\n\n正文",
                    }
                ]
            },
            "arbitration_notes": "Arbitrated by Gemini 3.8 Flash based on raw payload bytes.",
        }

        annotator_a_path.write_text(json.dumps(task_a) + "\n", encoding="utf-8")
        annotator_b_path.write_text(json.dumps(task_b) + "\n", encoding="utf-8")
        res_path.write_text(json.dumps(resolution) + "\n", encoding="utf-8")

        merged = merge_resolutions(
            annotator_a_path=annotator_a_path,
            annotator_b_path=annotator_b_path,
            resolutions_path=res_path,
            output_path=out_path,
        )

        assert len(merged) == 1
        rec = merged[0]
        assert rec["id"] == "content_test_01"
        assert rec["verification_status"] == "ARBITRATED"
        assert "adjudication_metadata" in rec
        meta = rec["adjudication_metadata"]
        assert "content_hash" in meta
        assert len(meta["content_hash"]) == 64
        assert meta["payload_sha256"] == meta["content_hash"]

    def test_render_benchmark_markdown_report_renders_all_five_pillars(self) -> None:
        """render_benchmark_markdown_report renders all 5 evaluation pillars in executive score card."""
        rep = BenchmarkSummaryReport(
            total_samples=10,
            evaluated_at="2026-11-03T00:00:00Z",
            average_overall_score=94.5,
            average_list_discovery_f1=98.0,
            average_record_split_f1=96.0,
            average_accuracy_score=95.0,
            average_content_fidelity_score=92.0,
            average_cleanliness_score=97.5,
        )
        md = render_benchmark_markdown_report(rep)
        assert "## 1. Executive Score Card" in md
        assert "Pillar 1: List Discovery F1" in md
        assert "Pillar 2: Record Split F1" in md
        assert "Pillar 3: Metadata & Attachment Accuracy" in md
        assert "Pillar 4: Content DOM Boundary Fidelity" in md
        assert "Pillar 5: Text Cleanliness (干净度)" in md

