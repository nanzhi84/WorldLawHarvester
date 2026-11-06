"""Black-box publication evidence contracts specified before implementation.

Complete dates may come from explicit firstpublishedtime metadata. Metadata
presence does not settle page/document roles or approve real benchmark drafts.
"""

from __future__ import annotations

import copy
import json
import os
import platform
import subprocess
import sys
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from tests.benchmark_markdown_support import (
    HTML,
    ROOT,
    compact_text,
    freeze_cases,
    isolated_review,
    public_integrity,
    read_rows,
    rendered_dom,
    reviewed_case,
    sha256,
    validate_receipts,
    write_json,
)
from tests.test_benchmark_publication_metadata import PublicationCase

REAL_ID = "content_dd-501_cand_359"
PAYLOAD_SHA256 = "91c55757447a95907de75173706b089f7f71eea57d44b4b70c2131c56ea492ca"
COMPLETION_SHA256 = "b86ff4ecfa60cd8934d7419cb404f2810d9795c27ed86ec1b9e74b828371268a"
MARKDOWN = "# Publication notice\n\nThe entire notice remains unchanged."
META = '<meta name="firstpublishedtime" content="{}">'

CASES = (
    PublicationCase("complete-leap-day", META.format("2024-02-29"), "2024-02-29", True),
    PublicationCase("hyphen-time", META.format("2024-02-29-17:00:00"), "2024-02-29", True),
    PublicationCase("iso-time", META.format("2024-02-29T17:00:00Z"), "2024-02-29", True),
    PublicationCase("space-time", META.format("2024-02-29 17:00:00+08:00"), "2024-02-29", True),
    PublicationCase("invalid-leap-day", META.format("2023-02-29"), "2023-02-29", False),
    PublicationCase("invalid-rollover", META.format("2023-02-29"), "2023-03-01", False),
    PublicationCase("missing-day", META.format("2024-02"), "2024-02-01", False),
    PublicationCase("missing-month", META.format("2024"), "2024-01-01", False),
    PublicationCase("forged-day", META.format("2026-10-20"), "2026-10-21", False),
    PublicationCase(
        "script-fake-meta",
        f"<script>const text = '{META.format('2024-02-29')}';</script>",
        "2024-02-29",
        False,
    ),
    PublicationCase(
        "arbitrary-attribute",
        '<link data-firstpublishedtime="2024-02-29">',
        "2024-02-29",
        False,
    ),
    PublicationCase(
        "attribute-fake-meta",
        '<link title=\'<meta name="firstpublishedtime" content="2024-02-29">\'>',
        "2024-02-29",
        False,
    ),
    PublicationCase(
        "lookalike-name",
        '<meta name="custom.firstpublishedtime" content="2024-02-29">',
        "2024-02-29",
        False,
    ),
    *(
        PublicationCase(
            f"unrelated-{name}",
            f'<meta name="{name}" content="2024-02-29-17:00:00">',
            "2024-02-29",
            False,
        )
        for name in (
            "lastmodifiedtime",
            "dcterms.created",
            "creation_date",
            "generated",
            "description",
        )
    ),
    PublicationCase("url-only", "", "2024-02-29", False),
    PublicationCase(
        "reviewed-conflict-remains-null",
        META.format("2024-02-29") + '<meta name="dcterms.issued" content="2024-02-28">',
        None,
        True,
    ),
)


@pytest.fixture(scope="module")
def evidence_run() -> tuple[Path, Path]:
    base = ROOT / "work/benchmark-expansion-20261106/first-publication-evidence"
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=base))
    artifacts = (
        ROOT / "artifacts/benchmark-expansion-20261106/first-publication-evidence" / work.name
    )
    write_json(
        artifacts / "environment.json",
        {
            "python": sys.version,
            "platform": platform.platform(),
            "work": str(work),
            "command": "WLH_FIRST_PUBLICATION_REAL_DIR=work/benchmark-expansion-20261106 "
            ".venv/bin/python -m pytest --noconftest -q tests/test_benchmark_first_publication.py",
            "real_input": os.environ.get("WLH_FIRST_PUBLICATION_REAL_DIR"),
            "prerequisites": "Editable checkout and original text-03-1/recovery A/B files; "
            "no network, model, credentials, DB, S3 or GPU.",
            "payload_sha256": PAYLOAD_SHA256,
            "historical_completion_text_sha256": COMPLETION_SHA256,
            "scope": "Evidence admission only; isolated reviews do not approve real drafts.",
        },
    )
    return work, artifacts


