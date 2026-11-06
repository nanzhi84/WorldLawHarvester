"""Black-box acceptance for explicit JSON links, specified before implementation.

Relative URL fields must resolve against the original request URL. Identifiers
and pieces from different records cannot authorize invented destinations. These
file-only CLI runs preserve every current-page result, including its final link.
HTML/XML, escaped absolute JSON links and duplicates have existing coverage in
test_benchmark_url_evidence.py; this suite adds the missing JSON-relative contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
REQUEST = "https://example.gov/api/search/page?court=ca1"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


@pytest.fixture(scope="session")
def wlh_env() -> dict[str, str]:
    """This public file-only CLI requires no database, model or credentials."""
    return {}


def _run(arguments: list[str], artifact: Path) -> dict[str, Any]:
    command = [sys.executable, *arguments]
    process = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=45,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        },
    )
    result = {
        "command": command,
        "returncode": process.returncode,
        "stdout": process.stdout,
        "stderr": process.stderr,
    }
    _json(artifact, result)
    return result


@pytest.fixture(scope="module")
def json_links_run() -> dict[str, Any]:
    base = ROOT / "work" / "benchmark-expansion-20261106" / "native-json-links"
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=base))
    artifacts = (
        ROOT / "artifacts" / "benchmark-expansion-20261106" / "native-json-links" / work.name
    )
    _json(
        artifacts / "environment.json",
        {
            "python": sys.version,
            "platform": platform.platform(),
            "work": str(work),
            "command": ".venv/bin/python -m pytest -q tests/test_benchmark_json_links.py",
            "inputs": "Isolated complete native JSON HTTP-response bodies; no external I/O",
            "deadline_seconds_per_cli": 45,
        },
    )
    complete_urls = [
        url
        for index in range(1, 21)
        for url in (
            f"https://example.gov/opinion/{index}/case-{index}/",
            f"https://court.example.gov/files/{index}.pdf",
        )
    ]
    complete_payload = {
        "count": 37077,
        "next": "/api/search/page?cursor=next",
        "results": [
            {
                "caseName": f"Case {index}",
                "absolute_url": f"/opinion/{index}/case-{index}/",
                "opinions": [{"download_url": f"https://court.example.gov/files/{index}.pdf"}],
            }
            for index in range(1, 21)
        ],
    }
    cases: dict[str, tuple[object, list[str], bool]] = {
        "complete-current-page": (complete_payload, complete_urls, True),
        "relative-reference-forms": (
            {
                "items": [
                    {"url": "../opinion/21"},
                    {"href": "?id=22"},
                    {"download_url": "//court.example.gov/files/23.pdf"},
                ]
            },
            [
                "https://example.gov/api/opinion/21",
                "https://example.gov/api/search/page?id=22",
                "https://court.example.gov/files/23.pdf",
            ],
            True,
        ),
        "identifier-template-invention": (
            {"results": [{"bbbs": "only-an-id", "fileSetId": 72012}]},
            ["https://example.gov/opinion/only-an-id/"],
            False,
        ),
        "cross-item-concatenation": (
            {
                "results": [
                    {"url": "https://elsewhere.example.gov/opinion/"},
                    {"id": "separate-record"},
                ]
            },
            ["https://elsewhere.example.gov/opinion/separate-record"],
            False,
        ),
        "forged-final-relative-target": (
            complete_payload,
            [*complete_urls[:-1], "https://example.gov/opinion/absent-tail/"],
            False,
        ),
    }
    for name, url in (
        ("script", "javascript:alert(1)"),
        ("data", "data:text/plain,opinion"),
        ("file", "file:///tmp/opinion.txt"),
        ("mail", "mailto:court@example.gov"),
    ):
        cases[f"unsafe-{name}"] = ({"results": [{"absolute_url": url}]}, [url], False)
    sources: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    for ident, (payload, urls, _) in cases.items():
        raw = json.dumps(payload, ensure_ascii=False)
        digest = _sha(raw.encode())
        archive = work / f"{ident}.body.json"
        archive.write_text(raw, encoding="utf-8")
        truth = {"target_urls": urls}
        sources.append(
            {
                "id": ident,
                "task_type": "list_page",
                "source_id": "dd-999",
                "jurisdiction": "US",
                "request_url": REQUEST,
                "mime_type": "application/json",
                "raw_payload": raw,
                "payload_sha256": digest,
                "ground_truth": truth,
                "provenance": {
                    "kind": "archived_response",
                    "acquisition_id": f"json-links-{ident}",
                    "archive_path": str(archive),
                    "archive_sha256": digest,
                    "charset": "utf-8",
                    "representation": "http_body",
                },
                "review": {
                    "status": "approved",
                    "reviewer": "isolated-json-links-acceptance",
                    "payload_sha256": digest,
                    "ground_truth_sha256": _sha(json.dumps(truth, sort_keys=True).encode()),
                    "notes": "Isolated synthetic acceptance only; no real source approval.",
                },
            }
        )
        receipts.append(
            {
                "id": ident,
                "payload_sha256": digest,
                "prompt_tokens": 2000,
                "output_tokens": 2000,
                "finish_reason": "stop",
                "text": json.dumps(truth),
            }
        )
    bundle, completions = work / "input.jsonl", work / "completions.jsonl"
    _rows(bundle, sources)
    _rows(completions, receipts)
    before = {str(path): _sha(path.read_bytes()) for path in (bundle, completions)}
    output = work / "validated"
    run = _run(
        [
            "-m",
            "wlh.benchmark.annotator",
            "--validate-completions",
            str(completions),
            "--input",
            str(bundle),
            "--output-dir",
            str(output),
        ],
        artifacts / "receipt-cli.json",
    )
    observed: dict[str, dict[str, Any]] = {}
    for status in ("accepted", "rejected"):
        path = output / f"{status}.jsonl"
        assert path.is_file(), run
        for row in map(json.loads, path.read_text().splitlines()):
            observed[row["id"]] = {"status": status, "row": row}
    assertions = {
        ident: {
            "expected_valid": case[2],
            "actual_valid": observed[ident]["status"] == "accepted",
            "reasons": observed[ident]["row"].get("reasons", []),
        }
        for ident, case in cases.items()
    }
    _json(artifacts / "receipt-assertions.json", assertions)
    return {
        "work": work,
        "artifacts": artifacts,
        "assertions": assertions,
        "sources": sources,
        "before": before,
        "receipts": receipts,
        "observed": observed,
    }


class TestBenchmarkExplicitJsonLinks:
    @pytest.mark.parametrize("ident", ["complete-current-page", "relative-reference-forms"])
    def test_explicit_relative_fields_are_accepted_as_request_resolved_urls(
        self,
        json_links_run: dict[str, Any],
        ident: str,
    ) -> None:
        result = json_links_run["assertions"][ident]
        assert result["actual_valid"] is True, result

    def test_invented_identifiers_cross_item_urls_and_unsafe_schemes_are_rejected(
        self,
        json_links_run: dict[str, Any],
    ) -> None:
        rejected = [r for r in json_links_run["assertions"].values() if not r["expected_valid"]]
        assert all(r["actual_valid"] is False for r in rejected), rejected

    def test_complete_current_page_freeze_preserves_all_targets_and_final_entry(
        self,
        json_links_run: dict[str, Any],
    ) -> None:
        work, artifacts = json_links_run["work"], json_links_run["artifacts"]
        source = json_links_run["sources"][0]
        bundle, output = work / "complete-reviewed.jsonl", work / "frozen"
        _rows(bundle, [source])
        run = _run(
            [
                "-m",
                "wlh",
                "benchmark",
                "freeze",
                "--input",
                str(bundle),
                "--output-dir",
                str(output),
                "--version",
                "explicit-json-links-acceptance-v1",
            ],
            artifacts / "freeze-cli.json",
        )
        assert run["returncode"] == 0, run
        exported = json.loads((output / "ground_truth_benchmark.jsonl").read_text())
        assert exported["ground_truth"] == source["ground_truth"]
        assert len(exported["ground_truth"]["target_urls"]) == 40
        assert exported["ground_truth"]["target_urls"][-1].endswith("/files/20.pdf")
        assert exported["raw_payload"] == source["raw_payload"]
        _json(
            artifacts / "freeze-assertions.json",
            {
                "passed": True,
                "current_page_items": 20,
                "targets": 40,
                "complete_input_preserved": True,
                "all_targets_preserved": True,
            },
        )

    def test_validation_preserves_complete_inputs_and_raw_receipts(
        self,
        json_links_run: dict[str, Any],
    ) -> None:
        assert all(_sha(Path(p).read_bytes()) == h for p, h in json_links_run["before"].items())
        for receipt in json_links_run["receipts"]:
            actual = json_links_run["observed"][receipt["id"]]["row"]
            assert actual["completion"]["text"] == receipt["text"]
