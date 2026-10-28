"""Taiwan source date, document-link and transport delivery contracts."""

from __future__ import annotations

import io
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import psycopg

from wlh.fixtures import export_source_fixture

from .bridge_fixture_support import (
    bridge_fixture_servers as bridge_fixture_servers,
)
from .conftest import make_native_pdf


class TestStructuredXmlAndBridgeAdapters:
    def test_taiwan_minguo_dates_web_side_links_moj_laws_zip_budget_and_transport_tls_flags(
        self,
        initialized_db: dict[str, str],
        tmp_path: Path,
    ) -> None:
        """Taiwan Minguo dates normalize to ISO-8601, `Side_Links` sidebar PDFs and generic `'PDF'` attachment titles are handled cleanly, MOJ `<Laws>` bulk XML ZIP respects `budget.documents` and trims on fixture export, and `Transport` clears `VERIFY_X509_STRICT`."""
        import asyncio
        import importlib
        import ssl

        from wlh.adapters.web_fields import parse_date
        from wlh.config import load_file
        from wlh.engine.objects import Objects
        from wlh.engine.runs import execute_run, prepare_run
        from wlh.engine.transport import Budget, Recorder, Transport
        from wlh.projections.canonical import project_canonical

        # 1. Minguo (ROC) calendar date normalization
        assert parse_date("民國 115 年 10 月 15 日") == "2026-10-15"
        assert parse_date("中華民國115年6月4日") == "2026-06-04"
        assert parse_date("民國 32 年 02 月 17 日") == "1943-02-17"
        assert parse_date("115-06-05") == "2026-06-05"
        assert parse_date("115.10.25") == "2026-10-25"

        pdf_form_bytes = make_native_pdf(
            tmp_path / "application_form.pdf",
            ["Official Legal Document Full Text Body Content - Taiwan Application Form."],
        ).read_bytes()
        pdf_guide_bytes = make_native_pdf(
            tmp_path / "checklist_guide.pdf",
            ["Official Legal Document Full Text Body Content - Taiwan Checklist Guide."],
        ).read_bytes()
        pdf_sidebar_bytes = make_native_pdf(
            tmp_path / "sidebar_old.pdf",
            ["Obsolete Sidebar Link Document That Must Not Be Collected."],
        ).read_bytes()

        moj_xml_bytes = """<?xml version="1.0" encoding="UTF-8"?>
<Laws UpdateDate="2026/10/16">
  <Law>
    <LawLevel>憲法</LawLevel>
    <LawName>中華民國憲法</LawName>
    <LawURL>https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A0000001</LawURL>
    <LawCategory>憲法</LawCategory>
    <LawModifiedDate>19470101</LawModifiedDate>
    <LawEffectiveDate></LawEffectiveDate>
    <LawEffectiveNote></LawEffectiveNote>
    <LawAbandonNote></LawAbandonNote>
    <LawHasEngVersion>Y</LawHasEngVersion>
    <EngLawName>Constitution of the Republic of China (Taiwan)</EngLawName>
    <LawAttachements />
    <LawHistories>1.中華民國三十六年一月一日國民政府令公布
  中華民國三十六年十二月二十五日施行
  中華民國三十五年十二月二十五日國民大會通過</LawHistories>
    <LawForeword>中華民國國民大會受全體國民之付託，制定本憲法。</LawForeword>
    <LawArticles>
      <Article>
        <ArticleType>C</ArticleType>
        <ArticleNo></ArticleNo>
        <ArticleConctent>   第 一 章 總綱</ArticleConctent>
      </Article>
      <Article>
        <ArticleType>A</ArticleType>
        <ArticleNo>第 1 條</ArticleNo>
        <ArticleConctent>中華民國基於三民主義，為民有民治民享之民主共和國。</ArticleConctent>
      </Article>
    </LawArticles>
  </Law>
  <Law>
    <LawLevel>法律</LawLevel>
    <LawName>中央法規標準法</LawName>
    <LawURL>https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A0030055</LawURL>
    <LawCategory>行政＞院本部＞通用目</LawCategory>
    <LawModifiedDate>20040519</LawModifiedDate>
    <LawHistories>1.中華民國五十九年八月三十一日總統令制定公布</LawHistories>
    <LawForeword></LawForeword>
    <LawArticles>
      <Article>
        <ArticleType>A</ArticleType>
        <ArticleNo>第 1 條</ArticleNo>
        <ArticleConctent>中央法規之制定、施行、適用、修正及廢止，除憲法規定外，依本法之規定。</ArticleConctent>
      </Article>
      <Article>
        <ArticleType>A</ArticleType>
        <ArticleNo>第 26 條</ArticleNo>
        <ArticleConctent>本法自公布日施行。</ArticleConctent>
      </Article>
    </LawArticles>
  </Law>
  <Law>
    <LawLevel>法律</LawLevel>
    <LawName>行政程序法</LawName>
    <LawURL>https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A0030056</LawURL>
    <LawCategory>行政＞法務部＞法制目</LawCategory>
    <LawModifiedDate>20210120</LawModifiedDate>
    <LawHistories>1.中華民國八十八年二月三日總統令制定公布</LawHistories>
    <LawForeword></LawForeword>
    <LawArticles>
      <Article>
        <ArticleType>A</ArticleType>
        <ArticleNo>第 1 條</ArticleNo>
        <ArticleConctent>為使行政行為遵循公正、公開與民主之程序，確保依法行政之原則，特制定本法。</ArticleConctent>
      </Article>
    </LawArticles>
  </Law>
</Laws>""".encode("utf-8")
        moj_zip_buf = io.BytesIO()
        with zipfile.ZipFile(moj_zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("ChLaw.xml", moj_xml_bytes)
        moj_zip_bytes = moj_zip_buf.getvalue()

        port_holder: list[int] = []
        requested_paths: list[str] = []

        class _TaiwanFixtureHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                requested_paths.append(self.path)
                port = port_holder[0]
                if self.path == "/robots.txt":
                    body = b"User-agent: *\nAllow: /\n"
                    ctype = "text/plain"
                elif self.path == "/tw/index.html":
                    body = (
                        f"<html><body><ul>"
                        f'<li><a href="http://127.0.0.1:{port}/tw/detail-101.html">營業稅電子申報作業要點</a></li>'
                        f"</ul></body></html>"
                    ).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path == "/tw/detail-101.html":
                    body = (
                        "<html><body>"
                        '<h1 class="doc-title">營業稅電子申報作業要點</h1>'
                        '<span class="doc-no">台財稅字第11504567890號</span>'
                        '<span class="pub-date">民國 115 年 10 月 15 日</span>'
                        '<div class="law-body">'
                        "<p>第一點 為簡化營業人使用網際網路申報營業稅作業，特訂定本要點。</p>"
                        '<p>相關附件：<a href="/tw/files/application_form.pdf">PDF</a> '
                        '<a href="/tw/files/checklist_guide.pdf">PDF</a></p>'
                        "</div>"
                        '<div class="Side_Links">'
                        '<a href="/tw/files/sidebar_old.pdf">PDF</a>'
                        "</div>"
                        "</body></html>"
                    ).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path == "/tw/files/application_form.pdf":
                    body = pdf_form_bytes
                    ctype = "application/pdf"
                elif self.path == "/tw/files/checklist_guide.pdf":
                    body = pdf_guide_bytes
                    ctype = "application/pdf"
                elif self.path == "/tw/files/sidebar_old.pdf":
                    body = pdf_sidebar_bytes
                    ctype = "application/pdf"
                elif self.path == "/moj/index.html":
                    body = (
                        f'<html><body><a href="http://127.0.0.1:{port}/moj/ChLaw.zip">ChLaw.zip</a></body></html>'
                    ).encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path == "/moj/ChLaw.zip":
                    body = moj_zip_bytes
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

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _TaiwanFixtureHandler)
        port = srv.server_address[1]
        port_holder.append(port)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()

        try:
            # 1b. Web adapter detail page with Minguo date, Side_Links sidebar, and generic "PDF" anchor labels
            web_yaml = tmp_path / "dd-958.yaml"
            web_yaml.write_text(
                f"""
id: dd-958
adapter: web
archive_approved: true
scope: "Taiwan web detail Minguo date and Side_Links contract test"
entry_urls:
  - "http://127.0.0.1:{port}/tw/index.html"
allowed_hosts:
  - "127.0.0.1"
allowed_path_prefixes:
  - "/tw/"
delay: 0.0
metadata:
  source_name: "財政部賦稅署"
  document_type: "税务资料"
  language: "zh"
  jurisdiction: "TW"
documents:
  detail_url_patterns:
    - "^http://127\\\\.0\\\\.0\\\\.1:{port}/tw/detail-\\\\d+\\\\.html$"
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
            assert w_res["status"] == "complete", f"dd-958 collect failed: {w_res}"
            assert "/tw/files/sidebar_old.pdf" not in requested_paths
            assert "/tw/files/application_form.pdf" in requested_paths
            assert "/tw/files/checklist_guide.pdf" in requested_paths

            with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as conn:
                from psycopg.rows import dict_row

                with conn.cursor(row_factory=dict_row) as cur:
                    w_rows = cur.execute(
                        """
                        SELECT f.source_id, r.id AS resource_id, d.*
                        FROM wlh.document_row d
                        JOIN wlh.version v ON v.id = d.version_id
                        JOIN wlh.resource r ON r.id = v.resource_id
                        JOIN wlh.frontier f ON f.id = r.frontier_id
                        WHERE f.source_id = 'dd-958'
                        ORDER BY d.row_index
                        """
                    ).fetchall()
                    assert len(w_rows) == 3
                    w_primary = project_canonical(w_rows[0])
                    assert w_primary["published_at"] == "2026-10-15"
                    att_titles = [r["title"] for r in w_rows[1:]]
                    assert "PDF" not in att_titles
                    assert att_titles == [
                        "營業稅電子申報作業要點 - application_form",
                        "營業稅電子申報作業要點 - checklist_guide",
                    ]

            # 2. Taiwan MOJ <Laws> bulk XML ZIP extraction, budget truncation, and fixture export trimming
            moj_yaml = tmp_path / "dd-959.yaml"
            moj_yaml.write_text(
                f"""
id: dd-959
adapter: structured_xml
archive_approved: true
scope: "Taiwan MOJ Laws bulk XML ZIP and budget truncation contract test"
entry_urls:
  - "http://127.0.0.1:{port}/moj/index.html"
allowed_hosts:
  - "127.0.0.1"
allowed_path_prefixes:
  - "/moj/"
delay: 0.0
budget:
  requests: 20
  documents: 2
  seconds: 60
  response_bytes: 16777216
documents:
  detail_url_patterns:
    - "^http://127\\\\.0\\\\.0\\\\.1:{port}/moj/ChLaw\\\\.zip$"
metadata:
  source_name: "全國法規資料庫"
  document_type: "法律法规"
  language: "zh"
  jurisdiction: "TW"
""".strip(),
                encoding="utf-8",
            )

            m_run = prepare_run(moj_yaml, "collect")
            m_res = execute_run(m_run)
            assert m_res["status"] == "complete", f"dd-959 collect failed: {m_res}"
            assert m_res["report"]["truncated"] == "document_budget_exceeded"
            assert "document_budget_exceeded" in m_res["report"]["coverage"]["known_gaps"]

            objs = Objects()
            with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as conn:
                from psycopg.rows import dict_row

                with conn.cursor(row_factory=dict_row) as cur:
                    moj_rows = cur.execute(
                        """
                        SELECT f.source_id, r.id AS resource_id, d.*
                        FROM wlh.document_row d
                        JOIN wlh.version v ON v.id = d.version_id
                        JOIN wlh.resource r ON r.id = v.resource_id
                        JOIN wlh.frontier f ON f.id = r.frontier_id
                        WHERE f.source_id = 'dd-959'
                        ORDER BY r.id, d.row_index
                        """
                    ).fetchall()
                    assert len(moj_rows) == 2
                    by_title = {r["title"]: r for r in moj_rows}
                    assert "中華民國憲法" in by_title
                    const_row = by_title["中華民國憲法"]
                    const_proj = project_canonical(const_row)
                    assert const_proj["doc_number"] == "A0000001"
                    assert const_proj["published_at"] == "1947-01-01"
                    assert const_proj["effective_at"] == "1947-12-25"
                    assert const_proj["status"] == "現行有效"
                    assert const_proj["issuer"] == "憲法"
                    const_md = objs.get(const_row["markdown_hash"]).decode("utf-8")
                    assert "## 第 1 條" in const_md
                    assert "中華民國基於三民主義，為民有民治民享之民主共和國。" in const_md
                    assert "Constitution of the Republic of China (Taiwan)" not in const_md
                    assert "A0000001" not in const_md
                    assert "\n\nA\n\n" not in const_md
                    assert "中央法規標準法" in by_title
                    std_proj = project_canonical(by_title["中央法規標準法"])
                    assert std_proj["published_at"] == "2004-05-19"
                    assert std_proj["effective_at"] == "2004-05-19"
                    assert std_proj["status"] == "現行有效"
                    assert std_proj["issuer"] == "行政院"

            export_dir = tmp_path / "exported_moj" / "dd-959"
            export_source_fixture("dd-959", export_dir, max_resources=2, source_yaml=moj_yaml)
            exported_zips = list(export_dir.glob("*.zip"))
            assert len(exported_zips) == 1
            etree_mod: Any = importlib.import_module("lxml.etree")
            with zipfile.ZipFile(exported_zips[0]) as zf:
                xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
                assert len(xml_names) == 1
                exported_root = etree_mod.fromstring(zf.read(xml_names[0]))
                assert len(exported_root.xpath('.//*[local-name()="Law"]')) == 2

            # 3. Transport TLS VERIFY_X509_STRICT flag clearing
            raw_cfg: dict[str, Any] = load_file(moj_yaml).model_dump()

            async def _check_transport_ssl() -> Any:
                budget = Budget(raw_cfg, 0, None)
                recorder = Recorder(m_run, 1, "dd-959")
                transport = Transport(raw_cfg, budget, recorder)
                await transport.start()
                try:
                    assert transport.session is not None
                    return getattr(transport.session.connector, "_ssl", None)
                finally:
                    await transport.close()

            ssl_ctx = asyncio.run(_check_transport_ssl())
            assert isinstance(ssl_ctx, ssl.SSLContext)
            assert ssl_ctx.verify_mode == ssl.CERT_REQUIRED
            assert (ssl_ctx.verify_flags & ssl.VERIFY_X509_STRICT) == 0
        finally:
            srv.shutdown()
            thread.join(timeout=2.0)
            srv.server_close()
