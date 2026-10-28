"""Original HTML with omitted tags must retain browser-visible legal tables.

Fresh original-evidence contracts precede the HTML repair. All five parents and
their mandatory annexes are replayed intact, never just extracted table fragments.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from .legacy_table_browser import compare_tables, html5_reader, missing_fragments
from .test_kfs_content_fidelity import FIXTURES, OUTPUT
from .test_kfs_content_fidelity import _receipt as write_receipt  # pyright: ignore[reportPrivateUsage]
from .test_kfs_content_fidelity import _run as run_complete_case  # pyright: ignore[reportPrivateUsage]


@dataclass(frozen=True)
class LegacyCase:
    case: str
    affected_file: str
    complete_rows: int
    meaningful_shapes: tuple[tuple[int, int], ...]
    textual_tables: frozenset[int] = frozenset()
    compare_data: bool = True


CASES = [
    LegacyCase("113/12", "index.html", 1, ((6, 2), (1, 2)), frozenset({1}), False),
    LegacyCase("43/08", "07.html", 8, ((5, 2), (13, 2), (1, 2)), frozenset({2})),
    LegacyCase("43/14", "01.html", 2, ((14, 9),)),
    LegacyCase("45/04", "03.html", 5, ((6, 4),)),
    LegacyCase("46/15", "index.html", 1, ((2, 2),)),
]


class TestLegacyLegalTables:
    @pytest.mark.parametrize("case", CASES, ids=[c.case.replace("/", "-") for c in CASES])
    def test_original_omitted_tags_preserve_complete_ruling_and_visible_table_relationships(
        self, initialized_db: dict[str, str], tmp_path: Path, case: LegacyCase
    ) -> None:
        """HTML5 browser DOM is the source truth; no XML-style fixture normalization.

        The complete parent/annex bodies must remain in order. Source tables may
        keep HTML spans or expand them explicitly, and single-row declarations
        may become intact prose. Refusal is not a successful positive control.
        """
        name = "legacy-" + case.case.replace("/", "-")
        base = f"/service/JP/{case.case}/"
        affected = base + case.affected_file
        manifest: dict[str, Any] = json.loads((FIXTURES / "manifest.json").read_text())
        originals: dict[str, dict[str, Any]] = {}
        specs = [s for s in manifest["routes"] if s["path"].startswith(base)
                 and s["role"] == "original_full_document"]
        with html5_reader() as browser:
            for spec in specs:
                payload = (FIXTURES / spec["fixture"]).read_bytes()
                assert hashlib.sha256(payload).hexdigest() == spec["sha256"]
                originals[spec["path"]] = browser.original(payload, spec["encoding"])
            source_focus = originals[affected]
            source_shapes = tuple((len(t["meaningful_grid"]), len(t["meaningful_grid"][0]))
                                  for t in source_focus["tables"] if t["meaningful_grid"])
            assert source_shapes == case.meaningful_shapes, "The declared source browser baseline changed"
            assert len(originals) == case.complete_rows, "Complete parent inputs must remain in replay"
            report, rows, checks = run_complete_case(case.case, name, initialized_db, tmp_path)
            delivered: dict[str, dict[str, Any]] = {}
            for row in rows:
                assert hashlib.sha256(row["markdown"].encode()).hexdigest() == row["markdown_hash"]
                path = urlsplit(str(row["locator"]["url"])).path
                delivered[path] = browser.markdown(row["markdown"])
            empty_output = browser.markdown("")
            missing: dict[str, list[str]] = {
                path: missing_fragments(source, delivered.get(path, empty_output))
                for path, source in originals.items()
            }
            comparison = compare_tables(
                source_focus, delivered.get(affected, empty_output), case.textual_tables,
                compare_data=case.compare_data,
            )
            browser_version = browser.version
        checks.update({
            "complete_parent_and_all_annex_rows_delivered": report["exit_code"] == 0
            and len(rows) == case.complete_rows and set(delivered) == set(originals),
            "offline_reparse_successful": report["offline"]["exit_code"] == 0,
            "all_original_browser_body_fragments_preserved_in_order": not any(missing.values()),
            "browser_visible_table_relationships_and_declarations_preserved": comparison[
                "all_relationships_preserved"],
            "delivered_tables_have_no_overlapping_cells": not any(
                table["overlaps"] for document in delivered.values() for table in document["tables"]
            ),
        })
        directory = OUTPUT / name
        evidence = {"browser": f"Chromium {browser_version}", "raw_fixture_bytes_unchanged": True,
                    "originals": originals, "delivered": delivered, "affected_document": affected}
        (directory / "browser-dom-evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2)
        )
        write_receipt(name, checks, comparison=comparison, source_meaningful_shapes=source_shapes,
                      missing_or_reordered_full_case_body=missing, browser=browser_version)
