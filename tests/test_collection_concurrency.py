"""Concurrent collection and complete multi-record archive delivery contracts."""

from __future__ import annotations

import io
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg

from wlh.domain.delivery_quality import (
    verify_source_delivery,
)

from .bridge_fixture_support import (
    bridge_fixture_servers as bridge_fixture_servers,
)


class TestHighConcurrencyKeepAliveAndBulkXmlDelivery:
    def test_concurrent_web_keepalive_and_bulk_xml_archive_deliver_all_records(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """High-concurrency `web` collection reuses HTTP/1.1 Keep-Alive connections across 12 detail pages, and `structured_xml` delivers and offline-reparses all 40 `<法規>` records from a bulk `.xml.zip` archive."""
        from wlh.engine.runs import execute_run, prepare_run

        laws_xml_items: list[str] = []
        for idx in range(1, 41):
            day = 1 + (idx % 28)
            laws_xml_items.append(
                f"""  <法規>
    <法規性質>法律</法規性質>
    <法規名稱>測試行政法規第{idx:02d}號法</法規名稱>
    <法規網址>https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A00100{idx:02d}</法規網址>
    <法規類別>行政＞院本部＞通用目</法規類別>
    <最新異動日期>202604{day:02d}</最新異動日期>
    <法規內容>
      <條文>
        <條號>第 1 條</條號>
        <條文內容>為健全測試行政法規第{idx:02d}號法之法制規範與公開程序，保障人民權益並增進行政效能，特制定本法。</條文內容>
      </條文>
      <條文>
        <條號>第 2 條</條號>
        <條文內容>本法自公布日施行，主管機關應定期辦理法規執行成效檢討與公開申報作業（法規編號 A00100{idx:02d}）。</條文內容>
      </條文>
    </法規內容>
  </法規>"""
            )
        bulk_xml_bytes = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<Laws UpdateDate="2026/10/26">\n'
            + "\n".join(laws_xml_items)
            + "\n</Laws>"
        ).encode("utf-8")
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("ChLaw.xml", bulk_xml_bytes)
        bulk_zip_bytes = zip_buf.getvalue()

        port_holder: list[int] = []
        stats_lock = threading.Lock()
        distinct_client_connections: set[tuple[str, int]] = set()
        web_requests_served = 0

        class _KeepAliveServer(ThreadingHTTPServer):
            daemon_threads = True

        class _KeepAliveHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                nonlocal web_requests_served
                port = port_holder[0]
                if self.path == "/robots.txt":
                    body = b"User-agent: *\nAllow: /\n"
                    ctype = "text/plain; charset=utf-8"
                elif self.path == "/laws/index.html":
                    links = "\n".join(
                        f'<li><a href="http://127.0.0.1:{port}/laws/item-{i}.html">Law Item #{i:02d}</a></li>'
                        for i in range(1, 13)
                    )
                    body = f"<html><body><ul>{links}</ul></body></html>".encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path.startswith("/laws/item-") and self.path.endswith(".html"):
                    num_str = self.path.removeprefix("/laws/item-").removesuffix(".html")
                    idx = int(num_str)
                    day = 1 + (idx % 28)
                    body = (
                        "<html><body>"
                        f'<h1 class="doc-title">National Regulatory Act No. {idx:02d} of 2026</h1>'
                        f'<span class="doc-no">Act-2026-{idx:02d}</span>'
                        f'<span class="pub-date">2026-05-{day:02d}</span>'
                        '<div class="law-body">'
                        f"<p>Article 1. This Act establishes the statutory compliance framework and supervisory standards for regulatory domain #{idx:02d}.</p>"
                        f"<p>Article 2. All covered entities under regulatory domain #{idx:02d} shall submit annual compliance disclosures to the competent authority.</p>"
                        "</div>"
                        "</body></html>"
                    ).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path == "/moj/index.html":
                    body = (
                        f'<html><body><a href="http://127.0.0.1:{port}/moj/ChLaw.xml.zip">ChLaw.xml.zip</a></body></html>'
                    ).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path == "/moj/ChLaw.xml.zip":
                    body = bulk_zip_bytes
                    ctype = "application/zip"
                else:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                if self.path.startswith("/laws/") or self.path == "/robots.txt":
                    with stats_lock:
                        distinct_client_connections.add(
                            (self.client_address[0], self.client_address[1])
                        )
                        web_requests_served += 1

                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = _KeepAliveServer(("127.0.0.1", 0), _KeepAliveHandler)
        port = srv.server_address[1]
        port_holder.append(port)
        srv_thread = threading.Thread(target=srv.serve_forever, daemon=True)
        srv_thread.start()

        web_yaml = tmp_path / "dd-960.yaml"
        xml_yaml = tmp_path / "dd-961.yaml"
        try:
            # Part A: Web Keep-Alive & High Concurrency (12 detail pages)
            web_yaml.write_text(
                f"""
id: dd-960
adapter: web
archive_approved: true
scope: "High concurrency HTTP Keep-Alive web collection test"
concurrency: 12
delay: 0.01
budget:
  requests: 30
  documents: 12
  seconds: 60
  response_bytes: 10485760
entry_urls:
  - "http://127.0.0.1:{port}/laws/index.html"
allowed_hosts:
  - "127.0.0.1"
allowed_path_prefixes:
  - "/laws/"
metadata:
  source_name: "High Concurrency Legislative Portal"
  document_type: "法律法规"
  language: "en"
  jurisdiction: "INTL"
documents:
  detail_url_patterns:
    - "^http://127\\\\.0\\\\.0\\\\.1:{port}/laws/item-\\\\d+\\\\.html$"
  detail_body: always
  content_xpath: '//div[@class="law-body"]'
  fields:
    title:
      xpath: '//h1[@class="doc-title"]'
    document_number:
      xpath: '//span[@class="doc-no"]'
    published_at:
      xpath: '//span[@class="pub-date"]'
""".strip(),
                encoding="utf-8",
            )

            w_run = prepare_run(web_yaml, "collect")
            w_res = execute_run(w_run)
            assert w_res["status"] == "complete", f"dd-960 collect failed: {w_res}"
            assert w_res["report"]["counts"]["resources"] == 12
            assert (
                verify_source_delivery("dd-960", require_multi_resource=True)["resource_count"]
                == 12
            )

            with stats_lock:
                connections_opened = len(distinct_client_connections)
                requests_served = web_requests_served
            assert requests_served >= 13
            assert connections_opened < requests_served, (
                f"Expected HTTP Keep-Alive connection reuse (connections_opened={connections_opened} "
                f"< requests_served={requests_served})"
            )

            # Part B: Bulk XML Multi-Record Package (40 <法規> nodes under <Laws>)
            xml_yaml.write_text(
                f"""
id: dd-961
adapter: structured_xml
archive_approved: true
scope: "Taiwan LawBank 40-record bulk XML ZIP collection and offline reparse test"
concurrency: 12
delay: 0.0
budget:
  requests: 10
  documents: 40
  seconds: 60
  response_bytes: 10485760
entry_urls:
  - "http://127.0.0.1:{port}/moj/index.html"
allowed_hosts:
  - "127.0.0.1"
allowed_path_prefixes:
  - "/moj/"
documents:
  detail_url_patterns:
    - "^http://127\\\\.0\\\\.0\\\\.1:{port}/moj/ChLaw\\\\.xml\\\\.zip$"
metadata:
  source_name: "全國法規資料庫"
  document_type: "法律法规"
  language: "zh"
  jurisdiction: "TW"
""".strip(),
                encoding="utf-8",
            )

            x_run = prepare_run(xml_yaml, "collect")
            x_res = execute_run(x_run)
            assert x_res["status"] == "complete", f"dd-961 collect failed: {x_res}"
            assert x_res["report"]["counts"]["resources"] == 40
            assert (
                verify_source_delivery("dd-961", require_multi_resource=True)["resource_count"]
                == 40
            )
        finally:
            srv.shutdown()
            srv_thread.join(timeout=2.0)
            srv.server_close()

        # Offline reparse after shutting down the HTTP server
        rp_run = prepare_run(xml_yaml, "reparse")
        rp_res = execute_run(rp_run)
        assert rp_res["status"] == "complete", f"dd-961 reparse failed: {rp_res}"
        assert rp_res["report"]["counts"]["resources"] == 40
        assert rp_res["report"]["external_requests"] == 0

    def test_multi_xml_gazette_zip_archive_delivers_all_records_across_member_xmls(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """A `.zip` archive served from a query-string URL (`fileView.do?...&zipfile=094-01_01.zip`) containing multiple `<Gazette>` XML files in subdirectories extracts all `<Record>` items, normalizes ROC dates, strips legacy layout table wrappers without pipe noise (`\\|`), and passes delivery quality audit."""
        from psycopg.rows import dict_row

        from wlh.engine.objects import Objects
        from wlh.engine.runs import execute_run, prepare_run
        from wlh.projections.canonical import project_canonical

        xml_094 = """<?xml version="1.0" encoding="UTF-8"?>
<Gazette>
  <Record>
    <MetaId>533</MetaId>
    <Doc_Style_LName>法規</Doc_Style_LName>
    <PubGovName>大陸委員會</PubGovName>
    <PubGov>大陸委員會</PubGov>
    <Date_Created>中華民國94年1月3日</Date_Created>
    <Date_Published>中華民國94年1月3日</Date_Published>
    <GazetteId>陸人字第0930022638號</GazetteId>
    <Title>行政院大陸委員會令：修正「行政院大陸委員會顧問遴聘辦法」</Title>
    <HTMLContent><![CDATA[<td height="391"><table width="100%"><tr><td><h3>行政院大陸委員會令</h3></td><td>中華民國94年1月3日</td></tr><tr><td>第一條 本辦法依行政院大陸委員會組織條例第十九條規定訂定之。第二條 本會得視業務需要遴聘學者專家為本會顧問，任期一年，期滿得續聘之。</td></tr></table></td>]]></HTMLContent>
  </Record>
</Gazette>""".encode("utf-8")

        xml_115 = """<?xml version="1.0" encoding="UTF-8"?>
<Gazette>
  <Record>
    <MetaId>169040</MetaId>
    <Doc_Style_LName>行政規則</Doc_Style_LName>
    <PubGovName>教育部</PubGovName>
    <PubGov>教育部</PubGov>
    <Date_Created>中華民國115年10月22日</Date_Created>
    <Date_Published>中華民國115年10月22日</Date_Published>
    <GazetteId>臺教授國部字第1155703198A號</GazetteId>
    <Title>教育部令：修正「非正規教育課程認證作業要點」</Title>
    <HTMLContent><![CDATA[<div class="content"><p>修正「非正規教育課程認證作業要點」，自中華民國一百十六年一月一日生效。為建立終身學習體系並健全非正規教育課程認可及學分採認機制，特修正本要點。</p></div>]]></HTMLContent>
  </Record>
</Gazette>""".encode("utf-8")

        gazette_zip_buf = io.BytesIO()
        with zipfile.ZipFile(gazette_zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("094-01-03/094-01-03.xml", xml_094)
            zf.writestr("115-10-22/115-10-22.xml", xml_115)
        gazette_zip_bytes = gazette_zip_buf.getvalue()

        port_holder: list[int] = []

        class _GazetteHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                port = port_holder[0]
                if self.path == "/robots.txt":
                    body = b"User-agent: *\nAllow: /\n"
                    ctype = "text/plain; charset=utf-8"
                elif self.path == "/egFront/openData04.do?action=historyYearDaily&year=094":
                    body = (
                        "<html><body><ul>"
                        f'<li><a href="http://127.0.0.1:{port}/egFront/fileView.do?fileType=historyPath&amp;zipfile=094-01_01.zip">094-01_01.zip</a></li>'
                        "</ul></body></html>"
                    ).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path == "/egFront/fileView.do?fileType=historyPath&zipfile=094-01_01.zip":
                    body = gazette_zip_bytes
                    ctype = "application/zip"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _GazetteHandler)
        port = srv.server_address[1]
        port_holder.append(port)
        srv_thread = threading.Thread(target=srv.serve_forever, daemon=True)
        srv_thread.start()

        gazette_yaml = tmp_path / "dd-962.yaml"
        try:
            gazette_yaml.write_text(
                f"""
id: dd-962
adapter: structured_xml
archive_approved: true
scope: "Executive Yuan Gazette multi-XML ZIP archive extraction test"
delay: 0.0
budget:
  requests: 10
  documents: 10
  seconds: 60
  response_bytes: 10485760
entry_urls:
  - "http://127.0.0.1:{port}/egFront/openData04.do?action=historyYearDaily&year=094"
allowed_hosts:
  - "127.0.0.1"
allowed_path_prefixes:
  - "/egFront/"
documents:
  detail_url_patterns:
    - '^https?://[^/]+/egFront/fileView\\.do\\?.*zipfile='
metadata:
  source_name: "行政院公報資訊網"
  document_type: "通知公告"
  language: "zh"
  jurisdiction: "TW"
""".strip(),
                encoding="utf-8",
            )

            c_run = prepare_run(gazette_yaml, "collect")
            c_res = execute_run(c_run)
            assert c_res["status"] == "complete", f"dd-962 collect failed: {c_res}"
            assert c_res["report"]["counts"]["resources"] == 2
            verified = verify_source_delivery("dd-962", require_multi_resource=True)
            assert verified["passed"] is True
            assert verified["resource_count"] == 2

            objs = Objects()
            with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    rows = cur.execute(
                        """
                        SELECT f.source_id, r.id AS resource_id, d.*
                        FROM wlh.document_row d
                        JOIN wlh.version v ON v.id = d.version_id
                        JOIN wlh.resource r ON r.id = v.resource_id
                        JOIN wlh.frontier f ON f.id = r.frontier_id
                        WHERE f.source_id = 'dd-962'
                        ORDER BY r.id, d.row_index
                        """
                    ).fetchall()
                    assert len(rows) == 2
                    projected_rows = [project_canonical(r) for r in rows]
                    pub_dates = {str(p["published_at"]) for p in projected_rows}
                    assert pub_dates == {"2005-01-03", "2026-10-22"}
                    for r in rows:
                        md_text = objs.get(str(r["markdown_hash"])).decode("utf-8").strip()
                        assert len(md_text) > 0
                        assert "\\|" not in md_text
                    all_md = "\n".join(
                        objs.get(str(r["markdown_hash"])).decode("utf-8") for r in rows
                    )
                    assert "第一條 本辦法依行政院大陸委員會組織條例第十九條規定訂定之。" in all_md
                    assert "修正「非正規教育課程認證作業要點」" in all_md
        finally:
            srv.shutdown()
            srv_thread.join(timeout=2.0)
            srv.server_close()

    def test_warm_multi_worker_pdf_pool_delivers_batch_of_table_pdfs_in_parallel(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """A batch of 20 6-page ruled-table PDFs (120 pages total) is extracted and delivered in parallel via a warm multi-worker PDF pool during both `collect` and offline `reparse`."""
        import time
        from typing import Any

        import fitz  # type: ignore[import-untyped]

        from wlh.engine.runs import execute_run, prepare_run

        fitz_mod: Any = fitz
        pdf_payloads: dict[str, bytes] = {}
        for idx in range(1, 21):
            doc = fitz_mod.open()
            for page_no in range(1, 7):
                p = doc.new_page(width=595, height=842)
                p.insert_text(
                    (72, 100),
                    f"Act No. {idx:02d} of 2026 — Part {page_no}",
                    fontname="helv",
                    fontsize=12,
                )
                for x in (72.0, 200.0, 480.0):
                    p.draw_line(
                        fitz_mod.Point(x, 140), fitz_mod.Point(x, 230), color=(0, 0, 0), width=0.5
                    )
                for y in (140.0, 170.0, 200.0, 230.0):
                    p.draw_line(
                        fitz_mod.Point(72, y), fitz_mod.Point(480, y), color=(0, 0, 0), width=0.5
                    )
                p.insert_text((80, 160), "Article", fontname="helv", fontsize=10)
                p.insert_text((210, 160), "Provision", fontname="helv", fontsize=10)
                p.insert_text((80, 190), "1", fontname="helv", fontsize=10)
                p.insert_text((210, 190), "Compliance standard", fontname="helv", fontsize=10)
                p.insert_text((80, 220), "2", fontname="helv", fontsize=10)
                p.insert_text((210, 220), "Reporting duty", fontname="helv", fontsize=10)
                p.insert_textbox(
                    fitz_mod.Rect(72, 260, 520, 420),
                    f"Section {page_no} of Act No. {idx:02d} establishes mandatory regulatory "
                    f"supervision, periodic disclosure obligations, and statutory audit standards "
                    f"across all covered entities operating within jurisdiction #{idx:02d}.",
                    fontsize=11,
                    fontname="helv",
                )
            pdf_payloads[f"/pdfs/item-{idx:02d}.pdf"] = bytes(doc.tobytes())
            doc.close()

        port_holder: list[int] = []

        class _PdfPoolHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                port = port_holder[0]
                if self.path == "/robots.txt":
                    body = b"User-agent: *\nAllow: /\n"
                    ctype = "text/plain; charset=utf-8"
                elif self.path == "/pdfs/index.html":
                    links = "\n".join(
                        f'<li><a href="http://127.0.0.1:{port}/pdfs/item-{i:02d}.pdf">Act #{i:02d}</a></li>'
                        for i in range(1, 21)
                    )
                    body = f"<html><body><ul>{links}</ul></body></html>".encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path in pdf_payloads:
                    body = pdf_payloads[self.path]
                    ctype = "application/pdf"
                else:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _PdfPoolHandler)
        port = srv.server_address[1]
        port_holder.append(port)
        srv_thread = threading.Thread(target=srv.serve_forever, daemon=True)
        srv_thread.start()

        pdf_yaml = tmp_path / "dd-963.yaml"
        try:
            pdf_yaml.write_text(
                f"""
id: dd-963
adapter: web
archive_approved: true
scope: "Warm multi-worker PDF pool parallel extraction test (20 6-page ruled-table PDFs)"
concurrency: 16
delay: 0.0
budget:
  requests: 40
  documents: 20
  seconds: 60
  response_bytes: 20971520
entry_urls:
  - "http://127.0.0.1:{port}/pdfs/index.html"
allowed_hosts:
  - "127.0.0.1"
allowed_path_prefixes:
  - "/pdfs/"
metadata:
  source_name: "Parallel PDF Gazette Portal"
  document_type: "法律法规"
  language: "en"
  jurisdiction: "INTL"
documents:
  detail_url_patterns:
    - "^http://127\\\\.0\\\\.0\\\\.1:{port}/pdfs/item-\\\\d+\\\\.pdf$"
  require_complete_text: true
""".strip(),
                encoding="utf-8",
            )
            c_run = prepare_run(pdf_yaml, "collect")
            t0 = time.monotonic()
            res = execute_run(c_run)
            elapsed = time.monotonic() - t0
            assert res["status"] == "complete", f"dd-963 collect failed: {res}"
            assert res["report"]["counts"]["resources"] == 20
            assert elapsed < 2.8, f"Expected parallel PDF pool collect < 2.8s, took {elapsed:.2f}s"
        finally:
            srv.shutdown()
            srv_thread.join(timeout=2.0)
            srv.server_close()

        rp_run = prepare_run(pdf_yaml, "reparse")
        t1 = time.monotonic()
        rp_res = execute_run(rp_run)
        reparse_elapsed = time.monotonic() - t1
        assert rp_res["status"] == "complete", f"dd-963 reparse failed: {rp_res}"
        assert rp_res["report"]["external_requests"] == 0
        assert rp_res["report"]["counts"]["resources"] == 20
        assert reparse_elapsed < 2.5, (
            f"Expected parallel PDF pool reparse < 2.5s, took {reparse_elapsed:.2f}s"
        )

    def test_streaming_inflight_harvest_and_japan_tier3_source_configs_avoid_straggler_stall(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """Japan Tier 3 sources (`dd-517`, `dd-519`, `dd-582`) declare `concurrency: 24, delay: 0.0`, and `fetch_batch()` harvests completed responses in a streaming fashion without blocking behind a slow attachment straggler."""
        import time
        from typing import Any

        import fitz  # type: ignore[import-untyped]

        from wlh.config import load_file
        from wlh.engine.runs import execute_run, prepare_run

        repo_root = Path(__file__).resolve().parents[1]
        for source_id in ("dd-517", "dd-519", "dd-582"):
            yaml_path = repo_root / "sources" / f"{source_id}.yaml"
            if yaml_path.is_file():
                cfg = load_file(yaml_path)
                assert cfg.concurrency == 24, f"Expected {source_id} concurrency == 24, got {cfg.concurrency}"
                assert cfg.delay == 0.0, f"Expected {source_id} delay == 0.0, got {cfg.delay}"

        fitz_mod: Any = fitz
        pdf_payloads: dict[str, bytes] = {}
        for idx in range(1, 13):
            doc = fitz_mod.open()
            page = doc.new_page(width=595, height=842)
            page.insert_text(
                (72, 100),
                f"Annex for Document {idx} statutory text. Mandatory statutory compliance provisions for annex {idx}.",
                fontname="helv",
                fontsize=11,
            )
            pdf_payloads[f"/portal/att-{idx}.pdf"] = bytes(doc.tobytes())
            doc.close()

        port_holder: list[int] = []
        ts_lock = threading.Lock()
        doc7_requested_at: list[float] = []
        att1_finished_sleep_at: list[float] = []

        class _StreamingCatalogHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                port = port_holder[0]
                if self.path == "/robots.txt":
                    body = b"User-agent: *\nAllow: /\n"
                    ctype = "text/plain; charset=utf-8"
                elif self.path == "/portal/page1.html":
                    doc_links = "\n".join(
                        f'<li><a href="http://127.0.0.1:{port}/portal/doc-{i}.html">Document {i}</a></li>'
                        for i in range(1, 7)
                    )
                    body = (
                        "<html><body>"
                        f'<a href="http://127.0.0.1:{port}/portal/page2.html">Next Page</a>'
                        f"<ul>{doc_links}</ul>"
                        "</body></html>"
                    ).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path == "/portal/page2.html":
                    doc_links = "\n".join(
                        f'<li><a href="http://127.0.0.1:{port}/portal/doc-{i}.html">Document {i}</a></li>'
                        for i in range(7, 13)
                    )
                    body = f"<html><body><ul>{doc_links}</ul></body></html>".encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path.startswith("/portal/doc-") and self.path.endswith(".html"):
                    idx_str = self.path.removeprefix("/portal/doc-").removesuffix(".html")
                    idx = int(idx_str)
                    if idx == 7:
                        with ts_lock:
                            doc7_requested_at.append(time.monotonic())
                    body = (
                        "<html><body>"
                        f"<h1>Document {idx}</h1>"
                        f'<div class="law-body">Mandatory legal provision text for document {idx} with sufficient length for quality checks.</div>'
                        f'<a href="/portal/att-{idx}.pdf">Annex {idx}</a>'
                        "</body></html>"
                    ).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path in pdf_payloads:
                    if self.path == "/portal/att-1.pdf":
                        time.sleep(0.9)
                        with ts_lock:
                            att1_finished_sleep_at.append(time.monotonic())
                    body = pdf_payloads[self.path]
                    ctype = "application/pdf"
                else:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _StreamingCatalogHandler)
        port = srv.server_address[1]
        port_holder.append(port)
        srv_thread = threading.Thread(target=srv.serve_forever, daemon=True)
        srv_thread.start()

        portal_yaml = tmp_path / "dd-964.yaml"
        try:
            portal_yaml.write_text(
                f"""
id: dd-964
adapter: web
archive_approved: true
scope: "Streaming in-flight harvest test avoiding slow attachment straggler stall"
concurrency: 24
delay: 0.0
budget:
  requests: 50
  documents: 12
  seconds: 60
  response_bytes: 10485760
entry_urls:
  - "http://127.0.0.1:{port}/portal/page1.html"
allowed_hosts:
  - "127.0.0.1"
allowed_path_prefixes:
  - "/portal/"
metadata:
  source_name: "Streaming Portal"
  document_type: "法律法规"
  language: "en"
  jurisdiction: "INTL"
documents:
  detail_url_patterns:
    - "^http://127\\\\.0\\\\.0\\\\.1:{port}/portal/doc-\\\\d+\\\\.html$"
  detail_body: always
  content_xpath: '//div[@class="law-body"]'
  fields:
    title:
      xpath: '//h1'
""".strip(),
                encoding="utf-8",
            )

            run_id = prepare_run(portal_yaml, "collect")
            res = execute_run(run_id)
            assert res["status"] == "complete", f"dd-964 collect failed: {res}"
            assert res["report"]["counts"]["resources"] == 12
            assert doc7_requested_at, "Expected /portal/doc-7.html to be requested"
            assert att1_finished_sleep_at, "Expected /portal/att-1.pdf to be requested"
            assert doc7_requested_at[0] < att1_finished_sleep_at[0], (
                f"Expected /portal/doc-7.html (t={doc7_requested_at[0]:.3f}) to be requested before "
                f"/portal/att-1.pdf finished sleeping (t={att1_finished_sleep_at[0]:.3f})"
            )
        finally:
            srv.shutdown()
            srv_thread.join(timeout=2.0)
            srv.server_close()
