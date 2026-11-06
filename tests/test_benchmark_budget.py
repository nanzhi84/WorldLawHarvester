"""Real CLI contracts for complete-content budgets across local and Colab runs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from transformers import AutoTokenizer

from tests.test_benchmark_completion import (
    ROOT,
    TOKENIZERS,
    read_rows,
    select,
    task,
    write_rows,
)


@pytest.fixture(scope="session")
def budget_tokenizers() -> dict[str, Any]:
    return {
        label: cast(Any, AutoTokenizer).from_pretrained(
            model, revision=revision, local_files_only=True
        )
        for label, (model, revision) in TOKENIZERS.items()
    }


class TestBenchmarkOutputBudget:
    def test_long_markup_with_short_complete_content_is_admitted_without_input_rewriting(
        self, tmp_path: Path, budget_tokenizers: dict[str, Any]
    ) -> None:
        visible = (
            "前言必须保留。\n示例法规\n第一条 完整正文。\n类别 金额\n甲类 100元\n"
            "补充说明必须保留。\n附件 https://example.gov/files/law.pdf\n最后一条 完整结尾。"
        )
        payload = (
            "<html><head><style>"
            + ".layout { color: red; padding: 0; margin: 0; }\n" * 350
            + "</style><script>"
            + "window.metrics.push({event: 'page', debug: false});\n" * 350
            + "</script></head><body><header>前言必须保留。</header>"
            "<article><h1>示例法规</h1><p>第一条 完整正文。</p></article>"
            "<table><tr><th>类别</th><th>金额</th></tr><tr><td>甲类</td><td>100元</td>"
            "</tr></table><aside>补充说明必须保留。</aside>"
            '<a href="https://example.gov/files/law.pdf">附件</a>'
            "<footer>最后一条 完整结尾。</footer></body></html>"
        )
        source = task("rich-markup-complete-content", payload)
        result, selected, report_file = select(
            tmp_path, [source], "--max-output-tokens", "2048", "--local-files-only"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert selected.read_bytes() == (tmp_path / "input.jsonl").read_bytes()
        assert read_rows(selected) == [source]
        report = json.loads(report_file.read_text())
        counts = report["decisions"][0]["token_counts"]
        for label, tokenizer in budget_tokenizers.items():
            raw_count = len(tokenizer.encode(payload, add_special_tokens=False))
            visible_count = len(tokenizer.encode(visible, add_special_tokens=False))
            assert raw_count > 8192, "Fixture must exercise the old raw-payload false rejection"
            assert counts[label]["payload_tokens"] == raw_count
            assert counts[label]["prompt_tokens"] > raw_count
            assert visible_count <= counts[label]["required_output_tokens"] <= 2048

    def test_body_tables_long_links_and_all_json_records_cannot_hide_output_cost(
        self, tmp_path: Path, budget_tokenizers: dict[str, Any]
    ) -> None:
        paragraphs = [f"第{i}条 任何机关均应完整保留该项独立规定，不得删去。" for i in range(150)]
        links = [
            f"https://example.gov/laws/{i}/" + "complete-attachment-path-segment/" * 20
            for i in range(60)
        ]
        records = [
            {"title": f"第{i}项独立记录", "details": f"编号{i}的完整字段不得截断。" * 4}
            for i in range(80)
        ]
        sources = [
            task("safe-short"),
            task(
                "all-table-cells",
                "<article>本表各行均为法规条文。</article><table>"
                + "".join(f"<tr><td>{paragraph}</td></tr>" for paragraph in paragraphs)
                + "</table><footer>最后一行之后仍须完整保留。</footer>",
            ),
            {
                **task(
                    "all-long-links",
                    "<nav>"
                    + "".join(f'<a href="{url}">法规{i}</a>' for i, url in enumerate(links))
                    + "</nav>",
                ),
                "task_type": "list_page",
            },
            {
                **task("all-json-fields", json.dumps({"records": records}, ensure_ascii=False)),
                "mime_type": "application/json",
            },
        ]
        minimum_contents = {
            "all-table-cells": "\n".join(paragraphs),
            "all-long-links": "\n".join(links),
            "all-json-fields": "\n".join(row["details"] for row in records),
        }
        result, selected, report_file = select(
            tmp_path, sources, "--max-output-tokens", "2048", "--local-files-only"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert read_rows(selected) == [sources[0]], "Complete large outputs cannot fit 2048 tokens"
        decisions = {row["id"]: row for row in json.loads(report_file.read_text())["decisions"]}
        for sample_id, complete_content in minimum_contents.items():
            decision = decisions[sample_id]
            for label, tokenizer in budget_tokenizers.items():
                minimum = len(tokenizer.encode(complete_content, add_special_tokens=False))
                assert minimum > 2048, f"Fixture too short: {sample_id}:{label}"
                assert decision["token_counts"][label]["required_output_tokens"] >= minimum
                assert f"output_budget_exceeded:{label}" in decision["reasons"]


class TestBenchmarkBudgetConfiguration:
    def test_native_supported_budgets_prepare_the_same_complete_local_and_colab_bundle(
        self, tmp_path: Path
    ) -> None:
        source = task(
            "expanded-budget", "<article>" + "完整中文条文。" * 900 + "最后一条。</article>"
        )
        arguments = (
            "--context-tokens",
            "32768",
            "--max-output-tokens",
            "12288",
            "--safety-tokens",
            "768",
        )
        local, selected, local_report_path = select(
            tmp_path / "local", [source], *arguments, "--local-files-only"
        )
        assert local.returncode == 0, local.stdout + local.stderr
        assert read_rows(selected) == [source]
        bundle = tmp_path / "input.jsonl"
        write_rows(bundle, [source])
        colab_output = tmp_path / "colab"
        colab_report_path = tmp_path / "colab-report.json"
        command = [
            sys.executable,
            "-m",
            "wlh.benchmark.colab",
            "--prepare-only",
            "--input",
            str(bundle),
            "--output-dir",
            str(colab_output),
            "--selection-report",
            str(colab_report_path),
            *arguments,
        ]
        result = subprocess.run(
            command,
            cwd=tmp_path,
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
        (tmp_path / "colab-command-result.json").write_text(
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
        assert result.returncode == 0, result.stdout + result.stderr
        assert (colab_output / "selected.jsonl").read_bytes() == selected.read_bytes()
        local_report = json.loads(local_report_path.read_text())
        remote_report = json.loads(colab_report_path.read_text())
        assert remote_report["decisions"] == local_report["decisions"]
        assert remote_report["budget"] == local_report["budget"]
        budget = remote_report["budget"]
        assert budget["context_tokens"] == 32768
        assert budget["max_output_tokens"] == 12288
        assert budget["safety_tokens"] == 768
        assert isinstance(budget["output_policy"], str) and budget["output_policy"]
        for count in remote_report["decisions"][0]["token_counts"].values():
            assert count["context_tokens"] == 32768
            assert count["reserved_output_tokens"] == 12288
            assert count["safety_tokens"] == 768
