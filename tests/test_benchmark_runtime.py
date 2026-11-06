"""Offline CLI acceptance of bounded runtime resource settings before GPU allocation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.test_benchmark_completion import ROOT, task, write_rows


def prepare(
    directory: Path, entrypoint: str, source: dict[str, Any], *arguments: str
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    bundle = directory / "input.jsonl"
    output = directory / "prepared"
    selected = output / "selected.jsonl"
    report = output / "selection.json"
    write_rows(bundle, [source])
    mode = (
        ["--prepare-only"]
        if entrypoint == "colab"
        else ["--select-only", "--selected-bundle", str(selected), "--local-files-only"]
    )
    command = [
        sys.executable,
        "-m",
        f"wlh.benchmark.{entrypoint}",
        *mode,
        "--input",
        str(bundle),
        "--output-dir",
        str(output),
        "--selection-report",
        str(report),
        *arguments,
    ]
    result = subprocess.run(
        command,
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=40,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "CUDA_VISIBLE_DEVICES": "",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
        },
    )
    (directory / "command-result.json").write_text(
        json.dumps(
            {
                "argv": command,
                "exit": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return result, selected, report


class TestBenchmarkRuntimeConfiguration:
    @pytest.mark.parametrize("entrypoint", ["annotator", "colab"])
    def test_cpu_offload_defaults_and_supported_values_preserve_input_and_budget(
        self, tmp_path: Path, entrypoint: str
    ) -> None:
        source = task(
            "complete-runtime-input",
            "<article><h1>示例法规</h1><p>第一条 原文必须完整保留。</p>"
            "<p>最后一条 不得因运行参数改变而裁剪。</p></article>",
        )
        baseline, selected, report_path = prepare(tmp_path / "default", entrypoint, source)
        assert baseline.returncode == 0, baseline.stdout + baseline.stderr
        default_report = json.loads(report_path.read_text())
        assert default_report["runtime"]["cpu_offload_gb"] == 0.0
        assert default_report["runtime"]["kv_cache_dtype"] == "auto"
        assert default_report["runtime"]["batch_size"] == 4
        assert selected.read_bytes() == (tmp_path / "default/input.jsonl").read_bytes()
        for amount in ("16.0", "32.0"):
            result, configured, configured_report_path = prepare(
                tmp_path / amount,
                entrypoint,
                source,
                "--cpu-offload-gb",
                amount,
                "--kv-cache-dtype",
                "fp8",
                "--batch-size",
                "2",
            )
            assert result.returncode == 0, result.stdout + result.stderr
            configured_report = json.loads(configured_report_path.read_text())
            assert configured.read_bytes() == selected.read_bytes()
            assert configured_report["budget"] == default_report["budget"]
            assert configured_report["decisions"] == default_report["decisions"]
            assert configured_report["runtime"]["cpu_offload_gb"] == float(amount)
            assert configured_report["runtime"]["kv_cache_dtype"] == "fp8"
            assert configured_report["runtime"]["batch_size"] == 2

    @pytest.mark.parametrize("entrypoint", ["annotator", "colab"])
    @pytest.mark.parametrize("amount", ["-0.01", "nan", "inf", "-inf", "32.01"])
    def test_invalid_cpu_offload_is_rejected_before_creating_a_selected_bundle(
        self, tmp_path: Path, entrypoint: str, amount: str
    ) -> None:
        result, selected, _ = prepare(
            tmp_path, entrypoint, task("invalid-runtime"), f"--cpu-offload-gb={amount}"
        )
        assert result.returncode != 0
        errors = result.stdout + result.stderr
        assert "unrecognized arguments" not in errors, "The CLI must validate the supported option"
        assert "error" in errors.lower(), errors
        assert "Starting pass" not in errors
        assert not selected.exists() or not selected.read_text().strip()
