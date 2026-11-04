"""Fresh KFS content contract: real HTTP -> CLI -> current rows/CAS -> offline reparse.

Failure boundaries were recorded before this test in
artifacts/us-jp/kfs-content-contract/contract-before-implementation.json.
The author inspected complete frozen originals, not product implementation.
This bounded replay asserts content fidelity, never a full-source denominator.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from lxml import html as html_library  # type: ignore[import-untyped]
from markdown_it import MarkdownIt

from .source_replay_support import (
    KFS_FIXTURES,
    KFS_OUTPUT,
    compute_test_signature,
    create_test_receipt,
    create_test_run,
    kfs_case_routes,
    normalized,
)

FIXTURES = KFS_FIXTURES
OUTPUT = KFS_OUTPUT
RENDERER = MarkdownIt("commonmark", {"html": True}).enable("table")
HTML: Any = html_library
BODY_CONTROL = "本文中の引用語句「トップに戻る」は原文の内容として保持する。"


def _dom(markdown: str) -> Any:
    return HTML.fromstring("<div>" + RENDERER.render(markdown) + "</div>")


_routes = kfs_case_routes
_signature = compute_test_signature
_run = create_test_run
_receipt = create_test_receipt


def _annex(rows: list[dict[str, Any]], suffix: str) -> str:
    selected = [row for row in rows if row["locator"]["url"].endswith(suffix)]
    return selected[0]["markdown"] if len(selected) == 1 else ""


def _missing_text(fixture: str, markdown: str, *, ordered: bool = False) -> list[str]:
    original = HTML.fromstring((FIXTURES / fixture).read_bytes().decode("cp932"))
    actual = normalized(_dom(markdown).text_content())
    missing: list[str] = []
    position = 0
    for node in original.xpath(
        '//*[@id="main"]//text()[not(ancestor::*[@id="pankuzu" or @class="pagetop"])'
        ' and not(ancestor::script or ancestor::style)]'
    ):
        expected = normalized(str(node))
        if not expected:
            continue
        found = actual.find(expected, position)
        if found < 0:
            missing.append(str(node).strip())
        elif ordered:
            position = found + len(expected)
    return missing


def _table_grid(table: Any) -> list[list[str]]:
    """Read the rendered table's HTML span semantics, with no empty-cell inference."""
    cells: dict[tuple[int, int], str] = {}
    rows = table.xpath("./tr | ./thead/tr | ./tbody/tr | ./tfoot/tr")
    for y, row in enumerate(rows):
        x = 0
        for cell in row.xpath("./th | ./td"):
            while (y, x) in cells:
                x += 1
            width, height = int(cell.get("colspan", "1")), int(cell.get("rowspan", "1"))
            assert 0 < width < 30 and 0 < height < 30, "Unbounded or invalid table span"
            for dy in range(height):
                for dx in range(width):
                    assert (y + dy, x + dx) not in cells, "Rendered cell overlap"
                    cells[y + dy, x + dx] = normalized(cell.text_content())
            x += width
    width = max((x for _, x in cells), default=-1) + 1
    return [[cells.get((y, x), "") for x in range(width)] for y in range(len(rows))]


def _display_text(text: str) -> str:
    """Keep labels, masked amounts and arithmetic; ignore only width/whitespace."""
    return "".join(unicodedata.normalize("NFKC", text).split())


def _label_value_representation(cell: Any, expected: list[list[str]]) -> str | None:
    """Accept a rendered subtable or explicit equivalent label/value blocks."""
    for table in cell.xpath(".//table"):
        rows = table.xpath("./tr | ./thead/tr | ./tbody/tr | ./tfoot/tr")
        observed = [[_display_text(c.text_content()) for c in row.xpath("./th | ./td")]
                    for row in rows]
        if observed == expected:
            return "table"
    for definition in cell.xpath(".//dl"):
        entries = definition.xpath("./dt | ./dd")
        if len(entries) == 4 and [e.tag for e in entries] == ["dt", "dd", "dt", "dd"]:
            observed = [[_display_text(entries[i].text_content()),
                         _display_text(entries[i + 1].text_content())] for i in (0, 2)]
            if observed == expected:
                return "definition-list"
    for listing in cell.xpath(".//ol | .//ul"):
        values = [_display_text(item.text_content()) for item in listing.xpath("./li")]
        if values == ["".join(pair) for pair in expected]:
            return "labelled-list"
    return None


