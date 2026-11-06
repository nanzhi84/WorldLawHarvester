"""Public file/CLI contracts for bounded native links, dates and table metadata.

Written before implementation, independently of quality/date_evidence internals.
The immutable real sources are never approved here: only isolated test copies are.
"""

from __future__ import annotations

import copy
import json
import platform
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.benchmark_markdown_support import (
    ROOT,
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

BASE = ROOT / "work/benchmark-expansion-20261106"
ARTIFACTS = ROOT / "artifacts/benchmark-expansion-20261106/structured-evidence-contract"
REAL_FILES = (
    BASE / "reviewed-32.jsonl",
    BASE / "colab-02-pending-corrections-01/pending-archive-metadata-media-12.jsonl",
)
REAL_IDS = (
    "content_dd-557_cand_559",
    "content_dd-511_cand_414",
    "content_dd-132_cand_146",
    "content_dd-520_cand_485",
)
TITLE = "原文结构证据规则"
TAIL = "完整原文和全部适用条件必须逐项保留，不得任意省略、更换或增补内容。"
NAMES = "資産評価企画官情報<br>資産課税課情報"
NUMBERS = "第3号<br>第15号"
PAIRED = "資産評価企画官情報第3号・資産課税課情報第15号"
TABLE = (
    f"<table><tr><td>{NAMES}</td><td>{NUMBERS}</td>"
    "<td>令和3年7月6日</td><td>国税庁</td></tr></table>"
)


@dataclass(frozen=True)
class EvidenceCase:
    name: str
    row: dict[str, Any]
    accepted: bool


def _copy_reviewed(
    work: Path, name: str, source: dict[str, Any], truth: dict[str, Any]
) -> dict[str, Any]:
    row = reviewed_case(work, name, source["raw_payload"], source["mime_type"], "")
    for key in ("source_id", "jurisdiction", "request_url"):
        row[key] = source[key]
    row["ground_truth"] = copy.deepcopy(truth)
    row["review"] = isolated_review(row["payload_sha256"], row["ground_truth"])
    return row


def _native(
    work: Path,
    name: str,
    body: str,
    markdown: str,
    field: str,
    value: str,
    accepted: bool,
    jurisdiction: str = "TW",
) -> EvidenceCase:
    raw = f"<article><h1>{TITLE}</h1>{body}<p>{TAIL}</p></article>"
    row = reviewed_case(work, name, raw, "text/html", f"# {TITLE}\n\n{markdown}\n\n{TAIL}")
    row["jurisdiction"] = jurisdiction
    row["ground_truth"]["records"][0].update(title=TITLE, **{field: value})
    row["review"] = isolated_review(row["payload_sha256"], row["ground_truth"])
    return EvidenceCase(name, row, accepted)


def _real_cases(work: Path) -> list[EvidenceCase]:
    found: dict[str, dict[str, Any]] = {}
    for path in REAL_FILES:
        assert path.is_file(), f"Required complete acquired native input is missing: {path}"
        for item in read_rows(path):
            if item["id"] in REAL_IDS:
                found[item["id"]] = item
    assert set(found) == set(REAL_IDS)
    cases: list[EvidenceCase] = []
    for ident in REAL_IDS:
        item = found[ident]
        source = item.get("source", item)
        truth = item.get("proposed_truth", item.get("ground_truth"))
        assert truth is not None
        assert sha256(source["raw_payload"].encode()) == source["payload_sha256"]
        row = _copy_reviewed(work, ident, source, truth)
        cases.append(EvidenceCase(ident, row, True))
    us = cases[0].row
    original = "/us/usc/t18/s2252–2252A"
    assert original in us["raw_payload"]
    rendered = rendered_dom(us["ground_truth"]["records"][0]["markdown"])
    assert "/us/usc/t18/s2252%E2%80%932252A" in rendered.xpath(".//a/@href")
    for name, target in (
        ("xml-link-altered-tail", "/us/usc/t18/s2252–2252B"),
        ("xml-link-unattested-query", original + "?invented=1"),
        ("xml-link-different-source", "https://unattested.example" + original),
    ):
        truth = copy.deepcopy(us["ground_truth"])
        truth["records"][0]["markdown"] = truth["records"][0]["markdown"].replace(
            f"]({original})", f"]({target})"
        )
        assert truth != us["ground_truth"]
        cases.append(EvidenceCase(name, _copy_reviewed(work, name, us, truth), False))
    return cases


def _date_cases(work: Path) -> list[EvidenceCase]:
    cases: list[EvidenceCase] = []
    full = "業經本部於107年4月11日修正發布，並自4月1日生效。"
    cases.append(
        _native(
            work,
            "same-sentence-year-anchor",
            f"<p>{full}</p>",
            full,
            "effective_at",
            "2018-04-01",
            True,
        )
    )
    roc = "<table><tr><th>公發布日</th><td>民國 80 年 06 月 29 日</td></tr></table>"
    body = "本辦法自80年6月1日起施行。"
    for name, date, accepted in (
        ("explicit-page-roc-short-date", "1991-06-01", True),
        ("roc-date-as-year-eighty", "0080-06-01", False),
    ):
        cases.append(
            _native(
                work,
                name,
                roc + f"<p>{body}</p>",
                roc + "\n\n" + body,
                "effective_at",
                date,
                accepted,
            )
        )
    cases.append(
        _native(
            work,
            "foreign-short-year-no-roc",
            f"<p>{body}</p>",
            body,
            "effective_at",
            "1991-06-01",
            False,
            "CN",
        )
    )
    for name, html_body, markdown in (
        ("month-day-without-year", "<p>本辦法並自4月1日生效。</p>", "本辦法並自4月1日生效。"),
        (
            "year-in-different-paragraph",
            "<p>本部於107年4月11日發布說明。</p><p>本辦法自4月1日生效。</p>",
            "本部於107年4月11日發布說明。\n\n本辦法自4月1日生效。",
        ),
        (
            "conflicting-years-in-sentence",
            "<p>本辦法於107年4月11日或108年4月11日修正發布，並自4月1日生效。</p>",
            "本辦法於107年4月11日或108年4月11日修正發布，並自4月1日生效。",
        ),
        (
            "year-in-different-record",
            "<section><h2>甲規則</h2><p>本部於107年4月11日發布。</p>"
            "</section><section><h2>乙規則</h2><p>自4月1日生效。</p></section>",
            "## 甲規則\n\n本部於107年4月11日發布。\n\n## 乙規則\n\n自4月1日生效。",
        ),
    ):
        cases.append(_native(work, name, html_body, markdown, "effective_at", "2018-04-01", False))
    invalid = "本辦法於107年4月11日修正發布，並自2月29日生效。"
    cases.append(
        _native(
            work,
            "inherited-year-invalid-calendar",
            f"<p>{invalid}</p>",
            invalid,
            "effective_at",
            "2018-02-29",
            False,
        )
    )
    return cases


def _number_cases(work: Path) -> list[EvidenceCase]:
    cases: list[EvidenceCase] = []
    for name, number, accepted in (
        ("paired-number-lines", PAIRED, True),
        ("paired-number-swapped", "資産評価企画官情報第15号・資産課税課情報第3号", False),
        ("paired-number-forged", "資産評価企画官情報第4号・資産課税課情報第15号", False),
        ("paired-number-omitted", "資産評価企画官情報第3号", False),
    ):
        cases.append(_native(work, name, TABLE, TABLE, "doc_number", number, accepted, "JP"))
    for name, table in (
        (
            "pairing-across-tables",
            f"<table><tr><td>{NAMES}</td></tr></table><table><tr><td>{NUMBERS}</td></tr></table>",
        ),
        (
            "pairing-across-rows",
            f"<table><tr><td>{NAMES}</td></tr><tr><td>{NUMBERS}</td></tr></table>",
        ),
        ("pairing-mismatched-line-count", TABLE.replace(NUMBERS, "第3号")),
        ("pairing-without-number-structure", TABLE.replace(NUMBERS, "3<br>15")),
    ):
        cases.append(_native(work, name, table, table, "doc_number", PAIRED, False, "JP"))
    return cases


@pytest.fixture(scope="session")
def wlh_env() -> dict[str, str]:
    """File-only public acceptance does not need DB, CAS or model services."""
    return {}


@pytest.fixture(scope="module")
def evidence_run() -> dict[str, Any]:
    run_root = BASE / "structured-evidence-contract"
    run_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=run_root))
    artifacts = ARTIFACTS / work.name
    originals = {str(path): sha256(path.read_bytes()) for path in REAL_FILES}
    cases = _real_cases(work) + _date_cases(work) + _number_cases(work)
    write_json(
        artifacts / "environment.json",
        {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "work": str(work),
            "real_input_hashes": originals,
            "command": ".venv/bin/python -m pytest --noconftest -q "
            "tests/test_benchmark_structured_evidence.py",
            "scope": "Offline native HTML/XML/JSON; no binary body, original receipt or formal GT changes",
        },
    )
    integrity = {case.name: public_integrity(case.row) for case in cases}
    write_json(artifacts / "integrity.json", list(integrity.values()))
    receipt = validate_receipts([case.row for case in cases], work / "receipts", artifacts)
    receipt_ids = {row["id"] for row in receipt["accepted"]}
    freeze = {
        case.name: freeze_cases([case.row], work / case.name, artifacts / case.name)
        for case in cases
    }
    unchanged = {str(path): sha256(path.read_bytes()) for path in REAL_FILES} == originals
    assertions = [
        {
            "id": case.name,
            "expected_accepted": case.accepted,
            "integrity": integrity[case.name],
            "receipt_accepted": case.name in receipt_ids,
            "freeze_accepted": freeze[case.name]["returncode"] == 0,
            "integrity_passed": integrity[case.name]["valid"] == case.accepted,
            "receipt_passed": (case.name in receipt_ids) == case.accepted,
            "freeze_passed": (freeze[case.name]["returncode"] == 0) == case.accepted,
        }
        for case in cases
    ]
    write_json(
        artifacts / "assertions.json",
        {
            "case_count": len(cases),
            "original_sources_unchanged": unchanged,
            "receipt_inputs_unchanged": receipt["inputs_unchanged"],
            "raw_receipts_unchanged": receipt["raw_receipts_unchanged"],
            "cases": assertions,
        },
    )
    write_json(ARTIFACTS / "latest-run.json", {"artifacts": str(artifacts), "work": str(work)})
    return {
        "cases": cases,
        "integrity": integrity,
        "receipt": receipt,
        "receipt_ids": receipt_ids,
        "freeze": freeze,
        "unchanged": unchanged,
    }


