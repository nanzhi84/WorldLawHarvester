"""High-value black-box test suite for Benchmark CLI (`wlh benchmark run` and `sample`).

Adheres strictly to Universal Harness guidelines:
- Domain-named test file (tests/test_benchmark_cli.py).
- class Test<Subject>:
- def test_<scenario_or_condition>_<expected_behavior>():
- Zero mocks: uses real subprocess invocation and real filesystem I/O.
- Strict type checking compliance for pyright.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

SAMPLE_CANDIDATES_PATH = (
    Path("tests/fixtures/benchmark/candidates_sample.jsonl")
    if Path("tests/fixtures/benchmark/candidates_sample.jsonl").is_file()
    else Path("data/benchmark/candidates_sample.jsonl")
)


def run_wlh_cli(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Execute wlh CLI as a clean separate subprocess."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    cmd = [sys.executable, "-m", "wlh", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False, env=merged_env, timeout=60)


def run_benchmark_module(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Execute standalone python -m wlh.benchmark as a clean separate subprocess."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    cmd = [sys.executable, "-m", "wlh.benchmark", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False, env=merged_env, timeout=60)


def _create_sample_candidate(
    sample_id: str,
    source_id: str = "dd-501",
    title: str = "Test Title",
    doc_number: str | None = "No. 101",
    published_at: str | None = "2026-01-01",
    effective_at: str | None = "2026-01-01",
    issuer: str | None = "Ministry of Justice",
    doc_type: str = "regulation",
    markdown: str = "This is a clean substantive test excerpt for quantitative validation.",
) -> dict[str, Any]:
    """Helper to produce well-formed candidate dictionaries."""
    return {
        "id": sample_id,
        "source_id": source_id,
        "jurisdiction": "CN",
        "detail_url": f"https://example.gov/{sample_id}",
        "current_extracted": {
            "title": title,
            "doc_number": doc_number,
            "published_at": published_at,
            "effective_at": effective_at,
            "issuer": issuer,
            "doc_type": doc_type,
            "markdown": markdown,
        },
    }


# ==============================================================================
# Domain 1: Benchmark CLI Parser & Subcommand Registration
# ==============================================================================


class TestBenchmarkCliParser:
    """Black-box validation of benchmark CLI argument parsing and flags."""

    def test_wlh_benchmark_help_flag_displays_subcommands_and_exits_zero(self) -> None:
        """Invoking wlh benchmark --help displays subcommands and exits 0."""
        proc = run_wlh_cli(["benchmark", "--help"])
        assert proc.returncode == 0
        assert "run" in proc.stdout
        assert "sample" in proc.stdout
        assert "onboard" not in proc.stdout

    def test_wlh_benchmark_sample_help_flag_displays_sample_parameters_and_exits_zero(self) -> None:
        """Invoking wlh benchmark sample --help displays all sample extraction flags."""
        proc = run_wlh_cli(["benchmark", "sample", "--help"])
        assert proc.returncode == 0
        assert "--output-dir" in proc.stdout
        assert "--html" in proc.stdout
        assert "--csv" in proc.stdout

    def test_wlh_benchmark_run_help_flag_displays_evaluation_parameters_and_exits_zero(self) -> None:
        """Invoking wlh benchmark run --help displays evaluation flags."""
        proc = run_wlh_cli(["benchmark", "run", "--help"])
        assert proc.returncode == 0
        assert "--input" in proc.stdout
        assert "--ground-truth" in proc.stdout
        assert "--output-dir" in proc.stdout

    def test_wlh_benchmark_unknown_subcommand_returns_exit_code_two(self) -> None:
        """Providing an unknown subcommand to wlh benchmark returns exit code 2."""
        proc = run_wlh_cli(["benchmark", "invalid_sub"])
        assert proc.returncode == 2
        assert "invalid_arguments" in proc.stdout or "invalid" in proc.stderr or "unrecognized" in proc.stderr

    def test_wlh_benchmark_invalid_option_flag_returns_exit_code_two(self) -> None:
        """Providing an unrecognized option flag returns exit code 2."""
        proc = run_wlh_cli(["benchmark", "run", "--non-existent-flag"])
        assert proc.returncode == 2
        assert "invalid_arguments" in proc.stdout or "unrecognized" in proc.stderr


# ==============================================================================
# Domain 2: Benchmark CLI Run Execution & Reports
# ==============================================================================


class TestBenchmarkCliRunExecution:
    """Execution of wlh benchmark run evaluating input against ground truth."""

    def test_run_subcommand_evaluates_input_against_ground_truth_and_generates_reports(
        self, tmp_path: Path
    ) -> None:
        """Run subcommand evaluates candidate input against ground truth and writes reports."""
        in_file = tmp_path / "candidates.jsonl"
        gt_file = tmp_path / "ground_truth.jsonl"
        out_dir = tmp_path / "artifacts"

        cand = _create_sample_candidate("cand_01", "dd-501")
        in_file.write_text(json.dumps(cand) + "\n", encoding="utf-8")

        gt_record = {
            "id": "cand_01",
            "source_id": "dd-501",
            "jurisdiction": "CN",
            "detail_url": "https://example.gov/cand_01",
            "current_extracted": cand["current_extracted"],
            "ground_truth": {
                "title": "Test Title",
                "doc_number": "No. 101",
                "published_at": "2026-01-01",
                "effective_at": "2026-01-01",
                "issuer": "Ministry of Justice",
                "doc_type": "regulation",
                "markdown": "This is a clean substantive test excerpt for quantitative validation.",
            },
            "verification_status": "CONSENSUS",
            "arbitration_notes": "Authoritative",
        }
        gt_file.write_text(json.dumps(gt_record) + "\n", encoding="utf-8")

        proc = run_wlh_cli([
            "benchmark",
            "run",
            "--input",
            str(in_file),
            "--ground-truth",
            str(gt_file),
            "--output-dir",
            str(out_dir),
        ])
        assert proc.returncode == 0
        assert (out_dir / "benchmark_report.json").is_file()
        assert (out_dir / "benchmark_report.md").is_file()

    def test_run_subcommand_with_custom_output_dir_generates_json_and_markdown_reports(
        self, tmp_path: Path
    ) -> None:
        """Run subcommand writes reports to custom --output-dir path."""
        custom_out = tmp_path / "deeply" / "nested" / "reports"
        proc = run_wlh_cli([
            "benchmark",
            "run",
            "--input",
            str(SAMPLE_CANDIDATES_PATH),
            "--output-dir",
            str(custom_out),
        ])
        assert proc.returncode == 0
        assert (custom_out / "benchmark_report.json").is_file()
        assert (custom_out / "benchmark_report.md").is_file()

    def test_run_subcommand_defaults_to_repository_ground_truth_when_flag_omitted(
        self, tmp_path: Path
    ) -> None:
        """Run subcommand uses data/benchmark/ground_truth_benchmark.jsonl when --ground-truth is omitted."""
        out_dir = tmp_path / "reports_default_gt"
        proc = run_wlh_cli([
            "benchmark",
            "run",
            "--input",
            str(SAMPLE_CANDIDATES_PATH),
            "--output-dir",
            str(out_dir),
        ])
        assert proc.returncode == 0
        report_json = out_dir / "benchmark_report.json"
        assert report_json.is_file()
        report_data = json.loads(report_json.read_text(encoding="utf-8"))
        assert report_data["total_samples"] == 2
        assert report_data["average_accuracy_score"] is not None

    def test_sample_subcommand_extracts_candidates_and_manifest(
        self, tmp_path: Path
    ) -> None:
        """Sample subcommand extracts all 721 candidates and writes raw_candidates.jsonl and candidate_manifest.json."""
        out_dir = tmp_path / "sampled_candidates"
        proc = run_wlh_cli([
            "benchmark",
            "sample",
            "--output-dir",
            str(out_dir),
        ])
        assert proc.returncode == 0
        raw_cands = out_dir / "raw_candidates.jsonl"
        manifest = out_dir / "candidate_manifest.json"
        assert raw_cands.is_file()
        assert manifest.is_file()
        lines = [ln for ln in raw_cands.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == 721

    def test_run_subcommand_backward_compatibility_when_input_flag_passed_directly_to_benchmark(
        self, tmp_path: Path
    ) -> None:
        """Backwards compatibility: wlh benchmark --input ... defaults to evaluation workflow."""
        out_dir = tmp_path / "reports_compat"
        proc = run_wlh_cli([
            "benchmark",
            "--input",
            str(SAMPLE_CANDIDATES_PATH),
            "--output-dir",
            str(out_dir),
        ])
        assert proc.returncode == 0
        assert (out_dir / "benchmark_report.json").is_file()
        assert (out_dir / "benchmark_report.md").is_file()

    def test_standalone_module_execution_via_python_m_wlh_benchmark_run_exits_zero(
        self, tmp_path: Path
    ) -> None:
        """Standalone module execution via python -m wlh.benchmark run executes evaluation."""
        out_dir = tmp_path / "reports_module"
        proc = run_benchmark_module([
            "run",
            "--input",
            str(SAMPLE_CANDIDATES_PATH),
            "--output-dir",
            str(out_dir),
        ])
        assert proc.returncode == 0
        assert (out_dir / "benchmark_report.json").is_file()


# ==============================================================================
# Domain 3: 100% Offline Network Isolation for Evaluation
# ==============================================================================


class TestBenchmarkCliOfflineIsolation:
    """Verifies that evaluation runs 100% locally and initiates zero network requests."""

    def test_run_subcommand_succeeds_with_unreachable_blackhole_proxy(
        self, tmp_path: Path
    ) -> None:
        """Evaluation succeeds cleanly when pointing to unreachable blackhole proxy."""
        out_dir = tmp_path / "reports_offline"
        blackhole_env = {
            "http_proxy": "http://127.0.0.1:59999",
            "https_proxy": "http://127.0.0.1:59999",
        }
        proc = run_wlh_cli(
            [
                "benchmark",
                "run",
                "--input",
                str(SAMPLE_CANDIDATES_PATH),
                "--output-dir",
                str(out_dir),
            ],
            env=blackhole_env,
        )
        assert proc.returncode == 0, f"Benchmark run attempted network connection! stderr:\n{proc.stderr}"
        assert (out_dir / "benchmark_report.json").is_file()


# ==============================================================================
# Domain 4: Edge Cases, Error Handling & Exit Codes
# ==============================================================================


class TestBenchmarkCliEdgeCasesAndErrors:
    """Validation of error conditions, missing files, and exit codes."""

    def test_run_nonexistent_input_file_returns_clean_exit_code_two(self) -> None:
        """Run with non-existent input file returns exit code 2."""
        proc = run_wlh_cli(["benchmark", "run", "--input", "non_existent_path.jsonl"])
        assert proc.returncode == 2

    def test_run_nonexistent_ground_truth_file_returns_clean_exit_code_two(
        self, tmp_path: Path
    ) -> None:
        """Run with non-existent ground truth file returns exit code 2."""
        in_file = tmp_path / "candidates.jsonl"
        in_file.write_text(json.dumps(_create_sample_candidate("cand_01")) + "\n", encoding="utf-8")
        proc = run_wlh_cli([
            "benchmark",
            "run",
            "--input",
            str(in_file),
            "--ground-truth",
            "non_existent_gt.jsonl",
        ])
        assert proc.returncode == 2

    def test_benchmark_without_subcommand_or_input_returns_clean_exit_code_two(self) -> None:
        """Invoking wlh benchmark without a subcommand or --input returns exit code 2."""
        proc = run_wlh_cli(["benchmark"])
        assert proc.returncode == 2