@pytest.fixture(scope="module")
def cases(evidence_run: tuple[Path, Path]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for case in CASES:
        raw = (
            f"<!doctype html><html><head>{case.head}</head><body><main>"
            "<h1>Publication notice</h1><p>The entire notice remains unchanged.</p>"
            "</main></body></html>"
        )
        row = reviewed_case(evidence_run[0], case.name, raw, "text/html", MARKDOWN)
        row["ground_truth"]["records"][0]["title"] = "Publication notice"
        row["ground_truth"]["records"][0]["published_at"] = case.published_at
        if case.name == "url-only":
            row["request_url"] = "https://example.gov/2024-02-29/notice"
        row["review"] = isolated_review(row["payload_sha256"], row["ground_truth"])
        rows[case.name] = row
    write_json(
        evidence_run[1] / "input-manifest.json",
        [{"id": r["id"], "payload_sha256": r["payload_sha256"]} for r in rows.values()],
    )
    return rows


class TestBenchmarkFirstPublication:
    @pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
    def test_public_integrity_accepts_only_explicit_complete_publication_days(
        self,
        evidence_run: tuple[Path, Path],
        cases: dict[str, dict[str, Any]],
        case: PublicationCase,
    ) -> None:
        result = public_integrity(cases[case.name])
        write_json(
            evidence_run[1] / f"integrity-{case.name}.json", {**result, "expected": case.accepted}
        )
        assert result["valid"] == case.accepted, result

    def test_completion_validation_preserves_truth_and_rejects_unsupported_days(
        self,
        evidence_run: tuple[Path, Path],
        cases: dict[str, dict[str, Any]],
    ) -> None:
        work, artifacts = evidence_run
        result = validate_receipts(list(cases.values()), work / "receipts", artifacts / "receipts")
        assert result["inputs_unchanged"] and result["raw_receipts_unchanged"]
        expected = {c.name for c in CASES if c.accepted}
        assert {r["id"] for r in result["accepted"]} == expected, result["rejected"]
        assert {r["id"] for r in result["rejected"]} == set(cases) - expected
        assert all(r["ground_truth"] == cases[r["id"]]["ground_truth"] for r in result["accepted"])

    @pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
    def test_reviewed_freeze_keeps_valid_truth_and_refuses_unsupported_publication_days(
        self,
        evidence_run: tuple[Path, Path],
        cases: dict[str, dict[str, Any]],
        case: PublicationCase,
    ) -> None:
        work, artifacts = evidence_run
        name = f"freeze-{case.name}"
        result = freeze_cases([cases[case.name]], work / name, artifacts / name)
        assert result["inputs_unchanged"]
        assert (result["returncode"] == 0) == case.accepted, result
        assert result["published"] == case.accepted
        if case.accepted:
            assert result["complete_payload_and_truth_preserved"]
        else:
            assert not result["exported"]


@pytest.fixture(scope="module")
def real_sample(evidence_run: tuple[Path, Path]) -> Generator[dict[str, Any], None, None]:
    configured = os.environ.get("WLH_FIRST_PUBLICATION_REAL_DIR")
    if not configured:
        pytest.skip(
            "Set WLH_FIRST_PUBLICATION_REAL_DIR to the original benchmark expansion directory"
        )
    base = Path(configured).resolve()
    source = base / "text-03-1.jsonl"
    receipts = {
        s: base / f"colab-text03-recovery-{s}/rejected_{s.upper()}.jsonl" for s in ("a", "b")
    }
    originals = {str(p): sha256(p.read_bytes()) for p in (source, *receipts.values())}
    row = next(r for r in read_rows(source) if r["id"] == REAL_ID)
    assert sha256(row["raw_payload"].encode()) == row["payload_sha256"] == PAYLOAD_SHA256
    completions = {
        s: next(r for r in read_rows(path) if r["id"] == REAL_ID)["completion"]
        for s, path in receipts.items()
    }
    for completion in completions.values():
        assert completion["payload_sha256"] == PAYLOAD_SHA256
        assert sha256(completion["text"].encode()) == COMPLETION_SHA256
    yield {"row": row, "completions": completions}
    after = {path: sha256(Path(path).read_bytes()) for path in originals}
    write_json(
        evidence_run[1] / "historical-immutability.json",
        {"before": originals, "after": after, "unchanged": originals == after},
    )
    assert originals == after


class TestBenchmarkRealFirstPublication:
    def test_full_original_and_both_full_receipts_preserve_document_date_and_every_character(
        self,
        evidence_run: tuple[Path, Path],
        real_sample: dict[str, Any],
    ) -> None:
        source = HTML.fromstring(real_sample["row"]["raw_payload"])
        dates = source.xpath('//head/meta[@name="firstpublishedtime"]/@content')
        assert dates == ["2026-10-20-17:00:00"]
        body = source.xpath(
            '//*[contains(concat(" ",normalize-space(@class)," ")," pages_content ")]'
        )
        assert len(body) == 1
        for completion in real_sample["completions"].values():
            record = json.loads(completion["text"])["records"][0]
            assert record["published_at"] == "2026-10-20"
            assert "2026年10月3日" in record["markdown"]
            assert compact_text(rendered_dom(record["markdown"]).text_content()) == compact_text(
                body[0].text_content()
            )
        write_json(
            evidence_run[1] / "real-full-content.json",
            {
                "source_id": REAL_ID,
                "payload_sha256": PAYLOAD_SHA256,
                "metadata": dates,
                "published_at": "2026-10-20",
                "signature_date": "2026-10-03",
                "both_receipts_match_entire_business_body": True,
            },
        )

    @pytest.mark.parametrize("side", ("a", "b"))
    def test_historical_complete_receipt_passes_actual_validation_without_rewriting(
        self,
        evidence_run: tuple[Path, Path],
        real_sample: dict[str, Any],
        side: str,
    ) -> None:
        work, artifacts = evidence_run
        directory = work / f"real-{side}"
        directory.mkdir()
        bundle, receipt = directory / "input.jsonl", directory / "completion.jsonl"
        row, completion = real_sample["row"], real_sample["completions"][side]
        bundle.write_text(json.dumps(row, ensure_ascii=False) + "\n")
        receipt.write_text(json.dumps(completion, ensure_ascii=False) + "\n")
        before = {str(p): sha256(p.read_bytes()) for p in (bundle, receipt)}
        command = [
            sys.executable,
            "-m",
            "wlh.benchmark.annotator",
            "--validate-completions",
            str(receipt),
            "--input",
            str(bundle),
            "--output-dir",
            str(directory / "validated"),
            "--context-tokens",
            str(completion["budget"]["context_tokens"]),
            "--max-output-tokens",
            str(completion["budget"]["max_output_tokens"]),
            "--safety-tokens",
            str(completion["budget"]["safety_tokens"]),
        ]
        process = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=45,
            env={
                **os.environ,
                "PYTHONPATH": str(ROOT / "src"),
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
            },
        )
        accepted = read_rows(directory / "validated/accepted.jsonl")
        rejected = read_rows(directory / "validated/rejected.jsonl")
        observation = {
            "command": command,
            "stdout": process.stdout,
            "stderr": process.stderr,
            "returncode": process.returncode,
            "accepted_ids": [r["id"] for r in accepted],
            "rejected": rejected,
            "input_and_receipt_unchanged": all(
                sha256(Path(p).read_bytes()) == h for p, h in before.items()
            ),
            "completion_preserved": all(
                r["id"] == completion["id"]
                and all(r["completion"].get(k) == v for k, v in completion.items() if k != "id")
                and sha256(r["completion"]["text"].encode()) == COMPLETION_SHA256
                for r in accepted + rejected
            ),
        }
        write_json(artifacts / f"real-{side}-validation.json", observation)
        assert observation["input_and_receipt_unchanged"] and observation["completion_preserved"]
        assert process.returncode == 0 and not rejected, observation
        assert len(accepted) == 1 and accepted[0]["ground_truth"] == json.loads(completion["text"])

    def test_isolated_review_freezes_complete_original_and_truth_without_selecting_another_date(
        self,
        evidence_run: tuple[Path, Path],
        real_sample: dict[str, Any],
    ) -> None:
        work, artifacts = evidence_run
        row = copy.deepcopy(real_sample["row"])
        archive = work / "complete-original.html"
        archive.write_text(row["raw_payload"], encoding="utf-8")
        row["ground_truth"] = json.loads(real_sample["completions"]["a"]["text"])
        row["provenance"] = {
            "kind": "archived_response",
            "acquisition_id": "isolated-first-publication",
            "archive_path": str(archive),
            "archive_sha256": PAYLOAD_SHA256,
            "charset": "utf-8",
            "representation": "http_body",
        }
        row["review"] = isolated_review(PAYLOAD_SHA256, row["ground_truth"])
        result = freeze_cases([row], work / "real-freeze", artifacts / "real-freeze")
        assert result["inputs_unchanged"] and sha256(archive.read_bytes()) == PAYLOAD_SHA256
        assert result["returncode"] == 0 and result["published"], result
        assert result["complete_payload_and_truth_preserved"]
