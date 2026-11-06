"""Public validation/freeze acceptance for complete native URL inventories.

Failure modes fixed before implementation: unbounded full-list validation; lost
tail entries; forged, unsafe or repeated targets; relative/entity/percent/JSON
slash evidence rejected; attachment destinations confused with binary inputs.
No network, model, database, production dataset mutation, or implementation mocks.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEADLINE_SECONDS = 45
REAL_ID = "list_dd-551_native_b1b63f2dd503"
INTEGRITY_RUNNER = """
import json, sys
from wlh.benchmark.arbitrator import validate_ground_truth_integrity
rows = [json.loads(line) for line in open(sys.argv[1], encoding='utf-8')]
if len(sys.argv) > 2 and sys.argv[2] == '--tail-negative-controls':
    original = rows[0]
    for suffix, final_url in (
        ('forged', original['ground_truth']['target_urls'][-1] + '?unattested=1'),
        ('unsafe', 'javascript:alert(1)'),
        ('duplicate', original['ground_truth']['target_urls'][0]),
    ):
        urls = list(original['ground_truth']['target_urls'])
        urls[-1] = final_url
        rows.append({**original, 'id': original['id'] + '-' + suffix,
                     'ground_truth': {'target_urls': urls}})
results = []
for row in rows:
    valid, reason = validate_ground_truth_integrity(
        row['ground_truth'], row['task_type'], row['raw_payload'], row['request_url'],
        mime_type=row['mime_type'], jurisdiction=row['jurisdiction'])
    results.append({'id': row['id'], 'valid': valid, 'reason': reason})
