"""Full-source CLI acceptance of explicitly evidenced date normalization.

Independent synthetic source/receipt pairs cover complete English dates and
Japanese era XML attributes. No models, network, database or mocks are used.
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

ROOT = Path(__file__).resolve().parents[1]
EN_TITLE = "Date Evidence Regulation"
EN_BODY = (
    "Section 1. Preserve every provision and the complete source citation. "
    "Section 2. An unknown date must remain unknown; no missing date part may be invented."
)
JP_TITLE = "日付根拠規則"
JP_BODY = (
    "第一条 この規則は、文書に記された日付の根拠を保存する。"
    "第二条 日付が不明な場合は、存在しない日付を補わない。"
)


@dataclass(frozen=True)
class DateEvidenceCase:
    name: str
    payload: str
    markdown: str
    date: str
    accepted: bool
    jurisdiction: str = "US"
    request_url: str = "https://example.gov/legal/document"


def english_case(
    name: str, citation_date: str, date: str, *, accepted: bool = True
) -> DateEvidenceCase:
    citation = f"76 FR 6312, {citation_date}, unless otherwise noted."
    return DateEvidenceCase(
        name=name,
        payload=(
            f"<DIV1><HEAD>{EN_TITLE}</HEAD><P>{EN_BODY}</P><SOURCE>{citation}</SOURCE></DIV1>"
        ),
        markdown=f"# {EN_TITLE}\n\n{EN_BODY}\n\n{citation}",
        date=date,
        accepted=accepted,
    )


def era_case(name: str, attributes: str, date: str, *, accepted: bool = True) -> DateEvidenceCase:
    return DateEvidenceCase(
        name=name,
        payload=(
            f"<Law {attributes}><LawBody><LawTitle>{JP_TITLE}</LawTitle>"
            f"<MainProvision>{JP_BODY}</MainProvision></LawBody></Law>"
        ),
        markdown=f"# {JP_TITLE}\n\n{JP_BODY}",
        date=date,
        accepted=accepted,
        jurisdiction="JP",
    )


def hyphen_date_cases() -> tuple[DateEvidenceCase, ...]:
    """Published-date claims must be dates, not reordered tokens or unrelated numbers."""
    examples = [
        (
            "table",
            "<table><tr><td>Ngày ban hành</td><td>22-10-2026</td></tr></table>",
            # Preserve cells: this contract tests literal dates, not prose
            # fabricated by joining a table label and value without structure.
            "<table><tr><td>Ngày ban hành</td><td>22-10-2026</td></tr></table>",
            "2026-10-22",
            True,
        ),
        (
            "inline",
            "<p>Ngày ban hành: <span>22</span>-<b>10</b>-2026</p>",
            "Ngày ban hành: 22-10-2026",
            "2026-10-22",
            True,
        ),
        (
            "href",
            '<p><a href="/documents/22-10-2026">Document</a></p>',
            "[Document](https://example.gov/documents/22-10-2026)",
            "2026-10-22",
            False,
        ),
        (
            "paragraphs",
            "<p>Ngày ban hành: 22-</p><p>10-2026</p>",
            "Ngày ban hành: 22-\n\n10-2026",
            "2026-10-22",
            False,
        ),
        (
            "rows",
            "<table><tr><td>Ngày ban hành: 22-</td></tr><tr><td>10-2026</td></tr></table>",
            "Ngày ban hành: 22-\n\n10-2026",
            "2026-10-22",
            False,
        ),
        ("script", '<script>"Ngày ban hành: 22-10-2026"</script>', "", "2026-10-22", False),
    ]
    examples.extend(
        (name, f"<p>{visible}</p>", visible, date, accepted)
        for name, visible, date, accepted in (
            ("leap-day", "Ngày ban hành: 29-02-2024", "2024-02-29", True),
            ("iso-order", "Ngày ban hành: 2026-02-10", "2026-02-10", True),
            ("iso-reversed", "Ngày ban hành: 2026-02-10", "2026-10-02", False),
            ("version", "Version: 22-10-2026", "2026-10-22", False),
            ("visible-url", "https://example.gov/documents/22-10-2026", "2026-10-22", False),
            ("impossible-day", "Ngày ban hành: 31-04-2026", "2026-05-01", False),
            ("invalid-leap-day", "Ngày ban hành: 29-02-2026", "2026-03-01", False),
            ("request-url", "No date given.", "2026-10-22", False),
        )
    )
    return tuple(
        DateEvidenceCase(
            name=f"dmy-{name}",
            payload=f"<article><h1>{EN_TITLE}</h1>{html}<p>{EN_BODY}</p></article>",
            markdown=f"# {EN_TITLE}\n\n{visible}\n\n{EN_BODY}",
            date=date,
            accepted=accepted,
            jurisdiction="VN",
            request_url=(
                "https://example.gov/documents/22-10-2026"
                if name == "request-url"
                else "https://example.gov/legal/document"
            ),
        )
        for name, html, visible, date, accepted in examples
    )


def date_cases() -> tuple[DateEvidenceCase, ...]:
    cases: list[DateEvidenceCase] = []
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    for number, month in enumerate(months, 1):
        for punctuation in ("", "."):
            suffix = "dot" if punctuation else "no-dot"
            cases.append(
                english_case(
                    f"english-{month.lower()}-{suffix}",
                    f"{month}{punctuation} 4, 2011",
                    f"2011-{number:02d}-04",
                )
            )
    for era, year, expected in (
        ("Meiji", "28", "1895-04-05"),
        ("Taisho", "5", "1916-04-05"),
        ("Showa", "25", "1950-04-05"),
        ("Heisei", "23", "2011-04-05"),
        ("Reiwa", "2", "2020-04-05"),
    ):
        cases.append(
            era_case(
                f"era-{era.lower()}",
                f'Era="{era}" Year="{year}" PromulgateMonth="04" PromulgateDay="05"',
                expected,
            )
        )
    cases.extend(
        [
            english_case("english-valid-leap-day", "Feb. 29, 2012", "2012-02-29"),
            era_case(
                "era-valid-leap-day",
                'Era="Heisei" Year="24" PromulgateMonth="02" PromulgateDay="29"',
                "2012-02-29",
            ),
            english_case("english-month-year-only", "Feb. 2011", "2011-02-04", accepted=False),
            english_case("english-no-year", "Feb. 4", "2011-02-04", accepted=False),
            english_case("english-year-only", "2011", "2011-02-04", accepted=False),
            english_case("english-invalid-leap-day", "Feb. 29, 2011", "2011-02-29", accepted=False),
            english_case(
                "english-invalid-month-day", "Apr. 31, 2011", "2011-04-31", accepted=False
            ),
            era_case(
                "era-invalid-leap-day",
                'Era="Heisei" Year="23" PromulgateMonth="02" PromulgateDay="29"',
                "2011-02-29",
                accepted=False,
            ),
            era_case(
                "era-invalid-month",
                'Era="Heisei" Year="23" PromulgateMonth="13" PromulgateDay="04"',
                "2011-13-04",
                accepted=False,
            ),
        ]
    )
    for name, attributes in (
        ("missing-era", 'Year="28" PromulgateMonth="04" PromulgateDay="05"'),
        ("missing-year", 'Era="Meiji" PromulgateMonth="04" PromulgateDay="05"'),
        ("missing-month", 'Era="Meiji" Year="28" PromulgateDay="05"'),
        ("missing-day", 'Era="Meiji" Year="28" PromulgateMonth="04"'),
    ):
        cases.append(era_case(f"era-{name}", attributes, "1895-04-05", accepted=False))
    for name, url in (
        ("no-date-evidence", "https://example.gov/legal/document"),
        ("request-url-date-only", "https://example.gov/legal/2011/02/04/document"),
    ):
        cases.append(
            DateEvidenceCase(
                name=name,
                payload=f"<article><h1>{EN_TITLE}</h1><p>{EN_BODY}</p></article>",
                markdown=f"# {EN_TITLE}\n\n{EN_BODY}",
                date="2011-02-04",
                accepted=False,
                request_url=url,
            )
        )
    return (*cases, *hyphen_date_cases())


CASES = date_cases()


@pytest.fixture(scope="session")
def wlh_env() -> dict[str, str]:
    """The real completion CLI is file-only and needs no integration services."""
    return {}


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    assert path.is_file(), f"Missing CLI evidence: {path}"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture(scope="module")
def date_evidence_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    configured_workdir = os.environ.get("WLH_DATE_EVIDENCE_WORKDIR")
    directory = (
        Path(configured_workdir).resolve()
        if configured_workdir
        else tmp_path_factory.mktemp("benchmark-date-evidence")
    )
    directory.mkdir(parents=True, exist_ok=True)
    sources: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    truths: dict[str, dict[str, Any]] = {}
    for case in CASES:
        payload_sha = hashlib.sha256(case.payload.encode("utf-8")).hexdigest()
        sources.append(
            {
                "id": case.name,
                "task_type": "content_page",
                "source_id": "dd-999",
                "jurisdiction": case.jurisdiction,
                "request_url": case.request_url,
                "mime_type": "application/xml" if case.jurisdiction == "JP" else "text/html",
                "raw_payload": case.payload,
                "payload_sha256": payload_sha,
            }
        )
        truth: dict[str, Any] = {
            "records": [
                {
                    "doc_type": "法律法规",
                    "title": JP_TITLE if case.jurisdiction == "JP" else EN_TITLE,
                    "doc_number": None,
                    "published_at": case.date,
                    "effective_at": None,
                    "issuer": None,
                    "attachment_urls": [],
                    "markdown": case.markdown,
                }
            ]
        }
        truths[case.name] = truth
        receipts.append(
            {
                "id": case.name,
                "payload_sha256": payload_sha,
                "prompt_tokens": 300,
                "output_tokens": 200,
                "finish_reason": "stop",
                "text": json.dumps(truth, ensure_ascii=False, indent=2),
            }
        )
    bundle = directory / "input.jsonl"
    completions = directory / "completions.jsonl"
    for path, rows in ((bundle, sources), (completions, receipts)):
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
        )
    original_source = bundle.read_bytes()
    original_receipts = completions.read_bytes()
    command = [
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
    write_json(
        directory / "cli-result.json",
        {
            "argv": command,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "prerequisites": "Editable wlh checkout; no network, models, GPU, DB or S3 required.",
        },
    )
    accepted = read_jsonl(directory / "validated/accepted.jsonl")
    rejected = read_jsonl(directory / "validated/rejected.jsonl")
    accepted_ids = {row["id"] for row in accepted}
    rejected_by_id = {row["id"]: row for row in rejected}
    summary = {
        "expected_accepted": sum(case.accepted for case in CASES),
        "expected_rejected": sum(not case.accepted for case in CASES),
        "actual_accepted": len(accepted),
        "actual_rejected": len(rejected),
        "source_unchanged": bundle.read_bytes() == original_source,
        "receipts_unchanged": completions.read_bytes() == original_receipts,
        "cases": [
            {
                "id": case.name,
                "expected": "accepted" if case.accepted else "rejected",
                "actual": "accepted" if case.name in accepted_ids else "rejected",
                "passed": (case.name in accepted_ids) == case.accepted,
                "reasons": rejected_by_id.get(case.name, {}).get("reasons", []),
                "published_at": case.date,
            }
            for case in CASES
        ],
    }
    write_json(directory / "assertion-results.json", summary)
    return {
        "directory": directory,
        "accepted": {row["id"]: row for row in accepted},
        "rejected": rejected_by_id,
        "sources": sources,
        "receipts": receipts,
        "truths": truths,
        "summary": summary,
        "returncode": result.returncode,
    }


class TestBenchmarkDateEvidence:
    @pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
    def test_complete_source_date_evidence_controls_normalized_truth_admission(
        self, date_evidence_run: dict[str, Any], case: DateEvidenceCase
    ) -> None:
        accepted = date_evidence_run["accepted"]
        rejected = date_evidence_run["rejected"]
        if case.accepted:
            assert case.name in accepted, rejected.get(case.name)
            assert accepted[case.name]["ground_truth"] == date_evidence_run["truths"][case.name]
        else:
            assert case.name in rejected, f"Unevidenced or invalid date was accepted: {case.name}"
            assert rejected[case.name]["reasons"]

    def test_mixed_receipts_preserve_full_sources_and_raw_completion_evidence(
        self, date_evidence_run: dict[str, Any]
    ) -> None:
        result = date_evidence_run
        assert result["returncode"] != 0, "The mixed batch must report invalid annotations"
        assert result["summary"]["source_unchanged"]
        assert result["summary"]["receipts_unchanged"]
        receipt_by_id = {row["id"]: row for row in result["receipts"]}
        rows = [*result["accepted"].values(), *result["rejected"].values()]
        assert len(rows) == len(CASES)
        assert {row["id"] for row in rows} == {case.name for case in CASES}
        for row in rows:
            assert row["completion"]["text"] == receipt_by_id[row["id"]]["text"]
        for source, case in zip(result["sources"], CASES, strict=True):
            assert source["raw_payload"] == case.payload
            assert source["payload_sha256"] == hashlib.sha256(case.payload.encode()).hexdigest()


class TestBenchmarkHyphenDateFreeze:
    @pytest.mark.parametrize("expected_accepted", (True, False), ids=("valid", "invalid"))
    def test_freeze_preserves_complete_dated_truth_and_rejects_unsupported_claims(
        self, date_evidence_run: dict[str, Any], expected_accepted: bool
    ) -> None:
        cases = [case for case in hyphen_date_cases() if case.accepted == expected_accepted]
        directory = date_evidence_run["directory"] / f"freeze-{expected_accepted}"
        directory.mkdir(parents=True, exist_ok=True)
        reviewed: list[dict[str, Any]] = []
        originals: dict[Path, bytes] = {}
        sources = {row["id"]: row for row in date_evidence_run["sources"]}
        for case in cases:
            source = dict(sources[case.name])
            truth = date_evidence_run["truths"][case.name]
            archive = directory / f"{case.name}.html"
            archive.write_bytes(case.payload.encode())
            originals[archive] = archive.read_bytes()
            source.update(
                ground_truth=truth,
                provenance={
                    "kind": "archived_response",
                    "acquisition_id": case.name,
                    "archive_path": archive.name,
                    "archive_sha256": source["payload_sha256"],
                    "charset": "utf-8",
                    "representation": "http_body",
                },
                review={
                    "status": "approved",
                    "reviewer": "independent-date-contract-acceptance",
                    "payload_sha256": source["payload_sha256"],
                    "ground_truth_sha256": hashlib.sha256(
                        json.dumps(truth, ensure_ascii=False, sort_keys=True).encode()
                    ).hexdigest(),
                    "notes": "Synthetic acceptance inputs; explicit date contract reviewed independently.",
                },
            )
            reviewed.append(source)
        bundle = directory / "reviewed.jsonl"
        bundle.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in reviewed))
        originals[bundle] = bundle.read_bytes()
        output = directory / "frozen"
        command = [
            sys.executable,
            "-m",
            "wlh",
            "benchmark",
            "freeze",
            "--input",
            str(bundle),
            "--output-dir",
            str(output),
            "--version",
            "hyphen-date-acceptance-v1",
        ]
        result = subprocess.run(
            command,
            cwd=directory,
            text=True,
            capture_output=True,
            timeout=45,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        )
        golden_path = output / "ground_truth_benchmark.jsonl"
        golden = read_jsonl(golden_path) if golden_path.is_file() else []
        unchanged = all(path.read_bytes() == value for path, value in originals.items())
        write_json(
            directory / "assertion-results.json",
            {
                "argv": command,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "expected_accepted": expected_accepted,
                "inputs_unchanged": unchanged,
                "frozen_ids": [row["id"] for row in golden],
                "ledger_exists": (output / "annotation_pipeline.json").is_file(),
            },
        )
        assert unchanged
        assert (result.returncode == 0) == expected_accepted, result.stdout + result.stderr
        assert (output / "annotation_pipeline.json").is_file() == expected_accepted
        if expected_accepted:
            frozen_by_id = {row["id"]: row for row in golden}
            assert set(frozen_by_id) == {row["id"] for row in reviewed}
            for source in reviewed:
                assert frozen_by_id[source["id"]]["raw_payload"] == source["raw_payload"]
                assert frozen_by_id[source["id"]]["ground_truth"] == source["ground_truth"]
        else:
            assert not golden
