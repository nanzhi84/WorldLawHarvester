"""Archived source fixture bundles and offline replay contracts."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import psycopg

from wlh.cleaners.delivery_quality import (
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
        """Every `dd-*` fixture directory in `tests/fixtures/` is self-describing via a valid `wlh.fixture.v1` `manifest.json` with real on-disk fixtures and stratified sampling metadata."""
        assert FIXTURES_DIR.is_dir(), "Fixture directory tests/fixtures must exist"
        fixture_dirs = sorted(
            d for d in FIXTURES_DIR.iterdir() if d.is_dir() and d.name.startswith("dd-")
        )
        assert len(fixture_dirs) >= 72
        valid_strata = {
            "p5_shortest",
            "p95_longest",
            "accept_with_warning",
            "missing_doc_number",
            "missing_effective_at",
            "baseline",
        }
        for d in fixture_dirs:
            manifest = load_fixture_manifest(FIXTURES_DIR, d.name)
            assert manifest["schema"] == "wlh.fixture.v1", f"Invalid schema in {d}"
            assert manifest["source_id"] == d.name
            routes: list[dict[str, Any]] = manifest["routes"]
            assert len(routes) >= 1
            for route in routes:
                assert "binary_attr" not in route, (
                    f"{d.name} route {route.get('path')} must use real fixture file instead of binary_attr"
                )
            if "sampling_strata" in manifest:
                raw_strata = manifest["sampling_strata"]
                assert isinstance(raw_strata, list)
                strata = [str(s) for s in cast(list[Any], raw_strata)]
                assert len(strata) >= 1
                assert set(strata).issubset(valid_strata)

    def test_stateless_fixture_routing_resolves_concurrent_sources_without_global_active_source_id(
        self, bridge_fixture_servers: tuple[int, int]
    ) -> None:
        """`FixtureRouter` and `BridgeFixtureHandler` resolve routes statelessly via `X-WLH-Source-Id` header or `/__source__/<source_id>/...` path prefix without any global `active_source_id` state."""
        import urllib.error
        import urllib.request

        from .bridge_fixture_support import BridgeFixtureHandler

        assert not hasattr(BridgeFixtureHandler, "active_source_id")
        port, _port_v6 = bridge_fixture_servers
        base_url = f"http://127.0.0.1:{port}"

        req_header = urllib.request.Request(
            f"{base_url}/eng/ppr/dip.htm",
            headers={"X-WLH-Source-Id": "dd-507"},
        )
        with urllib.request.urlopen(req_header, timeout=5) as resp:
            assert resp.status == 200
            body_header = resp.read().decode("utf-8", errors="ignore")
            assert "dipn03.pdf" in body_header

        req_prefix = urllib.request.Request(
            f"{base_url}/__source__/dd-501/gongbao/2026/issue_13046/202610/content_7083670.html"
        )
        with urllib.request.urlopen(req_prefix, timeout=5) as resp:
            assert resp.status == 200
            body_prefix = resp.read().decode("utf-8", errors="ignore")
            assert "中华人民共和国国家发展和改革委员会令（第45号）" in body_prefix

        req_missing_source = urllib.request.Request(f"{base_url}/eng/ppr/dip.htm")
        try:
            urllib.request.urlopen(req_missing_source, timeout=5)
            raise AssertionError("Expected HTTP 400 when source_id is omitted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400

    def test_collected_run_exports_cas_fixture_bundle_and_replays_cleanly_via_fixture_router(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """A collected source in PostgreSQL + S3 CAS exports via `wlh fixture export` into a self-contained `manifest.json` bundle with Anomaly-First Stratified Sampling (`sampling_strata`) covering all 6 strata and replays offline without the original server."""
        from wlh.runbook_samples import stratify_verification_samples
        from wlh.storage.cas import Objects

        single_strat = stratify_verification_samples(
            [
                {
                    "resource_id": "single-1",
                    "title": "Single Resource Act 2026",
                    "doc_number": "Act 1",
                    "effective_at": "2026-01-01",
                    "markdown": "Section 1. Normal legal body text for single item stratification.",
                }
            ],
            max_items=5,
        )
        assert len(single_strat) == 1
        assert single_strat[0]["sampling_strata"] == ["p5_shortest"]

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
                        f'<li><a href="http://127.0.0.1:{port}/laws/act-3.html">Data Governance Guidelines 2026</a></li>'
                        f'<li><a href="http://127.0.0.1:{port}/laws/act-4.html">Cybersecurity Resilience Act 2026</a></li>'
                        f'<li><a href="http://127.0.0.1:{port}/laws/act-5.html">Cross-Border Payments Regulation 2026</a></li>'
                        f"</ul></body></html>"
                    ).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path == "/laws/act-1.html":
                    body = (
                        "<html><body>"
                        '<h1 class="doc-title">Digital Commerce Act 2026</h1>'
                        '<div class="doc-meta"><span class="doc-no">Act No. 11 of 2026</span>'
                        '<span class="pub-date">2026-03-15</span>'
                        '<span class="eff-date">2026-04-01</span></div>'
                        '<div class="law-body"><p>Section 1. This Act regulates commercial electronic transactions and digital marketplaces.</p></div>'
                        "</body></html>"
                    ).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path == "/laws/act-2.html":
                    body = (
                        "<html><body>"
                        '<h1 class="doc-title">Electronic Signatures Act 2026</h1>'
                        '<div class="doc-meta"><span class="doc-no">Act No. 12 of 2026</span>'
                        '<span class="pub-date">2026-04-20</span></div>'
                        '<div class="law-body">'
                        "<p>Section 1. Qualified electronic signatures backed by accredited cryptographic certificates shall carry full legal equivalence to handwritten signatures across all commercial and administrative proceedings.</p>"
                        "<p>Section 2. Certification authorities shall maintain auditable revocation registries, hardware security modules, and cross-border mutual recognition protocols in accordance with international standards.</p>"
                        "<p>Section 3. Relying parties and supervisory bodies shall verify timestamping tokens and long-term archival validation records under uniform technical rules.</p>"
                        "</div>"
                        "</body></html>"
                    ).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path == "/laws/act-3.html":
                    body = (
                        "<html><body>"
                        '<h1 class="doc-title">Data Governance Guidelines 2026</h1>'
                        '<div class="doc-meta">'
                        '<span class="pub-date">2026-05-10</span>'
                        '<span class="eff-date">2026-06-01</span></div>'
                        '<div class="law-body"><p>Section 1. Public-sector data sharing, cross-border data stewardship, and algorithmic accountability obligations shall be supervised by the Data Governance Commission.</p></div>'
                        "</body></html>"
                    ).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path == "/laws/act-4.html":
                    body = (
                        "<html><body>"
                        '<h1 class="doc-title">Cybersecurity Resilience Act 2026</h1>'
                        '<div class="doc-meta"><span class="doc-no">Act No. 14 of 2026</span>'
                        '<span class="pub-date">2026-06-15</span>'
                        '<span class="eff-date">2026-07-01</span></div>'
                        '<div class="law-body"><p>Section 1. Critical infrastructure operators shall establish incident response plans, vulnerability disclosure channels, and supply chain risk assessments.</p></div>'
                        "</body></html>"
                    ).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path == "/laws/act-5.html":
                    body = (
                        "<html><body>"
                        '<h1 class="doc-title">Cross-Border Payments Regulation 2026</h1>'
                        '<div class="doc-meta"><span class="doc-no">Act No. 15 of 2026</span>'
                        '<span class="pub-date">2026-07-20</span>'
                        '<span class="eff-date">2026-08-01</span></div>'
                        '<div class="law-body"><p>Section 1. Payment service providers shall disclose settlement fees, foreign exchange reference rates, and clearing timelines before executing transfers.</p></div>'
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
scheduler:
  scope: "CAS fixture bundle export and replay E2E test"
  entry_urls:
    - "http://127.0.0.1:{origin_port}/laws/index.html"
  allowed_hosts:
    - "127.0.0.1"
  allowed_path_prefixes:
    - "/laws/"
fetcher:
  mode: http
  delay: 0.0
processor:
  parser:
    type: html
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
        effective_at:
          xpath: '//span[@class="eff-date"]'
          regex: '\\d{{4}}-\\d{{2}}-\\d{{2}}'
cleaners: {{}}
storage:
  archive_approved: true
  metadata:
    source_name: "Test Legislative Portal"
    document_type: "法律法规"
    language: "en"
    jurisdiction: "INTL"
""".strip(),
                encoding="utf-8",
            )

            from wlh.scheduler.runs import execute_run, prepare_run

            c_run = prepare_run(source_yaml, "collect")
            c_res = execute_run(c_run)
            assert c_res["status"] == "complete", f"Initial collect failed: {c_res}"
            assert verify_source_delivery("dd-905", require_multi_resource=True)["resource_count"] == 5

            # Attach a CAS result_ref with recommended_route='accept_with_warning' on act-4.html
            objects = Objects()
            warn_ref = objects.put_json(
                {
                    "deliverables": [
                        {
                            "quality_report": {
                                "contract": "wlh.quality.v2",
                                "recommended_route": "accept_with_warning",
                            }
                        }
                    ]
                }
            )
            with psycopg.connect(initialized_db["WLH_DATABASE_URL"], autocommit=True) as conn:
                conn.execute("ALTER TABLE wlh.version DISABLE TRIGGER USER")
                try:
                    conn.execute(
                        """
                        UPDATE wlh.version v
                        SET result_ref = %s
                        FROM wlh.resource r
                        WHERE r.current_version_id = v.id
                          AND r.public_url LIKE '%%/laws/act-4.html'
                        """,
                        (warn_ref,),
                    )
                finally:
                    conn.execute("ALTER TABLE wlh.version ENABLE TRIGGER USER")

            export_root = tmp_path / "exported_fixtures"
            export_dir = export_root / "dd-905"
            rc, out, err = run_wlh_cli(
                "fixture",
                "export",
                "dd-905",
                "--output-dir",
                str(export_dir),
                "--max-resources",
                "5",
                env=initialized_db,
            )
            assert rc == 0, f"wlh fixture export failed: {out} {err}"
            direct_manifest = export_source_fixture(
                "dd-905", export_dir, max_resources=5, source_yaml=source_yaml
            )
            assert direct_manifest["schema"] == "wlh.fixture.v1"
            assert (export_dir / "manifest.json").is_file()
            manifest = load_fixture_manifest(export_root, "dd-905")
            assert manifest["schema"] == "wlh.fixture.v1"
            assert len(manifest["routes"]) >= 6
            assert "sampling_strata" in manifest
            strata_set = set(manifest["sampling_strata"])
            assert {
                "p5_shortest",
                "p95_longest",
                "accept_with_warning",
                "missing_doc_number",
                "missing_effective_at",
                "baseline",
            }.issubset(strata_set), f"Expected all 6 strata in manifest, got {strata_set}"
            assert len(manifest.get("samples", [])) == 5

            html_files = list(export_dir.glob("*.html"))
            assert len(html_files) >= 6
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
                    "",
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
            from .conftest import TRUNCATE_TABLES_SQL

            with psycopg.connect(initialized_db["WLH_DATABASE_URL"], autocommit=True) as conn:
                conn.execute(TRUNCATE_TABLES_SQL)

            replay_yaml = tmp_path / "dd-905-replay.yaml"
            replay_yaml.write_text(
                source_yaml.read_text(encoding="utf-8").replace(
                    str(origin_port), str(replay_port)
                ),
                encoding="utf-8",
            )

            from wlh.scheduler.runs import execute_run, prepare_run

            rc_run = prepare_run(replay_yaml, "collect")
            rc_res = execute_run(rc_run)
            assert rc_res["status"] == "complete", f"Replay collect failed: {rc_res}"
            assert rc_res["errors"] == []

            verified = verify_source_delivery("dd-905", require_multi_resource=True)
            assert verified["passed"] is True
            assert verified["resource_count"] == 5

            rp_run = prepare_run(replay_yaml, "reparse")
            rp_res = execute_run(rp_run)
            assert rp_res["status"] == "complete", f"Replay reparse failed: {rp_res}"
            assert rp_res["report"]["external_requests"] == 0
        finally:
            replay_srv.shutdown()
            replay_thread.join(timeout=2.0)
            replay_srv.server_close()
