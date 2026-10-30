"""Explicit failure contracts for empty discovery and failed browser subrequests."""

from __future__ import annotations

from pathlib import Path

import psycopg

from .bridge_fixture_support import (
    BridgeFixtureHandler as _BridgeFixtureHandler,
)
from .bridge_fixture_support import (
    bridge_fixture_servers as bridge_fixture_servers,
)


class TestAdaptedSourceConfigReplayAndZeroTargetGuard:
    def test_collect_with_zero_discovered_targets_fails_with_explicit_error(
        self,
        initialized_db: dict[str, str],
        bridge_fixture_servers: tuple[int, int],
        tmp_path: Path,
    ) -> None:
        """When an entry page returns HTTP 200 OK but discovers 0 document targets, collect fails with `zero_targets_discovered` without polluting HTTP failure cases."""
        port, _ = bridge_fixture_servers
        _BridgeFixtureHandler.active_source_id = "dd-998"

        try:
            empty_yaml = tmp_path / "dd-998.yaml"
            empty_yaml.write_text(
                f"""
id: dd-998
scheduler:
  scope: "Zero target foolproof guard contract test"
  entry_urls:
    - "http://127.0.0.1:{port}/shenpan/gengduo/empty.html"
  allowed_hosts:
    - "127.0.0.1"
  allowed_path_prefixes:
    - "/shenpan/"
fetcher:
  mode: http
  delay: 0.0
processor:
  parser:
    type: html
    documents:
      detail_url_patterns:
        - "^http://127\\\\.0\\\\.0\\\\.1:{port}/shenpan/xiangqing/\\\\d+\\\\.html$"
      detail_body: always
      content_xpath: '//div[@class="txt_txt"]'
cleaners: {{}}
storage:
  archive_approved: true
  metadata:
    source_name: "Empty List Test Source"
    document_type: "案例"
    language: "zh"
    jurisdiction: "CN"
""".strip(),
                encoding="utf-8",
            )

            from wlh.scheduler.runs import execute_run, prepare_run

            c_run = prepare_run(empty_yaml, "collect")
            c_res = execute_run(c_run)
            assert c_res["status"] == "failed", f"Expected collect to fail on 0 targets: {c_res}"
            assert "zero_targets_discovered" in c_res["errors"]
            assert "zero_targets_discovered" in c_res["report"]["coverage"]["known_gaps"]
            assert c_res["report"]["failures"].get("enumeration_incomplete", 0) == 1
            assert c_res["report"]["coverage"]["selected_scope_enumeration"] == "incomplete"

            not_found_yaml = tmp_path / "dd-999.yaml"
            not_found_yaml.write_text(
                empty_yaml.read_text(encoding="utf-8")
                .replace("id: dd-998", "id: dd-999")
                .replace("/shenpan/gengduo/empty.html", "/shenpan/gengduo/missing-404.html"),
                encoding="utf-8",
            )
            nf_run = prepare_run(not_found_yaml, "collect")
            nf_res = execute_run(nf_run)
            assert nf_res["status"] == "failed"
            assert "http_404" in nf_res["errors"]
            assert "zero_targets_discovered" not in nf_res["errors"]
            assert nf_res["report"]["failures"] == {"network_failed": 1}
        finally:
            _BridgeFixtureHandler.active_source_id = ""

    def test_browser_detail_subrequest_http_503_propagates_failure_and_blocks_target(
        self,
        initialized_db: dict[str, str],
        bridge_fixture_servers: tuple[int, int],
        tmp_path: Path,
    ) -> None:
        """When a rendered_dom detail page encounters a same-origin/in-scope subrequest HTTP 503, collect propagates `http_503` and refuses to commit the truncated detail target."""
        srv_port, _ = bridge_fixture_servers
        _BridgeFixtureHandler.active_source_id = "dd-997"
        try:
            sub503_yaml = tmp_path / "dd-997.yaml"
            sub503_yaml.write_text(
                f"""
id: dd-997
scheduler:
  scope: "Detail page subrequest 503 propagation E2E test"
  entry_urls:
    - "http://127.0.0.1:{srv_port}/dt"
  allowed_hosts:
    - "127.0.0.1"
  allowed_path_prefixes:
    - "/dt"
  max_depth: 1
fetcher:
  mode: rendered_dom
  robots: false
  wait_until: load
  delay: 0.0
  retries: 0
  page_timeout_ms: 15000
processor:
  parser:
    type: html
    documents:
      detail_url_patterns:
        - "^http://127\\\\.0\\\\.0\\\\.1:{srv_port}/dt/[^/?#]+/\\\\d{{15,}}$"
      detail_body: always
      content_xpath: '//div[contains(@class, "info-dt-l")] | //div[@id="nav-home"]'
cleaners: {{}}
storage:
  archive_approved: true
  metadata:
    source_name: "Subrequest 503 Test"
    document_type: "法律法规"
    language: "vi"
    jurisdiction: "VN"
""".strip(),
                encoding="utf-8",
            )
            from wlh.scheduler.runs import execute_run, prepare_run

            run_id = prepare_run(sub503_yaml, "collect")
            res = execute_run(run_id)
            assert res["status"] == "failed", f"Expected detail subrequest 503 run to fail: {res}"
            assert "http_503" in res["errors"]
            assert "http_503" in res["report"]["coverage"]["known_gaps"]
            assert res["report"]["failures"].get("network_failed", 0) >= 1
            with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as conn:
                with conn.cursor() as cur:
                    row_count = cur.execute(
                        """
                        SELECT count(*)
                        FROM wlh.document_row d
                        JOIN wlh.version v ON v.id = d.version_id
                        JOIN wlh.resource r ON r.id = v.resource_id
                        JOIN wlh.frontier f ON f.id = r.frontier_id
                        WHERE f.source_id = 'dd-997'
                        """
                    ).fetchone()
                    assert row_count is not None and row_count[0] == 0
        finally:
            _BridgeFixtureHandler.active_source_id = ""
