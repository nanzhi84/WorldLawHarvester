"""Public validation/freeze contracts for explicit publication-date metadata.

The gate establishes evidence existence. Conflicting document/page publication
semantics still require source review; these contracts never auto-select a date.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.test_benchmark_date_evidence import ROOT, read_jsonl, write_json

TITLE = "Publication Date Notice"
BODY = "Every paragraph of this notice must remain in the reviewed ground truth."


@dataclass(frozen=True)
class PublicationCase:
    name: str
    head: str
    published_at: str | None
    accepted: bool
    visible: str = "June 2008"


CASES = (
    PublicationCase(
        "issued-complete", '<meta name="dcterms.issued" content="2008-06-02">', "2008-06-02", True
    ),
    PublicationCase(
        "issued-leap-day",
        '<meta name="dcterms.issued" content="2024-02-29">',
        "2024-02-29",
        True,
        "February 2024",
    ),
    PublicationCase(
        "issued-month-only", '<meta name="dcterms.issued" content="2008-06">', "2008-06-01", False
    ),
    PublicationCase(
        "issued-invalid-calendar",
        '<meta name="dcterms.issued" content="2023-02-29">',
        "2023-02-29",
        False,
        "February 2023",
    ),
    PublicationCase(
        "issued-invalid-rollover",
        '<meta name="dcterms.issued" content="2023-02-29">',
        "2023-03-01",
        False,
        "February 2023",
    ),
    PublicationCase(
        "modified-only", '<meta name="dcterms.modified" content="2008-06-02">', "2008-06-02", False
    ),
    PublicationCase(
        "created-only", '<meta name="dcterms.created" content="2008-06-02">', "2008-06-02", False
    ),
    PublicationCase(
        "creation-only", '<meta name="creation_date" content="2008-06-02">', "2008-06-02", False
    ),
    PublicationCase(
        "cache-only", '<meta http-equiv="last-modified" content="2008-06-02">', "2008-06-02", False
    ),
    PublicationCase(
        "unrelated-metadata", '<meta name="description" content="2008-06-02">', "2008-06-02", False
    ),
    PublicationCase(
        "publication-lookalike",
        '<meta name="custom.dcterms.issued" content="2008-06-02">',
        "2008-06-02",
        False,
    ),
    PublicationCase(
        "script-fake-meta",
        '<script>const text = \'<meta name="dcterms.issued" content="2008-06-02">\';</script>',
        "2008-06-02",
        False,
    ),
    PublicationCase(
        "body-month-does-not-supply-day",
        '<meta name="dcterms.issued" content="2017-06-22">',
        "1999-08-01",
        False,
        "August 1999",
    ),
    PublicationCase(
        "reviewed-conflict-keeps-null",
        '<meta name="dcterms.issued" content="2008-06-02">',
        None,
        True,
        "August 2008",
    ),
)


@pytest.fixture(scope="session")
def wlh_env() -> dict[str, str]:
    """File-only CLI execution has no database, network or model prerequisite."""
    return {}


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def _run(directory: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", *args]
    result = subprocess.run(
        command,
        cwd=directory,
        text=True,
        capture_output=True,
        timeout=45,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "CUDA_VISIBLE_DEVICES": "",
        },
    )
    write_json(
        directory / "cli-result.json",
        {
            "argv": command,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "python": platform.python_version(),
            "prerequisites": "Editable wlh checkout; no network, models, GPU, DB or S3.",
        },
    )
    return result


@pytest.fixture(scope="module")
def publication_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    configured = os.environ.get("WLH_PUBLICATION_METADATA_WORKDIR")
    directory = Path(configured).resolve() if configured else tmp_path_factory.mktemp("publication")
    directory.mkdir(parents=True, exist_ok=True)
    sources: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    truths: dict[str, dict[str, Any]] = {}
    for case in CASES:
        raw = (
            f"<!doctype html><html><head>{case.head}</head><body><main>"
            f"<h1>{TITLE}</h1><p>{case.visible}</p><p>{BODY}</p></main></body></html>"
        )
        digest = hashlib.sha256(raw.encode()).hexdigest()
        sources.append(
            {
                "id": case.name,
                "source_id": "dd-999",
                "task_type": "content_page",
                "jurisdiction": "CA",
                "mime_type": "text/html",
                "request_url": "https://example.gov/notice",
                "raw_payload": raw,
                "payload_sha256": digest,
            }
        )
        truths[case.name] = {
            "records": [
                {
                    "doc_type": "政策文件",
                    "title": TITLE,
                    "doc_number": None,
                    "published_at": case.published_at,
                    "effective_at": None,
                    "issuer": None,
                    "attachment_urls": [],
                    "markdown": f"# {TITLE}\n\n{case.visible}\n\n{BODY}",
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
                "text": json.dumps(truths[case.name], ensure_ascii=False, indent=2),
            }
        )
    bundle, completion = directory / "input.jsonl", directory / "completions.jsonl"
    _write_rows(bundle, sources)
    _write_rows(completion, receipts)
    originals = {path: path.read_bytes() for path in (bundle, completion)}
    result = _run(
        directory,
        [
            "wlh.benchmark.annotator",
            "--validate-completions",
            str(completion),
            "--input",
            str(bundle),
            "--output-dir",
            str(directory / "validated"),
        ],
    )
    accepted = {row["id"]: row for row in read_jsonl(directory / "validated/accepted.jsonl")}
    rejected = {row["id"]: row for row in read_jsonl(directory / "validated/rejected.jsonl")}
    summary = {
        "returncode": result.returncode,
        "sources_and_receipts_unchanged": all(p.read_bytes() == b for p, b in originals.items()),
        "expected_accepted": sum(case.accepted for case in CASES),
        "actual_accepted": len(accepted),
        "actual_rejected": len(rejected),
        "cases": [
            {
                "id": case.name,
                "expected_accepted": case.accepted,
                "actual_accepted": case.name in accepted,
                "passed": (case.name in accepted) == case.accepted,
                "reasons": rejected.get(case.name, {}).get("reasons", []),
            }
            for case in CASES
        ],
    }
    write_json(directory / "assertion-results.json", summary)
    return {
        "directory": directory,
        "sources": sources,
        "receipts": receipts,
        "truths": truths,
        "accepted": accepted,
        "rejected": rejected,
        "summary": summary,
    }


class TestBenchmarkPublicationMetadata:
    @pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
    def test_explicit_publication_evidence_controls_full_completion_admission(
        self,
        publication_run: dict[str, Any],
        case: PublicationCase,
    ) -> None:
        result = publication_run
        if case.accepted:
            assert case.name in result["accepted"], result["rejected"].get(case.name)
            assert result["accepted"][case.name]["ground_truth"] == result["truths"][case.name]
        else:
            assert case.name in result["rejected"], f"Unsupported publication date: {case.name}"
            assert result["rejected"][case.name]["reasons"]

    def test_validation_preserves_complete_input_and_original_model_receipts(
        self,
        publication_run: dict[str, Any],
    ) -> None:
        result = publication_run
        assert result["summary"]["returncode"] != 0
        assert result["summary"]["sources_and_receipts_unchanged"]
        rows = [*result["accepted"].values(), *result["rejected"].values()]
        receipts = {row["id"]: row for row in result["receipts"]}
        assert len(rows) == len(CASES)
        assert {row["id"] for row in rows} == set(receipts)
        for row in rows:
            assert row["completion"]["text"] == receipts[row["id"]]["text"]

    @pytest.mark.parametrize("accepted", (True, False), ids=("supported", "unsupported"))
    def test_reviewed_freeze_preserves_truth_or_refuses_unsupported_publication_dates(
        self,
        publication_run: dict[str, Any],
        accepted: bool,
    ) -> None:
        result = publication_run
        directory = result["directory"] / f"freeze-{accepted}"
        directory.mkdir(parents=True, exist_ok=True)
        cases = {case.name for case in CASES if case.accepted == accepted}
        rows: list[dict[str, Any]] = []
        originals: dict[Path, bytes] = {}
        for source in result["sources"]:
            if source["id"] not in cases:
                continue
            truth = result["truths"][source["id"]]
            archive = directory / f"{source['id']}.html"
            archive.write_bytes(source["raw_payload"].encode())
            originals[archive] = archive.read_bytes()
            rows.append(
                {
                    **source,
                    "ground_truth": truth,
                    "provenance": {
                        "kind": "archived_response",
                        "acquisition_id": source["id"],
                        "archive_path": archive.name,
                        "archive_sha256": source["payload_sha256"],
                        "charset": "utf-8",
                        "representation": "http_body",
                    },
                    "review": {
                        "status": "approved",
                        "reviewer": "independent-publication-contract-review",
                        "payload_sha256": source["payload_sha256"],
                        "ground_truth_sha256": hashlib.sha256(
                            json.dumps(truth, ensure_ascii=False, sort_keys=True).encode()
                        ).hexdigest(),
                        "notes": "Synthetic contract review: conflicting publication semantics retain null; "
                        "a review approval must never bypass missing or invalid date evidence.",
                    },
                }
            )
        bundle, output = directory / "reviewed.jsonl", directory / "frozen"
        _write_rows(bundle, rows)
        originals[bundle] = bundle.read_bytes()
        execution = _run(
            directory,
            [
                "wlh",
                "benchmark",
                "freeze",
                "--input",
                str(bundle),
                "--output-dir",
                str(output),
                "--version",
                "publication-metadata-acceptance-v1",
            ],
        )
        golden_path = output / "ground_truth_benchmark.jsonl"
        golden = read_jsonl(golden_path) if golden_path.is_file() else []
        unchanged = all(p.read_bytes() == b for p, b in originals.items())
        ledger_exists = (output / "annotation_pipeline.json").is_file()
        write_json(
            directory / "assertion-results.json",
            {
                "expected_accepted": accepted,
                "returncode": execution.returncode,
                "inputs_unchanged": unchanged,
                "frozen_ids": [row["id"] for row in golden],
                "ledger_exists": ledger_exists,
            },
        )
        assert unchanged
        assert (execution.returncode == 0) == accepted, execution.stdout + execution.stderr
        assert ledger_exists == accepted
        if accepted:
            frozen = {row["id"]: row for row in golden}
            assert set(frozen) == cases
            for row in rows:
                assert frozen[row["id"]]["raw_payload"] == row["raw_payload"]
                assert frozen[row["id"]]["ground_truth"] == row["ground_truth"]
        else:
            assert not golden
