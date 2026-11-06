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

import csv
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

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
    ContentFidelityResult,
    DiscoveryResult,
    RecordSplitResult,
)
from wlh.benchmark.sampler import export_processor_batch_bundle, run_preflight_checks
from wlh.benchmark.schema import (
    get_content_page_json_schema,
    get_list_page_json_schema,
    validate_xgrammar_schema,
)


def run_wlh_cli(
    args: list[str], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Execute wlh CLI as a clean separate subprocess."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    cmd = [sys.executable, "-m", "wlh", *args]
    return subprocess.run(
        cmd, capture_output=True, text=True, check=False, env=merged_env, timeout=60
    )


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

    def test_benchmark_excludes_synthetic_contract_test_stubs(self, tmp_path: Path) -> None:
        """Benchmark datasets and batch bundle export exclude synthetic contract test stubs and only include registered Source IDs."""
        synthetic_stubs = {
            "dd-910",
            "dd-911",
            "dd-912",
            "dd-913",
            "dd-914",
            "dd-915",
            "dd-997",
        }

        matrix_path = Path("docs/runbooks/source-adaptation-matrix.csv")
        assert matrix_path.is_file()
        with matrix_path.open("r", encoding="utf-8-sig") as f:
            registered_source_ids = {
                str(r["Source ID"]).strip() for r in csv.DictReader(f) if r.get("Source ID")
            }
        assert registered_source_ids.isdisjoint(synthetic_stubs)

        ledger_path = Path("data/benchmark/annotation_pipeline.json")
        assert ledger_path.is_file()
        ledger_data: dict[str, Any] = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger_items = cast(list[dict[str, Any]], ledger_data["items"])
        ledger_source_ids = {str(item["source_id"]) for item in ledger_items}
        assert ledger_source_ids.isdisjoint(synthetic_stubs)
        assert ledger_source_ids.issubset(registered_source_ids)

        gt_path = Path("data/benchmark/ground_truth_benchmark.jsonl")
        assert gt_path.is_file()
        gt_source_ids: set[str] = set()
        with gt_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    row = cast(dict[str, Any], json.loads(line))
                    gt_source_ids.add(str(row["source_id"]))
        assert gt_source_ids.isdisjoint(synthetic_stubs)
        assert gt_source_ids.issubset(registered_source_ids)

        tasks = export_processor_batch_bundle(output_path=tmp_path / "bundle.jsonl")
        bundle_source_ids = {t.source_id for t in tasks}
        assert bundle_source_ids.isdisjoint(synthetic_stubs)
        assert bundle_source_ids.issubset(registered_source_ids)


class TestBenchmarkCliSubcommands:
    """Subprocess black-box verification of benchmark CLI subcommands."""

    def test_preflight_subcommand_via_cli_executes_successfully(self) -> None:
        """CLI: `wlh benchmark preflight` runs and reports passed status."""
        proc = run_wlh_cli(["benchmark", "preflight"])
        assert proc.returncode == 0
        assert "passed" in proc.stdout

    def test_dry_run_annotator_module_executes_successfully(self, tmp_path: Path) -> None:
        """Annotator module executes dry-run smoke test and batching from an isolated directory."""
        root = Path(__file__).resolve().parents[1]
        out_dir = tmp_path / "isolated_annotations"
        cmd = [
            sys.executable,
            "-m",
            "wlh.benchmark.annotator",
            "--dry-run",
            "--input",
            str(root / "data/benchmark/ground_truth_benchmark.jsonl"),
            "--output-dir",
            str(out_dir),
        ]
        env = {**os.environ, "PYTHONPATH": str(root / "src")}
        proc = subprocess.run(
            cmd, cwd=tmp_path, env=env, capture_output=True, text=True, check=False, timeout=60
        )
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


class TestBenchmarkStratifiedSplits:
    """Black-box verification of explicit dev/test membership and filtering."""

    def test_evaluate_benchmark_dataset_filters_by_dev_and_test_splits(self) -> None:
        """evaluate_benchmark_dataset supports split='dev', 'test', and 'all', and rejects invalid splits."""
        import pytest

        from wlh.benchmark.evaluator import evaluate_benchmark_dataset

        synthetic_records = [
            {
                "id": f"content_dd-501_0{i}",
                "task_type": "content_page",
                "source_id": "dd-501",
                "jurisdiction": "CN",
                "split": "dev" if i <= 3 else "test",
                "current_extracted": {
                    "title": f"测试法规 {i}",
                    "doc_type": "法律法规",
                    "markdown": f"# 测试法规 {i}\n\n第一条 规范内容 {i}。",
                },
                "ground_truth": {
                    "records": [
                        {
                            "title": f"测试法规 {i}",
                            "doc_type": "法律法规",
                            "markdown": f"# 测试法规 {i}\n\n第一条 规范内容 {i}。",
                        }
                    ]
                },
            }
            for i in range(1, 6)
        ]

        rep_all = evaluate_benchmark_dataset(synthetic_records, split="all")
        rep_dev = evaluate_benchmark_dataset(synthetic_records, split="dev")
        rep_test = evaluate_benchmark_dataset(synthetic_records, split="test")

        assert rep_all.total_samples == 5
        assert rep_dev.total_samples == 3
        assert rep_test.total_samples == 2
        assert rep_all.split_scores["dev"]["sample_count"] == 3
        assert rep_all.split_scores["test"]["sample_count"] == 2

        with pytest.raises(ValueError, match="Unsupported benchmark split"):
            evaluate_benchmark_dataset(synthetic_records, split="invalid_split")

        # Verify list-only multi-sample source stratification and sample_id / external GT metadata enrichment
        from wlh.benchmark.sampler import assign_stratified_splits

        list_only_records = [
            {"sample_id": "list_dd-501_01", "task_type": "list_page", "source_id": "dd-501"},
            {"sample_id": "list_dd-501_02", "task_type": "list_page", "source_id": "dd-501"},
        ]
        list_splits = assign_stratified_splits(list_only_records)
        assert set(list_splits.values()) == {"dev", "test"}

        sample_id_predictions = [
            {
                "sample_id": f"content_dd-501_0{i}",
                "current_extracted": {
                    "title": f"测试法规 {i}",
                    "doc_type": "法律法规",
                    "markdown": f"# 测试法规 {i}\n\n第一条 规范内容 {i}。",
                },
            }
            for i in range(1, 3)
        ]
        external_gt = [
            {
                "id": f"content_dd-501_0{i}",
                "source_id": "dd-501",
                "jurisdiction": "CN",
                "task_type": "content_page",
                "split": "dev" if i == 1 else "test",
                "ground_truth": {
                    "records": [
                        {
                            "title": f"测试法规 {i}",
                            "doc_type": "法律法规",
                            "markdown": f"# 测试法规 {i}\n\n第一条 规范内容 {i}。",
                        }
                    ]
                },
            }
            for i in range(1, 3)
        ]
        rep_ext_test = evaluate_benchmark_dataset(
            sample_id_predictions,
            ground_truth_path_or_records=external_gt,
            split="test",
        )
        assert rep_ext_test.total_samples == 1
        assert rep_ext_test.sample_results[0].sample_id in {
            "content_dd-501_01",
            "content_dd-501_02",
        }
        assert rep_ext_test.sample_results[0].split == "test"
        assert rep_ext_test.split_scores["test"]["sample_count"] == 1
        assert "dev" not in rep_ext_test.split_scores
