"""Whole-law table contracts independently derived from recorded official eGov payloads.

Failure boundaries precede implementation in artifacts/us-jp/xml-table-contract/.
Only HTTP, CLI, documented delivery rows and S3 bytes are observed; no parser imports.
An empty continuation cell does not preserve a nonempty merged cell's legal scope.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import unicodedata
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import boto3  # type: ignore[import-untyped]
import psycopg
import yaml
from playwright.sync_api import sync_playwright
from psycopg.rows import dict_row

from .conftest import run_wlh_cli
from .source_replay_support import ROOT, collect_artifact, replay_config, source_server

FIXTURES = ROOT / "tests/fixtures/dd-517/xml-tables"
NAME = "xml-table-contract"
ARTIFACTS = ROOT / "artifacts/us-jp" / NAME
Cell = tuple[str, int, int]
Grid = list[list[str]]
Position = tuple[int, int]
Geometry = list[list[dict[str, Any]]]


def visible(text: str) -> str:
    """Ignore presentation syntax/space, while retaining legal punctuation and numbers."""
    text = re.sub(r"<rt\b[^>]*>.*?</rt>", "", text, flags=re.I | re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = unescape(re.sub(r"<[^>]*>", "", text)).replace("**", "")
    text = re.sub(r"\\([|*_`])", r"\1", text)
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def source_text(node: ET.Element) -> str:
    """Ruby readings remain annotations, not characters in the operative legal word."""
    if node.tag == "Rt":
        return ""
    return (node.text or "") + "".join(
        source_text(child) + (child.tail or "") for child in node
    )


def expand(cells: list[list[Cell]]) -> Grid:
    """Decode public row/column span geometry into every covered semantic position."""
    occupied: dict[tuple[int, int], str] = {}
    for row_index, row in enumerate(cells):
        column = 0
        for value, height, width in row:
            if height < 1 or width < 1 or row_index + height > len(cells):
                raise ValueError("Invalid table span outside physical rows")
            while (row_index, column) in occupied:
                column += 1
            for dy in range(height):
                for dx in range(width):
                    position = row_index + dy, column + dx
                    if position in occupied:
                        raise ValueError("Overlapping table cells")
                    occupied[position] = visible(value)
            column += width
    width = max((column for _, column in occupied), default=-1) + 1
    return [[occupied.get((row, column), "") for column in range(width)]
            for row in range(len(cells))]


def source_tables(root: ET.Element) -> list[Grid]:
    return [expand([
        [(source_text(cell), int(cell.get("rowspan", "1")), int(cell.get("colspan", "1")))
         for cell in row if cell.tag in {"TableColumn", "TableHeaderColumn"}]
        for row in table if row.tag in {"TableRow", "TableHeaderRow"}
    ]) for table in root.iter("Table")]


class TableReader(HTMLParser):
    """Read visible HTML table semantics without requiring the producer's formatting."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[Cell]] = []
        self.cell: list[str] | None = None
        self.height = self.width = 1
        self.reading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.rows.append([])
        elif tag in {"td", "th"}:
            attributes = dict(attrs)
            self.cell = []
            self.height = int(attributes.get("rowspan") or "1")
            self.width = int(attributes.get("colspan") or "1")
        elif tag == "rt":
            self.reading = True
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell is not None:
            self.rows[-1].append(("".join(self.cell), self.height, self.width))
            self.cell = None
        elif tag == "rt":
            self.reading = False

    def handle_data(self, data: str) -> None:
        if self.cell is not None and not self.reading:
            self.cell.append(data)


def rendered_tables(markdown: str) -> list[tuple[Grid, str]]:
    """Accept semantic HTML spans or explicitly expanded Markdown tables in source order."""
    tables: list[tuple[int, Grid, str]] = []
    html_tables = list(re.finditer(r"<table\b[^>]*>.*?</table>", markdown, re.I | re.S))
    for match in html_tables:
        reader = TableReader()
        reader.feed(match.group())
        tables.append((match.start(), expand(reader.rows), match.group()))
    offset, start = 0, 0
    pending: Grid = []
    for line in markdown.splitlines(keepends=True) + ["\n"]:
        in_html = any(match.start() <= offset < match.end() for match in html_tables)
        text = line.strip()
        if not in_html and text.startswith("|") and text.endswith("|"):
            values = re.split(r"(?<!\\)\|", text[1:-1])
            if not all(re.fullmatch(r"\s*:?-{3,}:?\s*", value) for value in values):
                if not pending:
                    start = offset
                pending.append([visible(value) for value in values])
        elif pending:
            markup = "<table>" + "".join("<tr>" + "".join(
                f"<td>{escape(value)}</td>" for value in row
            ) + "</tr>" for row in pending) + "</table>"
            tables.append((start, pending, markup))
            pending = []
        offset += len(line)
    return [(grid, markup) for _, grid, markup in sorted(tables, key=lambda item: item[0])]


