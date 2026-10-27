"""Black-box contract tests for structured legal XML and open-source bridge adapters."""

from __future__ import annotations

import io
import socket
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml

from wlh.core import WlhError
from wlh.domain.delivery_quality import (
    assert_delivery_quality,
    audit_delivery_rows,
    verify_source_delivery,
)
from wlh.fixtures import FixtureRouter, export_source_fixture, load_fixture_manifest

from .conftest import make_native_pdf, run_wlh_cli

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _make_rii_zip_bytes(
    doknr: str = "JURE100055033",
    aktenzeichen: str = "VIII ZR 12/25",
    ecli: str = "ECLI:DE:BGH:2026:150426UVIIIZR12.25.0",
    titelzeile: str = "Kaufrechtliche Gewährleistung bei digitalen Produkten",
) -> bytes:
    buf = io.BytesIO()
    xml_payload = f"""<?xml version="1.0" encoding="UTF-8"?>
<dokument>
  <doknr>{doknr}</doknr>
  <ecli>{ecli}</ecli>
  <gertyp>BGH</gertyp>
  <entsch-datum>20260415</entsch-datum>
  <aktenzeichen>{aktenzeichen}</aktenzeichen>
  <titelzeile>{titelzeile}</titelzeile>
  <tenor><p>Die Revision der Beklagten gegen das Urteil des Oberlandesgerichts wird zurückgewiesen.</p></tenor>
  <entscheidungsgruende><p>Die zulässige Revision hat in der Sache keinen Erfolg. Der Kläger hat Anspruch auf Schadensersatz.</p></entscheidungsgruende>
</dokument>""".encode("utf-8")
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{doknr}.xml", xml_payload)
    return buf.getvalue()


def _make_fixture_zip_bytes(arcname: str, fixture_rel: str) -> bytes:
    buf = io.BytesIO()
    xml_bytes = (FIXTURES_DIR / fixture_rel).read_bytes()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(arcname, xml_bytes)
    return buf.getvalue()


class _IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


class _BridgeFixtureHandler(BaseHTTPRequestHandler):
    sample_pdf: bytes = b""
    scotus_pdf_1: bytes = b""
    scotus_pdf_2: bytes = b""
    secondary_netloc: str = ""
    active_source_id: str = ""
    rii_zip: bytes = _make_rii_zip_bytes(
        "JURE100055033", "VIII ZR 12/25", "ECLI:DE:BGH:2026:150426UVIIIZR12.25.0"
    )
    rii_zip_2: bytes = _make_rii_zip_bytes(
        "JURE100055034",
        "VIII ZR 13/25",
        "ECLI:DE:BGH:2026:150426UVIIIZR13.25.0",
        titelzeile="Mietrechtliche Kündigung wegen Zahlungsverzugs",
    )
    gg_zip: bytes = _make_fixture_zip_bytes("BJNR000010949.xml", "dd-550/BJNR000010949.xml")
    bgb_zip: bytes = _make_fixture_zip_bytes("BJNR001950896.xml", "dd-550/BJNR001950896.xml")
    usc01_zip: bytes = _make_fixture_zip_bytes("usc01.xml", "dd-558/usc01.xml")
    usc02_zip: bytes = _make_fixture_zip_bytes("usc02.xml", "dd-558/usc02.xml")
    router: FixtureRouter | None = None

    def log_message(self, format: str, *args: object) -> None:
        return

    @classmethod
    def _get_router(cls) -> FixtureRouter:
        if cls.router is None:
            cls.router = FixtureRouter(
                FIXTURES_DIR,
                binary_providers={
                    "sample_pdf": cls.sample_pdf,
                    "scotus_pdf_1": cls.scotus_pdf_1,
                    "scotus_pdf_2": cls.scotus_pdf_2,
                    "rii_zip": cls.rii_zip,
                    "rii_zip_2": cls.rii_zip_2,
                    "gg_zip": cls.gg_zip,
                    "bgb_zip": cls.bgb_zip,
                    "usc01_zip": cls.usc01_zip,
                    "usc02_zip": cls.usc02_zip,
                },
            )
        return cls.router

    def do_GET(self) -> None:
        router = self._get_router()
        rendered = router.resolve_and_render(
            self.active_source_id,
            self.path,
            headers=dict(self.headers.items()),
            secondary_netloc=self.secondary_netloc,
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


@pytest.fixture(scope="module")
def bridge_fixture_servers(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[int, int]]:
    pdf_dir = tmp_path_factory.mktemp("bridge_pdfs")
    _BridgeFixtureHandler.sample_pdf = make_native_pdf(
        pdf_dir / "sample.pdf",
        ["Official Legal Document Full Text Body Content for Verification."],
    ).read_bytes()
    _BridgeFixtureHandler.scotus_pdf_1 = make_native_pdf(
        pdf_dir / "scotus1.pdf",
        [
            "SUPREME COURT OF THE UNITED STATES No. 26A388 PEOPLE NOT POLITICIANS, ET AL. v. ROBERT ONDER, ET AL. ON APPLICATION FOR STAY [September 25, 2026] PER CURIAM. The application for stay presented to the Court is denied."
        ],
    ).read_bytes()
    _BridgeFixtureHandler.scotus_pdf_2 = make_native_pdf(
        pdf_dir / "scotus2.pdf",
        [
            "SUPREME COURT OF THE UNITED STATES Syllabus WEST VIRGINIA ET AL. v. B. P. J., BY HER NEXT FRIEND AND MOTHER, HEATHER JACKSON CERTIORARI TO THE UNITED STATES COURT OF APPEALS FOR THE FOURTH CIRCUIT No. 24–43. Argued January 13, 2026—Decided June 30, 2026 Held: The judgment of the Court of Appeals is reversed."
        ],
    ).read_bytes()
    _BridgeFixtureHandler.router = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), _BridgeFixtureHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    server_v6 = _IPv6ThreadingHTTPServer(("::1", 0), _BridgeFixtureHandler)
    port_v6 = server_v6.server_address[1]
    thread_v6 = threading.Thread(target=server_v6.serve_forever, daemon=True)
    thread_v6.start()
    _BridgeFixtureHandler.secondary_netloc = f"[::1]:{port_v6}"

    try:
        yield port, port_v6
    finally:
        _BridgeFixtureHandler.secondary_netloc = ""
        _BridgeFixtureHandler.active_source_id = ""
        _BridgeFixtureHandler.router = None
        server.shutdown()
        server_v6.shutdown()
        thread.join(timeout=2.0)
        thread_v6.join(timeout=2.0)
        server.server_close()
        server_v6.server_close()


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
        bridge_fixture_servers: tuple[int, int],
        tmp_path: Path,
        source_id: str,
        adapter_name: str,
        entry_path: str,
        expected_doc_type: str,
        expected_substr: str,
    ) -> None:
        """Each structured XML and bridge adapter collects into `wlh.document_row` and replays offline without network."""
        port, _ = bridge_fixture_servers
        _BridgeFixtureHandler.active_source_id = source_id

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

            rp_run = prepare_run(source_yaml, "reparse")
            rp_res = execute_run(rp_run)
            assert rp_res["status"] == "complete", f"{adapter_name} reparse failed: {rp_res}"
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
adapter: structured_xml
archive_approved: true
scope: "USLM Public Law RSS discovery and XML parsing contract test"
entry_urls:
  - "http://127.0.0.1:{port}/rss/plaw.xml"
