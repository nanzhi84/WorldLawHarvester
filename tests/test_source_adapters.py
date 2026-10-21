"""Black-box contract tests for structured legal XML and open-source bridge adapters."""

from __future__ import annotations

import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest
import yaml

from .conftest import make_native_pdf


def _make_rii_zip_bytes() -> bytes:
    buf = io.BytesIO()
    xml_payload = """<?xml version="1.0" encoding="UTF-8"?>
<dokument>
  <doknr>JURE100055033</doknr>
  <ecli>ECLI:DE:BGH:2026:150426UVIIIZR12.25.0</ecli>
  <gertyp>BGH</gertyp>
  <entsch-datum>20260415</entsch-datum>
  <aktenzeichen>VIII ZR 12/25</aktenzeichen>
  <titelzeile>Kaufrechtliche Gewährleistung bei digitalen Produkten</titelzeile>
  <tenor><p>Die Revision der Beklagten gegen das Urteil des Oberlandesgerichts wird zurückgewiesen.</p></tenor>
  <entscheidungsgruende><p>Die zulässige Revision hat in der Sache keinen Erfolg. Der Kläger hat Anspruch auf Schadensersatz.</p></entscheidungsgruende>
</dokument>""".encode("utf-8")
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("JURE100055033.xml", xml_payload)
    return buf.getvalue()