def source_boundaries(table: ET.Element) -> tuple[
    list[tuple[Position, Position, bool]], dict[Position, str]
]:
    """Visible seams and connected blank cells follow source borders, not guessed labels."""
    slots: dict[Position, ET.Element] = {}
    for row_index, row in enumerate(table):
        column = 0
        for cell in row:
            while (row_index, column) in slots:
                column += 1
            for dy in range(int(cell.get("rowspan", "1"))):
                for dx in range(int(cell.get("colspan", "1"))):
                    slots[row_index + dy, column + dx] = cell
            column += int(cell.get("colspan", "1"))
    relations: list[tuple[Position, Position, bool]] = []
    links: dict[Position, set[Position]] = {position: set() for position in slots}
    for (row, column), cell in slots.items():
        for adjacent, first, second in [((row + 1, column), "Bottom", "Top"),
                                         ((row, column + 1), "Right", "Left")]:
            neighbor = slots.get(adjacent)
            if neighbor is None:
                continue
            position = row, column
            # Explicit spans already allow equivalent expanded tables; do not add
            # a visual-format restriction to their established semantic contract.
            separated = cell is not neighbor and (
                cell.get("Border" + first, "solid") != "none"
                or neighbor.get("Border" + second, "solid") != "none"
            )
            if cell is not neighbor:
                relations.append((position, adjacent, separated))
            if not separated:
                links[position].add(adjacent)
                links[adjacent].add(position)
    alternatives: dict[Position, str] = {}
    unseen = set(slots)
    while unseen:
        pending, group = [unseen.pop()], set[Position]()
        while pending:
            position = pending.pop()
            if position in group:
                continue
            group.add(position)
            unseen.discard(position)
            pending.extend(links[position] - group)
        words = {visible(source_text(slots[position])) for position in group} - {""}
        if len(words) == 1:
            value = words.pop()
            alternatives.update({position: value for position in group
                                 if not visible(source_text(slots[position]))})
    return relations, alternatives


