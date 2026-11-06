"""Black-box table evidence contracts specified independently before implementation.

The real review packets are optional outside the local acceptance environment.
Their text-only approval copies must never approve the original draft or dataset.
"""

from __future__ import annotations

import copy
import json
import os
import platform
import sys
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from tests.benchmark_markdown_support import (
    HTML,
    ROOT,
    TITLE,
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

REAL_IDS = ("content_dd-448_cand_51", "content_dd-455_02")
REAL_COUNTS = {REAL_IDS[0]: (357, 96), REAL_IDS[1]: (376, 46)}
VISIBLE = "依法登记并保护申请人的全部合法权益"
INVENTED = "所有申请人必须缴纳十万元额外保证金否则永久取消其登记资格"
TABLE_MD = "| 项目 | 类别 |\n| --- | --- |\n| 登记 | 移动、客户端 |"
TABLE_HTML = "<table><tr><th>项目</th><th>类别</th></tr><tr><td>登记</td>{}</tr></table>"


@pytest.fixture(scope="session")
def wlh_env() -> dict[str, str]:
    """The public file-only path needs no database, model, browser or secrets."""
    return {}


@pytest.fixture(scope="module")
def table_run() -> tuple[Path, Path]:
    base = ROOT / "work/benchmark-expansion-20261106/dynamic-table-acceptance"
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=base))
    artifacts = ROOT / "artifacts/benchmark-expansion-20261106/dynamic-table-acceptance" / work.name
    write_json(
        artifacts / "environment.json",
        {
            "python": sys.version,
            "platform": platform.platform(),
            "work": str(work),
            "command": ".venv/bin/python -m pytest -q tests/test_benchmark_dynamic_tables.py",
            "review_dir": os.environ.get("WLH_DYNAMIC_TABLE_REVIEW_DIR"),
            "network": "No source requests, downloads, model calls or JavaScript execution",
            "review_scope": "Isolated text-gate copies; original draft remains unapproved",
        },
    )
    return work, artifacts


@pytest.fixture(scope="module")
def table_cases(table_run: tuple[Path, Path]) -> dict[str, dict[str, Any]]:
    work, _ = table_run
    literal_cell = (
        "<td><script>var _badw = '移动、客户端';"
        "if (_badw && _badw.length > 0) {"
        "_badw = _badw.replaceAll('、', '<br>');document.write(_badw);}</script></td>"
    )
    literal_column = (
        "<script>var _sblb = '移动、客户端';"
        "if (_sblb == '原') {document.write(\"<td align='center'>&nbsp;</td>\");}"
        'else {document.write("<td align=\'center\'>" + _sblb + "</td>");}</script>'
    )
    definitions: dict[str, tuple[str, str, bool]] = {
        "static-short-cells": (TABLE_HTML.format("<td>移动、客户端</td>"), TABLE_MD, True),
        "literal-in-cell": (TABLE_HTML.format(literal_cell), TABLE_MD, True),
        "literal-generated-cell": (TABLE_HTML.format(literal_column), TABLE_MD, True),
        "table-row-as-fullwidth-labeled-value": (
            "<table><tr><td>申报类别</td><td>移动、客户端</td></tr></table>",
            "**申报类别：** 移动、客户端",
            True,
        ),
        "table-row-as-ascii-labeled-value": (
            "<table><tr><td>申报类别</td><td>移动、客户端</td></tr></table>",
            "**申报类别**: 移动、客户端",
            True,
        ),
        "unknown-expression": (
            TABLE_HTML.format("<td><script>document.write(readCategory());</script></td>"),
            TABLE_MD.replace("移动、客户端", INVENTED),
            False,
        ),
        "unused-script-variable": (
            f"<script>var unused = '{INVENTED}';</script><p>{VISIBLE}</p>",
            INVENTED,
            False,
        ),
        "unknown-call-literal-is-not-output": (
            f"<script>document.write(transformUnknown('{INVENTED}'));</script><p>{VISIBLE}</p>",
            INVENTED,
            False,
        ),
        "computed-expression-is-not-executed": (
            "<table><tr><th>校验码</th></tr><tr>"
            "<td><script>document.write(String(174839 * 23917));</script></td></tr></table>",
            "| 校验码 |\n| --- |\n| 4181624363 |",
            False,
        ),
        "invented-table-value": (
            TABLE_HTML.format(literal_cell),
            TABLE_MD.replace("移动、客户端", INVENTED),
            False,
        ),
        "cross-cell-phrase": (
            "<table><tr><td>禁止</td><td>收费</td></tr></table>",
            "禁止收费",
            False,
        ),
    }
    cases: dict[str, dict[str, Any]] = {}
    for ident, (body, markdown, expected) in definitions.items():
        raw = f"<!doctype html><html><body><h1>{TITLE}</h1>{body}</body></html>"
        row = reviewed_case(work, ident, raw, "text/html", f"# {TITLE}\n\n{markdown}")
        cases[ident] = {"row": row, "expected": expected}
    return cases


