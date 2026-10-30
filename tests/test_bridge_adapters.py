"""Structured legal XML and legal-data bridge collection contracts."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest

from .bridge_fixture_support import (
    BridgeFixtureHandler as _BridgeFixtureHandler,
)
from .bridge_fixture_support import (
    bridge_fixture_servers as bridge_fixture_servers,
)


class TestStructuredXmlAndBridgeAdapters:
    @pytest.mark.parametrize(
        ("source_id", "parser_type", "entry_path", "expected_doc_type", "expected_substr"),
        [
            ("dd-910", "xml", "/xml/akn-act-2026.xml", "法律法规", "2026 c. 15"),
            ("dd-911", "html", "/wwl/congbao-list.html", "法律法规", "55/2026/NĐ-CP"),
            ("dd-912", "json", "/juriscraper/ca1-opinions.json", "案例", "25-1001"),
            ("dd-913", "json", "/oldp/bgh-cases.html", "案例", "VIII ZR 12/25"),
            ("dd-914", "json", "/opencaselaw/ch-bger.html", "案例", "4A_101/2026"),
            ("dd-915", "json", "/openstates/bills.json", "法律法规", "HR 4200"),
        ],
    )
    def test_collect_and_offline_reparse_projects_expected_document_rows(
        self,
        initialized_db: dict[str, str],
        bridge_fixture_servers: tuple[int, int],
        tmp_path: Path,
        source_id: str,
        parser_type: str,
        entry_path: str,
        expected_doc_type: str,
        expected_substr: str,
    ) -> None:
        """Each structured XML and bridge parser collects into `wlh.document_row` and replays offline without network."""
        port, _ = bridge_fixture_servers
        _BridgeFixtureHandler.active_source_id = source_id

        try:
            prefix = "/" + entry_path.strip("/").split("/")[0] + "/"
            docs_block = (
                """
    documents:
      detail_url_patterns:
        - "/wwl/detail/"
      fields:
        document_number:
          xpath: '//span[@class="doc-number"]'
        issuer:
          xpath: '//span[@class="issuer"]'
        published_at:
          xpath: '//span[@class="published-at"]'
""".rstrip()
                if source_id == "dd-911"
                else ""
            )
            source_yaml = tmp_path / f"{source_id}.yaml"
            source_yaml.write_text(
                f"""
id: {source_id}
scheduler:
  scope: "Parser contract test for {parser_type} ({source_id})"
  entry_urls:
    - "http://127.0.0.1:{port}{entry_path}"
  allowed_hosts:
    - "127.0.0.1"
  allowed_path_prefixes:
    - "{prefix}"
fetcher:
  mode: http
  delay: 0.0
processor:
  parser:
    type: {parser_type}{docs_block}
cleaners: {{}}
storage:
  archive_approved: true
  metadata:
    source_name: "Test {parser_type}"
    document_type: "{expected_doc_type}"
    language: "en"
    jurisdiction: "INTL"