allowed_hosts:
  - "127.0.0.1"
allowed_path_prefixes:
  - "/rss/"
  - "/content/pkg/"
delay: 0.0
fetch:
  mode: http
  max_depth: 1
documents:
  detail_url_patterns:
    - "^http://127\\\\.0\\\\.0\\\\.1:{port}/content/pkg/PLAW-[^/]+/uslm/PLAW-[^/]+\\\\.xml$"
metadata:
  source_name: "GovInfo Public Laws"
  document_type: "法律法规"
  language: "en"
  jurisdiction: "US"
""".strip(),
                encoding="utf-8",
            )

            from wlh.engine.objects import Objects
            from wlh.engine.runs import execute_run, prepare_run
            from wlh.projections.canonical import project_canonical

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
                    assert std_proj["issuer"] == "行政＞院本部＞通用目"

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


class TestAdaptedSourceConfigReplayAndZeroTargetGuard:
    @pytest.mark.parametrize(
        ("source_id", "expected_doc_type", "expected_substr"),
        [
            ("dd-017", "案例", "指导性案例220号"),
            ("dd-040", "政策文件", "2026年第1号"),
            ("dd-041", "案例", "第六十批指导性案例"),
            ("dd-102", "通知公告", "京汇罚〔2026〕15号"),
            ("dd-183", "政策文件", "地方政府专项债券用途调整操作指引"),
            ("dd-375", "行业标准", "国统字〔2019〕66号"),
            ("dd-448", "通知公告", "影剧备字〔2026〕第1230号"),
            ("dd-455", "通知公告", "2026年进口网络游戏审批信息"),
            ("dd-492", "法律法规", "105/2026/NĐ-CP"),
            ("dd-493", "法律法规", "48/2026/QĐ-TTg"),
            ("dd-501", "政策文件", "国办发〔2026〕12号"),
            ("dd-502", "法律法规", "国令第779号"),
            ("dd-513", "法律法规", "37/CT-TTg"),
            ("dd-495", "法律法规", "145/2026/TT-BTC"),
            ("dd-496", "案例", "1463/2026/DS-PT"),
            ("dd-499", "法律法规", "2026-08801"),
            ("dd-520", "税务资料", "第1章　納税義務者"),
            ("dd-527", "税务资料", "Tax Treatment of Research & Development Expenditure"),
            ("dd-548", "案例", "[2024] UKSC 1"),
            ("dd-549", "税务资料", "ADML3100"),
            ("dd-550", "法律法规", "GG"),
            ("dd-551", "案例", "VIII ZR 12/25"),
            ("dd-556", "案例", "4A_101/2026"),
            ("dd-558", "法律法规", "1"),
            ("dd-559", "法律法规", "1 CFR Part 1"),
            ("dd-560", "法律法规", "HB 1091"),
            ("dd-561", "案例", "25-1402"),
            ("dd-562", "案例", "26A388"),
            ("dd-563", "税务资料", "2026-16"),
            ("dd-564", "法律法规", "A-1"),
            ("dd-565", "案例", "2022 SCC 18"),
            ("dd-570", "法律法规", "2024 No 1"),
            ("dd-572", "法律法规", "Act No. 718 of 1963"),
            ("dd-506", "案例", "HCAL 371/2021"),
            ("dd-507", "税务资料", "dipn03"),
            ("dd-512", "法律法规", "46/2026/QH16"),
            ("dd-514", "案例", "84/2026/AL"),
            ("dd-516", "税务资料", "Đề nghị hướng dẫn"),
            ("dd-517", "法律法规", "平成五年法律第八十八号"),
            ("dd-575", "法律法规", "Luật sửa đổi, bổ sung một số điều của 09 luật về quân sự, quốc phòng"),
            ("dd-525", "法律法规", "Accountants Act 2004"),
            ("dd-557", "法律法规", "Public Law 118–5"),
            ("dd-518", "政策文件", "第1815号"),
            ("dd-571", "税务资料", "2026-NZSC-121"),
            ("dd-142", "其他", "「2026 정부혁신 왕중왕전」온라인 정책소통 OPEN!"),
            (
                "dd-019",
                "通知公告",
                "最高人民法院发布《关于审理涉及刑事犯罪的民事纠纷案件若干问题的规定》",
            ),
            ("dd-039", "政策文件", "检察机关深入开展“益路长征·赓续守护”专项监督"),
            ("dd-482", "通知公告", "关于在政府采购活动中对有关美国企业采取相关措施的通知"),
            ("dd-423", "通知公告", "宋志勇会见空客首席执行官"),
            ("dd-385", "通知公告", "关于开展季度调查监测的通知"),
            (
                "dd-265",
                "税务资料",
                "中国注册税务师协会关于第七届理事会部分理事及常务理事调整的公告",
            ),
            (
                "dd-153",
                "其他",
                "关于征集2026年度互联网域名管理技术国家工程实验室开放课题指南建议的通知",
            ),
            ("dd-108", "通知公告", "2025信息通信行业信用建设研讨会成功召开"),
            ("dd-198", "通知公告", "黑龙江省黑土资源交易“表土剥离独立交易窗口”上线"),
            ("dd-209", "通知公告", "广州市番禺城市排水管理有限公司"),
            ("dd-320", "通知公告", "山西省自然资源厅"),
            (
                "dd-323",
                "通知公告",
                "吉林省桦甸市半截沟区金多金属矿勘查探矿权挂牌出让成交结果公示",
            ),
            ("dd-324", "通知公告", "李强主持召开国务院常务会议"),
            ("dd-327", "通知公告", "《浙江省自然资源厅关于建立健全国土空间详细规划体系的通知》政策解读"),
            ("dd-337", "通知公告", "2026年第6期自贸港机关讲坛开讲"),
            ("dd-132", "其他", "商標規費收費標準"),
            ("dd-162", "财务报告", "外籍員工持股信託"),
            ("dd-509", "法律法规", "中央法規標準法"),
            ("dd-511", "税务资料", "所得稅法"),
            ("dd-577", "政策文件", "學生輔導法"),
            ("dd-578", "法律法规", "中華民國憲法"),
            ("dd-579", "法律法规", "動員戡亂完成憲政實施綱要"),
        ],
    )
    def test_adapted_production_source_configs_collect_and_reparse_against_local_samples(
        self,
        initialized_db: dict[str, str],
        bridge_fixture_servers: tuple[int, int],
        tmp_path: Path,
        source_id: str,
        expected_doc_type: str,
        expected_substr: str,
    ) -> None:
        """Production `sources/dd-*.yaml` extraction rules collect and offline-reparse cleanly against local fixtures."""
        import re
        from urllib.parse import urlsplit, urlunsplit

        port, port_v6 = bridge_fixture_servers
        _BridgeFixtureHandler.active_source_id = source_id

        try:
            repo_root = Path(__file__).resolve().parents[1]
            raw_cfg: dict[str, Any] = yaml.safe_load(
                (repo_root / "sources" / f"{source_id}.yaml").read_text(encoding="utf-8")
            )
            orig_hosts = list(raw_cfg.get("allowed_hosts", []))
            primary_host = orig_hosts[0]
            secondary_hosts = orig_hosts[1:]

            rewritten_urls: list[str] = []
            entry_urls: list[str] = raw_cfg["entry_urls"]
            for entry_url in entry_urls:
                parts = urlsplit(entry_url)
                rewritten_urls.append(
                    urlunsplit(("http", f"127.0.0.1:{port}", parts.path, parts.query, ""))
                )
            raw_cfg["entry_urls"] = rewritten_urls
            raw_cfg["allowed_hosts"] = ["127.0.0.1", "::1"] if secondary_hosts else ["127.0.0.1"]
            if "host_path_scopes" in raw_cfg:
                orig_scopes: dict[str, list[str]] = raw_cfg["host_path_scopes"]
                new_scopes: dict[str, list[str]] = {}
                if primary_host in orig_scopes:
                    new_scopes["127.0.0.1"] = list(orig_scopes[primary_host])
                sec_scoped: list[str] = []
                for sec_host in secondary_hosts:
                    for p in orig_scopes.get(sec_host, []):
                        if p not in sec_scoped:
                            sec_scoped.append(p)
                if sec_scoped:
                    new_scopes["::1"] = sec_scoped
                raw_cfg["host_path_scopes"] = new_scopes
                allowed_prefixes: list[str] = list(raw_cfg.get("allowed_path_prefixes", []))
                for p_list in new_scopes.values():
                    for p in p_list:
                        if p not in allowed_prefixes:
                            allowed_prefixes.append(p)
                raw_cfg["allowed_path_prefixes"] = allowed_prefixes
            raw_cfg["delay"] = 0.0
            if "documents" in raw_cfg:

                def _rewrite_pat(pat: str) -> str:
                    out = pat
                    for sec_host in secondary_hosts:
                        out = out.replace(re.escape(sec_host), rf"\[::1\]:{port_v6}")
                        out = out.replace(sec_host.replace(".", r"\."), rf"\[::1\]:{port_v6}")
                    out = out.replace(re.escape(primary_host), rf"127\.0\.0\.1:{port}")
                    out = out.replace(primary_host.replace(".", r"\."), rf"127\.0\.0\.1:{port}")
                    out = re.sub(r"^\^https?://", "^http://", out)
                    return out

                if "detail_url_patterns" in raw_cfg["documents"]:
                    raw_cfg["documents"]["detail_url_patterns"] = [
                        _rewrite_pat(pat) for pat in raw_cfg["documents"]["detail_url_patterns"]
                    ]
                if "attachment_url_pattern" in raw_cfg["documents"]:
                    raw_cfg["documents"]["attachment_url_pattern"] = _rewrite_pat(
                        raw_cfg["documents"]["attachment_url_pattern"]
                    )

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

            multi_resource_sources = {
                "dd-017",
                "dd-019",
                "dd-039",
                "dd-108",
                "dd-132",
                "dd-142",
                "dd-153",
                "dd-162",
                "dd-198",
                "dd-209",
                "dd-265",
                "dd-320",
                "dd-323",
                "dd-324",
                "dd-327",
                "dd-337",
                "dd-385",
                "dd-423",
                "dd-482",
                "dd-492",
                "dd-493",
                "dd-502",
                "dd-506",
                "dd-507",
                "dd-509",
                "dd-511",
                "dd-512",
                "dd-514",
                "dd-516",
                "dd-517",
                "dd-518",
                "dd-525",
                "dd-527",
                "dd-548",
                "dd-549",
                "dd-550",
                "dd-551",
                "dd-557",
                "dd-558",
                "dd-559",
                "dd-562",
                "dd-564",
                "dd-565",
                "dd-570",
                "dd-571",
                "dd-572",
                "dd-575",
                "dd-577",
                "dd-578",
                "dd-579",
            }

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
                        ORDER BY d.row_index, r.id
                        """,
                        (source_id,),
                    ).fetchall()
                    assert len(rows) >= 1, f"Expected document_row for {source_id}"
                    from wlh.engine.objects import Objects

                    objs = Objects()
                    quality_report = assert_delivery_quality(
                        rows,
                        objects=objs,
                        source_config=raw_cfg,
                        require_multi_resource=(source_id in multi_resource_sources),
                    )
                    assert quality_report["passed"] is True
                    assert quality_report["violations"] == []

                    verified = verify_source_delivery(
                        source_id,
                        require_multi_resource=(source_id in multi_resource_sources),
                    )
                    assert verified["passed"] is True

                    projected_rows = [project_canonical(r) for r in rows]
                    projected = next(
                        (
                            p
                            for p in projected_rows
                            if expected_substr in (p["doc_number"] or "")
                            or expected_substr in (p["title"] or "")
                        ),
                        projected_rows[0],
                    )
                    assert projected["doc_type"] == expected_doc_type
                    assert (
                        expected_substr in (projected["doc_number"] or "")
                        or expected_substr in (projected["title"] or "")
                    ), f"Expected {expected_substr} in {projected}"
                    if source_id in {
                        "dd-492",
                        "dd-493",
                        "dd-499",
                        "dd-507",
                        "dd-512",
                        "dd-513",
                        "dd-514",
                        "dd-560",
                        "dd-561",
                        "dd-562",
                        "dd-565",
                        "dd-575",
                    }:
                        assert {
                            r["resource_id"] for r in rows if r["original_hash"] is not None
                        } == {r["resource_id"] for r in rows}, (
                            f"Expected captured attachment/original for every resource of {source_id}"
                        )
                    if source_id in {"dd-512", "dd-516", "dd-565", "dd-575"}:
                        primary_rows = [r for r in rows if r["role"] == "primary"] or rows
                        for r in primary_rows:
                            md_text = objs.get(r["markdown_hash"]).decode(
                                "utf-8", errors="replace"
                            )
                            if source_id == "dd-575":
                                assert "Toàn văn hồ sơ dự án" in md_text
                            elif source_id == "dd-512":
                                assert (
                                    "Official Legal Document" in md_text or "Nghị quyết" in md_text
                                )
                            elif source_id == "dd-516":
                                assert r["doc_number"] is None
                                assert "Đề nghị hướng dẫn" in md_text
                                assert "Trả lời:" in md_text
                                ans_part = md_text.split("Trả lời:", 1)[1].strip()
                                assert "Căn cứ " in ans_part and len(ans_part) >= 50
                            elif source_id == "dd-565":
                                assert (
                                    md_text.count(
                                        "Full judgment reasons of the Supreme Court of Canada"
                                    )
                                    == 1
                                )
                    if source_id in {"dd-550", "dd-558"}:
                        assert rows[0]["original_media_type"] == "application/zip"
                        assert (projected["original_file"] or "").endswith(".zip")
                    if source_id == "dd-512":
                        assert projected["issuer"] == "Quốc hội"
                        assert projected["published_at"] == "2026-08-24"
                        assert projected["title"] != projected["doc_number"]
                        assert not re.fullmatch(
                            r"(?:Luật|Pháp lệnh|Nghị quyết)(?:\s+số)?\s+"
                            + re.escape(projected["doc_number"] or ""),
                            (projected["title"] or "").strip(),
                            re.I,
                        ), (
                            f"dd-512 title {projected['title']!r} must include descriptive subject (Trích yếu), "
                            "not just bare document number"
                        )
                    if source_id == "dd-516":
                        assert projected["issuer"] == "Bộ Tài chính"
                        assert not (projected["title"] or "").startswith("Bộ Tài")
                        assert not (projected["title"] or "").endswith("...")
                    if source_id == "dd-575":
                        assert "Bộ Quốc phòng" in (projected["issuer"] or "")
                        for r in rows:
                            assert r["doc_number"] is None
                            assert r["role"] == "primary"
                            assert r["original_hash"] is not None
                    if source_id == "dd-548":
                        assert projected["issuer"] == "The Supreme Court"
                        assert projected["published_at"] == "2024-01-11"
                    if source_id == "dd-496":
                        assert projected["issuer"] == "TAND TP. Cần Thơ"
                        assert projected["published_at"] == "2026-10-15"
                    if source_id == "dd-502":
                        assert projected["issuer"] == "国务院"
                        assert projected["published_at"] == "2026-10-03"
                    if source_id == "dd-520":
                        assert projected["issuer"] == "国税庁"
                    if source_id == "dd-549":
                        assert projected["issuer"] == "HM Revenue & Customs"
                        assert projected["published_at"] == "2016-03-11"
                    if source_id == "dd-562":
                        assert projected["issuer"] == "Supreme Court of the United States"
                        assert projected["published_at"] in {"2026-09-25", "2026-06-30"}
                        assert " v. " in (projected["title"] or "")
                    if source_id == "dd-578":
                        const_row = next(p for p in projected_rows if p["title"] == "中華民國憲法")
                        assert const_row["published_at"] == "1947-01-01"
                        assert const_row["effective_at"] == "1947-12-25"
                        assert const_row["status"] == "現行有效"
                    if source_id == "dd-579":
                        assert projected["effective_at"] == "1947-07-19"
                        assert projected["expired_at"] == "1991-05-17"
                        assert projected["status"] == "已廢止"
                    if source_id == "dd-509":
                        assert projected["published_at"] == "2004-05-19"
                        assert projected["effective_at"] == "2004-05-19"

            rp_run = prepare_run(replay_yaml, "reparse")
            rp_res = execute_run(rp_run)
            assert rp_res["status"] == "complete", f"{source_id} reparse failed: {rp_res}"
            assert rp_res["report"]["external_requests"] == 0
        finally:
            _BridgeFixtureHandler.active_source_id = ""

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
adapter: web
archive_approved: true
robots: false
scope: "Detail page subrequest 503 propagation E2E test"
entry_urls:
  - "http://127.0.0.1:{srv_port}/dt"