class TestBenchmarkDynamicTableEvidence:
    def test_public_validation_respects_visible_cells_and_literal_output_boundaries(
        self, table_run: tuple[Path, Path], table_cases: dict[str, dict[str, Any]]
    ) -> None:
        original = copy.deepcopy(table_cases)
        results = [
            {**public_integrity(case["row"]), "expected": case["expected"]}
            for case in table_cases.values()
        ]
        write_json(table_run[1] / "integrity-assertions.json", results)
        assert table_cases == original
        assert all(result["valid"] == result["expected"] for result in results), results

    def test_receipts_preserve_inputs_and_apply_the_same_cell_evidence_contract(
        self, table_run: tuple[Path, Path], table_cases: dict[str, dict[str, Any]]
    ) -> None:
        work, artifacts = table_run
        result = validate_receipts(
            [case["row"] for case in table_cases.values()], work / "receipts", artifacts
        )
        assert result["inputs_unchanged"] and result["raw_receipts_unchanged"]
        expected = {ident for ident, case in table_cases.items() if case["expected"]}
        assert {row["id"] for row in result["accepted"]} == expected, result["rejected"]
        assert {row["id"] for row in result["rejected"]} == set(table_cases) - expected

    @pytest.mark.parametrize("valid", [True, False], ids=["complete", "invalid"])
    def test_isolated_freeze_preserves_valid_truth_and_refuses_unattested_values(
        self,
        table_run: tuple[Path, Path],
        table_cases: dict[str, dict[str, Any]],
        valid: bool,
    ) -> None:
        work, artifacts = table_run
        selected = [case["row"] for case in table_cases.values() if case["expected"] == valid]
        groups = [selected] if valid else [[row] for row in selected]
        results: list[dict[str, Any]] = []
        for rows in groups:
            name = "complete" if valid else rows[0]["id"]
            result = freeze_cases(rows, work / "freeze" / name, artifacts / "freeze" / name)
            results.append(result)
        assert all(result["inputs_unchanged"] for result in results)
        if valid:
            assert all(
                result["returncode"] == 0 and result["complete_payload_and_truth_preserved"]
                for result in results
            ), results
        else:
            assert all(
                result["returncode"] != 0 and not result["published"] for result in results
            ), results


@pytest.fixture(scope="module")
def real_packets(table_run: tuple[Path, Path]) -> Generator[dict[str, Any], None, None]:
    default = ROOT / "work/benchmark-expansion-20261106/independent-review-74"
    directory = Path(os.environ.get("WLH_DYNAMIC_TABLE_REVIEW_DIR", str(default)))
    if not directory.is_dir():
        pytest.skip("Set WLH_DYNAMIC_TABLE_REVIEW_DIR to the complete independent review packets")
    selected = ROOT / "work/benchmark-expansion-20261106/colab-02-text/selected.jsonl"
    paths = [selected, directory / "special-structures.json"]
    paths.extend(directory / f"{ident}.json" for ident in REAL_IDS)
    before = {str(path): sha256(path.read_bytes()) for path in paths}
    source_rows = {row["id"]: row for row in read_rows(selected) if row["id"] in REAL_IDS}
    packets = {ident: json.loads((directory / f"{ident}.json").read_text()) for ident in REAL_IDS}
    for ident, packet in packets.items():
        assert packet["source"]["raw_payload"] == source_rows[ident]["raw_payload"]
        assert (
            sha256(packet["source"]["raw_payload"].encode()) == source_rows[ident]["payload_sha256"]
        )
    yield {
        "packets": packets,
        "structures": json.loads((directory / "special-structures.json").read_text()),
    }
    after = {str(path): sha256(path.read_bytes()) for path in paths}
    write_json(
        table_run[1] / "real-original-immutability.json",
        {"before": before, "after": after, "unchanged": before == after},
    )
    assert before == after, "Acceptance must not rewrite original sources or model receipts"


