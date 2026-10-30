"""Full KFS rulings must retain row-header meaning and single legal clause labels.

The independent author inspected original bytes and browser-visible differences
before implementation. See representation-contract-before-implementation.json.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import psycopg
import pytest

from .conftest import run_wlh_cli
from .kfs_literal_browser import application_lines, capture_document, subtitle_position
from .legacy_table_browser import compare_tables, html5_reader, missing_fragments
from .legal_presentation_browser import (
    body_without_separately_checked_labels,
    capture_focus,
    displayed_labels,
    header_relationships,
    row_headers,
    show_document,
    source_literal_labels,
)
from .source_replay_support import (
    ARTIFACTS,
    ROOT,
    Route,
    collect_artifact,
    replay_config,
    source_server,
)
from .test_kfs_content_fidelity import FIXTURES, HTML, OUTPUT
from .test_kfs_content_fidelity import _receipt as write_receipt  # pyright: ignore[reportPrivateUsage]
from .test_kfs_content_fidelity import _run as run_complete_case  # pyright: ignore[reportPrivateUsage]
from .test_kfs_content_fidelity import _signature as db_signature  # pyright: ignore[reportPrivateUsage]

LITERAL_FIXTURES = ROOT / "tests/fixtures/dd-582/literal-fidelity"
LITERAL_OUTPUT = ARTIFACTS / "kfs-literal-contract"
HREF = re.compile(r"(<a\b[^>]*?\bhref\s*=\s*)([\"'])(.*?)(\2)", re.I | re.S)


def _literal_routes(case: str, directory: Path) -> dict[str, Route]:
    if not (LITERAL_FIXTURES / "manifest.json").is_file():
        pytest.skip("Private fixture directory tests/fixtures/dd-582/literal-fidelity not present")
    manifest: dict[str, Any] = json.loads((LITERAL_FIXTURES / "manifest.json").read_text())
    for spec in manifest["routes"]:
        raw = (LITERAL_FIXTURES / spec["fixture"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == spec["sha256"]
    base = f"/service/JP/{case}/"
    catalog = {
        "/service/index.html",
        "/service/JP/index.html",
        f"/service/JP/idx/{case.split('/')[0]}.html",
    }
    selected = [
        r for r in manifest["routes"] if r["path"] in catalog or r["path"].startswith(base)
    ]
    allowed = {r["path"] for r in selected}
    routes: dict[str, Route] = {}
    evidence: list[dict[str, Any]] = []
    for spec in selected:
        payload = (LITERAL_FIXTURES / spec["fixture"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == spec["sha256"]
        changes: list[str] = []
        if "text/html" in spec["content_type"]:
            text = payload.decode(spec["encoding"])
            original = HTML.fromstring(text)
            protected: set[str] = set()
            if spec["role"] != "complete_recorded_directory":
                protected = set(
                    original.xpath('//*[@id="main"]//a[not(ancestor::*[@id="pankuzu"])]/@href')
                )

            def restrict(
                match: re.Match[str],
                source_url: str = spec["source_url"],
                protected_hrefs: set[str] = protected,
            ) -> str:
                href = match.group(3)
                path = urlsplit(urljoin(source_url, href)).path
                if href.startswith("#") or href in protected_hrefs or path in allowed:
                    return match.group(0)
                changes.append(href)
                return match.group(1) + match.group(2) + "#unselected" + match.group(4)

            payload = HREF.sub(restrict, text).encode(spec["encoding"])
        routes[spec["path"]] = (payload, spec["content_type"])
        evidence.append({
            "path": spec["path"],
            "role": spec["role"],
            "source_sha256": spec["sha256"],
            "replay_sha256_before_origin_rewrite": hashlib.sha256(payload).hexdigest(),
            "unselected_navigation_hrefs": changes,
        })
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "scope.json").write_text(
        json.dumps(
            {
                "scope": (
                    "Complete original parent and all required attachments "
                    "via full recorded directories"
                ),
                "source_config": "sources/dd-582.yaml; original extraction rules unchanged",
                "live_census_claimed": False,
                "routes": evidence,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return routes


def _run_literal_case(
    case: str,
    name: str,
    expected_rows: int,
    parent_published_at: str,
    env: dict[str, str],
    tmp_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, bool]]:
    directory = LITERAL_OUTPUT / name
    routes = _literal_routes(case, directory)
    with source_server(routes, ["www.kfs.go.jp"]) as (origin, requests):
        config = replay_config("dd-582", origin, tmp_path)
        report, rows = collect_artifact(
            "dd-582",
            config,
            env,
            str(LITERAL_OUTPUT.relative_to(ARTIFACTS) / name),
            requests,
        )
        required = {path for path in routes if path.endswith(".html")}
        transport_ok = required <= set(requests) and not (
            set(requests) - set(routes) - {"/robots.txt"}
        )
    before = db_signature(env)
    with psycopg.connect(env["WLH_DATABASE_URL"]) as conn:
        db_dates = conn.execute(
            "SELECT d.detail_url, d.role, d.published_at::text, d.effective_at::text "
            "FROM wlh.document_row d "
            "JOIN wlh.resource r ON r.current_version_id=d.version_id "
            "JOIN wlh.frontier f ON f.id=r.frontier_id "
            "WHERE f.source_id='dd-582' ORDER BY d.detail_url, d.row_index"
        ).fetchall()
    code, stdout, stderr = run_wlh_cli("reparse", str(config), "--no-progress", env=env)
    offline: dict[str, Any] = {"exit_code": code, "stderr": stderr}
    try:
        offline_result: dict[str, Any] = json.loads(stdout)
    except json.JSONDecodeError:
        offline_result = {}
        offline["stdout"] = stdout
    offline["result"] = offline_result
    after = db_signature(env)
    (directory / "reparse.json").write_text(json.dumps(offline, ensure_ascii=False, indent=2))
    report["offline"] = offline
    offline_report: dict[str, Any] = offline_result.get("report", {})
    parent_path = f"/service/JP/{case}/index.html"
    dates_ok = (
        len(db_dates) == expected_rows
        and sum(
            1
            for url, role, pub, eff in db_dates
            if urlsplit(str(url)).path == parent_path
            and role == "primary"
            and pub == parent_published_at
            and eff is None
        )
        == 1
        and sum(
            1
            for _, role, pub, eff in db_dates
            if role == "independent_attachment" and pub is None and eff is None
        )
        == expected_rows - 1
    )
    checks: dict[str, bool] = {
        "all_full_parent_and_annex_responses_fetched_without_missing_routes": transport_ok,
        "expected_row_count_and_date_contract_preserved": dates_ok,
        "offline_reparse_external_requests_zero": offline_report.get("external_requests") == 0,
        "offline_reparse_preserves_visible_current_delivery": before == after,
    }
    return report, rows, checks


class TestLegalHtmlPresentation:
    @pytest.mark.parametrize(("case", "affected_file", "kind"), [
        ("44/10", "index.html", "row-headers"),
        ("103/09", "besshi02.html", "literal-labels"),
    ], ids=["row-header-meaning", "single-explicit-numbering"])
    def test_complete_ruling_preserves_source_visible_presentation_semantics(
        self, initialized_db: dict[str, str], tmp_path: Path,
        case: str, affected_file: str, kind: str,
    ) -> None:
        name = "presentation-" + case.replace("/", "-")
        base = f"/service/JP/{case}/"
        affected = base + affected_file
        directory = OUTPUT / name
        directory.mkdir(parents=True, exist_ok=True)
        if not (FIXTURES / "manifest.json").is_file():
            pytest.skip("Private fixture directory tests/fixtures/dd-582/content not present")
        manifest: dict[str, Any] = json.loads((FIXTURES / "manifest.json").read_text())
        specs = [s for s in manifest["routes"] if s["path"].startswith(base)
                 and s["role"] == "original_full_document"]
        assert len(specs) == 2, "The complete parent and required annex must remain in replay"
        originals: dict[str, dict[str, Any]] = {}
        with html5_reader() as browser:
            for spec in specs:
                payload = (FIXTURES / spec["fixture"]).read_bytes()
                assert hashlib.sha256(payload).hexdigest() == spec["sha256"]
                originals[spec["path"]] = browser.original(payload, spec["encoding"])
            focus_spec = next(s for s in specs if s["path"] == affected)
            show_document(browser.page, (FIXTURES / focus_spec["fixture"]).read_bytes().decode(
                focus_spec["encoding"]), original=True)
            source_headers = row_headers(browser.page, original=True) if kind == "row-headers" else []
            labels = source_literal_labels(browser.page) if kind == "literal-labels" else []
            source_labels = displayed_labels(browser.page, labels, original=True)
            if kind == "row-headers":
                assert [h["label"] for h in source_headers] == [
                    "総所得金額", "分離課税の長期譲渡所得の金額", "納付すべき税額", "過少申告加算税の額",
                ]
            else:
                assert len(labels) == 17 and [s["label"] for s in labels[:3]] == ["1", "2", "3"]
                assert all(s["exactly_one_expected_label"] for s in source_labels)
            capture_focus(browser.page, directory, kind, original=True)

            report, rows, checks = run_complete_case(case, name, initialized_db, tmp_path)
            delivered: dict[str, dict[str, Any]] = {}
            markdown: dict[str, str] = {}
            for row in rows:
                assert hashlib.sha256(row["markdown"].encode()).hexdigest() == row["markdown_hash"]
                path = urlsplit(str(row["locator"]["url"])).path
                markdown[path] = row["markdown"]
                delivered[path] = browser.markdown(row["markdown"])
            empty = browser.markdown("")
            missing = {
                path: missing_fragments(
                    body_without_separately_checked_labels(source, labels if path == affected else []),
                    delivered.get(path, empty),
                ) for path, source in originals.items()
            }
            show_document(browser.page, markdown.get(affected, ""), original=False)
            actual_headers = row_headers(browser.page, original=False) if kind == "row-headers" else []
            actual_labels = displayed_labels(browser.page, labels, original=False)
            if affected in markdown:
                capture_focus(browser.page, directory, kind, original=False)
            checks.update({
                "complete_parent_and_required_annex_delivered": report["exit_code"] == 0
                and len(rows) == 2 and set(delivered) == set(originals),
                "offline_reparse_successful": report["offline"]["exit_code"] == 0,
                "complete_original_body_words_amounts_and_order_preserved": not any(missing.values()),
            })
            if kind == "row-headers":
                checks["browser_rowheader_roles_keep_their_associated_values"] = (
                    header_relationships(source_headers) == header_relationships(actual_headers)
                )
            else:
                checks["each_explicit_legal_number_displayed_exactly_once"] = all(
                    s["exactly_one_expected_label"] for s in actual_labels
                )
            evidence = {"browser": browser.version, "originals": originals, "delivered": delivered,
                        "source_headers": source_headers, "actual_headers": actual_headers,
                        "source_labels": source_labels, "actual_labels": actual_labels,
                        "raw_bytes_not_repaired": True, "missing_full_case_body": missing}
            (directory / "browser-presentation-evidence.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2)
            )
        write_receipt(name, checks, missing_full_case_body=missing,
                      expected_row_headers=header_relationships(source_headers),
                      actual_row_headers=header_relationships(actual_headers),
                      displayed_labels=actual_labels)

    @pytest.mark.parametrize(
        ("case", "affected_file", "kind", "expected_rows", "parent_published_at"),
        [
            ("50/10", "index.html", "application", 1, "1995-12-18"),
            ("72/33", "09.html", "subtitle", 17, "2006-07-12"),
        ],
        ids=["literal-application-labels", "direct-main-subtitle"],
    )
    def test_complete_ruling_preserves_literal_application_labels_and_direct_main_subtitle(
        self,
        initialized_db: dict[str, str],
        tmp_path: Path,
        case: str,
        affected_file: str,
        kind: str,
        expected_rows: int,
        parent_published_at: str,
    ) -> None:
        name = "literal-" + case.replace("/", "-")
        base = f"/service/JP/{case}/"
        affected = base + affected_file
        directory = LITERAL_OUTPUT / name
        directory.mkdir(parents=True, exist_ok=True)
        if not (LITERAL_FIXTURES / "manifest.json").is_file():
            pytest.skip("Private fixture directory tests/fixtures/dd-582/literal-fidelity not present")
        manifest: dict[str, Any] = json.loads((LITERAL_FIXTURES / "manifest.json").read_text())
        specs = [
            s
            for s in manifest["routes"]
            if s["path"].startswith(base) and s["role"] != "complete_recorded_directory"
        ]
        assert len(specs) == expected_rows, (
            "The complete parent and every required attachment must remain in replay"
        )
        originals: dict[str, dict[str, Any]] = {}
        with html5_reader() as browser:
            for spec in specs:
                payload = (LITERAL_FIXTURES / spec["fixture"]).read_bytes()
                assert hashlib.sha256(payload).hexdigest() == spec["sha256"]
                originals[spec["path"]] = browser.original(payload, spec["encoding"])
            focus_spec = next(s for s in specs if s["path"] == affected)
            focus_text = (LITERAL_FIXTURES / focus_spec["fixture"]).read_bytes().decode(
                focus_spec["encoding"]
            )
            source_obs = capture_document(
                browser.page, focus_text, directory, kind, original=True
            )
            if kind == "application":
                assert source_obs["application_lines"] == application_lines(
                    browser.page, original=True
                )
                assert all(
                    item["label_and_line_boundary_preserved"]
                    for item in source_obs["application_lines"]
                )
                assert not any(
                    item["owner_contains_application"]
                    for item in source_obs["application_lines"][2:]
                )
            else:
                assert source_obs["subtitle"] == subtitle_position(browser.page, original=True)
                assert source_obs["subtitle"]["matches"] == 1
                assert source_obs["subtitle"]["visible"]
                assert not source_obs["subtitle"]["in_table"]
                assert source_obs["subtitle"]["precedes_every_table"]

            report, rows, checks = _run_literal_case(
                case, name, expected_rows, parent_published_at, initialized_db, tmp_path
            )
            delivered: dict[str, dict[str, Any]] = {}
            markdown: dict[str, str] = {}
            for row in rows:
                assert hashlib.sha256(row["markdown"].encode()).hexdigest() == row["markdown_hash"]
                path = urlsplit(str(row["locator"]["url"])).path
                markdown[path] = row["markdown"]
                delivered[path] = browser.markdown(row["markdown"])
            empty = browser.markdown("")
            missing = {
                path: missing_fragments(source, delivered.get(path, empty))
                for path, source in originals.items()
            }
            table_diffs = {
                path: compare_tables(source, delivered.get(path, empty), frozenset())
                for path, source in originals.items()
            }
            actual_obs = capture_document(
                browser.page, markdown.get(affected, ""), directory, kind, original=False
            )
            checks.update({
                "every_complete_input_has_matching_delivered_row": (
                    report["exit_code"] == 0
                    and len(rows) == expected_rows
                    and set(delivered) == set(originals)
                ),
                "offline_reparse_successful": report["offline"]["exit_code"] == 0,
                "complete_original_body_fragments_preserved_in_order": not any(missing.values()),
                "all_meaningful_table_coordinates_preserved": all(
                    bool(diff.get("all_relationships_preserved"))
                    for diff in table_diffs.values()
                ),
            })
            if kind == "application":
                checks["both_application_labels_display_literal_one_in_order"] = all(
                    item["label_and_line_boundary_preserved"]
                    for item in actual_obs["application_lines"]
                )
                checks["adjudication_paragraphs_b_and_c_outside_application_list"] = not any(
                    item["owner_contains_application"]
                    for item in actual_obs["application_lines"][2:]
                )
            else:
                subtitle = actual_obs["subtitle"]
                checks["direct_main_subtitle_visible_before_every_table"] = bool(
                    subtitle.get("matches") == 1
                    and subtitle.get("visible")
                    and not subtitle.get("in_table")
                    and subtitle.get("precedes_every_table")
                )
            evidence = {
                "browser": browser.version,
                "originals": originals,
                "delivered": delivered,
                "source_observation": source_obs,
                "actual_observation": actual_obs,
                "missing_full_case_body": missing,
                "table_comparisons": table_diffs,
                "raw_bytes_not_repaired": True,
            }
            (directory / "browser-literal-evidence.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2)
            )
        result = {
            "checks": checks,
            "passed": all(checks.values()),
            "source_observation": source_obs,
            "actual_observation": actual_obs,
            "missing_full_case_body": missing,
            "table_comparisons": table_diffs,
        }
        (directory / "assertions.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str)
        )
        assert result["passed"], {
            "failed": [k for k, v in checks.items() if not v],
            "actual_observation": actual_obs,
            "missing_full_case_body": {k: v for k, v in missing.items() if v},
        }