print(json.dumps(results))
"""


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def _reviewed(directory: Path, ident: str, raw: str, mime: str, urls: list[str]) -> dict[str, Any]:
    archive = directory / "archive" / f"{ident}.body"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(raw, encoding="utf-8")
    digest = _sha(raw.encode())
    truth = {"target_urls": urls}
    return {
        "id": ident,
        "task_type": "list_page",
        "source_id": "dd-999",
        "jurisdiction": "DE",
        "request_url": "https://example.gov/legal/index/page",
        "mime_type": mime,
        "raw_payload": raw,
        "payload_sha256": digest,
        "ground_truth": truth,
        "provenance": {
            "kind": "archived_response",
            "acquisition_id": f"url-evidence-{ident}",
            "archive_path": str(archive),
            "archive_sha256": digest,
            "charset": "utf-8",
            "representation": "http_body",
        },
        "review": {
            "status": "approved",
            "reviewer": "independent-url-evidence-acceptance",
            "payload_sha256": digest,
            "ground_truth_sha256": _sha(
                json.dumps(truth, ensure_ascii=False, sort_keys=True).encode()
            ),
            "notes": "隔离验收输入；按完整原件中明确链接逐项枚举，故意错误项用于拒绝验收。",
        },
    }


@pytest.fixture(scope="session")
def wlh_env() -> dict[str, str]:
    """These local CLI paths require no service credentials or network services."""
    return {}


@pytest.fixture(scope="module")
def evidence_run() -> tuple[Path, Path]:
    work_root = ROOT / "work" / "benchmark-url-evidence"
    work_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=work_root))
    artifacts = ROOT / "artifacts" / "benchmark-url-evidence" / work.name
    _json(
        artifacts / "environment.json",
        {
            "python": sys.version,
            "platform": platform.platform(),
            "work": str(work),
            "deadline_seconds_per_operation": DEADLINE_SECONDS,
            "deadline_rationale": (
                "A single local 23 MB native inventory must validate/freeze in 45 seconds; "
                "the existing provenance CLI acceptance uses this same bounded deadline. "
                "This is an operational completion limit, not a microbenchmark or speed ratio."
            ),
            "network": "No HTTP client invoked; subprocess proxies point to closed loopback port.",
            "command": ".venv/bin/python -m pytest -q tests/test_benchmark_url_evidence.py",
            "real_input": os.environ.get("WLH_URL_EVIDENCE_REAL_INPUT"),
        },
    )
    return work, artifacts


def _run(evidence_run: tuple[Path, Path], name: str, arguments: list[str]) -> dict[str, Any]:
    _, artifacts = evidence_run
    command = [sys.executable, *arguments]
    started = time.monotonic()
    result: dict[str, Any] = {
        "name": name,
        "command": command,
        "deadline_seconds": DEADLINE_SECONDS,
    }
    try:
        process = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=DEADLINE_SECONDS,
            env={
                **os.environ,
                "PYTHONPATH": str(ROOT / "src"),
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            },
        )
        result.update(
            timed_out=False,
            returncode=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
        )
    except subprocess.TimeoutExpired:
        result.update(timed_out=True, returncode=None, stdout="", stderr="operation timed out")
    result["elapsed_seconds"] = time.monotonic() - started
    _json(artifacts / f"{name}.json", result)
    return result


def _freeze_arguments(bundle: Path, output: Path) -> list[str]:
    return [
        "-m",
        "wlh",
        "benchmark",
        "freeze",
        "--input",
        str(bundle),
        "--output-dir",
        str(output),
        "--version",
        "url-evidence-acceptance-v1",
    ]


@pytest.fixture(scope="module")
def native_cases(evidence_run: tuple[Path, Path]) -> dict[str, dict[str, Any]]:
    work, _ = evidence_run
    cases = (
        (
            "html-relative-attachment-targets",
            "text/html",
            '<html><body><a href="../law/7">法令</a>'
            '<a href="/files/act.pdf">PDF</a><a href="/files/act.docx">Word</a>'
            '<a href="//example.gov/files/act.zip">Archive</a>'
            '<a href="/files/act.pdf">同一附件再次出现</a></body></html>',
            [
                "https://example.gov/legal/law/7",
                "https://example.gov/files/act.pdf",
                "https://example.gov/files/act.docx",
                "https://example.gov/files/act.zip",
            ],
            True,
        ),
        (
            "xml-entity-percent-target",
            "application/xml",
            "<items><link>https://example.gov/laws/%E7%A8%8E?x=1&amp;y=2</link></items>",
            ["https://example.gov/laws/税?x=1&y=2"],
            True,
        ),
        (
            "json-escaped-slash-target",
            "application/json",
            r'{"items":[{"url":"https:\/\/example.gov\/files\/act.pdf"}]}',
            ["https://example.gov/files/act.pdf"],
            True,
        ),
        (
            "forged-tail-target",
            "text/html",
            '<html><a href="/law/7">法令</a></html>',
            ["https://example.gov/law/7", "https://example.gov/law/absent"],
            False,
        ),
        (
            "unsafe-target",
            "text/html",
            '<html><a href="javascript:alert(1)">法令</a></html>',
            ["javascript:alert(1)"],
            False,
        ),
        (
            "duplicate-target",
            "text/html",
            '<html><a href="/law/7">法令</a></html>',
            ["https://example.gov/law/7", "https://example.gov/law/7"],
            False,
        ),
    )
    return {
        ident: {"row": _reviewed(work, ident, raw, mime, urls), "expected_valid": expected}
        for ident, mime, raw, urls, expected in cases
    }


class TestBenchmarkNativeUrlEvidence:
    def test_native_link_semantics_and_invalid_targets_match_public_integrity_contract(
        self, evidence_run: tuple[Path, Path], native_cases: dict[str, dict[str, Any]]
    ) -> None:
        work, artifacts = evidence_run
        bundle = work / "native-reviewed.jsonl"
        _write_rows(bundle, [case["row"] for case in native_cases.values()])
        result = _run(evidence_run, "native-integrity", ["-c", INTEGRITY_RUNNER, str(bundle)])
        assert result["returncode"] == 0, result
        actual = {row["id"]: row for row in json.loads(result["stdout"])}
        assertions = [
            {"id": ident, "expected_valid": case["expected_valid"], **actual[ident]}
            for ident, case in native_cases.items()
        ]
        _json(artifacts / "native-integrity-assertions.json", assertions)
        assert all(r["valid"] == r["expected_valid"] for r in assertions), assertions

    @pytest.mark.parametrize("valid", [True, False], ids=["accepted", "rejected"])
    def test_freeze_preserves_valid_native_targets_and_rejects_invalid_targets(
        self,
        evidence_run: tuple[Path, Path],
        native_cases: dict[str, dict[str, Any]],
        valid: bool,
    ) -> None:
        work, artifacts = evidence_run
        groups = (
            [[case["row"] for case in native_cases.values() if case["expected_valid"]]]
            if valid
            else [[case["row"]] for case in native_cases.values() if not case["expected_valid"]]
        )
        for rows in groups:
            name = "native-freeze-" + ("valid" if valid else rows[0]["id"])
            bundle, output = work / f"{name}.jsonl", work / name
            _write_rows(bundle, rows)
            before = _sha(bundle.read_bytes())
            result = _run(evidence_run, name, _freeze_arguments(bundle, output))
            assert result["timed_out"] is False, result
            assert _sha(bundle.read_bytes()) == before
            if valid:
                assert result["returncode"] == 0, result
                exported = [
                    json.loads(line)
                    for line in (output / "ground_truth_benchmark.jsonl").read_text().splitlines()
                ]
                assert {r["id"]: r["ground_truth"] for r in exported} == {
                    r["id"]: r["ground_truth"] for r in rows
                }
                assert {r["id"]: r["raw_payload"] for r in exported} == {
                    r["id"]: r["raw_payload"] for r in rows
                }
            else:
                assert result["returncode"] != 0, result
                assert not (output / "annotation_pipeline.json").exists()
            _json(artifacts / f"{name}-assertions.json", {"passed": True, "inputs_unchanged": True})


@pytest.fixture(scope="module")
def complete_inventory(evidence_run: tuple[Path, Path]) -> tuple[Path, dict[str, Any]]:
    configured = os.environ.get("WLH_URL_EVIDENCE_REAL_INPUT")
    if not configured:
        pytest.skip("Set WLH_URL_EVIDENCE_REAL_INPUT to the acquired full native selected.jsonl")
    source = Path(configured)
    assert source.is_file(), f"Missing explicitly configured native archive: {source}"
    raw_row = next(
        json.loads(line)
        for line in source.open(encoding="utf-8")
        if json.loads(line)["id"] == REAL_ID
    )
    raw = str(raw_row["raw_payload"])
    assert _sha(raw.encode()) == "b1b63f2dd503be06b2bb844a9b6847a98c10645fbc5505046b4f53ce3ad45f18"
    urls = [str(element.text) for element in ElementTree.fromstring(raw).findall("./item/link")]
    assert len(urls) == len(set(urls)) == 84696
    assert urls[-1].endswith("/jb-WBRE410021432.zip")
    work, artifacts = evidence_run
    row = _reviewed(work, REAL_ID, raw, "application/xml", urls)
    row.update(source_id="dd-551", request_url=raw_row["request_url"])
    bundle = work / "complete-reviewed.jsonl"
    _write_rows(bundle, [row])
    _json(
        artifacts / "complete-input.json",
        {
            "source": str(source),
            "id": REAL_ID,
            "payload_sha256": row["payload_sha256"],
            "payload_bytes": len(raw.encode()),
            "target_count": len(urls),
            "first_target": urls[0],
            "last_target": urls[-1],
            "ground_truth_sha256": row["review"]["ground_truth_sha256"],
            "enumeration": "All ./item/link elements from the complete XML, in document order",
        },
    )
    return bundle, row


class TestBenchmarkCompleteUrlInventory:
    def test_complete_real_inventory_public_integrity_finishes_within_deadline(
        self, evidence_run: tuple[Path, Path], complete_inventory: tuple[Path, dict[str, Any]]
    ) -> None:
        bundle, _ = complete_inventory
        result = _run(
            evidence_run,
            "complete-integrity",
            [
                "-c",
                INTEGRITY_RUNNER,
                str(bundle),
                "--tail-negative-controls",
            ],
        )
        assert result["timed_out"] is False, result
        assert result["returncode"] == 0, result
        validations = json.loads(result["stdout"])
        assert [row["valid"] for row in validations] == [True, False, False, False], result
        _json(
            evidence_run[1] / "complete-integrity-assertions.json",
            {
                "passed": True,
                "validations": validations,
                "negative_controls": "Only final target changed to forged, unsafe or duplicate URL",
            },
        )

    def test_complete_real_inventory_freeze_preserves_every_target_within_deadline(
        self, evidence_run: tuple[Path, Path], complete_inventory: tuple[Path, dict[str, Any]]
    ) -> None:
        work, artifacts = evidence_run
        bundle, row = complete_inventory
        before = _sha(bundle.read_bytes())
        output = work / "complete-frozen"
        result = _run(evidence_run, "complete-freeze", _freeze_arguments(bundle, output))
        assert result["timed_out"] is False, result
        assert result["returncode"] == 0, result
        golden = output / "ground_truth_benchmark.jsonl"
        exported = json.loads(golden.read_text())
        assert exported["ground_truth"] == row["ground_truth"]
        assert exported["raw_payload"] == row["raw_payload"]
        assert _sha(bundle.read_bytes()) == before
        ledger = json.loads((output / "annotation_pipeline.json").read_text())
        assert ledger["dataset_sha256"] == _sha(golden.read_bytes())
        _json(
            artifacts / "complete-freeze-assertions.json",
            {
                "passed": True,
                "target_count": len(exported["ground_truth"]["target_urls"]),
                "all_targets_and_order_preserved": True,
                "complete_payload_preserved": True,
                "inputs_unchanged": True,
                "ledger_digest_matches_export": True,
            },
        )