class TestBenchmarkArchivedDynamicTables:
    def test_complete_model_tables_match_every_independently_reviewed_source_cell(
        self, table_run: tuple[Path, Path], real_packets: dict[str, Any]
    ) -> None:
        observations: list[dict[str, Any]] = []
        for ident, packet in real_packets["packets"].items():
            source_dom = HTML.fromstring(packet["source"]["raw_payload"])
            evidence = real_packets["structures"][ident]
            assert evidence["input_sha256"] == packet["source"]["payload_sha256"]
            tables = [table for table in evidence["tables"] if len(table["rows"]) > 1]
            expected = [
                [
                    [compact_text(cell["text_or_literal"]) for cell in row["cells"]]
                    for row in table["rows"]
                ]
                for table in tables
            ]
            cells = [cell for table in tables for row in table["rows"] for cell in row["cells"]]
            script_cells = [cell for cell in cells if cell["script_literal"] is not None]
            assert (len(cells), len(script_cells)) == REAL_COUNTS[ident]
            for cell in cells:
                node = source_dom.xpath(cell["script_locator"] or cell["locator"])[0]
                if cell["script_literal"] is not None:
                    assert cell["script_text"] == node.text
                    assert cell["script_literal"] in node.text
                else:
                    assert compact_text(node.text_content()) == compact_text(
                        cell["text_or_literal"]
                    )
            for model, completion in packet["models"].items():
                assert json.loads(completion["raw_output"]) == completion["truth"]
                rendered = rendered_dom(completion["truth"]["records"][0]["markdown"])
                observed = [
                    [
                        [compact_text(cell.text_content()) for cell in row.xpath("./td|./th")]
                        for row in table.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr")
                    ]
                    for table in rendered.xpath(".//table")
                ]
                assert observed == expected, (ident, model)
                observations.append(
                    {
                        "id": ident,
                        "model": model,
                        "cells": len(cells),
                        "script_cells": len(script_cells),
                    }
                )
        write_json(table_run[1] / "real-independent-cell-assertions.json", observations)

    def test_archived_table_text_passes_validation_receipts_and_isolated_freeze(
        self, table_run: tuple[Path, Path], real_packets: dict[str, Any]
    ) -> None:
        work, artifacts = table_run
        original = copy.deepcopy(real_packets)
        rows: list[dict[str, Any]] = []
        for ident, packet in real_packets["packets"].items():
            for model, completion in packet["models"].items():
                row = copy.deepcopy(packet["source"])
                row["id"] = f"{ident}-{model}"
                row["ground_truth"] = copy.deepcopy(completion["truth"])
                row["review"] = isolated_review(row["payload_sha256"], row["ground_truth"])
                rows.append(row)
        validations = [public_integrity(row) for row in rows]
        write_json(artifacts / "real-integrity-assertions.json", validations)
        receipts = validate_receipts(rows, work / "real-receipts", artifacts / "real")
        frozen = freeze_cases(rows, work / "real-freeze", artifacts / "real")
        assert real_packets == original
        assert all(result["valid"] for result in validations), validations
        assert receipts["inputs_unchanged"] and receipts["raw_receipts_unchanged"]
        assert {row["id"] for row in receipts["accepted"]} == {row["id"] for row in rows}
        assert frozen["returncode"] == 0 and frozen["complete_payload_and_truth_preserved"], frozen
