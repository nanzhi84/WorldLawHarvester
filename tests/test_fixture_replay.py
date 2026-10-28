"""Archived source fixture bundles and offline replay contracts."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import psycopg

from wlh.domain.delivery_quality import (
    verify_source_delivery,
)
from wlh.fixtures import FixtureRouter, export_source_fixture, load_fixture_manifest

from .bridge_fixture_support import (
    FIXTURES_DIR,
)
from .bridge_fixture_support import (
    bridge_fixture_servers as bridge_fixture_servers,
)
from .conftest import run_wlh_cli


class TestCasFixtureBundleExportAndReplay:
    def test_all_fixture_directories_contain_valid_manifest_without_hardcoded_route_table(
        self,
    ) -> None:
        """Every `dd-*` fixture directory in `tests/fixtures/` is self-describing via a valid `wlh.fixture.v1` `manifest.json`."""
        fixture_dirs = sorted(
            d for d in FIXTURES_DIR.iterdir() if d.is_dir() and d.name.startswith("dd-")
        )
        assert len(fixture_dirs) >= 40
        for d in fixture_dirs:
            manifest = load_fixture_manifest(FIXTURES_DIR, d.name)
            assert manifest["schema"] == "wlh.fixture.v1", f"Invalid schema in {d}"
            assert manifest["source_id"] == d.name
            routes: list[dict[str, Any]] = manifest["routes"]
            assert len(routes) >= 1

    def test_collected_run_exports_cas_fixture_bundle_and_replays_cleanly_via_fixture_router(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """A collected source in PostgreSQL + S3 CAS exports via `wlh fixture export` into a self-contained `manifest.json` bundle and replays offline without the original server."""
        origin_port_holder: list[int] = []

        class _OriginHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                port = origin_port_holder[0]
                if self.path == "/robots.txt":
                    body = b"User-agent: *\nAllow: /\n"
                    ctype = "text/plain"
                elif self.path == "/laws/index.html":
                    body = (
                        f"<html><body><ul>"
                        f'<li><a href="http://127.0.0.1:{port}/laws/act-1.html">Digital Commerce Act 2026</a></li>'
                        f'<li><a href="http://127.0.0.1:{port}/laws/act-2.html">Electronic Signatures Act 2026</a></li>'
                        f'<li><a href="http://127.0.0.1:{port}/laws/act-3.html">Data Governance Act 2026</a></li>'
                        f"</ul></body></html>"
                    ).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path == "/laws/act-1.html":
                    body = (
                        "<html><body>"
                        '<h1 class="doc-title">Digital Commerce Act 2026</h1>'
                        '<div class="doc-meta"><span class="doc-no">Act No. 11 of 2026</span>'
                        '<span class="pub-date">2026-03-15</span></div>'
                        '<div class="law-body"><p>Section 1. This Act regulates commercial electronic transactions, intermediary liability, and consumer protection across digital marketplaces.</p></div>'
                        "</body></html>"
                    ).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path == "/laws/act-2.html":
                    body = (
                        "<html><body>"
                        '<h1 class="doc-title">Electronic Signatures Act 2026</h1>'
                        '<div class="doc-meta"><span class="doc-no">Act No. 12 of 2026</span>'
                        '<span class="pub-date">2026-04-20</span></div>'
                        '<div class="law-body"><p>Section 1. Qualified electronic signatures backed by accredited cryptographic certificates shall carry full legal equivalence to handwritten signatures.</p></div>'
                        "</body></html>"
                    ).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path == "/laws/act-3.html":
                    body = (
                        "<html><body>"
                        '<h1 class="doc-title">Data Governance Act 2026</h1>'
                        '<div class="doc-meta"><span class="doc-no">Act No. 13 of 2026</span>'
                        '<span class="pub-date">2026-05-10</span></div>'
                        '<div class="law-body"><p>Section 1. Public-sector data sharing, cross-border data stewardship, and algorithmic accountability obligations shall be supervised by the Data Governance Commission.</p></div>'
                        "</body></html>"
                    ).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        origin_srv = ThreadingHTTPServer(("127.0.0.1", 0), _OriginHandler)
        origin_port = origin_srv.server_address[1]
        origin_port_holder.append(origin_port)
        origin_thread = threading.Thread(target=origin_srv.serve_forever, daemon=True)
        origin_thread.start()

        try:
            source_yaml = tmp_path / "dd-905.yaml"
            source_yaml.write_text(
                f"""
