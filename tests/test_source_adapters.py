"""Black-box contract tests for structured legal XML and open-source bridge adapters."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest

from .conftest import make_native_pdf


class _BridgeFixtureHandler(BaseHTTPRequestHandler):
    sample_pdf: bytes = b""

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/xml/akn-act-2026.xml":
            body = """<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act name="publicGeneralAct">
    <meta>
      <identification source="#legislature">
        <FRBRWork>
          <FRBRthis value="/akn/uk/act/2026/15/!main"/>
          <FRBRuri value="/akn/uk/act/2026/15"/>
          <FRBRdate date="2026-03-15" name="enactment"/>
          <FRBRauthor href="#parliament"/>
          <FRBRnumber value="2026 c. 15"/>
        </FRBRWork>
      </identification>
    </meta>
    <preface>
      <longTitle><p><docTitle>Digital Legal Accountability Act 2026</docTitle></p></longTitle>
      <p><docNumber>2026 c. 15</docNumber></p>
    </preface>
    <body>
      <section eId="sec_1">
        <num>1</num>
        <heading>Short title and commencement</heading>
        <content><p>This Act may be cited as the Digital Legal Accountability Act 2026.</p></content>
      </section>
    </body>
  </act>
</akomaNtoso>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/wwl/congbao-list.html":
            body = """<html><body>
            <div class="congbao-item">
              <a class="doc-link" href="/wwl/detail/cb-2026-01.html">Nghị định 55/2026/NĐ-CP về thương mại điện tử</a>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/wwl/detail/cb-2026-01.html":
            body = """<html><head><title>Nghị định 55/2026/NĐ-CP</title></head><body>
            <h1>Nghị định 55/2026/NĐ-CP về thương mại điện tử</h1>
            <span class="doc-number">55/2026/NĐ-CP</span>
            <span class="issuer">Chính phủ</span>
            <span class="published-at">2026-04-10</span>
            <div class="content"><p>Điều 1. Phạm vi điều chỉnh của Nghị định này.</p></div>
            <a href="/wwl/files/nd55.pdf">Toàn văn PDF</a>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/wwl/files/nd55.pdf", "/juriscraper/opinions/25-1001.pdf"):
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(_BridgeFixtureHandler.sample_pdf)))
            self.end_headers()
            self.wfile.write(_BridgeFixtureHandler.sample_pdf)
        elif self.path == "/juriscraper/ca1-opinions.json":
            payload = {
                "court_id": "ca1",
                "court_name": "United States Court of Appeals for the First Circuit",
                "cases": [
                    {
                        "case_name": "United States v. Acme Data Corp.",
                        "docket_number": "25-1001",
                        "date_filed": "2026-06-12",
                        "precedential_status": "Published",
                        "download_url": "/juriscraper/opinions/25-1001.pdf",
                        "detail_url": "/juriscraper/ca1-opinions.json",
                    }
                ],
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/oldp/bgh-cases.html":
            body = """<html><body>
            <article class="case-item" data-ecli="ECLI:DE:BGH:2026:150426UVIII_ZR_12_25.0">
              <h2 class="title">BGH Urteil vom 15.04.2026 - VIII ZR 12/25</h2>
              <span class="file-number">VIII ZR 12/25</span>
              <span class="court">Bundesgerichtshof (BGH)</span>
              <span class="date">2026-04-15</span>
              <div class="tenor"><p>Die Revision der Beklagten wird zurückgewiesen.</p></div>
            </article>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/opencaselaw/ch-bger.html":
            body = """<html><body>
            <div class="decision" data-id="BGE-152-III-101">
              <h1 class="title">Urteil 4A_101/2026 vom 20. Mai 2026 (BGE 152 III 101)</h1>
              <span class="docket">4A_101/2026</span>
              <span class="court">Schweizerisches Bundesgericht</span>
              <span class="date">2026-05-20</span>
              <div class="body"><p>Das Bundesgericht erkennt: Die Beschwerde wird abgewiesen.</p></div>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/openstates/bills.json":
            payload = {
                "jurisdiction": "United States Congress",
                "bills": [
                    {
                        "identifier": "HR 4200",
                        "title": "Open Federal Legal Materials Act of 2026",
                        "classification": ["bill"],
                        "from_organization": "U.S. House of Representatives",
                        "latest_action_date": "2026-02-20",
                        "status": "Passed House",
                        "url": "/openstates/bills.json",
                        "abstract": "A bill to require machine-readable publication of federal legal materials.",
                    }
                ],
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


class TestStructuredXmlAndBridgeAdapters:
    @pytest.mark.parametrize(
        ("source_id", "adapter_name", "entry_path", "expected_doc_type", "expected_substr"),
        [
            ("dd-910", "structured_xml", "/xml/akn-act-2026.xml", "法律法规", "2026 c. 15"),
            ("dd-911", "worldwidelaw_bridge", "/wwl/congbao-list.html", "法律法规", "55/2026/NĐ-CP"),
            ("dd-912", "juriscraper_bridge", "/juriscraper/ca1-opinions.json", "案例", "25-1001"),
            ("dd-913", "oldp_bridge", "/oldp/bgh-cases.html", "案例", "VIII ZR 12/25"),
            ("dd-914", "opencaselaw_bridge", "/opencaselaw/ch-bger.html", "案例", "4A_101/2026"),
            ("dd-915", "openstates_bridge", "/openstates/bills.json", "法律法规", "HR 4200"),
        ],
    )
    def test_collect_and_offline_reparse_projects_expected_document_rows(
        self,
        initialized_db: dict[str, str],
        tmp_path: Path,
        source_id: str,
        adapter_name: str,
        entry_path: str,
        expected_doc_type: str,
        expected_substr: str,
    ) -> None:
        """Each structured XML and bridge adapter collects into `wlh.document_row` and replays offline without network."""
        _BridgeFixtureHandler.sample_pdf = make_native_pdf(
            tmp_path / "sample.pdf",
            ["Official Legal Document Full Text Body Content for Verification."],
        ).read_bytes()

        server = ThreadingHTTPServer(("127.0.0.1", 0), _BridgeFixtureHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            prefix = "/" + entry_path.strip("/").split("/")[0] + "/"
            source_yaml = tmp_path / f"{source_id}.yaml"
            source_yaml.write_text(
                f"""
id: {source_id}
adapter: {adapter_name}
archive_approved: true
scope: "Adapter contract test for {adapter_name}"
entry_urls:
  - "http://127.0.0.1:{port}{entry_path}"
allowed_hosts:
  - "127.0.0.1"
allowed_path_prefixes:
  - "{prefix}"
metadata:
  source_name: "Test {adapter_name}"
  document_type: "{expected_doc_type}"
  language: "en"
  jurisdiction: "INTL"
""".strip(),
                encoding="utf-8",
            )

            from wlh.engine.runs import execute_run, prepare_run
            from wlh.projections.canonical import project_canonical

            c_run = prepare_run(source_yaml, "collect")
            c_res = execute_run(c_run)
            assert c_res["status"] == "complete", f"{adapter_name} collect failed: {c_res}"

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
                    assert len(rows) >= 1, f"Expected document_row for {adapter_name}"
                    projected = project_canonical(rows[0])
                    assert projected["doc_type"] == expected_doc_type
                    assert (
                        expected_substr in (projected["doc_number"] or "")
                        or expected_substr in (projected["title"] or "")
                    ), f"Expected {expected_substr} in {projected}"

            server.shutdown()
            rp_run = prepare_run(source_yaml, "reparse")
            rp_res = execute_run(rp_run)
            assert rp_res["status"] == "complete", f"{adapter_name} reparse failed: {rp_res}"
        finally:
            server.shutdown()
            server.server_close()
