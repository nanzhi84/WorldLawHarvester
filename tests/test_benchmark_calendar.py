"""Real CLI contracts for source-scoped ROC date evidence and immutable receipts."""

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
TITLE = "公開資料保存規則"
BODY = "第一條 主管機關應保存完整公開資料。第二條 未載明之日期不得推測補入。"


@dataclass(frozen=True)
class CalendarCase:
    name: str
    html: str
    visible: str
    date: str
    accepted: bool
    jurisdiction: str | None = "TW"
    request_url: str = "https://example.gov/legal/document"


CASES = (
    CalendarCase(
        "tw-published-date",
        "<li>發布日期：105-12-28</li>",
        "發布日期：105-12-28",
        "2016-12-28",
        True,
    ),
    CalendarCase(
        "tw-one-digit-year", "<p>發文日期：1-01-01</p>", "發文日期：1-01-01", "1912-01-01", True
    ),
    CalendarCase(
        "tw-two-digit-year", "<p>發布日期：99-12-30</p>", "發布日期：99-12-30", "2010-12-30", True
    ),
    CalendarCase(
        "tw-valid-leap-day", "<p>發文日期：105-02-29</p>", "發文日期：105-02-29", "2016-02-29", True
    ),
    CalendarCase(
        "other-jurisdiction",
        "<li>發布日期：105-12-28</li>",
        "發布日期：105-12-28",
        "2016-12-28",
        False,
        "CN",
    ),
    CalendarCase(
        "version-is-not-date",
        "<p>版本編號：105-12-28</p>",
        "版本編號：105-12-28",
        "2016-12-28",
        False,
    ),
    CalendarCase(
        "request-url-is-not-date",
        "<p>本頁未載明日期。</p>",
        "本頁未載明日期。",
        "2016-12-28",
        False,
        request_url="https://example.gov/legal/105-12-28/document",
    ),
    CalendarCase(
        "link-url-is-not-date",
        '<p>發布日期：<a href="https://example.gov/legal/105-12-28">未載明</a></p>',
        "發布日期：[未載明](https://example.gov/legal/105-12-28)",
        "2016-12-28",
        False,
    ),
    CalendarCase(
        "script-is-not-date",
        '<script>const metadata = "發布日期：105-12-28";</script><p>本頁未載明日期。</p>',
        "本頁未載明日期。",
        "2016-12-28",
        False,
    ),
    CalendarCase(
        "cross-block-fragments",
        "<p>發布日期：105-</p><p>12-28</p>",
        "發布日期：105-\n\n12-28",
        "2016-12-28",
        False,
    ),
    CalendarCase(
        "invalid-calendar-day",
        "<li>發布日期：105-02-30</li>",
        "發布日期：105-02-30",
        "2016-03-01",
        False,
    ),
)


@pytest.fixture(scope="session")
def wlh_env() -> dict[str, str]:
    """The file-only CLI contract requires no database or object store."""
    return {}


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def _read_rows(path: Path) -> list[dict[str, Any]]:
    assert path.is_file(), f"Missing CLI evidence: {path}"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _source_and_truth(case: CalendarCase) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = f"<!doctype html><html><body><article><h1>{TITLE}</h1>{case.html}"
    raw += f"<p>{BODY}</p></article></body></html>"
    source: dict[str, Any] = {
        "id": case.name,
        "task_type": "content_page",
        "source_id": "dd-999",
        "request_url": case.request_url,
        "mime_type": "text/html",
        "raw_payload": raw,
        "payload_sha256": _sha(raw.encode()),
    }
    if case.jurisdiction is not None:
        source["jurisdiction"] = case.jurisdiction
    truth: dict[str, Any] = {
        "records": [
            {
                "doc_type": "法律法规",
                "title": TITLE,
                "doc_number": None,
                "published_at": case.date,
                "effective_at": None,
                "issuer": None,
                "attachment_urls": [],
                "markdown": f"# {TITLE}\n\n{case.visible}\n\n{BODY}",
            }
        ]
    }
    return source, truth