class TestBenchmarkStructuredEvidence:
    @pytest.mark.parametrize("entrypoint", ("integrity", "receipt", "freeze"))
    def test_native_evidence_and_adversarial_boundaries_match_public_admission_contract(
        self,
        evidence_run: dict[str, Any],
        entrypoint: str,
    ) -> None:
        failures: list[dict[str, Any]] = []
        for case in evidence_run["cases"]:
            if entrypoint == "integrity":
                observed = evidence_run["integrity"][case.name]["valid"]
            elif entrypoint == "receipt":
                observed = case.name in evidence_run["receipt_ids"]
            else:
                observed = evidence_run["freeze"][case.name]["returncode"] == 0
            if observed != case.accepted:
                failures.append(
                    {
                        "id": case.name,
                        "expected": case.accepted,
                        "actual": observed,
                        "integrity": evidence_run["integrity"][case.name],
                    }
                )
        assert not failures, json.dumps(failures, ensure_ascii=False, indent=2)

    def test_source_and_receipt_preservation_and_freeze_publication_remain_exact(
        self,
        evidence_run: dict[str, Any],
    ) -> None:
        assert evidence_run["unchanged"]
        receipt = evidence_run["receipt"]
        assert receipt["inputs_unchanged"] and receipt["raw_receipts_unchanged"]
        for case in evidence_run["cases"]:
            result = evidence_run["freeze"][case.name]
            assert result["inputs_unchanged"]
            if result["returncode"] == 0:
                assert result["published"] and result["complete_payload_and_truth_preserved"]
            else:
                assert not result["published"] and not result["exported"]
