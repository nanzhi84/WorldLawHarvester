"""Black-box CLI contracts for inline date evidence and structural boundaries."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
TITLE = "日期证据规则"
TAIL = "原始文本必须逐段保留，不得据上下文补全年月日。"


@dataclass(frozen=True)
class InlineDateCase:
    name: str
    html_body: str
    markdown_body: str
    effective_at: str
    accepted: bool
    request_url: str = "https://example.gov/rules/document"


CASES = (
    InlineDateCase(
        "complete-plain-date",
        "<p>本政策自2021年11月7日起实施。</p>",
        "本政策自2021年11月7日起实施。",
        "2021-11-07",
        True,
    ),
    InlineDateCase(
        "same-paragraph-split-year-and-range",
        "<p><span>自20</span><span>21</span><span>年11月7日起至202</span>"
        "<span>5</span><span>年</span><span>12</span><span>月</span><span>31</span>"
        "<span>日止，适用本政策。</span></p>",
        "自2021年11月7日起至2025年12月31日止，适用本政策。",
        "2021-11-07",
        True,
    ),
    InlineDateCase(
        "same-paragraph-nested-inline-date",
        "<p>本政策自<span>20<strong>21</strong></span>年<em>11</em>月<b>7</b>日起实施。</p>",
        "本政策自2021年11月7日起实施。",
        "2021-11-07",
        True,
    ),
    InlineDateCase(
        "same-paragraph-split-iso-date",
        "<p>Effective <span>20</span><em>21-11</em><strong>-07</strong>.</p>",
        "Effective 2021-11-07.",
        "2021-11-07",
        True,
    ),
    InlineDateCase(
        "same-paragraph-split-english-date",
        "<p>Effective <span>Feb.</span> 4, <b>20</b><em>11</em>.</p>",
        "Effective Feb. 4, 2011.",
        "2011-02-04",
        True,
    ),
    InlineDateCase(
        "different-paragraph-date-fragments",
        "<p>本政策自20</p><p>21年11月7日起实施。</p>",
        "本政策自20\n\n21年11月7日起实施。",
        "2021-11-07",
        False,
    ),
    InlineDateCase(
        "different-table-row-date-fragments",
        "<table><tr><td>本政策自20</td></tr><tr><td>21年11月7日起实施。</td></tr></table>",
        "| 本政策自20 |\n| --- |\n| 21年11月7日起实施。 |",
        "2021-11-07",
        False,
    ),
    InlineDateCase(
        "unrelated-sibling-block-date-fragments",
        "<div>本政策自20</div><section>21年11月7日起实施。</section>",
        "本政策自20\n\n21年11月7日起实施。",
        "2021-11-07",
        False,
    ),
    InlineDateCase(
        "date-only-in-hidden-script",
        '<script type="application/json">{"effective_at":"2021-11-07"}</script>'
        "<p>本页未载明实施日期。</p>",
        "本页未载明实施日期。",
        "2021-11-07",
        False,
    ),
    InlineDateCase(
        "script-cannot-complete-inline-year",
        "<p>本政策自20<script>21</script>年11月7日起实施。</p>",
        "本政策自20年11月7日起实施。",
        "2021-11-07",
        False,
    ),
    InlineDateCase(
        "date-only-in-request-url",
        "<p>本页未载明实施日期。</p>",
        "本页未载明实施日期。",
        "2021-11-07",
        False,
        "https://example.gov/rules/2021/11/07/document",
    ),
    InlineDateCase(
        "date-only-in-link-url",
        '<p><a href="/rules/2021-11-07">本页未载明实施日期。</a></p>',
        "[本页未载明实施日期。](https://example.gov/rules/2021-11-07)",
        "2021-11-07",
        False,
    ),
    InlineDateCase(
        "same-paragraph-invalid-calendar-date",
        "<p>本政策自<span>20</span><span>21</span>年2月29日起实施。</p>",
        "本政策自2021年2月29日起实施。",
        "2021-02-29",
        False,
    ),
)


@pytest.fixture(scope="session")
def wlh_env() -> dict[str, str]:
    """Real file/CLI validation has no database or object-storage requirement."""
    return {}


@pytest.fixture(scope="module")
def inline_date_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    configured = os.environ.get("WLH_INLINE_DATE_WORKDIR")
    directory = Path(configured).resolve() if configured else tmp_path_factory.mktemp("inline-date")
    directory.mkdir(parents=True, exist_ok=True)
    sources: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    for case in CASES:
        raw = f"<article><h1>{TITLE}</h1>{case.html_body}<p>{TAIL}</p></article>"
        digest = hashlib.sha256(raw.encode()).hexdigest()
        sources.append(
            {
                "id": case.name,
                "source_id": "dd-999",
                "task_type": "content_page",
                "jurisdiction": "CN",
                "mime_type": "text/html",
                "request_url": case.request_url,
                "raw_payload": raw,
                "payload_sha256": digest,
            }
        )
        truth: dict[str, Any] = {
            "records": [
                {
                    "doc_type": "政策文件",
                    "title": TITLE,
                    "doc_number": None,
                    "published_at": None,
                    "effective_at": case.effective_at,
                    "issuer": None,
                    "attachment_urls": [],
                    "markdown": f"# {TITLE}\n\n{case.markdown_body}\n\n{TAIL}",
                }
            ]
        }
        receipts.append(
            {
                "id": case.name,
                "payload_sha256": digest,
                "prompt_tokens": 300,
                "output_tokens": 200,
                "finish_reason": "stop",
                "text": json.dumps(truth, ensure_ascii=False, indent=2),
            }
        )
    bundle, completion_file = directory / "input.jsonl", directory / "completions.jsonl"
    for path, rows in ((bundle, sources), (completion_file, receipts)):
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
        )
    original_source, original_receipts = bundle.read_bytes(), completion_file.read_bytes()
    command = [
        sys.executable,
        "-m",
        "wlh.benchmark.annotator",
        "--validate-completions",
        str(completion_file),
        "--input",
        str(bundle),
        "--output-dir",
        str(directory / "validated"),
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
        },
    )
    outputs: dict[str, dict[str, Any]] = {}
    for status in ("accepted", "rejected"):
        path = directory / "validated" / f"{status}.jsonl"
        assert path.is_file(), result.stdout + result.stderr
        outputs[status] = {row["id"]: row for row in map(json.loads, path.read_text().splitlines())}
    summary = {
        "argv": command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "expected_accepted": sum(case.accepted for case in CASES),
        "expected_rejected": sum(not case.accepted for case in CASES),
        "actual_accepted": len(outputs["accepted"]),
        "actual_rejected": len(outputs["rejected"]),
        "source_unchanged": original_source == bundle.read_bytes(),
        "receipts_unchanged": original_receipts == completion_file.read_bytes(),
        "cases": [
            {
                "id": case.name,
                "expected": "accepted" if case.accepted else "rejected",
                "actual": "accepted" if case.name in outputs["accepted"] else "rejected",
                "passed": (case.name in outputs["accepted"]) == case.accepted,
                "reasons": outputs["rejected"].get(case.name, {}).get("reasons", []),
            }
            for case in CASES
        ],
    }
    (directory / "assertion-results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"outputs": outputs, "summary": summary, "receipts": receipts}


class TestBenchmarkInlineDates:
    @pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
    def test_complete_inline_date_requires_contiguous_visible_evidence(
        self, inline_date_run: dict[str, Any], case: InlineDateCase
    ) -> None:
        outputs = inline_date_run["outputs"]
        if case.accepted:
            assert case.name in outputs["accepted"], outputs["rejected"].get(case.name)
        else:
            assert case.name in outputs["rejected"], f"Invented date accepted: {case.name}"
            reasons = outputs["rejected"][case.name]["reasons"]
            if case.name == "same-paragraph-invalid-calendar-date":
                assert "invalid_ground_truth_schema" in reasons or (
                    "record_0:unattested_effective_at" in reasons
                )
            else:
                assert "record_0:unattested_effective_at" in reasons

    def test_mixed_validation_preserves_full_sources_and_raw_receipts(
        self, inline_date_run: dict[str, Any]
    ) -> None:
        summary = inline_date_run["summary"]
        assert summary["returncode"] != 0
        assert summary["source_unchanged"] and summary["receipts_unchanged"]
        expected = {receipt["id"]: receipt for receipt in inline_date_run["receipts"]}
        outputs = inline_date_run["outputs"]
        rows = [*outputs["accepted"].values(), *outputs["rejected"].values()]
        assert len(rows) == len(CASES)
        assert {row["id"] for row in rows} == set(expected)
        for row in rows:
            assert row["completion"]["text"] == expected[row["id"]]["text"]
            if row["id"] in outputs["accepted"]:
                assert row["ground_truth"] == json.loads(expected[row["id"]]["text"])
