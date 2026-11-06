"""Public evidence/receipt/freeze acceptance, specified before implementation.

The failure boundaries are recorded in the markdown-evidence contract artifact.
Render with the existing independent Markdown renderer; never rewrite a source or
truth to make validation pass. Real drafts remain pending review outside copies.
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
    FORGED,
    HTML,
    MARKDOWN,
    PARAGRAPH,
    REAL_IDS,
    RENDERER,
    ROOT,
    SOURCE,
    TABLE,
    TAIL,
    TITLE,
    compact_text,
    freeze_cases,
    isolated_review,
    public_integrity,
    read_rows,
    rendered_dom,
    reviewed_case,
    sha256,
    table_signature,
    validate_receipts,
    write_json,
)


@pytest.fixture(scope="session")
def wlh_env() -> dict[str, str]:
    """These local public interfaces require no database, model or credentials."""
    return {}


@pytest.fixture(scope="module")
def evidence_run() -> tuple[Path, Path]:
    base = ROOT / "work/benchmark-expansion-20261106/markdown-evidence"
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=base))
    artifacts = ROOT / "artifacts/benchmark-expansion-20261106/markdown-evidence" / work.name
    write_json(
        artifacts / "environment.json",
        {
            "python": sys.version,
            "platform": platform.platform(),
            "work": str(work),
            "command": ".venv/bin/python -m pytest -q tests/test_benchmark_markdown_evidence.py",
            "optional_real_input": os.environ.get("WLH_MARKDOWN_EVIDENCE_REAL_INPUT"),
            "inputs": "Isolated complete HTML/XML/JSON responses; attachments referenced only",
            "deadline_seconds_per_cli": 45,
            "network": "No source requests or model calls",
        },
    )
    return work, artifacts


@pytest.fixture(scope="module")
def cases(evidence_run: tuple[Path, Path]) -> dict[str, dict[str, Any]]:
    work, _ = evidence_run
    definitions: dict[str, tuple[str, str, str, bool]] = {
        "complete-html": (SOURCE, "text/html", MARKDOWN, True),
        "complete-xml": (SOURCE, "application/xml", MARKDOWN, True),
        "complete-json": (
            json.dumps({"body": SOURCE}, ensure_ascii=False),
            "application/json",
            MARKDOWN,
            True,
        ),
        "invented-visible-body": (SOURCE, "text/html", MARKDOWN + "\n\n" + FORGED, False),
        "rewritten-visible-body": (SOURCE, "text/html", MARKDOWN.replace(PARAGRAPH, FORGED), False),
        "omitted-final-clause": (SOURCE, "text/html", MARKDOWN.replace(TAIL, ""), False),
        "omitted-whole-table": (SOURCE, "text/html", MARKDOWN.replace(TABLE, ""), False),
    }
    for label, hidden in (
        ("script", f"<script>{FORGED}</script>"),
        ("style", f"<style>{FORGED}</style>"),
        ("template", f"<template>{FORGED}</template>"),
        ("comment", f"<!-- {FORGED} -->"),
        ("hidden-attribute", f"<div hidden>{FORGED}</div>"),
        ("hidden-css", f'<div style="display:none">{FORGED}</div>'),
    ):
        definitions[f"invented-hidden-{label}"] = (
            SOURCE,
            "text/html",
            MARKDOWN + "\n\n" + hidden,
            False,
        )
    definitions["attested-body-hidden-in-script"] = (
        SOURCE,
        "text/html",
        MARKDOWN.replace(PARAGRAPH, f"<script>{PARAGRAPH}</script>"),
        False,
    )
    ordinary = (
        f"# {TITLE}\n\n**{PARAGRAPH}**\n\n- 原始规则必须保留。\n- 末项条款不可丢失。\n\n"
        "| 类别 | 金额 |\n| --- | --- |\n| 甲类 | 100元 |\n\n"
        "`X < 5`\n\n[完整附件](https://example.gov/files/rules.pdf)\n\n" + TAIL
    )
    definitions["traditional-markdown"] = (RENDERER.render(ordinary), "text/html", ordinary, True)
    literal = f"# {TITLE}\n\n{PARAGRAPH}\n\n`<strong>` 是字面标记。\n\n{TAIL}"
    definitions["literal-tag-code"] = (RENDERER.render(literal), "text/html", literal, True)
    definitions["forged-visible-tag-code"] = (
        RENDERER.render(literal),
        "text/html",
        literal + f"\n\n`<script>{FORGED}</script>`",
        False,
    )
    fenced = (
        f"# {TITLE}\n\n{PARAGRAPH}\n\n```html\n<script>const allowed = 1;</script>\n```\n\n{TAIL}"
    )
    definitions["literal-script-fenced-code"] = (RENDERER.render(fenced), "text/html", fenced, True)
    references = (
        '<a href="https://example.gov/files/rules.pdf">完整附件</a>\n\n'
        '<img src="https://example.gov/images/diagram.png" alt="核算图示">'
    )
    linked_source = SOURCE.replace("</article>", references + "</article>")
    linked_output = MARKDOWN + "\n\n" + references
    definitions["embedded-html-references"] = (linked_source, "text/html", linked_output, True)
    nested = (
        MARKDOWN
        + "\n\n[![核算图示](https://example.gov/images/diagram.png)](https://example.gov/files/rules.pdf)"
    )
    nested_source = SOURCE.replace(
        "</article>",
        '<a href="https://example.gov/files/rules.pdf"><img '
        'src="https://example.gov/images/diagram.png" alt="核算图示"></a></article>',
    )
    definitions["nested-markdown-image-link"] = (nested_source, "text/html", nested, True)
    for label, target in (("href", "files/rules.pdf"), ("image-src", "images/diagram.png")):
        definitions[f"forged-html-{label}"] = (
            linked_source,
            "text/html",
            linked_output.replace(target, "unattested/forged"),
            False,
        )
    return {
        ident: {"row": reviewed_case(work, ident, raw, mime, markdown), "expected": expected}
        for ident, (raw, mime, markdown, expected) in definitions.items()
    }


class TestBenchmarkMarkdownEvidence:
    def test_complete_embedded_html_matches_visible_source_and_table_structure(
        self,
        evidence_run: tuple[Path, Path],
    ) -> None:
        rendered, source = rendered_dom(MARKDOWN), HTML.fromstring(SOURCE)
        assert compact_text(rendered.text_content()) == compact_text(source.text_content())
        assert table_signature(rendered.xpath(".//table")[0]) == table_signature(
            source.xpath(".//table")[0]
        )
        assert len(rendered.xpath(".//sup")) == len(rendered.xpath(".//sub")) == 2
        write_json(
            evidence_run[1] / "independent-render.json",
            {
                "visible_text_equal": True,
                "table_cells_and_spans_equal": True,
                "superscripts": 2,
                "subscripts": 2,
            },
        )

    def test_public_integrity_accepts_complete_rendering_and_rejects_changed_or_hidden_text(
        self,
        evidence_run: tuple[Path, Path],
        cases: dict[str, dict[str, Any]],
    ) -> None:
        results = [
            {**public_integrity(case["row"]), "expected": case["expected"]}
            for case in cases.values()
        ]
        write_json(evidence_run[1] / "integrity-assertions.json", results)
        assert all(r["valid"] == r["expected"] for r in results), results

    def test_receipts_validate_the_same_truth_without_mutating_inputs_or_outputs(
        self,
        evidence_run: tuple[Path, Path],
        cases: dict[str, dict[str, Any]],
    ) -> None:
        work, artifacts = evidence_run
        result = validate_receipts([c["row"] for c in cases.values()], work / "receipts", artifacts)
        assert result["inputs_unchanged"] and result["raw_receipts_unchanged"]
        expected = {key for key, case in cases.items() if case["expected"]}
        assert {r["id"] for r in result["accepted"]} == expected, result["rejected"]
        assert {r["id"] for r in result["rejected"]} == set(cases) - expected
        assert all(
            r["ground_truth"] == cases[r["id"]]["row"]["ground_truth"] for r in result["accepted"]
        )

    @pytest.mark.parametrize("valid", [True, False], ids=["complete", "invalid"])
    def test_freeze_preserves_complete_truth_and_refuses_invalid_content(
        self,
        evidence_run: tuple[Path, Path],
        cases: dict[str, dict[str, Any]],
        valid: bool,
    ) -> None:
        work, artifacts = evidence_run
        selected = [c["row"] for c in cases.values() if c["expected"] == valid]
        groups = [selected] if valid else [[row] for row in selected]
        results: list[dict[str, Any]] = []
        for rows in groups:
            name = "freeze-complete" if valid else rows[0]["id"]
            result = freeze_cases(rows, work / name, artifacts / name)
            results.append(result)
        assert all(r["inputs_unchanged"] for r in results)
        if valid:
            assert all(
                r["returncode"] == 0 and r["complete_payload_and_truth_preserved"] for r in results
            ), results
        else:
            assert all(r["returncode"] != 0 and not r["published"] for r in results), results


@pytest.fixture(scope="module")
def real_rows(evidence_run: tuple[Path, Path]) -> Generator[list[dict[str, Any]], None, None]:
    configured = os.environ.get("WLH_MARKDOWN_EVIDENCE_REAL_INPUT")
    if not configured:
        pytest.skip("Set WLH_MARKDOWN_EVIDENCE_REAL_INPUT to the original native-3-drafts.jsonl")
    source = Path(configured)
    rows = [row for row in read_rows(source) if row["id"] in REAL_IDS]
    assert {row["id"] for row in rows} == set(REAL_IDS)
    paths = [source, *(Path(row["provenance"]["archive_path"]) for row in rows)]
    before = {str(path): sha256(path.read_bytes()) for path in paths}
    write_json(
        evidence_run[1] / "real-input.json",
        {
            "path": str(source),
            "sha256": sha256(source.read_bytes()),
            "ids": REAL_IDS,
            "review_states": {r["id"]: r["review"] for r in rows},
        },
    )
    yield rows
    after = {str(path): sha256(path.read_bytes()) for path in paths}
    write_json(
        evidence_run[1] / "real-original-immutability.json",
        {
            "before": before,
            "after": after,
            "unchanged": before == after,
        },
    )
    assert before == after


class TestBenchmarkRealMarkdownEvidence:
    def test_real_drafts_render_every_business_character_cell_and_span(
        self,
        evidence_run: tuple[Path, Path],
        real_rows: list[dict[str, Any]],
    ) -> None:
        observations: list[dict[str, Any]] = []
        for row in real_rows:
            source, rendered = (
                HTML.fromstring(row["raw_payload"]),
                rendered_dom(row["ground_truth"]["records"][0]["markdown"]),
            )
            assert sha256(row["raw_payload"].encode()) == row["payload_sha256"]
            if row["id"].endswith("349"):
                # These two metadata rows are login controls, outside the public judgment.
                metadata = [
                    tr
                    for tr in source.xpath("(//table)[1]//tr")
                    if not tr.text_content().strip().startswith(("Áp dụng án lệ:", "Đính chính:"))
                ]
                pages = source.xpath("//*[@data-page-no]")
                assert [p.get("data-page-no") for p in pages] == ["1", "2", "3", "4", "5", "6"]
                roots = source.xpath("//h1[@class='document-title']") + metadata + pages
                assert len(rendered.xpath(".//sup")) == 12
            else:
                roots = source.xpath(
                    "//h1[@data-role='title'] | //*[@data-role='publishdate'] | //*[@data-role='sapo'] | //*[@itemprop='articleBody']"
                )
                tables = source.xpath("//*[@itemprop='articleBody']//table")
                actual_tables = rendered.xpath(".//table")
                assert len(tables) == len(actual_tables) == 4
                assert sum(len(t.xpath(".//tr")) for t in actual_tables) == 210
                assert sum(len(t.xpath(".//th | .//td")) for t in actual_tables) == 1192
                assert [table_signature(t) for t in tables] == [
                    table_signature(t) for t in actual_tables
                ]
            expected = compact_text("".join(root.text_content() for root in roots))
            actual = compact_text(rendered.text_content())
            assert actual == expected
            observations.append(
                {
                    "id": row["id"],
                    "characters": len(expected),
                    "visible_text_equal": True,
                    "source_sha256": sha256(expected.encode()),
                    "rendered_sha256": sha256(actual.encode()),
                    "tables": len(rendered.xpath(".//table")),
                    "rows": len(rendered.xpath(".//tr")),
                    "cells": len(rendered.xpath(".//th | .//td")),
                }
            )
        write_json(evidence_run[1] / "real-render-assertions.json", observations)

    def test_real_complete_drafts_pass_integrity_receipts_and_isolated_freeze_without_rewriting(
        self,
        evidence_run: tuple[Path, Path],
        real_rows: list[dict[str, Any]],
    ) -> None:
        work, artifacts = evidence_run
        original = copy.deepcopy(real_rows)
        validations = [public_integrity(row) for row in real_rows]
        write_json(artifacts / "real-integrity-assertions.json", validations)
        receipts = validate_receipts(real_rows, work / "real-receipts", artifacts / "real")
        reviewed = copy.deepcopy(real_rows)
        for row in reviewed:
            row["review"] = isolated_review(row["payload_sha256"], row["ground_truth"])
        frozen = freeze_cases(reviewed, work / "real-freeze", artifacts / "real")
        assert real_rows == original
        assert all(row["review"]["status"] == "needsreview" for row in real_rows)
        assert all(row["valid"] for row in validations), validations
        assert (
            receipts["returncode"] == 0
            and receipts["inputs_unchanged"]
            and receipts["raw_receipts_unchanged"]
        )
        assert {r["id"] for r in receipts["accepted"]} == set(REAL_IDS)
        assert frozen["returncode"] == 0 and frozen["complete_payload_and_truth_preserved"], frozen