id: dd-905
adapter: web
archive_approved: true
scope: "CAS fixture bundle export and replay E2E test"
entry_urls:
  - "http://127.0.0.1:{origin_port}/laws/index.html"
allowed_hosts:
  - "127.0.0.1"
allowed_path_prefixes:
  - "/laws/"
delay: 0.0
metadata:
  source_name: "Test Legislative Portal"
  document_type: "法律法规"
  language: "en"
  jurisdiction: "INTL"
documents:
  detail_url_patterns:
    - "^http://127\\\\.0\\\\.0\\\\.1:{origin_port}/laws/act-\\\\d+\\\\.html$"
  detail_body: always
  content_xpath: '//div[@class="law-body"]'
  fields:
    title:
      xpath: '//h1[@class="doc-title"]'
    document_number:
      xpath: '//span[@class="doc-no"]'
    issuer:
      value: "Parliament"
    published_at:
      xpath: '//span[@class="pub-date"]'
      regex: '\\d{{4}}-\\d{{2}}-\\d{{2}}'
""".strip(),
                encoding="utf-8",
            )

            from wlh.engine.runs import execute_run, prepare_run

            c_run = prepare_run(source_yaml, "collect")
            c_res = execute_run(c_run)
            assert c_res["status"] == "complete", f"Initial collect failed: {c_res}"
            assert verify_source_delivery("dd-905", require_multi_resource=True)["resource_count"] == 3

            export_root = tmp_path / "exported_fixtures"
            export_dir = export_root / "dd-905"
            rc, out, err = run_wlh_cli(
                "fixture",
                "export",
                "dd-905",
                "--output-dir",
                str(export_dir),
                "--max-resources",
                "2",
                env=initialized_db,
            )
            assert rc == 0, f"wlh fixture export failed: {out} {err}"
            direct_manifest = export_source_fixture(
                "dd-905", export_dir, max_resources=2, source_yaml=source_yaml
            )
            assert direct_manifest["schema"] == "wlh.fixture.v1"
            assert (export_dir / "manifest.json").is_file()
            manifest = load_fixture_manifest(export_root, "dd-905")
            assert manifest["schema"] == "wlh.fixture.v1"
            assert len(manifest["routes"]) >= 3

            html_files = list(export_dir.glob("*.html"))
            assert len(html_files) >= 3
            assert any("{host}" in f.read_text(encoding="utf-8") for f in html_files)
            assert all(
                f"127.0.0.1:{origin_port}" not in f.read_text(encoding="utf-8")
                for f in html_files
            )
        finally:
            origin_srv.shutdown()
            origin_thread.join(timeout=2.0)
            origin_srv.server_close()

        replay_router = FixtureRouter(export_root)

        class _ReplayHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                rendered = replay_router.resolve_and_render(
                    "dd-905",
                    self.path,
                    headers=dict(self.headers.items()),
                )
                if rendered is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                status, content_type, body = rendered
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        replay_srv = ThreadingHTTPServer(("127.0.0.1", 0), _ReplayHandler)
        replay_port = replay_srv.server_address[1]
        replay_thread = threading.Thread(target=replay_srv.serve_forever, daemon=True)
        replay_thread.start()

        try:
            with psycopg.connect(initialized_db["WLH_DATABASE_URL"], autocommit=True) as conn:
                conn.execute("DROP SCHEMA IF EXISTS wlh CASCADE")
            from wlh.db import initialize

            initialize()

            replay_yaml = tmp_path / "dd-905-replay.yaml"
            replay_yaml.write_text(
                source_yaml.read_text(encoding="utf-8").replace(
                    str(origin_port), str(replay_port)
                ),
                encoding="utf-8",
            )

            from wlh.engine.runs import execute_run, prepare_run

            rc_run = prepare_run(replay_yaml, "collect")
            rc_res = execute_run(rc_run)
            assert rc_res["status"] == "complete", f"Replay collect failed: {rc_res}"
            assert rc_res["errors"] == []

            verified = verify_source_delivery("dd-905", require_multi_resource=True)
            assert verified["passed"] is True
            assert verified["resource_count"] == 2

            rp_run = prepare_run(replay_yaml, "reparse")
            rp_res = execute_run(rp_run)
            assert rp_res["status"] == "complete", f"Replay reparse failed: {rp_res}"
            assert rp_res["report"]["external_requests"] == 0
        finally:
            replay_srv.shutdown()
            replay_thread.join(timeout=2.0)
            replay_srv.server_close()
