"""Public receipt CLI contracts for source-backed list structure."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
TITLE = "商標の検索方法"
BODY = (
    "<p>登録商標を検索する方法を説明します。</p><ol>"
    "<li>検索画面を開きます。</li>"
    "<li>検索条件を入力します。<ul><li>読み方を選択します。</li>"
    "<li>キーワードを入力します。</li></ul></li>"
    "<li>検索ボタンを押します。</li><li>登録番号のリンクを選択します。</li></ol>"
    "<p>結果の詳細画面が表示されます。</p>"
)
MARKDOWN = (
    f"# {TITLE}\n\n登録商標を検索する方法を説明します。\n\n"
    "1. 検索画面を開きます。\n"
    "2. 検索条件を入力します。\n"
    "   - 読み方を選択します。\n"
    "   - キーワードを入力します。\n"
    "3. 検索ボタンを押します。\n"
    "4. 登録番号のリンクを選択します。\n\n"
    "結果の詳細画面が表示されます。"
)


@pytest.fixture(scope="session")
def wlh_env() -> dict[str, str]:
    """The real CLI consumes complete local files without service credentials."""
    return {}


@pytest.fixture(scope="module")
def list_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    configured = os.environ.get("WLH_LIST_EVIDENCE_WORKDIR")
    directory = Path(configured) if configured else tmp_path_factory.mktemp("list-evidence")
    directory.mkdir(parents=True, exist_ok=True)
    raw = f"<!doctype html><html><body><article><h1>{TITLE}</h1>{BODY}</article></body></html>"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    sources: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    for ident, markdown in (
        ("complete-ordered-list", MARKDOWN),
        (
            "invented-operation",
            MARKDOWN + "\n\n5. 利用者は登録手数料として百万円を支払わなければならない。",
        ),
    ):
        sources.append(
            {
                "id": ident,
                "task_type": "content_page",
                "source_id": "dd-999",
                "jurisdiction": "JP",
                "request_url": "https://example.gov/manual/search",
                "mime_type": "text/html",
                "raw_payload": raw,
                "payload_sha256": digest,
            }
        )
        truth: dict[str, Any] = {
            "records": [
                {
                    "doc_type": "其他",
                    "title": TITLE,
                    "doc_number": None,
                    "published_at": None,
                    "effective_at": None,
                    "issuer": None,
                    "attachment_urls": [],
                    "markdown": markdown,
                }
            ]
        }
        receipts.append(
            {
                "id": ident,
                "payload_sha256": digest,
                "prompt_tokens": 400,
                "output_tokens": 200,
                "finish_reason": "stop",
                "text": json.dumps(truth, ensure_ascii=False, indent=2),
            }
        )
    bundle = directory / "input.jsonl"
    completions = directory / "completions.jsonl"
    for path, data in ((bundle, sources), (completions, receipts)):
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in data))
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (bundle, completions)}
    argv = [
        sys.executable,
        "-m",
        "wlh.benchmark.annotator",
        "--validate-completions",
        str(completions),
        "--input",
        str(bundle),
        "--output-dir",
        str(directory / "validated"),
    ]
    run = subprocess.run(
        argv,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=40,
        env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
    )
    output: dict[str, dict[str, Any]] = {}
    for status in ("accepted", "rejected"):
        path = directory / "validated" / f"{status}.jsonl"
        assert path.is_file(), run.stdout + run.stderr
        output[status] = {r["id"]: r for r in map(json.loads, path.read_text().splitlines())}
    summary: dict[str, Any] = {
        "argv": argv,
        "returncode": run.returncode,
        "stdout": run.stdout,
        "stderr": run.stderr,
        "input_hashes": before,
        "inputs_unchanged": all(
            hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in before.items()
        ),
        "accepted_ids": list(output["accepted"]),
        "rejections": {k: v["reasons"] for k, v in output["rejected"].items()},
        "raw_output_text_preserved": all(
            next(output[s][r["id"]] for s in output if r["id"] in output[s])["completion"]["text"]
            == r["text"]
            for r in receipts
        ),
    }
    (directory / "assertion-results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    return summary


class TestBenchmarkListEvidence:
    def test_complete_html_ordered_list_preserves_markdown_steps(
        self, list_run: dict[str, Any]
    ) -> None:
        assert "complete-ordered-list" in list_run["accepted_ids"], list_run["rejections"]

    def test_invented_operation_is_rejected(self, list_run: dict[str, Any]) -> None:
        assert "invented-operation" in list_run["rejections"], list_run
        assert any("unsupported_body" in r for r in list_run["rejections"]["invented-operation"])

    def test_complete_inputs_and_raw_receipts_are_preserved(self, list_run: dict[str, Any]) -> None:
        assert list_run["inputs_unchanged"] is True
        assert list_run["raw_output_text_preserved"] is True
