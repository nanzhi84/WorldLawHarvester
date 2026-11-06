"""Public acceptance for bounded click navigation, specified before implementation.

Only complete native HTML and existing public helpers are used. Original pending
truth, archives and model receipts remain immutable; freeze uses isolated copies.
Failure modes are recorded in artifacts/.../navigation-evidence-contract.
"""

from __future__ import annotations

import copy
import html
import json
import platform
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.benchmark_markdown_support import (
    PARAGRAPH,
    ROOT,
    TAIL,
    TITLE,
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
ARTIFACTS = ROOT / "artifacts/benchmark-expansion-20261106/navigation-evidence-contract"
PENDING = BASE / "gap131-eight-pending/pending-gap131-eight.jsonl"
PENDING_SHA = "2c8db16627bab62a601cb8c87c5e88af785de167a983c27aca8e49535abb1fd5"
REAL_ID = "content_dd-525_cand_497"
REAL_SHA = "272a239c2c08ba960fb8499e65a388beedcdbc4eff62355b50ff4a9363be3d7b"
DESTINATION = "/Act/AJPA2016?WholeDoc=1"
REQUEST = "https://example.gov/legal/current/index?version=1"


@dataclass(frozen=True)
class NavigationCase:
    name: str
    row: dict[str, Any]
    accepted: bool


def _reviewed_copy(
    work: Path, name: str, source: dict[str, Any], truth: dict[str, Any]
) -> dict[str, Any]:
    row = reviewed_case(work, name, source["raw_payload"], source["mime_type"], "")
    for key in ("source_id", "jurisdiction", "request_url"):
        row[key] = source[key]
    row["ground_truth"] = copy.deepcopy(truth)
    row["review"] = isolated_review(row["payload_sha256"], row["ground_truth"])
    return row


def _original_paths(pending: list[dict[str, Any]]) -> list[Path]:
    paths = {
        PENDING,
        ROOT / "data/benchmark/ground_truth_benchmark.jsonl",
        ROOT / "data/benchmark/annotation_pipeline.json",
    }
    for item in pending:
        source = item["source"]
        archive = Path(source["provenance"]["archive_path"])
        assert sha256(archive.read_bytes()) == source["payload_sha256"]
        paths.add(archive)
        paths.add(ROOT / item["source_locator"].split("#", 1)[0])
        for receipt in item["historical_receipts"].values():
            paths.add(ROOT / receipt["raw_receipt_locator"].split("#", 1)[0])
        for suffix in ("json", "md"):
            paths.add(PENDING.parent / f"{item['id']}.pending.{suffix}")
    return sorted(paths)


def _real_cases(work: Path, pending: list[dict[str, Any]]) -> list[NavigationCase]:
    item = next(item for item in pending if item["id"] == REAL_ID)
    source, truth = item["source"], item["proposed_truth"]
    assert sha256(source["raw_payload"].encode()) == REAL_SHA
    assert (
        sha256(json.dumps(truth, ensure_ascii=False, sort_keys=True).encode())
        == (item["proposed_truth_sha256"])
    )
    assert f"window.location = '{DESTINATION}';" in source["raw_payload"]
    record = truth["records"][0]
    dom = rendered_dom(record["markdown"])
    assert dom.xpath('.//a[text()="Whole Document"]/@href') == [DESTINATION]
    assert {value for value in dom.xpath(".//@id") if re.fullmatch(r"pr\d+-", value)} == {
        "pr1-",
        "pr2-",
    }
    assert "Timeline" in dom.text_content() and "Table of Contents" in dom.text_content()
    assert record["published_at"] is None and record["effective_at"] is None
    cases = [NavigationCase(REAL_ID, _reviewed_copy(work, REAL_ID, source, truth), True)]
    for name, changed in (
        ("real-altered-tail", "/Act/AJPA2017?WholeDoc=1"),
        ("real-added-query", DESTINATION + "&invented=1"),
        ("real-cross-source", "https://unattested.example.gov" + DESTINATION),
    ):
        forged = copy.deepcopy(truth)
        forged["records"][0]["markdown"] = record["markdown"].replace(
            f'href="{DESTINATION}"', f'href="{html.escape(changed, quote=True)}"'
        )
        assert forged != truth
        cases.append(NavigationCase(name, _reviewed_copy(work, name, source, forged), False))
    return cases


def _native_case(
    work: Path, name: str, evidence: str, output_url: str, accepted: bool
) -> NavigationCase:
    raw = (
        f"<!doctype html><html><body><article><h1>{TITLE}</h1><p>{PARAGRAPH}</p>"
        f"{evidence}<p>{TAIL}</p></article></body></html>"
    )
    link = f'<a href="{html.escape(output_url, quote=True)}">Whole Document</a>'
    markdown = f"# {TITLE}\n\n{PARAGRAPH}\n\n{link}\n\n{TAIL}"
    row = reviewed_case(work, name, raw, "text/html", markdown)
    row["request_url"] = REQUEST
    return NavigationCase(name, row, accepted)


def _button(body: str, event: str = "click") -> str:
    binding = html.escape(f"{event}: function () {{ {body} }}", quote=True)
    return f'<button type="button" data-bind="{binding}">Whole Document</button>'


def _minimal_cases(work: Path) -> list[NavigationCase]:
    assignment = f"window.location = '{DESTINATION}';"
    absolute = "https://example.gov" + DESTINATION
    cases = [
        _native_case(work, "literal-root-navigation", _button(assignment), absolute, True),
        _native_case(
            work,
            "literal-relative-double-quotes",
            _button('window.location = "../whole?part=all&lang=en";'),
            "https://example.gov/legal/whole?part=all&lang=en",
            True,
        ),
    ]
    for name, evidence in (
        ("unused-variable", _button(f"var unused = '{DESTINATION}';")),
        ("unknown-function-argument", _button(f"unknown('{DESTINATION}');")),
        (
            "arbitrary-script-string",
            f"<script>const unused = '{DESTINATION}';</script><button>Whole Document</button>",
        ),
        ("dynamic-concatenation", _button("window.location = '/Act/' + 'AJPA2016?WholeDoc=1';")),
        ("variable-reference", _button(f"var target = '{DESTINATION}'; window.location = target;")),
        ("extra-leading-statement", _button("track(); " + assignment)),
        ("extra-trailing-statement", _button(assignment + " track();")),
        ("non-click-binding", _button(assignment, "text")),
        (
            "incomplete-function",
            '<button data-bind="'
            + html.escape("click: function () { " + assignment)
            + '">Whole Document</button>',
        ),
    ):
        cases.append(_native_case(work, name, evidence, absolute, False))
    for name, url in (
        ("unsafe-script", "javascript:alert(1)"),
        ("unsafe-data", "data:text/plain,law"),
        ("unsafe-file", "file:///tmp/law.html"),
        ("unsafe-mail", "mailto:law@example.gov"),
    ):
        cases.append(_native_case(work, name, _button(f"window.location = '{url}';"), url, False))
    return cases


@pytest.fixture(scope="module")
def navigation_run() -> dict[str, Any]:
    assert sha256(PENDING.read_bytes()) == PENDING_SHA
    pending = read_rows(PENDING)
    originals = {str(path): sha256(path.read_bytes()) for path in _original_paths(pending)}
    work_base = BASE / "navigation-evidence-contract"
    work_base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=work_base))
    artifacts = ARTIFACTS / work.name
    write_json(
        artifacts / "environment.json",
        {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "work": str(work),
            "command": ".venv/bin/python -m pytest --noconftest -q "
            "tests/test_benchmark_navigation_evidence.py",
            "preconditions": "Fixed pending SHA, all eight archived native inputs and historical "
            "A/B receipts available locally; no database, model or downloaded attachments",
            "original_hashes": originals,
            "scope": "Full current HTML page: timeline, contents, provisions 1–2; not full Act",
            "real_payload_sha256": REAL_SHA,
        },
    )
    cases = _real_cases(work, pending) + _minimal_cases(work)
    integrity = {case.name: public_integrity(case.row) for case in cases}
    write_json(artifacts / "integrity.json", list(integrity.values()))
    receipt = validate_receipts([case.row for case in cases], work / "receipts", artifacts)
    accepted_ids = {row["id"] for row in receipt["accepted"]}
    freezes = {
        case.name: freeze_cases([case.row], work / case.name, artifacts / case.name)
        for case in cases
    }
    after = {path: sha256(Path(path).read_bytes()) for path in originals}
    assertions = [
        {
            "id": case.name,
            "expected_accepted": case.accepted,
            "integrity_accepted": integrity[case.name]["valid"],
            "integrity_reason": integrity[case.name]["reason"],
            "receipt_accepted": case.name in accepted_ids,
            "freeze_accepted": freezes[case.name]["returncode"] == 0,
            "passed": all(
                observed == case.accepted
                for observed in (
                    integrity[case.name]["valid"],
                    case.name in accepted_ids,
                    freezes[case.name]["returncode"] == 0,
                )
            ),
        }
        for case in cases
    ]
    write_json(
        artifacts / "assertions.json",
        {
            "case_count": len(cases),
            "all_cases_passed": all(case["passed"] for case in assertions),
            "original_sources_and_history_unchanged": originals == after,
            "original_hashes_before": originals,
            "original_hashes_after": after,
            "receipt_inputs_unchanged": receipt["inputs_unchanged"],
            "raw_receipts_unchanged": receipt["raw_receipts_unchanged"],
            "cases": assertions,
        },
    )
    write_json(ARTIFACTS / "latest-run.json", {"artifacts": str(artifacts), "work": str(work)})
    return {
        "assertions": assertions,
        "receipt": receipt,
        "freezes": freezes,
        "originals_unchanged": originals == after,
    }


class TestBenchmarkNavigationEvidence:
    @pytest.mark.parametrize("entrypoint", ("integrity", "receipt", "freeze"))
    def test_complete_click_literals_and_forged_boundaries_follow_public_admission_contract(
        self, navigation_run: dict[str, Any], entrypoint: str
    ) -> None:
        failures = [
            row
            for row in navigation_run["assertions"]
            if row[f"{entrypoint}_accepted"] != row["expected_accepted"]
        ]
        assert not failures, json.dumps(failures, ensure_ascii=False, indent=2)

    def test_all_archives_history_receipts_and_frozen_current_page_remain_exact(
        self, navigation_run: dict[str, Any]
    ) -> None:
        assert navigation_run["originals_unchanged"]
        receipt = navigation_run["receipt"]
        assert receipt["inputs_unchanged"] and receipt["raw_receipts_unchanged"]
        for result in navigation_run["freezes"].values():
            assert result["inputs_unchanged"]
            if result["returncode"] == 0:
                assert result["published"] and result["complete_payload_and_truth_preserved"]
            else:
                assert not result["published"] and not result["exported"]