class TestKfsContentFidelity:
    def test_inner_calculation_rows_keep_label_value_pairs_and_responsible_party_ownership(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """Complete JP137/08 has a 2x2 calculation inside 原処分庁's outer claim cell.

        Boundaries predate HTML nested-table implementation and are recorded in
        nested-contract-before-implementation.json. Escaped pipe text, a detached
        table, swapped parties, missing arithmetic or blanket refusal cannot pass.
        Native HTML and explicit label/value lists satisfy the same visible contract.
        """
        name = "nested-calculation"
        report, rows, checks = _run("137/08", name, initialized_db, tmp_path)
        markdown = _annex(rows, "/137/08/index.html")
        rendered = _dom(markdown)
        outer_tables: list[Any] = []
        for table in rendered.xpath(".//table[not(ancestor::table)]"):
            table_rows = table.xpath("./tr | ./thead/tr | ./tbody/tr")
            if table_rows and [_display_text(c.text_content())
                               for c in table_rows[0].xpath("./th | ./td")] == ["請求人", "原処分庁"]:
                outer_tables.append(table)
        owners: list[Any] = []
        left_claim = _display_text("(2) 上記に基づき、請求人の本件猶予申請時における純資産を算定すると")
        for table in outer_tables:
            for row in table.xpath("./tr | ./thead/tr | ./tbody/tr"):
                cells = row.xpath("./th | ./td")
                if len(cells) == 2 and left_claim in _display_text(cells[0].text_content()):
                    owners.append(cells[1])
        expected = [[_display_text(label), _display_text(value)] for label, value in [
            ("(イ)", "生活維持費等の額 ○○○○円 (○○○○円－○○○○円)"),
            ("(ロ)", "所要資金の額 ０円 (イ)＋(ロ)＝○○○○円"),
        ]]
        representation = _label_value_representation(owners[0], expected) if len(owners) == 1 else None
        owner_text = _display_text(owners[0].text_content()) if len(owners) == 1 else ""
        markers = [
            "(3) 以上の事実に基づき、猶予取扱要領に従って計算すると",
            "イ 当座資金の額 ○○○○円", "ロ つなぎ資金の額",
            *["".join(pair) for pair in expected],
            "ハ 現在納付可能資金額（イ－ロ）○○○○円",
            "ニ 本件猶予申請に係る国税の額○○○○円及び未確定延滞税", "ホ 納付困難な額（ニ－ハ）０円",
        ]
        positions = [owner_text.find(_display_text(marker)) for marker in markers]
        missing = _missing_text("JP-137-08-index.html", markdown, ordered=True)
        checks.update({
            "complete_original_delivered_successfully": report["exit_code"] == 0 and len(rows) == 1,
            "offline_reparse_successful": report["offline"]["exit_code"] == 0,
            "outer_party_comparison_kept": len(outer_tables) == 1,
            "calculation_belongs_to_original_authority_claim_cell": len(owners) == 1,
            "both_inner_label_value_rows_explicitly_structured": representation is not None,
            "calculation_order_keeps_inner_rows_between_ro_and_ha": all(p >= 0 for p in positions)
            and positions == sorted(positions),
            "no_table_delimiters_masquerade_as_legal_text": "|" not in owner_text,
            "all_original_body_words_preserved_in_order": not missing,
        })
        _receipt(name, checks, accepted_representation=representation,
                 expected_inner_rows=expected, calculation_positions=positions,
                 missing_or_reordered_body_text=missing)

    def test_required_direct_child_figure_with_text_sibling_blocks_entire_parent(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """The title/alt and successful legal-text annex cannot replace a land diagram."""
        name = "required-figure"
        report, rows, checks = _run("113/13", name, initialized_db, tmp_path)
        checks.update({
            "collect_rejects_unverified_figure": report["exit_code"] != 0
            and report["result"].get("status") != "complete",
            "entire_parent_has_no_current_rows": not rows,
            "failure_identifies_unverified_html_image": "html_images_unverified"
            in json.dumps(report["result"]),
            "offline_reparse_also_rejects_figure": report["offline"]["exit_code"] != 0
            and report["offline"]["result"].get("status") != "complete",
        })
        _receipt(name, checks, actual_delivered_rows=len(rows),
                 errors=report["result"].get("errors", []))

    def test_caption_and_merged_decision_cells_preserve_each_year_and_tax_item_relationship(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """Two years each have three monetary items with two shared decision outcomes.

        A valid HTML table with spans and an explicitly expanded Markdown table both
        satisfy the same rendered contract. Empty continuation cells do not identify
        which items the decisions apply to. Blanket rejection is not acceptance.
        """
        name = "table-relations"
        report, rows, checks = _run("94/03", name, initialized_db, tmp_path)
        markdown = _annex(rows, "/94/03/behhyou.html")
        rendered = _dom(markdown)
        tables = [_table_grid(table) for table in rendered.xpath(".//table")]
        title = normalized("審査請求に至る経緯等")
        unit = normalized("（単位：円）")
        checks.update({
            "parent_and_annex_delivered_successfully": report["exit_code"] == 0 and len(rows) == 2,
            "offline_reparse_successful": report["offline"]["exit_code"] == 0,
            "table_title_preserved": title in normalized(rendered.text_content()),
            "yen_unit_caption_preserved": unit in normalized(rendered.text_content()),
            "one_structured_table_delivered": len(tables) == 1,
        })
        grid = tables[0] if len(tables) == 1 else []
        expected_columns = ["確定申告", "修正申告", "加算税の賦課決定処分",
                            "更正処分及び賦課決定処分", "異議申立て", "異議決定"]
        checks["shared_item_heading_and_decision_columns_preserved"] = bool(grid) and (
            grid[0][0] == "年分" and grid[0][1:-6] in (["区分項目"], ["区分項目", "区分項目"])
            and grid[0][-6:] == expected_columns
        )
        relationships: list[dict[str, Any]] = []
        for year in ("平成22年分", "平成23年分"):
            for item in ("総所得金額(事業所得の金額)", "納付すべき税額", "過少申告加算税の額"):
                matching = [r for r in grid if r and r[0] == year and normalized(item) in r]
                preserved = len(matching) == 1 and matching[0][-2:] == ["全部取り消し", "棄却"]
                checks[f"{year}_{item}_both_decision_relationships"] = preserved
                checks[f"{year}_{item}_shared_item_columns_explicit"] = len(matching) == 1 and (
                    matching[0][1:-6] in ([normalized(item)], [normalized(item), normalized(item)])
                )
                relationships.append({"year": year, "item": item, "observed_rows": matching,
                                      "expected_last_columns": ["全部取り消し", "棄却"]})
        _receipt(name, checks, rendered_grid=grid, relationships=relationships)

    @pytest.mark.parametrize("body_control", [False, True], ids=["original", "body-word-control"])
    def test_nested_return_navigation_removed_and_all_substantive_words_preserved(
        self, initialized_db: dict[str, str], tmp_path: Path, body_control: bool
    ) -> None:
        """The control adds one disclosed ordinary paragraph; original body remains full."""
        name = "navigation-body-control" if body_control else "navigation"
        report, rows, checks = _run("106/06", name, initialized_db, tmp_path, body_control=body_control)
        markdown = _annex(rows, "/106/06/bessi.html")
        actual = normalized(_dom(markdown).text_content())
        missing = _missing_text("JP-106-06-bessi.html", markdown)
        parent = _annex(rows, "/106/06/index.html")
        missing_parent = _missing_text("JP-106-06-index.html", parent)
        checks.update({
            "parent_and_annex_delivered_successfully": report["exit_code"] == 0 and len(rows) == 2,
            "offline_reparse_successful": report["offline"]["exit_code"] == 0,
            "original_annex_body_words_preserved": not missing,
            "original_parent_body_words_preserved": not missing_parent,
            "only_substantive_occurrence_of_return_words_remains": actual.count(
                normalized("トップに戻る")) == int(body_control),
            "synthetic_body_phrase_preserved": not body_control or normalized(BODY_CONTROL) in actual,
        })
        _receipt(name, checks, missing_annex_text=missing, missing_parent_text=missing_parent,
                 return_word_occurrences=actual.count(normalized("トップに戻る")))