def _cli(directory: Path, module: str, *args: str) -> subprocess.CompletedProcess[str]:
    directory.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", module, *args]
    result = subprocess.run(
        command,
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=45,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "CUDA_VISIBLE_DEVICES": "",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
        },
    )
    _write_json(
        directory / "cli-result.json",
        {
            "argv": command,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "prerequisites": "Installed wlh dev environment; file-only, no models/GPU/DB/network.",
        },
    )
    return result


@pytest.fixture(scope="module")
def calendar_directory(tmp_path_factory: pytest.TempPathFactory) -> Path:
    configured = os.environ.get("WLH_CALENDAR_WORKDIR")
    directory = Path(configured).resolve() if configured else tmp_path_factory.mktemp("calendar")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@pytest.fixture(scope="module")
def calendar_validation(calendar_directory: Path) -> dict[str, Any]:
    directory = calendar_directory / "validation"
    sources: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    truths: dict[str, dict[str, Any]] = {}
    for case in CASES:
        source, truth = _source_and_truth(case)
        sources.append(source)
        truths[case.name] = truth
        receipts.append(
            {
                "id": case.name,
                "payload_sha256": source["payload_sha256"],
                "prompt_tokens": 300,
                "output_tokens": 200,
                "finish_reason": "stop",
                "text": json.dumps(truth, ensure_ascii=False, indent=2),
            }
        )
    bundle, completions = directory / "input.jsonl", directory / "completions.jsonl"
    _write_rows(bundle, sources)
    _write_rows(completions, receipts)
    originals = {path: path.read_bytes() for path in (bundle, completions)}
    result = _cli(
        directory,
        "wlh.benchmark.annotator",
        "--validate-completions",
        str(completions),
        "--input",
        str(bundle),
        "--output-dir",
        str(directory / "validated"),
    )
    accepted = {row["id"]: row for row in _read_rows(directory / "validated/accepted.jsonl")}
    rejected = {row["id"]: row for row in _read_rows(directory / "validated/rejected.jsonl")}
    summary = {
        "returncode": result.returncode,
        "inputs_unchanged": all(path.read_bytes() == data for path, data in originals.items()),
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
    _write_json(directory / "assertion-results.json", summary)
    return {
        "accepted": accepted,
        "rejected": rejected,
        "truths": truths,
        "receipts": {row["id"]: row for row in receipts},
        "summary": summary,
    }


def _freeze(directory: Path, cases: tuple[CalendarCase, ...]) -> dict[str, Any]:
    reviewed: list[dict[str, Any]] = []
    originals: dict[Path, bytes] = {}
    for case in cases:
        source, truth = _source_and_truth(case)
        archive = directory / "archive" / f"{case.name}.html"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(source["raw_payload"], encoding="utf-8")
        originals[archive] = archive.read_bytes()
        source.update(
            ground_truth=truth,
            provenance={
                "kind": "archived_response",
                "acquisition_id": f"acquisition-{case.name}",
                "archive_path": str(archive.relative_to(directory)),
                "archive_sha256": source["payload_sha256"],
                "charset": "utf-8",
                "representation": "http_body",
            },
            review={
                "status": "approved",
                "reviewer": "independent-calendar-cli-acceptance",
                "payload_sha256": source["payload_sha256"],
                "ground_truth_sha256": _sha(
                    json.dumps(truth, ensure_ascii=False, sort_keys=True).encode()
                ),
                "notes": "完整核对原始标题、元数据与正文；日期按任务的明确法域及标识字段复核。",
            },
        )
        reviewed.append(source)
    bundle, output = directory / "reviewed.jsonl", directory / "frozen"
    _write_rows(bundle, reviewed)
    originals[bundle] = bundle.read_bytes()
    result = _cli(
        directory,
        "wlh",
        "benchmark",
        "freeze",
        "--input",
        str(bundle),
        "--output-dir",
        str(output),
        "--version",
        "calendar-acceptance-v1",
    )
    golden_file = output / "ground_truth_benchmark.jsonl"
    golden = _read_rows(golden_file) if golden_file.is_file() else []
    summary = {
        "returncode": result.returncode,
        "inputs_unchanged": all(p.read_bytes() == data for p, data in originals.items()),
        "expected_ids": [case.name for case in cases if case.accepted],
        "actual_ids": [row["id"] for row in golden],
    }
    _write_json(directory / "assertion-results.json", summary)
    return {
        "golden": golden,
        "reviewed": reviewed,
        "summary": summary,
        "result": result,
        "ledger_exists": (output / "annotation_pipeline.json").is_file(),
    }


class TestBenchmarkCalendar:
    def test_missing_jurisdiction_cannot_admit_roc_date(self, calendar_directory: Path) -> None:
        directory = calendar_directory / "missing-jurisdiction"
        source, truth = _source_and_truth(
            CalendarCase(
                "missing-jurisdiction",
                "<li>發布日期：105-12-28</li>",
                "發布日期：105-12-28",
                "2016-12-28",
                False,
                None,
            )
        )
        bundle, completions = directory / "input.jsonl", directory / "completions.jsonl"
        _write_rows(bundle, [source])
        _write_rows(
            completions,
            [
                {
                    "id": source["id"],
                    "payload_sha256": source["payload_sha256"],
                    "prompt_tokens": 300,
                    "output_tokens": 200,
                    "finish_reason": "stop",
                    "text": json.dumps(truth, ensure_ascii=False),
                }
            ],
        )
        originals = {p: p.read_bytes() for p in (bundle, completions)}
        output = directory / "validated"
        result = _cli(
            directory,
            "wlh.benchmark.annotator",
            "--validate-completions",
            str(completions),
            "--input",
            str(bundle),
            "--output-dir",
            str(output),
        )
        accepted = output / "accepted.jsonl"
        assert result.returncode != 0
        assert not accepted.is_file() or not _read_rows(accepted)
        assert all(p.read_bytes() == data for p, data in originals.items())

    @pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
    def test_validation_admits_only_explicit_tw_labeled_valid_roc_dates(
        self,
        calendar_validation: dict[str, Any],
        case: CalendarCase,
    ) -> None:
        accepted, rejected = calendar_validation["accepted"], calendar_validation["rejected"]
        if case.accepted:
            assert case.name in accepted, rejected.get(case.name)
            assert accepted[case.name]["ground_truth"] == calendar_validation["truths"][case.name]
            assert (
                accepted[case.name]["completion"]["text"]
                == calendar_validation["receipts"][case.name]["text"]
            )
        else:
            assert case.name not in accepted
            assert rejected[case.name]["reasons"]
        assert calendar_validation["summary"]["inputs_unchanged"]

    def test_freeze_retains_verified_tw_dates_and_original_payloads(
        self,
        calendar_directory: Path,
    ) -> None:
        result = _freeze(calendar_directory / "freeze-valid", tuple(c for c in CASES if c.accepted))
        assert result["summary"]["returncode"] == 0, (
            result["result"].stdout + result["result"].stderr
        )
        golden = {row["id"]: row for row in result["golden"]}
        assert set(golden) == set(result["summary"]["expected_ids"])
        assert result["ledger_exists"]
        for source in result["reviewed"]:
            assert golden[source["id"]]["raw_payload"] == source["raw_payload"]
            assert golden[source["id"]]["ground_truth"] == source["ground_truth"]
        assert result["summary"]["inputs_unchanged"]

    def test_freeze_rejects_unsupported_calendar_despite_approved_review(
        self,
        calendar_directory: Path,
    ) -> None:
        result = _freeze(calendar_directory / "freeze-non-tw", (CASES[4],))
        assert result["summary"]["returncode"] != 0
        assert not result["golden"]
        assert not result["ledger_exists"]
        assert result["summary"]["inputs_unchanged"]