class _BridgeFixtureHandler(BaseHTTPRequestHandler):
    sample_pdf: bytes = b""
    rii_zip: bytes = _make_rii_zip_bytes()

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
        elif self.path in (
            "/wwl/files/nd55.pdf",
            "/juriscraper/opinions/25-1001.pdf",
            "/cpp/files/vbpq/2026/04/105-nd.signed.pdf",
            "/content/pkg/FR-2026-04-20/pdf/2026-08801.pdf",
            "/Biennium/2025-26/Pdf/Bills/House%20Bills/1091-S.E.pdf",
            "/pdf/2026/07/01/us_v_nemc.pdf",
        ):
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
        elif self.path == "/shenpan/gengduo/77.html":
            body = """<html><head><meta charset="utf-8"><title>指导案例列表</title></head><body>
            <div class="list">
              <a href="/shenpan/xiangqing/202601.html">指导性案例220号：甲公司诉乙公司数据权益纠纷案</a>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/shenpan/gengduo/empty.html":
            body = """<html><head><meta charset="utf-8"><title>改版后的空列表</title></head><body>
            <div class="new-layout-without-detail-links"><p>暂无匹配链接</p></div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/shenpan/xiangqing/202601.html":
            body = """<html><head><meta charset="utf-8"><title>指导性案例220号</title></head><body>
            <div class="detail">
              <div class="title">指导性案例220号：甲公司诉乙公司数据权益纠纷案</div>
              <ul><li>发布时间：2026-03-15</li></ul>
              <div class="txt_txt">
                <p>关键词：民事/数据权益/不正当竞争/裁判要点。人民法院审理涉数据权益民事纠纷案件，应当依法保护企业合法收集与加工的数据集合权益。</p>
              </div>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/":
            body = """<html><head><meta charset="utf-8"></head><body>
            <form method="post" action="/">
              <input type="hidden" name="__VIEWSTATE" value="vs1" />
              <table id="ctrl_grvDocument">
                <tr><td><a href="/page/pagenew.aspx?docid=210001">Nghị định 105/2026/NĐ-CP</a></td></tr>
                <tr><td><span>1</span></td></tr>
              </table>
            </form>
            <div class="congbao-home">
              <a href="/van-ban/quyet-dinh-48-2026-qd-ttg.htm">Quyết định 48/2026/QĐ-TTg của Thủ tướng Chính phủ</a>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/van-ban/quyet-dinh-48-2026-qd-ttg.htm":
            body = """<html><head><meta charset="utf-8"><title>Quyết định 48/2026/QĐ-TTg</title></head><body>
            <h1>Quyết định 48/2026/QĐ-TTg của Thủ tướng Chính phủ về chuyển đổi số</h1>
            <div class="row"><div class="name">Số, ký hiệu:</div><div class="value">48/2026/QĐ-TTg</div></div>
            <div class="row"><div class="name">Cơ quan ban hành:</div><div class="value">Thủ tướng Chính phủ</div></div>
            <div class="row"><div class="name">Ngày ban hành:</div><div class="value">12-04-2026</div></div>
            <a href="/cpp/files/vbpq/2026/04/105-nd.signed.pdf">48-qd.pdf</a>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/page/pagenew.aspx?docid=210001":
            body = """<html><head><meta charset="utf-8"><title>Nghị định 105/2026/NĐ-CP</title></head><body>
            <h4 class="title">Nghị định 105/2026/NĐ-CP quy định về giao dịch điện tử</h4>
            <table>
              <tr><td>Số ký hiệu</td><td>105/2026/NĐ-CP</td></tr>
              <tr><td>Ngày ban hành</td><td>10-04-2026</td></tr>
              <tr><td>Cơ quan ban hành</td><td>Chính phủ</td></tr>
              <tr><td>Loại văn bản</td><td>Nghị định</td></tr>
              <tr><td>Trích yếu</td><td>Nghị định quy định về giao dịch điện tử</td></tr>
            </table>
            <a href="/cpp/files/vbpq/2026/04/105-nd.signed.pdf">105-nd.signed.pdf</a>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/v1/documents.json"):
            host = self.headers.get("Host", "127.0.0.1")
            payload = {
                "count": 1,
                "results": [
                    {
                        "document_number": "2026-08801",
                        "title": "Cybersecurity Risk Management Rule for Critical Infrastructure",
                        "type": "Rule",
                        "action": "Final rule.",
                        "agencies": [{"name": "Department of Homeland Security"}],
                        "publication_date": "2026-04-20",
                        "effective_on": "2026-06-01",
                        "citation": "91 FR 24001",
                        "html_url": f"http://{host}/documents/2026/04/20/2026-08801/cybersecurity-rule",
                        "full_text_xml_url": f"http://{host}/documents/full_text/xml/2026/04/20/2026-08801.xml",
                        "pdf_url": f"http://{host}/content/pkg/FR-2026-04-20/pdf/2026-08801.pdf",
                    }
                ],
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/documents/full_text/xml/2026/04/20/2026-08801.xml":
            body = """<?xml version="1.0" encoding="UTF-8"?>
<RULE>
  <PREAMB>
    <SUBJECT>Cybersecurity Risk Management Rule for Critical Infrastructure</SUBJECT>
    <HD SOURCE="HD1">Background and Regulatory Authority</HD>
    <P>This final rule establishes mandatory cybersecurity incident reporting and risk management standards for covered entities.</P>
  </PREAMB>
</RULE>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/jportal/docs/bsjrs/jb-JURE100055033.zip":
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(_BridgeFixtureHandler.rii_zip)))
            self.end_headers()
            self.wfile.write(_BridgeFixtureHandler.rii_zip)
        elif self.path.startswith("/api/decisions"):
            host = self.headers.get("Host", "127.0.0.1")
            payload = {
                "decisions": [
                    {
                        "decision_id": "bger_2026_4A_101_2026",
                        "docket_number": "4A_101/2026",
                        "title": "Urteil 4A_101/2026 vom 20. Mai 2026",
                        "court_name": "Schweizerisches Bundesgericht",
                        "decision_date": "2026-05-20",
                        "canonical_url": f"http://{host}/entscheid/4A_101_2026",
                        "full_text": "Das Schweizerische Bundesgericht hat in der Zivilsache 4A_101/2026 entschieden: Die Beschwerde wird abgewiesen.",
                    }
                ]
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/bills?jurisdiction=wa"):
            host = self.headers.get("Host", "127.0.0.1")
            payload = {
                "results": [
                    {
                        "id": "ocd-bill/wa-hb-1091",
                        "identifier": "HB 1091",
                        "title": "An Act Relating to Washington State Digital Legal Archives",
                        "from_organization": {"name": "Washington House of Representatives"},
                        "latest_action_date": "2026-10-10",
                        "latest_action_description": "Passed Legislature",
                        "openstates_url": f"http://{host}/bills/wa/hb1091",
                        "abstracts": [
                            {
                                "abstract": "Establishes digital preservation standards for state legislative records."
                            }
                        ],
                        "versions": [
                            {
                                "note": "Engrossed Substitute Bill",
                                "links": [
                                    {
                                        "media_type": "application/pdf",
                                        "url": f"http://{host}/Biennium/2025-26/Pdf/Bills/House%20Bills/1091-S.E.pdf",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/rest/v4/search/"):
            host = self.headers.get("Host", "127.0.0.1")
            payload = {
                "results": [
                    {
                        "cluster_id": 9988776,
                        "caseName": "United States v. New England Maritime Corp.",
                        "docketNumber": "25-1402",
                        "court": "United States Court of Appeals for the First Circuit",
                        "dateFiled": "2026-07-01",
                        "status": "Published",
                        "absolute_url": f"http://{host}/opinion/9988776/united-states-v-new-england-maritime/",
                        "opinions": [
                            {
                                "download_url": f"http://{host}/pdf/2026/07/01/us_v_nemc.pdf",
                            }
                        ],
                    }
                ]
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
        if not _BridgeFixtureHandler.sample_pdf:
            _BridgeFixtureHandler.sample_pdf = make_native_pdf(
                tmp_path / "sample.pdf",
                ["Official Legal Document Full Text Body Content for Verification."],
            ).read_bytes()

        server = ThreadingHTTPServer(("127.0.0.1", 0), _BridgeFixtureHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stopped = False

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
            thread.join(timeout=2.0)
            stopped = True
            rp_run = prepare_run(source_yaml, "reparse")
            rp_res = execute_run(rp_run)
            assert rp_res["status"] == "complete", f"{adapter_name} reparse failed: {rp_res}"
        finally:
            if not stopped:
                server.shutdown()
                thread.join(timeout=2.0)
            server.server_close()


class TestAdaptedSourceConfigReplayAndZeroTargetGuard:
    @pytest.mark.parametrize(
        ("source_id", "expected_doc_type", "expected_substr"),
        [
            ("dd-017", "案例", "指导性案例220号"),
            ("dd-492", "法律法规", "105/2026/NĐ-CP"),
            ("dd-493", "法律法规", "48/2026/QĐ-TTg"),
            ("dd-499", "法律法规", "2026-08801"),
            ("dd-551", "案例", "VIII ZR 12/25"),
            ("dd-556", "案例", "4A_101/2026"),
            ("dd-560", "法律法规", "HB 1091"),
            ("dd-561", "案例", "25-1402"),
        ],
    )
    def test_adapted_production_source_configs_collect_and_reparse_against_local_samples(
        self,
        initialized_db: dict[str, str],
        tmp_path: Path,
        source_id: str,
        expected_doc_type: str,
        expected_substr: str,
    ) -> None:
        """Production `sources/dd-*.yaml` extraction rules collect and offline-reparse cleanly against local fixtures."""
        from urllib.parse import urlsplit, urlunsplit

        if not _BridgeFixtureHandler.sample_pdf:
            _BridgeFixtureHandler.sample_pdf = make_native_pdf(
                tmp_path / "sample.pdf",
                ["Official Legal Document Full Text Body Content for Verification."],
            ).read_bytes()

        server = ThreadingHTTPServer(("127.0.0.1", 0), _BridgeFixtureHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stopped = False

        try:
            repo_root = Path(__file__).resolve().parents[1]
            raw_cfg = yaml.safe_load(
                (repo_root / "sources" / f"{source_id}.yaml").read_text(encoding="utf-8")
            )
            rewritten_urls = []
            for entry_url in raw_cfg["entry_urls"]:
                parts = urlsplit(entry_url)
                rewritten_urls.append(
                    urlunsplit(("http", f"127.0.0.1:{port}", parts.path, parts.query, ""))
                )
            raw_cfg["entry_urls"] = rewritten_urls
            raw_cfg["allowed_hosts"] = ["127.0.0.1"]
            raw_cfg["allowed_path_prefixes"] = ["/"]
            raw_cfg.pop("host_path_scopes", None)
            raw_cfg["delay"] = 0.0
            if "documents" in raw_cfg and "detail_url_patterns" in raw_cfg["documents"]:
                raw_cfg["documents"]["detail_url_patterns"] = [
                    pat.replace(r"^https://www\.court\.gov\.cn", rf"^http://127\.0\.0\.1:{port}")
                    for pat in raw_cfg["documents"]["detail_url_patterns"]
                ]

            replay_yaml = tmp_path / f"{source_id}-replay.yaml"
            replay_yaml.write_text(
                yaml.safe_dump(raw_cfg, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )

            from wlh.engine.runs import execute_run, prepare_run
            from wlh.projections.canonical import project_canonical

            c_run = prepare_run(replay_yaml, "collect")
            c_res = execute_run(c_run)
            assert c_res["status"] == "complete", f"{source_id} collect failed: {c_res}"

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
                    assert len(rows) >= 1, f"Expected document_row for {source_id}"
                    projected = project_canonical(rows[0])
                    assert projected["doc_type"] == expected_doc_type
                    assert (
                        expected_substr in (projected["doc_number"] or "")
                        or expected_substr in (projected["title"] or "")
                    ), f"Expected {expected_substr} in {projected}"

            server.shutdown()
            thread.join(timeout=2.0)
            stopped = True
            rp_run = prepare_run(replay_yaml, "reparse")
            rp_res = execute_run(rp_run)
            assert rp_res["status"] == "complete", f"{source_id} reparse failed: {rp_res}"
            assert rp_res["report"]["external_requests"] == 0
        finally:
            if not stopped:
                server.shutdown()
                thread.join(timeout=2.0)
            server.server_close()

    def test_collect_with_zero_discovered_targets_fails_with_explicit_error(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """When an entry page returns HTTP 200 OK but discovers 0 document targets, collect fails with `zero_targets_discovered` without polluting HTTP failure cases."""
        server = ThreadingHTTPServer(("127.0.0.1", 0), _BridgeFixtureHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            empty_yaml = tmp_path / "dd-998.yaml"
            empty_yaml.write_text(
                f"""
id: dd-998
adapter: web
archive_approved: true
scope: "Zero target foolproof guard contract test"
entry_urls:
  - "http://127.0.0.1:{port}/shenpan/gengduo/empty.html"
allowed_hosts:
  - "127.0.0.1"
allowed_path_prefixes:
  - "/shenpan/"
delay: 0.0
metadata:
  source_name: "Empty List Test Source"
  document_type: "案例"
  language: "zh"
  jurisdiction: "CN"
documents:
  detail_url_patterns:
    - "^http://127\\\\.0\\\\.0\\\\.1:{port}/shenpan/xiangqing/\\\\d+\\\\.html$"
  detail_body: always
  content_xpath: '//div[@class="txt_txt"]'
""".strip(),
                encoding="utf-8",
            )

            from wlh.engine.runs import execute_run, prepare_run

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
            server.shutdown()
            thread.join(timeout=2.0)
            server.server_close()