def browser_geometry(markups: list[str]) -> list[Geometry]:
    """Observe CSS borders in a real renderer, accepting shorthands and equivalent spans."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.abort())
            page.set_content("<style>table{border-collapse:collapse}td,th{border:1px solid}</style>"
                             + "".join(markups))
            result: list[Geometry] = page.evaluate("""() => [...document.querySelectorAll('table')].map(table => {
                const grid = []; let owner = 0;
                [...table.rows].forEach((row, r) => {
                    let c = 0; grid[r] ||= [];
                    [...row.cells].forEach(cell => {
                        while (grid[r][c]) c++;
                        const style = getComputedStyle(cell), data = {owner: owner++};
                        for (const side of ['Top', 'Bottom', 'Left', 'Right']) {
                            const kind = style['border' + side + 'Style'];
                            data[side] = kind === 'hidden' ? 'hidden' :
                                (kind === 'none' || parseFloat(style['border' + side + 'Width']) === 0 ? 'none' : 'line');
                        }
                        for (let y = 0; y < cell.rowSpan; y++) {
                            grid[r+y] ||= [];
                            for (let x = 0; x < cell.colSpan; x++) grid[r+y][c+x] = data;
                        }
                        c += cell.colSpan;
                    });
                });
                return grid;
            })""")
            return result
        finally:
            browser.close()


def rendered_boundary(geometry: Geometry, first: Position, second: Position) -> bool | None:
    try:
        before, after = geometry[first[0]][first[1]], geometry[second[0]][second[1]]
    except IndexError:
        return None
    if not before or not after:
        return None
    if before["owner"] == after["owner"]:
        return False
    sides = ("Bottom", "Top") if first[1] == second[1] else ("Right", "Left")
    borders = {before[sides[0]], after[sides[1]]}
    return "line" in borders and "hidden" not in borders


def prose_nodes(root: ET.Element) -> list[ET.Element]:
    if root.tag == "Table":
        return []
    if root.tag in {"Sentence", "LawTitle", "LawNum", "EnactStatement", "ArticleTitle",
                    "AppdxTableTitle", "TableStructTitle", "SupplProvisionLabel"}:
        return [root]
    return [node for child in root for node in prose_nodes(child)]


def document_checks(root: ET.Element, markdown: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    failures: list[dict[str, Any]] = []
    expected, rendered = source_tables(root), rendered_tables(markdown)
    actual = [grid for grid, _ in rendered]
    geometries = browser_geometry([markup for _, markup in rendered])
    if len(expected) != len(actual):
        failures.append({"contract": "all_tables_present", "expected": len(expected),
                         "actual": len(actual)})
    positions, boundaries = 0, 0
    source = list(root.iter("Table"))
    for table_number, grid in enumerate(expected, 1):
        relations, alternatives = source_boundaries(source[table_number - 1])
        output = actual[table_number - 1] if table_number <= len(actual) else []
        geometry = geometries[table_number - 1] if table_number <= len(geometries) else []
        for first, second, separated in relations:
            boundaries += 1
            observed = rendered_boundary(geometry, first, second)
            if observed != separated:
                failures.append({"contract": "visible_border_scope", "table": table_number,
                                 "from_row_column": [n + 1 for n in first],
                                 "to_row_column": [n + 1 for n in second],
                                 "expected_separator": separated, "actual_separator": observed})
        if len(grid) != len(output):
            failures.append({"contract": "table_rows", "table": table_number,
                             "expected": len(grid), "actual": len(output)})
        for row_number, row in enumerate(grid, 1):
            output_row = output[row_number - 1] if row_number <= len(output) else []
            if len(row) != len(output_row):
                failures.append({"contract": "table_columns", "table": table_number,
                                 "row": row_number, "expected": len(row), "actual": len(output_row)})
            for column_number, value in enumerate(row, 1):
                positions += 1
                found = output_row[column_number - 1] if column_number <= len(output_row) else None
                equivalent = alternatives.get((row_number - 1, column_number - 1))
                if found != value and not (equivalent is not None and found == equivalent):
                    failures.append({"contract": "cell_legal_scope", "table": table_number,
                                     "row": row_number, "column": column_number,
                                     "expected": value, "actual": found})
    content, cursor, statements = visible(markdown), 0, 0
    for node in prose_nodes(root):
        expected_text = visible(source_text(node))
        if not expected_text:
            continue
        statements += 1
        # A document may present its title before its legal number; this does not
        # reorder operative prose, unlike moving an enactment clause or sentence.
        identity = node.tag in {"LawTitle", "LawNum"}
        found = content.find(expected_text, 0 if identity else cursor)
        if found < 0:
            failures.append({"contract": "ordered_complete_prose", "tag": node.tag,
                             "expected": expected_text})
        elif not identity:
            cursor = found + len(expected_text)
    return failures, {"tables": len(expected), "cell_positions": positions,
                      "border_relations": boundaries,
                      "prose_blocks": statements, "failures": len(failures)}


def cas_contents(env: dict[str, str], digests: set[str]) -> dict[str, bytes]:
    """Read the isolated bucket via S3, without assuming the product's object-key format."""
    sdk: Any = boto3
    client: Any = sdk.client(
        "s3", endpoint_url=env["WLH_S3_ENDPOINT"], region_name=env["WLH_S3_REGION"],
        aws_access_key_id=env["WLH_S3_ACCESS_KEY"], aws_secret_access_key=env["WLH_S3_SECRET_KEY"],
    )
    found: dict[str, bytes] = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=env["WLH_S3_BUCKET"]):
        for entry in page.get("Contents", []):
            response = client.get_object(Bucket=env["WLH_S3_BUCKET"], Key=entry["Key"])
            with response["Body"] as body:
                payload: bytes = body.read()
            digest = hashlib.sha256(payload).hexdigest()
            if digest in digests:
                found[digest] = payload
            if found.keys() >= digests:
                return found
    return found