allowed_hosts:
  - "127.0.0.1"
allowed_path_prefixes:
  - "/dt"
delay: 0.0
fetch:
  mode: rendered_dom
  wait_until: load
  max_depth: 1
  retries: 0
  page_timeout_ms: 15000
metadata:
  source_name: "Subrequest 503 Test"
  document_type: "法律法规"
  language: "vi"
  jurisdiction: "VN"
documents:
  detail_url_patterns:
    - "^http://127\\\\.0\\\\.0\\\\.1:{srv_port}/dt/[^/?#]+/\\\\d{{15,}}$"
  detail_body: always
  content_xpath: '//div[contains(@class, "info-dt-l")] | //div[@id="nav-home"]'
""".strip(),
                encoding="utf-8",
            )
            from wlh.engine.runs import execute_run, prepare_run

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


class TestDeliverySemanticQualityContract:
    def test_valid_multi_resource_delivery_rows_pass_semantic_quality_audit(
        self, wlh_env: dict[str, str]
    ) -> None:
        """Multi-resource delivery rows with distinct substantive titles, valid metadata, and clean CAS Markdown pass semantic quality audit."""
        from wlh.engine.objects import Objects

        objs = Objects()
        md1_hash = objs.put(
            (
                "# 中华人民共和国数据安全法\n\n"
                "第一条 为了规范数据处理活动，保障数据安全，促进数据开发利用，"
                "保护个人、组织的合法权益，维护国家主权、安全和发展利益，制定本法。"
            ).encode("utf-8")
        )
        md2_hash = objs.put(
            (
                "# 中华人民共和国个人信息保护法\n\n"
                "第一条 为了保护个人信息权益，规范个人信息处理活动，促进个人信息合理利用，"
                "根据宪法，制定本法。"
            ).encode("utf-8")
        )
        rows = [
            {
                "source_id": "dd-901",
                "resource_id": "res-001",
                "row_index": 1,
                "role": "primary",
                "title": "中华人民共和国数据安全法",
                "markdown_file": f"数据安全法_{'a' * 8}_{md1_hash[:8]}.md",
                "doc_type": "法律法规",
                "acquired_at": "2026-10-25 06:00:00",
                "original_file": None,
                "volume_order": None,
                "doc_number": "主席令第八十四号",
                "issuer": "全国人民代表大会常务委员会",
                "published_at": "2021-06-10",
                "effective_at": "2021-09-01",
                "expired_at": None,
                "status": "有效",
                "source_name": "国家法律法规数据库",
                "detail_url": "https://flk.npc.gov.cn/detail1.html",
                "source_record_hash": "a" * 64,
                "markdown_hash": md1_hash,
                "original_hash": None,
            },
            {
                "source_id": "dd-901",
                "resource_id": "res-002",
                "row_index": 1,
                "role": "primary",
                "title": "中华人民共和国个人信息保护法",
                "markdown_file": f"个人信息保护法_{'b' * 8}_{md2_hash[:8]}.md",
                "doc_type": "法律法规",
                "acquired_at": "2026-10-25 06:01:00",
                "original_file": None,
                "volume_order": None,
                "doc_number": "主席令第九十一号",
                "issuer": "全国人民代表大会常务委员会",
                "published_at": "2021-08-20",
                "effective_at": "2021-11-01",
                "expired_at": None,
                "status": "有效",
                "source_name": "国家法律法规数据库",
                "detail_url": "https://flk.npc.gov.cn/detail2.html",
                "source_record_hash": "b" * 64,
                "markdown_hash": md2_hash,
                "original_hash": None,
            },
        ]
        report = audit_delivery_rows(rows, objects=objs, require_multi_resource=True)
        assert report["passed"] is True
        assert report["violations"] == []

    def test_defect_rows_report_boilerplate_duplicate_title_polluted_doc_number_and_signature_stub_violations(
        self, wlh_env: dict[str, str]
    ) -> None:
        """Semantic quality audit deterministically flags boilerplate titles, duplicate titles, polluted doc_numbers, signature-only stubs, duplicated body paragraphs, and single-resource collections."""
        from wlh.engine.objects import Objects

        objs = Objects()
        sig_stub_hash = objs.put(
            b"Signer: Ministry Office\nSigned at: 2026-10-23\nCertificate ID: 8849-1102"
        )
        repeated_para = (
            "Pursuant to Section 42 of the Administrative Procedure Act, the agency hereby "
            "establishes mandatory compliance reporting standards for all regulated entities."
        )
        dup_body_hash = objs.put(
            f"# Administrative Compliance Rule\n\n{repeated_para}\n\n{repeated_para}".encode(
                "utf-8"
            )
        )
        clean_md_hash = objs.put(
            (
                "# Substantive Legal Provision\n\n"
                "Article 1. All administrative agencies shall publish normative decisions in the official gazette."
            ).encode("utf-8")
        )

        # 1. Single resource in collection + boilerplate title + signature-only markdown + polluted doc_number
        single_defect_rows = [
            {
                "source_id": "dd-455",
                "resource_id": "res-only-1",
                "row_index": 1,
                "role": "primary",
                "title": "国家新闻出版署",
                "markdown_file": f"国家新闻出版署_{'c' * 8}_{sig_stub_hash[:8]}.md",
                "doc_type": "通知公告",
                "acquired_at": "2026-10-25 06:00:00",
                "original_file": None,
                "volume_order": None,
                "doc_number": "国新出发〔2026〕1号\n附表说明及长篇正文污染内容",
                "issuer": "国家新闻出版署",
                "published_at": "2026-01-26",
                "effective_at": None,
                "expired_at": None,
                "status": None,
                "source_name": "国家新闻出版署",
                "detail_url": "https://www.nppa.gov.cn/detail1.html",
                "source_record_hash": "c" * 64,
                "markdown_hash": sig_stub_hash,
                "original_hash": None,
            }
        ]
        report_single = audit_delivery_rows(
            single_defect_rows, objects=objs, require_multi_resource=True
        )
        assert report_single["passed"] is False
        codes_single = set(
            report_single.get("violation_codes")
            or [v["code"] for v in report_single["violations"]]
        )
        assert "single_resource_in_collection" in codes_single
        assert "boilerplate_or_garbage_title" in codes_single
        assert "polluted_or_malformed_doc_number" in codes_single
        assert "signature_only_markdown" in codes_single

        # 2. Duplicate resource titles + boilerplate attachment title ("Tải file đính kèm") + duplicated body markdown + long-title-duplicating doc_number
        long_shared_title = (
            "Administrative Enforcement Guidelines on Cross-Border Financial Data Transfers"
        )
        multi_defect_rows = [
            {
                "source_id": "dd-902",
                "resource_id": "res-dup-1",
                "row_index": 1,
                "role": "primary",
                "title": long_shared_title,
                "markdown_file": f"guidelines1_{'d' * 8}_{dup_body_hash[:8]}.md",
                "doc_type": "政策文件",
                "acquired_at": "2026-10-25 06:00:00",
                "original_file": None,
                "volume_order": None,
                "doc_number": long_shared_title,
                "issuer": "Financial Regulatory Authority",
                "published_at": "2026-05-01",
                "effective_at": None,
                "expired_at": None,
                "status": None,
                "source_name": "Financial Regulatory Portal",
                "detail_url": "https://reg.example.org/doc1",
                "source_record_hash": "d" * 64,
                "markdown_hash": dup_body_hash,
                "original_hash": None,
            },
            {
                "source_id": "dd-902",
                "resource_id": "res-dup-2",
                "row_index": 1,
                "role": "primary",
                "title": long_shared_title,
                "markdown_file": f"guidelines2_{'e' * 8}_{clean_md_hash[:8]}.md",
                "doc_type": "政策文件",
                "acquired_at": "2026-10-25 06:01:00",
                "original_file": None,
                "volume_order": None,
                "doc_number": None,
                "issuer": "Financial Regulatory Authority",
                "published_at": "2026-05-02",
                "effective_at": None,
                "expired_at": None,
                "status": None,
                "source_name": "Financial Regulatory Portal",
                "detail_url": "https://reg.example.org/doc2",
                "source_record_hash": "e" * 64,
                "markdown_hash": clean_md_hash,
                "original_hash": None,
            },
            {
                "source_id": "dd-902",
                "resource_id": "res-dup-2",
                "row_index": 2,
                "role": "independent_attachment",
                "title": "Tải file đính kèm",
                "markdown_file": f"attach_{'e' * 8}_{clean_md_hash[:8]}.md",
                "doc_type": "政策文件",
                "acquired_at": "2026-10-25 06:01:00",
                "original_file": None,
                "volume_order": None,
                "doc_number": None,
                "issuer": None,
                "published_at": None,
                "effective_at": None,
                "expired_at": None,
                "status": None,
                "source_name": "Financial Regulatory Portal",
                "detail_url": "https://reg.example.org/doc2",
                "source_record_hash": "e" * 64,
                "markdown_hash": clean_md_hash,
                "original_hash": None,
            },
        ]
        report_multi = audit_delivery_rows(
            multi_defect_rows, objects=objs, require_multi_resource=True
        )
        assert report_multi["passed"] is False
        codes_multi = set(
            report_multi.get("violation_codes")
            or [v["code"] for v in report_multi["violations"]]
        )
        assert "duplicate_resource_titles" in codes_multi
        assert "boilerplate_or_garbage_title" in codes_multi
        assert "polluted_or_malformed_doc_number" in codes_multi
        assert "duplicated_body_markdown" in codes_multi

        for bad_title in ("Tải file đính kèm", "Contents", "附件下载"):
            bad_title_row = {
                **multi_defect_rows[1],
                "title": bad_title,
                "doc_number": "DOC-2026-" + ("X" * 130),
            }
            rep = audit_delivery_rows([bad_title_row], objects=objs, require_multi_resource=False)
            rep_codes = set(
                rep.get("violation_codes") or [v["code"] for v in rep["violations"]]
            )
            assert "boilerplate_or_garbage_title" in rep_codes
            assert "polluted_or_malformed_doc_number" in rep_codes

        with pytest.raises(WlhError) as exc:
            assert_delivery_quality(
                multi_defect_rows, objects=objs, require_multi_resource=True
            )
        assert exc.value.code == "delivery_quality_failed"

    def test_vietnamese_paywall_placeholder_and_stub_attachment_rows_fail_semantic_quality_audit(
        self, wlh_env: dict[str, str]
    ) -> None:
        """Vietnamese login-wall/un-uploaded placeholders, gazette running-header attachment stubs, and UUID/salutation-polluted titles fail semantic quality audit."""
        from wlh.engine.objects import Objects

        objs = Objects()
        clean_md_hash = objs.put(
            (
                "# Luật Đầu tư công 2026\n\n"
                "Điều 1. Phạm vi điều chỉnh. Luật này quy định việc quản lý và sử dụng vốn đầu tư công; "
                "quản lý nhà nước về đầu tư công; quyền, nghĩa vụ và trách nhiệm của cơ quan, đơn vị, "
                "tổ chức, cá nhân có liên quan đến hoạt động đầu tư công."
            ).encode("utf-8")
        )
        base_valid_row: dict[str, Any] = {
            "source_id": "dd-495",
            "resource_id": "res-vn-1",
            "row_index": 1,
            "role": "primary",
            "title": "Luật Đầu tư công 2026",
            "markdown_file": f"luat_dau_tu_cong_{'a' * 8}_{clean_md_hash[:8]}.md",
            "doc_type": "法律法规",
            "acquired_at": "2026-10-25 06:00:00",
            "original_file": None,
            "volume_order": None,
            "doc_number": "58/2026/QH15",
            "issuer": "Quốc hội",
            "published_at": "2026-06-15",
            "effective_at": "2026-09-01",
            "expired_at": None,
            "status": "Còn hiệu lực",
            "source_name": "LuatVietnam 法规库",
            "detail_url": "https://luatvietnam.vn/dau-tu/luat-dau-tu-cong-2026-123456-d1.html",
            "source_record_hash": "a" * 64,
            "markdown_hash": clean_md_hash,
            "original_hash": None,
        }

        # 1. Primary rows containing Vietnamese VIP/login-wall or un-uploaded placeholders padded with metadata
        placeholder_samples = (
            (
                "# Thông tư 145/2026/TT-BTC\n\n"
                "Văn bản này đang cập nhật nội dung. Mời quý khách xem nội dung văn bản dưới dạng file PDF... "
                "Bạn chưa Đăng nhập thành viên. Vui lòng Đăng nhập để tải văn bản.\n\n"
                "| Thuộc tính | Giá trị |\n| --- | --- |\n| Số hiệu | 145/2026/TT-BTC |\n| Cơ quan ban hành | Bộ Tài chính |"
            ),
            (
                "# Quyết định 88/2026/QĐ-TTg\n\n"
                "Bạn chưa Đăng nhập thành viên. Đây là tiện ích dành cho tài khoản Tiêu chuẩn hoặc Nâng cao 6 tháng trở lên. "
                "Vui lòng Đăng nhập để xem chi tiết Nội dung.\n\n"
                "| Thuộc tính | Giá trị |\n| --- | --- |\n| Số hiệu | 88/2026/QĐ-TTg |"
            ),
            (
                "# Nghị quyết 46/2026/QH16\n\n"
                "Vui lòng xem nội dung chi tiết trong file đính kèm."
            ),
        )
        for sample_md in placeholder_samples:
            sample_hash = objs.put(sample_md.encode("utf-8"))
            placeholder_row = {
                **base_valid_row,
                "markdown_file": f"placeholder_{'b' * 8}_{sample_hash[:8]}.md",
                "markdown_hash": sample_hash,
            }
            rep_placeholder = audit_delivery_rows(
                [placeholder_row], objects=objs, require_multi_resource=False
            )
            assert rep_placeholder["passed"] is False
            codes_placeholder = set(
                rep_placeholder.get("violation_codes")
                or [v["code"] for v in rep_placeholder["violations"]]
            )
            assert codes_placeholder & {
                "blocked_or_placeholder_markdown",
                "stub_markdown",
            }, (
                f"Expected blocked_or_placeholder_markdown or stub_markdown for {sample_md!r}, "
                f"got {codes_placeholder}"
            )

        # 2. independent_attachment row whose Markdown is only a gazette running header stub
        gazette_stub_md = "CÔNG BÁO/Số 560/Ngày 23-10-2026\n\nCÔNG BÁO/Số 560/Ngày 23-10-2026"
        gazette_stub_hash = objs.put(gazette_stub_md.encode("utf-8"))
        stub_att_row = {
            **base_valid_row,
            "source_id": "dd-493",
            "row_index": 2,
            "role": "independent_attachment",
            "title": "Quyết định 48/2026/QĐ-TTg (Bản PDF Công báo)",
            "markdown_file": f"congbao_stub_{'c' * 8}_{gazette_stub_hash[:8]}.md",
            "original_file": f"congbao_stub_{'c' * 8}_{gazette_stub_hash[:8]}.pdf",
            "markdown_hash": gazette_stub_hash,
            "original_hash": gazette_stub_hash,
        }
        rep_att = audit_delivery_rows(
            [stub_att_row], objects=objs, require_multi_resource=False
        )
        assert rep_att["passed"] is False
        codes_att = set(
            rep_att.get("violation_codes") or [v["code"] for v in rep_att["violations"]]
        )
        assert "stub_markdown" in codes_att

        # 3. Rows whose title ends with a raw 32-hex UUID filename suffix or starts with an unstripped salutation fragment
        for polluted_title in (
            "Luật Phát triển đô thị - 690779d6257c4649a72bd5ce76e64cb1.pdf",
            "Bộ Tài Chính. Công ty tôi hoạt động trong lĩnh vực...",
        ):
            bad_title_row = {
                **base_valid_row,
                "title": polluted_title,
            }
            rep_title = audit_delivery_rows(
                [bad_title_row], objects=objs, require_multi_resource=False
            )
            assert rep_title["passed"] is False
            codes_title = set(
                rep_title.get("violation_codes")
                or [v["code"] for v in rep_title["violations"]]
            )
            assert "boilerplate_or_garbage_title" in codes_title, (
                f"Expected boilerplate_or_garbage_title for {polluted_title!r}, got {codes_title}"
            )


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


