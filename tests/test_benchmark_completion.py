"""Offline CLI acceptance of whole-input selection and complete inference receipts.

These are direct file/CLI contracts using cached public tokenizers. All fixtures
are synthetic text; no database, object store, GPU or model weights are used.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
ANNOTATOR_MODULE = "wlh.benchmark.annotator"
PAYLOAD = "<article><h1>示例法规</h1><p>第一条：完整保留中文正文。</p></article>"
MARKDOWN = "# 示例法规\n\n第一条：完整保留中文正文。"
TOKENIZERS = {
    "A": ("Qwen/Qwen3.8-27B", "1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0"),
    "B": ("google/gemma-4-31B-it", "842da3794eaa0b77d5f08bae87a17459d91ff475"),
}


@pytest.fixture(scope="session")
def wlh_env() -> dict[str, str]:
    """This offline file contract does not use the global DB/S3 integration fixture."""
    return {}


def task(task_id: str, payload: str = PAYLOAD) -> dict[str, Any]:
    return {
        "id": task_id,
        "task_type": "content_page",
        "source_id": "dd-999",
        "jurisdiction": "CN",
        "request_url": "https://example.gov/legal/document",
        "mime_type": "text/html",
        "raw_payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def read_rows(path: Path) -> list[dict[str, Any]]:
    assert path.is_file(), f"Missing CLI artifact: {path}"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def run_cli(
    directory: Path, *args: str, empty_tokenizer_cache: bool = False
) -> subprocess.CompletedProcess[str]:
    directory.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        PYTHONPATH=str(ROOT / "src"),
        CUDA_VISIBLE_DEVICES="",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        HF_DATASETS_OFFLINE="1",
    )
    if empty_tokenizer_cache:
        cache = str(directory / "empty-tokenizer-cache")
        env.update(
            HF_HOME=cache, HF_HUB_CACHE=cache, HUGGINGFACE_HUB_CACHE=cache, TRANSFORMERS_CACHE=cache
        )
    command = [sys.executable, "-m", ANNOTATOR_MODULE, *args]
    result = subprocess.run(
        command, cwd=directory, env=env, capture_output=True, text=True, timeout=40
    )
    (directory / "command-result.json").write_text(
        json.dumps(
            {
                "argv": command,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return result


def select(
    directory: Path,
    rows: list[dict[str, Any]],
    *budget_args: str,
    empty_tokenizer_cache: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    bundle = directory / "input.jsonl"
    selected = directory / "selected.jsonl"
    report = directory / "selection-report.json"
    write_rows(bundle, rows)
    result = run_cli(
        directory,
        "--select-only",
        "--input",
        str(bundle),
        "--selected-bundle",
        str(selected),
        "--selection-report",
        str(report),
        "--output-dir",
        str(directory / "annotations"),
        *budget_args,
        empty_tokenizer_cache=empty_tokenizer_cache,
    )
    return result, selected, report


def ground_truth(markdown: Any = MARKDOWN) -> dict[str, Any]:
    return {
        "records": [
            {
                "doc_type": "法律法规",
                "markdown": markdown,
                "title": "示例法规",
                "doc_number": None,
                "published_at": None,
                "effective_at": None,
                "issuer": None,
                "attachment_urls": [],
            }
        ]
    }


def receipt(source: dict[str, Any], **changes: Any) -> dict[str, Any]:
    row = {
        "id": source["id"],
        "payload_sha256": source["payload_sha256"],
        "prompt_tokens": 300,
        "output_tokens": 100,
        "finish_reason": "stop",
        "text": json.dumps(ground_truth(), ensure_ascii=False),
    }
    row.update(changes)
    return row


class TestBenchmarkCompletion:
    def test_complete_cjk_and_oversized_inputs_preserve_shared_selection(
        self, tmp_path: Path
    ) -> None:
        short = task("complete-cjk")
        semantic = task("long-semantic-cjk", "第一条 本法适用于中华人民共和国境内的活动。\n" * 300)
        assert len(semantic["raw_payload"].encode("utf-8")) > 3584
        list_long = {
            **task(
                "long-list-page",
                "<section>"
                + "<a href='https://example.gov/item'>法规条目链接</a>\n" * 650
                + "</section>",
            ),
            "task_type": "list_page",
        }
        output_heavy = task(
            "output-heavy-cjk",
            "<article>" + "第一条 完整中文法规条文内容。\n" * 650 + "</article>",
        )
        oversized = task("oversized-cjk", "<article>" + "完整中文条文。" * 8_000 + "</article>")
        rows = [short, semantic, list_long, output_heavy, oversized]
        result, selected, report_file = select(tmp_path / "first", rows)
        assert result.returncode == 0, result.stdout + result.stderr
        assert read_rows(selected) == [short, semantic, list_long]
        assert selected.read_bytes() == b"".join(
            (tmp_path / "first" / "input.jsonl").read_bytes().splitlines(keepends=True)[:3]
        ), "Selection must retain each original input row byte for byte"
        report = json.loads(report_file.read_text(encoding="utf-8"))
        assert (report["total_tasks"], report["selected_tasks"], report["excluded_tasks"]) == (
            5,
            3,
            2,
        )
        assert report["status"] == "preselected_pending_model_completion"
        decisions = {item["id"]: item for item in report["decisions"]}
        for source in (short, semantic, list_long):
            assert decisions[source["id"]]["selected"] is True
            assert decisions[source["id"]]["reasons"] == []
        for source in (output_heavy, oversized):
            assert decisions[source["id"]]["selected"] is False
        assert any(
            reason.startswith("output_budget_exceeded:")
            for reason in decisions[output_heavy["id"]]["reasons"]
        )
        assert not any(
            reason.startswith("context_budget_exceeded:")
            for reason in decisions[output_heavy["id"]]["reasons"]
        )
        for label, (model_id, revision) in TOKENIZERS.items():
            metadata = report["tokenizers"][label]
            assert metadata["model_id"] == model_id
            assert metadata["revision"] == revision
            for key in ("fingerprint", "chat_template_sha256"):
                assert len(metadata[key]) == 64
                assert all(char in "0123456789abcdef" for char in metadata[key])
        for source in rows:
            decision = decisions[source["id"]]
            assert set(decision["token_counts"]) == set(TOKENIZERS)
            exceeded: list[str] = []
            for label, (model_id, _) in TOKENIZERS.items():
                counts = decision["token_counts"][label]
                assert counts["model_id"] == model_id
                assert isinstance(counts["prompt_tokens"], int) and counts["prompt_tokens"] > 0
                assert isinstance(counts["payload_tokens"], int) and counts["payload_tokens"] > 0
                expected_required_output = (
                    min(counts["payload_tokens"] + 512, 4096)
                    if source["task_type"] == "list_page"
                    else math.ceil(1.25 * counts["payload_tokens"]) + 512
                )
                assert counts["required_output_tokens"] == expected_required_output
                assert counts["reserved_output_tokens"] == 8192
                assert counts["context_tokens"] == 24576
                assert counts["safety_tokens"] == 1024
                assert len(counts["prompt_sha256"]) == 64
                assert all(char in "0123456789abcdef" for char in counts["prompt_sha256"])
                if counts["required_output_tokens"] > counts["reserved_output_tokens"]:
                    exceeded.append(f"output_budget_exceeded:{label}")
                if (
                    counts["prompt_tokens"]
                    + counts["reserved_output_tokens"]
                    + counts["safety_tokens"]
                    > counts["context_tokens"]
                ):
                    exceeded.append(f"context_budget_exceeded:{label}")
            assert decision["reasons"] == exceeded
            assert decision["selected"] is (not exceeded)

        # Existing model-specific artifacts must not change this shared input set.
        next_dir = tmp_path / "existing-annotations"
        annotations = next_dir / "annotations"
        annotations.mkdir(parents=True)
        (annotations / "annotator_a.jsonl").write_text("not even valid JSON\n", encoding="utf-8")
        write_rows(annotations / "annotator_b.jsonl", [{"id": oversized["id"], "score": 100}])
        second, selected_again, second_report = select(next_dir, rows)
        assert second.returncode == 0, second.stdout + second.stderr
        assert read_rows(selected_again) == [short, semantic, list_long]
        assert (
            json.loads(second_report.read_text(encoding="utf-8"))["decisions"]
            == report["decisions"]
        )
        assert (
            hashlib.sha256(read_rows(selected_again)[0]["raw_payload"].encode("utf-8")).hexdigest()
            == short["payload_sha256"]
        )

    @pytest.mark.parametrize(
        "case",
        [
            "wrong-hash",
            "invalid-hash",
            "empty-id",
            "duplicate-id",
            "truncated",
            "output-over-ceiling",
            "context-over-ceiling",
            "negative-safety",
            "zero-output",
        ],
    )
    def test_invalid_input_or_budget_fails_without_a_selected_package(
        self, tmp_path: Path, case: str
    ) -> None:
        rows = [task("valid")]
        budget_args: list[str] = []
        if case == "wrong-hash":
            rows[0]["payload_sha256"] = "0" * 64
        elif case == "invalid-hash":
            rows[0]["payload_sha256"] = "not-a-sha256"
        elif case == "empty-id":
            rows[0]["id"] = ""
        elif case == "duplicate-id":
            rows.append(task("valid", "<article>第二份正文</article>"))
        elif case == "truncated":
            rows = [task("cut-input", PAYLOAD + "…(truncated)")]
        else:
            budget_args = {
                "output-over-ceiling": ["--max-output-tokens", "8193"],
                "context-over-ceiling": ["--context-tokens", "24577"],
                "negative-safety": ["--safety-tokens", "-1"],
                "zero-output": ["--max-output-tokens", "0"],
            }[case]
        result, selected, _ = select(tmp_path, rows, *budget_args, empty_tokenizer_cache=True)
        assert result.returncode != 0, f"Unsafe input succeeded: {case}"
        assert "Starting pass" not in result.stdout, "Invalid input must fail before model startup"
        assert "No module named" not in result.stderr, result.stderr
        assert not selected.exists() or not selected.read_text(encoding="utf-8").strip()
        assert "error" in (result.stdout + result.stderr).lower(), result.stdout + result.stderr

    def test_context_budget_includes_equal_rejects_one_less_and_honors_lower_output_reserve(
        self, tmp_path: Path
    ) -> None:
        source = task("budget-boundary")
        output_limit = 8192
        result, selected, report_file = select(tmp_path / "baseline", [source])
        assert result.returncode == 0, result.stdout + result.stderr
        assert read_rows(selected) == [source]
        decision = json.loads(report_file.read_text(encoding="utf-8"))["decisions"][0]
        prompt_counts = {
            label: item["prompt_tokens"] for label, item in decision["token_counts"].items()
        }
        assert set(prompt_counts) == set(TOKENIZERS)
        context_limit = max(prompt_counts.values()) + output_limit + 1024
        assert context_limit <= 24576
        equal, equal_selected, _ = select(
            tmp_path / "context-equal",
            [source],
            "--max-output-tokens",
            str(output_limit),
            "--context-tokens",
            str(context_limit),
        )
        assert equal.returncode == 0, equal.stdout + equal.stderr
        assert read_rows(equal_selected) == [source]
        rejected, rejected_file, rejected_report = select(
            tmp_path / "context-one-less", [source], "--context-tokens", str(context_limit - 1)
        )
        assert rejected.returncode != 0
        assert not rejected_file.exists() or not read_rows(rejected_file)
        report = json.loads(rejected_report.read_text(encoding="utf-8"))
        assert (report["total_tasks"], report["selected_tasks"], report["excluded_tasks"]) == (
            1,
            0,
            1,
        )
        assert report["decisions"][0]["reasons"] == [
            f"context_budget_exceeded:{label}"
            for label, count in prompt_counts.items()
            if count + output_limit + 1024 > context_limit - 1
        ]
        reduced, reduced_file, reduced_report = select(
            tmp_path / "reduced-output",
            [source],
            "--max-output-tokens",
            str(output_limit - 1),
            "--context-tokens",
            str(context_limit - 1),
        )
        assert reduced.returncode == 0, reduced.stdout + reduced.stderr
        assert read_rows(reduced_file) == [source]
        reduced_counts = json.loads(reduced_report.read_text(encoding="utf-8"))["decisions"][0][
            "token_counts"
        ]
        assert all(
            item["reserved_output_tokens"] == output_limit - 1 for item in reduced_counts.values()
        )
        min_required_output = max(
            item["required_output_tokens"] for item in decision["token_counts"].values()
        )
        exact_output, exact_output_selected, _ = select(
            tmp_path / "output-equal", [source], "--max-output-tokens", str(min_required_output)
        )
        assert exact_output.returncode == 0, exact_output.stdout + exact_output.stderr
        assert read_rows(exact_output_selected) == [source]
        output_rejected, output_rejected_file, output_rejected_report = select(
            tmp_path / "output-one-less",
            [source],
            "--max-output-tokens",
            str(min_required_output - 1),
        )
        assert output_rejected.returncode != 0
        assert not output_rejected_file.exists() or not read_rows(output_rejected_file)
        output_report_data = json.loads(output_rejected_report.read_text(encoding="utf-8"))
        assert (
            output_report_data["total_tasks"],
            output_report_data["selected_tasks"],
            output_report_data["excluded_tasks"],
        ) == (1, 0, 1)
        assert output_report_data["decisions"][0]["reasons"] == [
            f"output_budget_exceeded:{label}"
            for label, item in decision["token_counts"].items()
            if item["required_output_tokens"] > min_required_output - 1
        ]

    def test_mixed_inference_receipts_reject_incomplete_results_and_keep_complete_source(
        self, tmp_path: Path
    ) -> None:
        invalid_changes: dict[str, dict[str, Any]] = {
            "length-finish": {"finish_reason": "length"},
            "broken-json": {"text": '{"records":[{"markdown":"正文"'},
            "output-exhausted": {"output_tokens": 8192},
            "context-exhausted": {"prompt_tokens": 15361},
            "placeholder": {
                "text": json.dumps(
                    ground_truth("# Extracted Document\n\nContent extracted from source payload.")
                )
            },
            "wrong-source-hash": {"payload_sha256": "0" * 64},
            "empty-records": {"text": '{"records":[]}'},
            "blank-markdown": {"text": json.dumps(ground_truth("   "))},
            "extra-field": {"text": json.dumps({**ground_truth(), "unrequested": True})},
            "wrong-field-type": {"text": json.dumps(ground_truth(123))},
        }
        sources = [task("complete-stop"), task("context-exact")]
        records = [receipt(sources[0]), receipt(sources[1], prompt_tokens=15360)]
        for task_id, changes in invalid_changes.items():
            source = task(task_id)
            sources.append(source)
            records.append(receipt(source, **changes))
        for missing in ("payload_sha256", "prompt_tokens"):
            source = task(f"missing-{missing}")
            sources.append(source)
            row = receipt(source)
            del row[missing]
            records.append(row)
        bundle = tmp_path / "selected.jsonl"
        completions = tmp_path / "completions.jsonl"
        output = tmp_path / "validated"
        write_rows(bundle, sources)
        write_rows(completions, records)
        result = run_cli(
            tmp_path,
            "--validate-completions",
            str(completions),
            "--input",
            str(bundle),
            "--output-dir",
            str(output),
            empty_tokenizer_cache=True,
        )
        assert result.returncode != 0, "Mixed valid and invalid receipts must report failure"
        accepted = read_rows(output / "accepted.jsonl")
        rejected = read_rows(output / "rejected.jsonl")
        assert {row["id"] for row in accepted} == {"complete-stop", "context-exact"}
        expected_rejected = {row["id"] for row in sources} - {row["id"] for row in accepted}
        assert {row["id"] for row in rejected} == expected_rejected
        assert all(row["reasons"] for row in rejected)
        by_id = {row["id"]: row for row in records}
        for row in accepted:
            assert row["ground_truth"] == ground_truth()
            for key in ("payload_sha256", "prompt_tokens", "output_tokens", "finish_reason"):
                assert row["completion"][key] == by_id[row["id"]][key]
        assert read_rows(completions) == records, (
            "Validation must not repair or overwrite raw receipts"
        )