def write_json(name: str, value: Any) -> None:
    (ARTIFACTS / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


class TestLegalXmlTables:
    def test_complete_laws_preserve_all_merged_cell_scopes_and_prose_after_offline_reparse(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """Full originals and externally visible relationships decide success, not word presence."""
        manifest: dict[str, Any] = json.loads((FIXTURES / "provenance.json").read_text())
        samples: list[dict[str, Any]] = manifest["samples"]
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        write_json("input-provenance.json", manifest)
        records: list[dict[str, Any]] = []
        routes: dict[str, tuple[bytes, str]] = {
            "/": ((FIXTURES.parent / "egov-home.html").read_bytes(), "text/html; charset=utf-8")
        }
        trees: dict[str, ET.Element] = {}
        for sample in samples:
            payload = (FIXTURES / sample["fixture"]).read_bytes()
            assert hashlib.sha256(payload).hexdigest() == sample["sha256"]
            official = json.loads(payload)
            xml = base64.b64decode(official["law_full_text"], validate=True)
            assert hashlib.sha256(xml).hexdigest() == sample["xml_sha256"]
            trees[sample["revision_id"]] = ET.fromstring(xml)
            (ARTIFACTS / sample["fixture"].replace(".json", ".xml")).write_bytes(xml)
            records.append({key: official[key] for key in ("law_info", "revision_info")})
            routes[f"/api/2/law_data/{sample['revision_id']}"] = (payload, "application/json")
        routes["/api/2/laws?offset=0"] = (
            json.dumps({"total_count": len(records), "count": len(records), "laws": records}).encode(),
            "application/json",
        )
        scopes = {revision: source_tables(tree) for revision, tree in trees.items()}
        write_json("expected-table-scopes.json", scopes)
        retention = scopes["413RJNJ01034000_20260401_508RJNJ08012024"][5]
        period = "判定が行われ、又は要求が却下され、若しくは取り下げられる日に係る特定日以後三年"
        assert retention[0][:2] == ["人事管理文書の区分"] * 2
        assert [row[3] for row in retention[1:4]] == [period] * 3
        assert [row[1] for row in retention[1:4]] == [
            "第八十六条の要求の文書", "第八十七条の判定の文書(写しを含む。)",
            "第八十八条の勧告の文書(写しを含む。)",
        ]
        vehicle = scopes["428M60000800017_20261020_508M60000800080"][0]
        assert vehicle[0] == ["自動車審査試験項目"] * 4 + ["自動車審査試験項目別費用額"] * 2
        work = ROOT / "work/us-jp" / NAME / tmp_path.name
        work.mkdir(parents=True, exist_ok=True)
        with source_server(routes, []) as (origin, requests):
            config = replay_config("dd-517", origin, work)
            cfg = yaml.safe_load(config.read_text())
            cfg["budget"] = {"requests": 10, "documents": 2, "seconds": 90, "response_bytes": 67108864}
            config.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
            (ARTIFACTS / "source-config.yaml").write_text(config.read_text())
            report, rows = collect_artifact("dd-517", config, initialized_db, NAME, requests)
        failures: list[dict[str, Any]] = []
        checks: list[dict[str, Any]] = []
        if report["exit_code"] != 0 or report["result"].get("status") != "complete":
            failures.append({"contract": "successful_full_law_delivery", "result": report})
        if len(rows) != len(samples) or {row["title"] for row in rows} != {s["title"] for s in samples}:
            failures.append({"contract": "exact_complete_law_set", "actual": [r["title"] for r in rows]})
        expected_hashes = {s["sha256"] for s in samples} | {r["markdown_hash"] for r in rows}
        stored = cas_contents(initialized_db, expected_hashes)
        if stored.keys() != expected_hashes:
            failures.append({"contract": "complete_originals_and_markdown_in_cas",
                             "missing": sorted(expected_hashes - stored.keys())})
        for sample in samples:
            row = next((r for r in rows if r["title"] == sample["title"]), None)
            if row is None:
                continue
            issues, counts = document_checks(trees[sample["revision_id"]], row["markdown"])
            checks.append({"law_id": sample["law_id"], **counts})
            failures.extend({"law_id": sample["law_id"], **issue} for issue in issues)
            if stored.get(row["markdown_hash"]) != row["markdown"].encode():
                failures.append({"contract": "markdown_bytes_match_cas", "law_id": sample["law_id"]})
        write_json("collect-assertions.json", {"checks": checks, "failures": failures})
        code, stdout, stderr = run_wlh_cli("reparse", str(config), "--no-progress", env=initialized_db)
        replay: dict[str, Any] = json.loads(stdout)
        write_json("reparse.json", {"exit_code": code, "result": replay, "stderr": stderr})
        if code != 0 or replay.get("status") != "complete" or replay.get("report", {}).get("external_requests") != 0:
            failures.append({"contract": "offline_reparse_with_no_source_network", "result": replay})
        with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                after: list[dict[str, Any]] = cursor.execute(
                    "SELECT d.* FROM wlh.document_row d JOIN wlh.resource r "
                    "ON r.current_version_id=d.version_id JOIN wlh.frontier f ON f.id=r.frontier_id "
                    "WHERE f.source_id='dd-517' ORDER BY d.title"
                ).fetchall()
        def identity(row: dict[str, Any]) -> tuple[str, str | None, str]:
            return row["title"], row["original_hash"], row["markdown_hash"]

        if sorted(map(identity, rows)) != sorted(map(identity, after)):
            failures.append({"contract": "stable_offline_full_document_hashes"})
        replay_bytes = cas_contents(initialized_db, {r["markdown_hash"] for r in after})
        for index, row in enumerate(after, 1):
            markdown = replay_bytes[row["markdown_hash"]].decode()
            (ARTIFACTS / f"reparse-{index}.md").write_text(markdown)
            sample = next(s for s in samples if s["title"] == row["title"])
            issues, counts = document_checks(trees[sample["revision_id"]], markdown)
            checks.append({"law_id": sample["law_id"], "phase": "reparse", **counts})
            failures.extend({"law_id": sample["law_id"], "phase": "reparse", **issue} for issue in issues)
        write_json("assertions.json", {"passed": not failures, "checks": checks, "failures": failures,
                                      "cas_verified_hashes": sorted(stored)})
        assert not failures, f"{len(failures)} contract failures; first: {failures[:3]}; see {ARTIFACTS}"
