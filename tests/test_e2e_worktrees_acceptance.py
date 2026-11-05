"""End-to-end blackbox acceptance test suite for worktrees merge verification.

Validates that:
1. All merged code conforms strictly to AGENTS.md repository and testing conventions.
2. `harness doctor` executes cleanly and reports zero structural, link, or lifecycle issues.
3. Benchmark CLI can be invoked cleanly in standalone subprocess execution with valid quantitative reporting.
4. Core engines remain completely decoupled from site-specific selectors.

Zero mocking of internal implementations — uses real subprocess calls and real filesystem I/O.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


def _run_subprocess(args: list[str], cwd: Path = ROOT_DIR) -> subprocess.CompletedProcess[str]:
    """Execute a command as a clean separate subprocess."""
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


class TestUniversalHarnessConventionsAcceptance:
    """Acceptance tests verifying repository conventions governed by AGENTS.md."""

    def test_repository_root_layout_contains_prescribed_directories_and_files(self) -> None:
        """The repository root contains all mandatory files and directory sandboxes specified in AGENTS.md § 2."""
        mandatory_files = ["README.md", "PROJECTS.md", "AGENTS.md", "CHANGELOG.md"]
        mandatory_dirs = ["tests", "work", "artifacts"]

        for fname in mandatory_files:
            fpath = ROOT_DIR / fname
            assert fpath.is_file(), f"Mandatory repository file missing: {fname}"

        for dname in mandatory_dirs:
            dpath = ROOT_DIR / dname
            assert dpath.is_dir(), f"Mandatory directory sandbox missing: {dname}"

    def test_test_suite_conforms_to_class_and_method_naming_structure(self) -> None:
        """Every test file matches test_<domain>.py, every test class starts with Test, and no library names leak into test names."""
        tests_dir = ROOT_DIR / "tests"
        test_files = sorted(tests_dir.glob("test_*.py"))
        assert len(test_files) >= 15, "Expected a substantial test suite in tests/"

        domain_pattern = re.compile(r"^test_[a-z0-9_]+\.py$")
        forbidden_libraries = {"pytest", "unittest", "docx", "procrastinate", "mock"}

        violations: list[str] = []

        for py_file in test_files:
            if not domain_pattern.match(py_file.name):
                violations.append(f"Invalid domain test file name: {py_file.name}")

            # Disallow phase or ordinal naming like test_phase1.py or test1.py
            if re.match(r"^test_phase\d+", py_file.stem) or re.match(r"^test\d+", py_file.stem):
                violations.append(f"Ordinal/phase naming forbidden in test file: {py_file.name}")

            source_code = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source_code, filename=str(py_file))

            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    has_tests = any(
                        isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and m.name.startswith("test_")
                        for m in node.body
                    )
                    if has_tests:
                        if not node.name.startswith("Test"):
                            violations.append(
                                f"Test class '{node.name}' in {py_file.name} must start with 'Test'"
                            )
                        for lib in forbidden_libraries:
                            if lib in node.name.lower():
                                violations.append(
                                    f"Test class '{node.name}' in {py_file.name} contains forbidden library name '{lib}'"
                                )
                        for m in node.body:
                            if isinstance(
                                m, (ast.FunctionDef, ast.AsyncFunctionDef)
                            ) and m.name.startswith("test_"):
                                for lib in forbidden_libraries:
                                    if lib in m.name.lower():
                                        violations.append(
                                            f"Test method '{m.name}' in {py_file.name} contains forbidden library name '{lib}'"
                                        )
                elif isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and node.name.startswith("test_"):
                    violations.append(
                        f"Standalone test function '{node.name}' in {py_file.name} must be enclosed in a 'class Test...:'"
                    )

        assert not violations, "AGENTS.md test convention violations found:\n" + "\n".join(
            violations
        )

    def test_source_configuration_files_conform_to_declarative_schema_contracts(self) -> None:
        """All declarative source definitions under sources/ conform to dd-<id>.yaml naming and load cleanly."""
        from wlh.config import load_file

        sources_dir = ROOT_DIR / "sources"
        source_files = sorted(sources_dir.glob("dd-*.yaml"))
        assert len(source_files) >= 20, (
            f"Expected at least 20 source definitions, found {len(source_files)}"
        )

        dd_pattern = re.compile(r"^dd-\d{3}\.yaml$")
        for sfile in source_files:
            assert dd_pattern.match(sfile.name), f"Invalid source config file naming: {sfile.name}"
            config = load_file(sfile)
            assert config.id.startswith("dd-"), f"Source ID mismatch in {sfile.name}: {config.id}"
            assert config.storage.archive_approved is True, (
                f"Source {sfile.name} must have archive_approved: true"
            )


class TestHarnessDoctorAcceptance:
    """Acceptance tests verifying the repository doctor inspection."""

    def test_harness_doctor_cli_reports_zero_structural_or_link_issues(self) -> None:
        """Invoking `harness doctor` passes with return code 0 and discovers zero broken links or lifecycle violations."""
        result = _run_subprocess(["harness", "doctor"])
        assert result.returncode == 0, (
            f"`harness doctor` failed with code {result.returncode}:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        assert (
            "0 issue(s)" in result.stdout
            or "all structure, lifecycle, text, index, and link checks passed" in result.stdout
        ), f"`harness doctor` unexpected output:\n{result.stdout}"


class TestBenchmarkCliAcceptance:
    """Acceptance tests verifying quantitative benchmark CLI execution."""

    def test_benchmark_cli_help_displays_subcommands_and_usage(self) -> None:
        """Invoking `python -m wlh.benchmark --help` displays run and sample subcommands with return code 0."""
        result = _run_subprocess([sys.executable, "-m", "wlh.benchmark", "--help"])
        assert result.returncode == 0, (
            f"Help command exited with code {result.returncode}:\n{result.stderr}"
        )
        assert "run" in result.stdout
        assert "sample" in result.stdout

    def test_benchmark_run_without_required_input_exits_with_informative_error(self) -> None:
        """Invoking `python -m wlh.benchmark run` without `--input` fails with exit code 2 and usage instructions."""
        result = _run_subprocess([sys.executable, "-m", "wlh.benchmark", "run"])
        assert result.returncode != 0, (
            "Expected non-zero exit code when required --input is omitted"
        )
        assert "--input" in result.stderr or "--input" in result.stdout

    def test_benchmark_sample_evaluation_pipeline_produces_valid_reports_with_threshold_scores(
        self, tmp_path: Path
    ) -> None:
        """Running the benchmark against candidates_sample.jsonl executes successfully and outputs valid JSON and Markdown reports."""
        fixture_path = ROOT_DIR / "tests" / "fixtures" / "benchmark" / "candidates_sample.jsonl"
        assert fixture_path.is_file(), f"Deterministic sample fixture missing at {fixture_path}"

        output_dir = tmp_path / "benchmark_out"
        output_dir.mkdir(parents=True, exist_ok=True)

        result = _run_subprocess(
            [
                sys.executable,
                "-m",
                "wlh.benchmark",
                "run",
                "--input",
                str(fixture_path),
                "--output-dir",
                str(output_dir),
            ]
        )

        assert result.returncode == 0, (
            f"Benchmark sample run failed with code {result.returncode}:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

        json_report_path = output_dir / "benchmark_report.json"
        md_report_path = output_dir / "benchmark_report.md"

        assert json_report_path.is_file(), "benchmark_report.json was not generated"
        assert md_report_path.is_file(), "benchmark_report.md was not generated"

        report_data = json.loads(json_report_path.read_text(encoding="utf-8"))
        total_samples = report_data.get("total_samples") or report_data.get("total_evaluated")
        assert total_samples == 2, f"Expected 2 evaluated samples, got {total_samples}"

        overall_score = float(
            report_data.get("average_overall_score") or report_data.get("overall_score") or 0.0
        )
        cleanliness_score = float(
            report_data.get("average_cleanliness_score")
            or report_data.get("cleanliness_score")
            or 0.0
        )
        accuracy_score = float(
            report_data.get("average_accuracy_score") or report_data.get("accuracy_score") or 0.0
        )

        assert overall_score >= 50.0, f"Overall score {overall_score} below threshold 50.0"
        assert cleanliness_score >= 80.0, (
            f"Cleanliness score {cleanliness_score} below threshold 80.0"
        )
        assert accuracy_score >= 40.0, f"Accuracy score {accuracy_score} below threshold 40.0"

        md_content = md_report_path.read_text(encoding="utf-8")
        assert "Benchmark Evaluation Report" in md_content
        assert "Cleanliness Score" in md_content
        assert "Accuracy Score" in md_content


class TestWorktreesDecouplingAcceptance:
    """Acceptance tests verifying architectural decoupling of universal core engines."""

    def test_core_extractor_modules_contain_zero_site_specific_selectors(self) -> None:
        """Core HTML extractor modules do not contain leaked domain-specific or site-specific tokens."""
        core_files = [
            ROOT_DIR / "src" / "wlh" / "processor" / "extractors" / "html" / "dom_tree.py",
            ROOT_DIR / "src" / "wlh" / "processor" / "parsers" / "html" / "dom_cleanup.py",
            ROOT_DIR / "src" / "wlh" / "cleaners" / "text_cleaner.py",
        ]

        site_specific_tokens = [
            "courts.go.jp",
            "moj.go.jp",
            "judicial.gov.tw",
            "kfs.go.jp",
            "nta.go.jp",
            "gov.cn",
            "tax.gov",
        ]

        violations: list[str] = []
        for cfile in core_files:
            if not cfile.is_file():
                continue
            text = cfile.read_text(encoding="utf-8")
            for token in site_specific_tokens:
                if token in text:
                    violations.append(
                        f"Forbidden site token '{token}' found in core module {cfile.name}"
                    )

        assert not violations, "Architectural decoupling violations in core modules:\n" + "\n".join(
            violations
        )