""".strip(),
                encoding="utf-8",
            )

            from wlh.scheduler.runs import execute_run, prepare_run
            from wlh.storage.snapshots import project_canonical

            c_run = prepare_run(source_yaml, "collect")
            c_res = execute_run(c_run)
            assert c_res["status"] == "complete", f"{parser_type} collect failed: {c_res}"

            with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as conn:
                from psycopg.rows import dict_row

                with conn.cursor(row_factory=dict_row) as cur:
                    rows = cur.execute(
                        """
                        SELECT f.source_id, r.id AS resource_id, d.*
                        FROM wlh.document_row d
                        JOIN wlh.version v ON v.id = d.version_id
                        JOIN wlh.resource r ON r.id = v.resource_id
                        JOIN wlh.frontier f ON f.id = r.frontier_id
                        WHERE f.source_id = %s
                        ORDER BY d.row_index
                        """,
                        (source_id,),
                    ).fetchall()
                    assert len(rows) >= 1, f"Expected document_row for {parser_type}"
                    projected = project_canonical(rows[0])
                    assert projected["doc_type"] == expected_doc_type
                    assert (
                        expected_substr in (projected["doc_number"] or "")
                        or expected_substr in (projected["title"] or "")
                    ), f"Expected {expected_substr} in {projected}"

            rp_run = prepare_run(source_yaml, "reparse")
            rp_res = execute_run(rp_run)
            assert rp_res["status"] == "complete", f"{parser_type} reparse failed: {rp_res}"
            assert rp_res["report"]["external_requests"] == 0
        finally:
            _BridgeFixtureHandler.active_source_id = ""

    def test_rss_uslm_plaw_feed_discovers_uslm_detail_links_and_parses_public_law_metadata_and_sections(
        self,
        initialized_db: dict[str, str],
        tmp_path: Path,
    ) -> None:
        """An RSS 2.0 feed with HTML-escaped `<description>` links discovers matching USLM `<pLaw>` XML detail URLs without emitting the RSS index itself, and parses `<pLaw>` metadata (`dc:title`, `citableAs`, `approvedDate`) and section body."""
        port_holder: list[int] = []
        requested_paths: list[str] = []

        class _PlawRssHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                requested_paths.append(self.path)
                port = port_holder[0]
                if self.path == "/robots.txt":
                    body = b"User-agent: *\nAllow: /\n"
                    ctype = "text/plain"
                elif self.path == "/rss/plaw.xml":
                    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>GovInfo Public Laws</title>
    <item>
      <title>Public Law 118-7</title>
      <link>http://127.0.0.1:{port}/app/details/PLAW-118publ7</link>
      <description>&lt;a href="http://127.0.0.1:{port}/content/pkg/PLAW-118publ7/pdf/PLAW-118publ7.pdf"&gt;PDF&lt;/a&gt; &lt;a href="http://127.0.0.1:{port}/content/pkg/PLAW-118publ7/uslm/PLAW-118publ7.xml"&gt;XML&lt;/a&gt;</description>
    </item>
  </channel>
</rss>""".encode("utf-8")
                    ctype = "application/rss+xml; charset=utf-8"
                elif self.path == "/content/pkg/PLAW-118publ7/uslm/PLAW-118publ7.xml":
                    body = """<?xml version="1.0" encoding="UTF-8"?>
<pLaw xmlns="http://schemas.gpo.gov/xml/uslm" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <meta>
    <dc:title>Public Law 118–7: An Act to modify the authority of the Secretary of Veterans Affairs</dc:title>
    <docNumber>7</docNumber>
    <citableAs>Public Law 118–7</citableAs>
    <approvedDate>2023-06-30</approvedDate>
  </meta>
  <main>
    <longTitle>
      <docTitle>An Act</docTitle>
      <officialTitle>To modify the authority of the Secretary of Veterans Affairs to furnish care and services.</officialTitle>
    </longTitle>
    <section identifier="/us/pl/118/7/s1">
      <num value="1">SECTION 1.</num>
      <heading>SHORT TITLE.</heading>
      <content>This Act may be cited as the Veterans Care Act of 2023.</content>
    </section>
  </main>
</pLaw>""".encode("utf-8")
                    ctype = "application/xml; charset=utf-8"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _PlawRssHandler)
        port = srv.server_address[1]
        port_holder.append(port)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()

        try:
            source_yaml = tmp_path / "dd-557.yaml"
            source_yaml.write_text(
                f"""
id: dd-557
scheduler:
  scope: "USLM Public Law RSS discovery and XML parsing contract test"
  entry_urls:
    - "http://127.0.0.1:{port}/rss/plaw.xml"
  allowed_hosts:
    - "127.0.0.1"
  allowed_path_prefixes:
    - "/rss/"
    - "/content/pkg/"
  max_depth: 1
fetcher:
  mode: http
  delay: 0.0
processor:
  parser:
    type: xml
    documents:
      detail_url_patterns:
        - "^http://127\\\\.0\\\\.0\\\\.1:{port}/content/pkg/PLAW-[^/]+/uslm/PLAW-[^/]+\\\\.xml$"
cleaners: {{}}
storage:
  archive_approved: true
  metadata:
    source_name: "GovInfo Public Laws"
    document_type: "法律法规"
    language: "en"
    jurisdiction: "US"
""".strip(),
                encoding="utf-8",
            )

            from wlh.scheduler.runs import execute_run, prepare_run
            from wlh.storage.cas import Objects
            from wlh.storage.snapshots import project_canonical

            c_run = prepare_run(source_yaml, "collect")
            c_res = execute_run(c_run)
            assert c_res["status"] == "complete", f"dd-557 collect failed: {c_res}"
            assert "/content/pkg/PLAW-118publ7/uslm/PLAW-118publ7.xml" in requested_paths
            assert "/content/pkg/PLAW-118publ7/pdf/PLAW-118publ7.pdf" not in requested_paths

            with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as conn:
                from psycopg.rows import dict_row

                with conn.cursor(row_factory=dict_row) as cur:
                    rows = cur.execute(
                        """
                        SELECT f.source_id, r.id AS resource_id, d.*
                        FROM wlh.document_row d
                        JOIN wlh.version v ON v.id = d.version_id
                        JOIN wlh.resource r ON r.id = v.resource_id
                        JOIN wlh.frontier f ON f.id = r.frontier_id
                        WHERE f.source_id = 'dd-557'
                        ORDER BY d.row_index
                        """
                    ).fetchall()
                    assert len(rows) == 1
                    projected = project_canonical(rows[0])
                    assert (
                        projected["title"]
                        == "Public Law 118–7: An Act to modify the authority of the Secretary of Veterans Affairs"
                    )
                    assert projected["doc_number"] == "Public Law 118–7"
                    assert projected["published_at"] == "2023-06-30"
                    assert (
                        projected["detail_url"]
                        == f"http://127.0.0.1:{port}/content/pkg/PLAW-118publ7/uslm/PLAW-118publ7.xml"
                    )
                    md_text = Objects().get(rows[0]["markdown_hash"]).decode("utf-8")
                    assert "SECTION 1." in md_text
                    assert "Veterans Care Act of 2023" in md_text

            rp_run = prepare_run(source_yaml, "reparse")
            rp_res = execute_run(rp_run)
            assert rp_res["status"] == "complete", f"dd-557 reparse failed: {rp_res}"
            assert rp_res["report"]["external_requests"] == 0
        finally:
            srv.shutdown()
            thread.join(timeout=2.0)
            srv.server_close()
