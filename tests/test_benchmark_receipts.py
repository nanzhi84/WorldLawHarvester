"""A stored completion must authorize exactly the truth reused by the CLI."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_benchmark_completion import (
    TOKENIZERS,
    read_rows,
    receipt,
    run_cli,
    select,
    task,
    write_rows,
)


@pytest.fixture(scope="session")
def wlh_env() -> dict[str, str]:
    """File-only benchmark contracts need no shared database or S3 bootstrap."""
    return {}


class TestBenchmarkReceiptReuse:
    def test_only_receipts_bound_to_matching_truth_and_budget_can_be_reused(
        self, tmp_path: Path
    ) -> None:
        source = task("stored-receipt")
        result, selected, report_path = select(tmp_path, [source], "--local-files-only")
        assert result.returncode == 0, result.stdout + result.stderr
        selection = json.loads(report_path.read_text())
        counts = selection["decisions"][0]["token_counts"]
        annotations = tmp_path / "annotations"
        valid_rows: dict[str, dict[str, Any]] = {}
        for label, (model, revision) in TOKENIZERS.items():
            completions = tmp_path / f"completion-{label}.jsonl"
            write_rows(
                completions,
                [
                    receipt(
                        source,
                        prompt_tokens=counts[label]["prompt_tokens"],
                        budget=selection["budget"],
                    )
                ],
            )
            validated = tmp_path / f"validated-{label}"
            result = run_cli(
                validated,
                "--validate-completions",
                str(completions),
                "--input",
                str(selected),
                "--output-dir",
                str(validated),
            )
            assert result.returncode == 0, result.stdout + result.stderr
            row = read_rows(validated / "accepted.jsonl")[0]
            row["model"] = model
            row["completion"].update(
                tokenizer_revision=revision,
                prompt_sha256=counts[label]["prompt_sha256"],
            )
            valid_rows[label] = row
            write_rows(annotations / f"annotator_{label.lower()}.jsonl", [row])
        pair_args = (
            "--pair-only",
            "--input",
            str(selected),
            "--output-dir",
            str(annotations),
            "--local-files-only",
        )
        baseline = run_cli(tmp_path / "baseline", *pair_args)
        assert baseline.returncode == 0, baseline.stdout + baseline.stderr
        changed = copy.deepcopy(valid_rows["A"])
        changed["ground_truth"]["records"][0]["markdown"] += "\n\n第三条：一律免税。"
        assert changed["completion"] == valid_rows["A"]["completion"]
        write_rows(annotations / "annotator_a.jsonl", [changed])
        tampered = run_cli(tmp_path / "tampered", *pair_args)
        assert tampered.returncode != 0, (
            "A valid raw completion cannot authorize a different outer ground_truth"
        )
        for field in ("context_tokens", "max_output_tokens", "safety_tokens", "output_policy"):
            stale = copy.deepcopy(valid_rows["A"])
            stale_budget = stale["completion"]["budget"]
            stale_budget[field] = (
                "obsolete-policy" if field == "output_policy" else stale_budget[field] + 1
            )
            receipt_path = annotations / "annotator_a.jsonl"
            write_rows(receipt_path, [stale])
            original_receipt = receipt_path.read_bytes()
            incompatible = run_cli(tmp_path / f"stale-{field}", *pair_args)
            assert incompatible.returncode != 0, f"Changed {field} must be revalidated, not reused"
            assert receipt_path.read_bytes() == original_receipt
        missing = copy.deepcopy(valid_rows["A"])
        del missing["completion"]["budget"]
        write_rows(annotations / "annotator_a.jsonl", [missing])
        legacy = run_cli(tmp_path / "missing-budget", *pair_args)
        assert legacy.returncode != 0, "Legacy receipts without a budget policy cannot be reused"
