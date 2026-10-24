"""Black-box contract tests for structured legal XML and open-source bridge adapters."""

from __future__ import annotations

import io
import socket
import threading
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml

from .conftest import make_native_pdf

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

_D1N_COOKIE_CHALLENGE = (
    b'<html><body><script>document.cookie="D1N=ok123"'
    b'+"; expires=Thu, 31-Dec-37 23:55:55 GMT; path=/";'
    b"window.location.reload(true);</script></body></html>"
)


def _make_rii_zip_bytes(
    doknr: str = "JURE100055033",
    aktenzeichen: str = "VIII ZR 12/25",
    ecli: str = "ECLI:DE:BGH:2026:150426UVIIIZR12.25.0",
) -> bytes:
    buf = io.BytesIO()
    xml_payload = f"""<?xml version="1.0" encoding="UTF-8"?>
<dokument>
  <doknr>{doknr}</doknr>
  <ecli>{ecli}</ecli>
  <gertyp>BGH</gertyp>
  <entsch-datum>20260415</entsch-datum>
  <aktenzeichen>{aktenzeichen}</aktenzeichen>
  <titelzeile>Kaufrechtliche Gewährleistung bei digitalen Produkten</titelzeile>
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


@dataclass(frozen=True)
class _RouteSpec:
    fixture: str | None = None
    binary_attr: str | None = None
    raw_body: bytes | None = None
    content_type: str = "text/html; charset=utf-8"
    encoding: str = "utf-8"
    template: bool = False
    requires_d1n_cookie: bool = False
    status: int = 200


_PDF_ROUTE = _RouteSpec(binary_attr="sample_pdf", content_type="application/pdf")

_EXACT_ROUTES: dict[tuple[str, str], _RouteSpec] = {
    ("*", "/robots.txt"): _RouteSpec(
        raw_body=b"User-agent: *\nAllow: /\n", content_type="text/plain"
    ),
    # Source-specific root ("/") routes
    ("dd-492", "/"): _RouteSpec(fixture="dd-492/index.html"),
    ("dd-493", "/"): _RouteSpec(fixture="dd-493/index.html"),
    ("dd-513", "/"): _RouteSpec(fixture="dd-513/index.html"),
    ("dd-495", "/"): _RouteSpec(fixture="dd-495/index.html"),
    ("dd-512", "/"): _RouteSpec(fixture="dd-575/index.html"),
    ("dd-575", "/"): _RouteSpec(fixture="dd-575/index.html"),
    ("*", "/"): _RouteSpec(fixture="dd-492/index.html"),
    # Structured XML & open-source bridge contract fixtures (dd-910..dd-915)
    ("*", "/xml/akn-act-2026.xml"): _RouteSpec(
        fixture="dd-910/akn-act-2026.xml", content_type="application/xml; charset=utf-8"
    ),
    ("*", "/wwl/congbao-list.html"): _RouteSpec(fixture="dd-911/congbao-list.html"),
    ("*", "/wwl/detail/cb-2026-01.html"): _RouteSpec(fixture="dd-911/cb-2026-01.html"),
    ("*", "/juriscraper/ca1-opinions.json"): _RouteSpec(
        fixture="dd-912/ca1-opinions.json", content_type="application/json"
    ),
    ("*", "/oldp/bgh-cases.html"): _RouteSpec(fixture="dd-913/bgh-cases.html"),
    ("*", "/opencaselaw/ch-bger.html"): _RouteSpec(fixture="dd-914/ch-bger.html"),
    ("*", "/openstates/bills.json"): _RouteSpec(
        fixture="dd-915/bills.json", content_type="application/json"
    ),
    # Shared PDF binary endpoints
    ("*", "/wwl/files/nd55.pdf"): _PDF_ROUTE,
    ("*", "/juriscraper/opinions/25-1001.pdf"): _PDF_ROUTE,
    ("*", "/cpp/files/vbpq/2026/04/105-nd.signed.pdf"): _PDF_ROUTE,
    ("*", "/cpp/files/vbpq/2026/04/106-nd.signed.pdf"): _PDF_ROUTE,
    ("*", "/content/pkg/FR-2026-04-20/pdf/2026-08801.pdf"): _PDF_ROUTE,
    ("*", "/Biennium/2025-26/Pdf/Bills/House%20Bills/1091-S.E.pdf"): _PDF_ROUTE,
    ("*", "/pdf/2026/07/01/us_v_nemc.pdf"): _PDF_ROUTE,
    ("*", "/uploadFiles/host/local/du-thao-01.pdf"): _PDF_ROUTE,
    ("*", "/uploadFiles/host/local/du-thao-02.pdf"): _PDF_ROUTE,
    ("*", "/scc-csc/scc-csc/en/item/40000/judgment.pdf"): _PDF_ROUTE,
    ("*", "/scc-csc/scc-csc/en/item/40001/judgment.pdf"): _PDF_ROUTE,
    ("*", "/eng/pdf/dipn03.pdf"): _PDF_ROUTE,
    ("*", "/eng/pdf/dipn04.pdf"): _PDF_ROUTE,
    # dd-017: SPC guiding cases
    ("*", "/shenpan/gengduo/77.html"): _RouteSpec(fixture="dd-017/list-77.html"),
    ("*", "/shenpan/gengduo/76.html"): _RouteSpec(fixture="dd-017/list-76.html"),
    ("*", "/shenpan/gengduo/empty.html"): _RouteSpec(fixture="dd-017/empty.html"),
    ("*", "/shenpan/xiangqing/202601.html"): _RouteSpec(fixture="dd-017/detail-202601.html"),
    ("*", "/shenpan/xiangqing/202602.html"): _RouteSpec(fixture="dd-017/detail-202602.html"),
    # dd-040 & dd-041: SPP gazette & guiding cases
    ("*", "/spp/gjgb/index.shtml"): _RouteSpec(fixture="dd-040/gjgb-index.html"),
    ("*", "/spp/gjgb/202603/t20260319_722354.shtml"): _RouteSpec(
        fixture="dd-040/t20260319_722354.html"
    ),
    ("*", "/spp/jczdal/index.shtml"): _RouteSpec(fixture="dd-041/jczdal-index.html"),
    ("*", "/spp/jczdal/202509/t20250919_706599.shtml"): _RouteSpec(
        fixture="dd-041/t20250919_706599.html"
    ),
    # dd-102: SAFE penalties
    ("*", "/www/illegal?siteid=beijing"): _RouteSpec(fixture="dd-102/illegal-beijing.html"),
    ("*", "/www/illegal/listQuery?saffno=8a8b8c0123456789abcdef"): _RouteSpec(
        fixture="dd-102/listQuery-saffno.html"
    ),
    # dd-183: MOF local government debt
    ("*", "/zhuantilanmu/dfzgl/"): _RouteSpec(fixture="dd-183/dfzgl-index.html"),
    ("*", "/zhuantilanmu/dfzgl/zcfg/202111/t20211110_3764807.htm"): _RouteSpec(
        fixture="dd-183/t20211110_3764807.html"
    ),
    # dd-375: NBS statistical standards
    ("*", "/xxgk/tjbz/gjtjbz/201905/t20190521_1758938.html"): _RouteSpec(
        fixture="dd-375/t20190521_1758938.html"
    ),
    # dd-448: CFA film filing notices
    ("*", "/xxgk/gsxx/dybalx/"): _RouteSpec(fixture="dd-448/dybalx-index.html"),
    ("*", "/xxgk/gsxx/dybalx/202610/t20261013_1012172.html"): _RouteSpec(
        fixture="dd-448/t20261013_1012172.html"
    ),
    # dd-455: NPPA game approvals
    ("*", "/bsfw/jggs/yxspjg/"): _RouteSpec(fixture="dd-455/yxspjg-index.html"),
    ("*", "/bsfw/jggs/yxspjg/jkwlyxspxx/202601/t20260126_947910.html"): _RouteSpec(
        fixture="dd-455/t20260126_947910.html"
    ),
    # dd-492, dd-493, dd-495, dd-496, dd-513: Vietnam portals
    ("*", "/page/pagenew.aspx?docid=210001"): _RouteSpec(
        fixture="dd-492/doc-210001.html", template=True
    ),
    ("*", "/page/pagenew.aspx?docid=210002"): _RouteSpec(
        fixture="dd-492/doc-210002.html", template=True
    ),
    ("*", "/van-ban/quyet-dinh-48-2026-qd-ttg.htm"): _RouteSpec(fixture="dd-493/qd-48.html"),
    ("*", "/van-ban/quyet-dinh-49-2026-qd-ttg.htm"): _RouteSpec(fixture="dd-493/qd-49.html"),
    (
        "*",
        "/chi-thi-so-37-ct-ttg-tang-cuong-phong-chong-gian-lan-119261021100102474.htm",
    ): _RouteSpec(fixture="dd-513/chi-thi-37.html"),
    (
        "*",
        "/chung-khoan/thong-tu-145-2026-tt-btc-quy-dinh-dang-ky-449607-d1.html",
    ): _RouteSpec(fixture="dd-495/thong-tu-145.html"),
    ("*", "/ban-an/"): _RouteSpec(fixture="dd-496/ban-an-list.html"),
    ("*", "/ban-an/ban-an-1463-2026-ds-pt-2-1460069-d11.html"): _RouteSpec(
        fixture="dd-496/ban-an-1463.html"
    ),
    # dd-499: Federal Register XML
    ("*", "/documents/full_text/xml/2026/04/20/2026-08801.xml"): _RouteSpec(
        fixture="dd-499/2026-08801.xml", content_type="application/xml; charset=utf-8"
    ),
    # dd-501 & dd-502: State Council gazette & policies
    ("*", "/gongbao/"): _RouteSpec(fixture="dd-501/gongbao-home.html"),
    ("*", "/gongbao/index.htm"): _RouteSpec(fixture="dd-501/gongbao-home.html"),
    ("*", "/gongbao/currentissue.htm"): _RouteSpec(fixture="dd-501/gongbao-home.html"),
    ("*", "/gongbao/2026/issue_13046/"): _RouteSpec(fixture="dd-501/issue-13046.html"),
    ("*", "/gongbao/2026/issue_13046/202604/content_7065845.html"): _RouteSpec(
        fixture="dd-501/content-7065845.html"
    ),
    ("*", "/zhengce/index.htm"): _RouteSpec(fixture="dd-502/zhengce-index.html"),
    ("*", "/zhengce/xxgk/"): _RouteSpec(fixture="dd-502/zhengce-index.html"),
    ("*", "/zhengce/xxgk/gjgzk/index.htm"): _RouteSpec(fixture="dd-502/zhengce-index.html"),
    ("*", "/zhengce/content/202610/content_7066001.htm"): _RouteSpec(
        fixture="dd-502/content-700001.html"
    ),
    ("*", "/zhengce/content/202610/content_700001.htm"): _RouteSpec(
        fixture="dd-502/content-700001.html"
    ),
    ("*", "/zhengce/content/2026-10/03/content_700002.htm"): _RouteSpec(
        fixture="dd-502/content-700002.html"
    ),
    # dd-506 & dd-507: HK Judiciary & IRD
    ("*", "/lrs/common/ju/judgment.jsp"): _RouteSpec(fixture="dd-506/judgment.html"),
    ("*", "/lrs/common/ju/tjpv.jsp"): _RouteSpec(fixture="dd-506/tjpv.html"),
    ("*", "/lrs/common/ju/ju_frame.jsp?DIS=158000"): _RouteSpec(
        fixture="dd-506/ju-frame-158000.html"
    ),
    ("*", "/lrs/common/ju/ju_frame.jsp?DIS=158001"): _RouteSpec(
        fixture="dd-506/ju-frame-158001.html"
    ),
    ("*", "/lrs/common/ju/ju_body.jsp?DIS=158000"): _RouteSpec(
        fixture="dd-506/ju-body-158000.html"
    ),
    ("*", "/lrs/common/ju/ju_body.jsp?DIS=158001"): _RouteSpec(
        fixture="dd-506/ju-body-158001.html"
    ),
    ("*", "/eng/ppr/dip.htm"): _RouteSpec(fixture="dd-507/dip.html"),
    # dd-514: Vietnam Supreme Court precedents
    ("*", "/webcenter/portal/anle/anle"): _RouteSpec(fixture="dd-514/anle-list.html"),
    ("*", "/webcenter/portal/anle/chitietanle?dDocName=TAND377305"): _RouteSpec(
        fixture="dd-514/tand377305-loopback.html"
    ),
    (
        "*",
        "/webcenter/portal/anle/chitietanle?dDocName=TAND377305&_afrLoop=987654321&_afrWindowMode=0",
    ): _RouteSpec(fixture="dd-514/tand377305-detail.html"),
    ("*", "/webcenter/portal/anle/chitietanle?dDocName=TAND377338"): _RouteSpec(
        fixture="dd-514/tand377338-detail.html"
    ),
    (
        "*",
        "/webcenter/portal/anle/chitietanle?dDocName=TAND377338&_afrLoop=987654321&_afrWindowMode=0",
    ): _RouteSpec(fixture="dd-514/tand377338-detail.html"),
    # dd-516: Vietnam MOF Q&A
    ("*", "/hoidapcstc"): _RouteSpec(fixture="dd-516/hoidap-list.html"),
    ("*", "/hoidapcstc/Area/QuestionByArea/77"): _RouteSpec(fixture="dd-516/hoidap-list.html"),
    ("*", "/hoidapcstc/Area/QuestionByArea/64"): _RouteSpec(fixture="dd-516/hoidap-list.html"),
    ("*", "/hoidapcstc/home/cthoidap/166356"): _RouteSpec(fixture="dd-516/cthoidap-166356.html"),
    ("*", "/hoidapcstc/home/cthoidap/166357"): _RouteSpec(fixture="dd-516/cthoidap-166357.html"),
    # dd-517: Japan e-Gov law API
    ("*", "/api/1/lawlists/2"): _RouteSpec(
        fixture="dd-517/lawlists-2.xml", content_type="application/xml; charset=utf-8"
    ),
    ("*", "/api/1/lawdata/405AC0000000088"): _RouteSpec(
        fixture="dd-517/lawdata-405AC0000000088.xml",
        content_type="application/xml; charset=utf-8",
    ),
    ("*", "/api/1/lawdata/321CONSTITUTION"): _RouteSpec(
        fixture="dd-517/lawdata-321CONSTITUTION.xml",
        content_type="application/xml; charset=utf-8",
    ),
    # dd-520: Japan NTA Shift_JIS tax directives
    ("*", "/law/tsutatsu/kihon/hojin/01.htm"): _RouteSpec(
        fixture="dd-520/hojin-01.html", content_type="text/html"
    ),
    ("*", "/law/tsutatsu/kihon/hojin/01/01_01.htm"): _RouteSpec(
        fixture="dd-520/hojin-01-01.html", content_type="text/html", encoding="shift_jis"
    ),
    # dd-527: Singapore IRAS e-Tax guides
    ("*", "/quick-links/e-tax-guides"): _RouteSpec(fixture="dd-527/e-tax-guides.html"),
    (
        "*",
        "/taxes/corporate-income-tax/income-deductions-for-companies/tax-treatment-of-r-d-expenditure",
    ): _RouteSpec(fixture="dd-527/r-d-expenditure.html"),
    (
        "*",
        "/taxes/corporate-income-tax/income-deductions-for-companies/tax-treatment-of-ip-rights",
    ): _RouteSpec(fixture="dd-527/ip-rights.html"),
    # dd-548: UK National Archives caselaw
    ("*", "/uksc/2024/1/data.xml"): _RouteSpec(
        fixture="dd-548/uksc-2024-1.xml", content_type="application/xml; charset=utf-8"
    ),
    ("*", "/uksc/2024/2/data.xml"): _RouteSpec(
        fixture="dd-548/uksc-2024-2.xml", content_type="application/xml; charset=utf-8"
    ),
    # dd-549: UK HMRC internal manuals
    ("*", "/government/collections/hmrc-manuals"): _RouteSpec(fixture="dd-549/hmrc-manuals.html"),
    ("*", "/hmrc-internal-manuals/appeals-reviews-and-tribunals-guidance"): _RouteSpec(
        fixture="dd-549/hmrc-manuals.html"
    ),
    ("*", "/hmrc-internal-manuals/admin-law-manual/adml3000"): _RouteSpec(
        fixture="dd-549/adml3000.html"
    ),
    ("*", "/hmrc-internal-manuals/admin-law-manual/adml4000"): _RouteSpec(
        fixture="dd-549/adml3000.html"
    ),
    ("*", "/hmrc-internal-manuals/admin-law-manual/adml5000"): _RouteSpec(
        fixture="dd-549/adml3000.html"
    ),
    (
        "*",
        "/hmrc-internal-manuals/appeals-reviews-and-tribunals-guidance/artg2120",
    ): _RouteSpec(fixture="dd-549/adml3000.html"),
    ("*", "/hmrc-internal-manuals/admin-law-manual/adml3100"): _RouteSpec(
        fixture="dd-549/adml3100.html"
    ),
    ("*", "/hmrc-internal-manuals/admin-law-manual/adml3200"): _RouteSpec(
        fixture="dd-549/adml3200.html"
    ),
    # dd-550 & dd-551: Germany Gesetze im Internet & Rechtsprechung im Internet
    ("*", "/gii-toc.xml"): _RouteSpec(
        fixture="dd-550/gii-toc.xml", content_type="application/xml; charset=utf-8"
    ),
    ("*", "/gg/xml.zip"): _RouteSpec(binary_attr="gg_zip", content_type="application/zip"),
    ("*", "/bgb/xml.zip"): _RouteSpec(binary_attr="bgb_zip", content_type="application/zip"),
    ("*", "/jportal/docs/bsjrs/bsjrs-toc.xml"): _RouteSpec(
        fixture="dd-551/bsjrs-toc.xml", content_type="application/xml; charset=utf-8"
    ),
    ("*", "/jportal/docs/bsjrs/jb-JURE100055033.zip"): _RouteSpec(
        binary_attr="rii_zip", content_type="application/zip"
    ),
    ("*", "/jportal/docs/bsjrs/jb-JURE100055034.zip"): _RouteSpec(
        binary_attr="rii_zip_2", content_type="application/zip"
    ),
    # dd-558 & dd-559: US Code & eCFR
    ("*", "/download.html"): _RouteSpec(fixture="dd-558/download.html"),
    ("*", "/download/releasepoints/us/pl/119/121/xml_usc01@119-121.zip"): _RouteSpec(
        binary_attr="usc01_zip", content_type="application/zip"
    ),
    ("*", "/download/releasepoints/us/pl/119/121/xml_usc02@119-121.zip"): _RouteSpec(
        binary_attr="usc02_zip", content_type="application/zip"
    ),
    ("*", "/api/versioner/v1/titles.json"): _RouteSpec(
        fixture="dd-559/titles.json", content_type="application/json"
    ),
    ("*", "/api/versioner/v1/structure/2024-01-01/title-1.json"): _RouteSpec(
        fixture="dd-559/title-1-structure.json", content_type="application/json"
    ),
    ("*", "/api/versioner/v1/structure/2026-10-21/title-1.json"): _RouteSpec(
        fixture="dd-559/title-1-structure.json", content_type="application/json"
    ),
    ("*", "/api/versioner/v1/full/2024-01-01/title-1.xml?part=1"): _RouteSpec(
        fixture="dd-559/title-1-part-1.xml", content_type="application/xml; charset=utf-8"
    ),
    ("*", "/api/versioner/v1/full/2026-10-21/title-1.xml?part=1"): _RouteSpec(
        fixture="dd-559/title-1-part-1.xml", content_type="application/xml; charset=utf-8"
    ),
    ("*", "/api/versioner/v1/full/2024-01-01/title-1.xml?part=2"): _RouteSpec(
        fixture="dd-559/title-1-part-2.xml", content_type="application/xml; charset=utf-8"
    ),
    ("*", "/api/versioner/v1/full/2026-10-21/title-1.xml?part=2"): _RouteSpec(
        fixture="dd-559/title-1-part-2.xml", content_type="application/xml; charset=utf-8"
    ),
    # dd-562: US Supreme Court slip opinions
    ("*", "/opinions/opinions.aspx"): _RouteSpec(fixture="dd-562/opinions-root.html"),
    ("*", "/opinions/slipopinion/26"): _RouteSpec(fixture="dd-562/slipopinion.html"),
    ("*", "/opinions/relatingtoorders/26"): _RouteSpec(fixture="dd-562/slipopinion.html"),
    ("*", "/opinions/slipopinion/25"): _RouteSpec(fixture="dd-562/slipopinion.html"),
    ("*", "/opinions/relatingtoorders/25"): _RouteSpec(fixture="dd-562/slipopinion.html"),
    ("*", "/opinions/25pdf/26a388_q86b.pdf"): _RouteSpec(
        binary_attr="scotus_pdf_1", content_type="application/pdf"
    ),
    ("*", "/opinions/25pdf/24-43_2b35.pdf"): _RouteSpec(
        binary_attr="scotus_pdf_2", content_type="application/pdf"
    ),
    # dd-563: IRS Internal Revenue Bulletins
    ("*", "/irb"): _RouteSpec(fixture="dd-563/irb-index.html"),
    ("*", "/irb/2026-16_IRB"): _RouteSpec(fixture="dd-563/2026-16_IRB.html"),
    # dd-564: Justice Laws Canada XML
    ("*", "/eng/XML/Legis.xml"): _RouteSpec(
        fixture="dd-564/Legis.xml", content_type="application/xml; charset=utf-8"
    ),
    ("*", "/eng/XML/A-1.xml"): _RouteSpec(
        fixture="dd-564/A-1.xml", content_type="application/xml; charset=utf-8"
    ),
    ("*", "/eng/XML/C-46.xml"): _RouteSpec(
        fixture="dd-564/C-46.xml", content_type="application/xml; charset=utf-8"
    ),
    # dd-565: Supreme Court of Canada decisions
    ("*", "/judgments-jugements/cb/"): _RouteSpec(fixture="dd-565/scc-list.html"),
    ("*", "/scc-csc/scc-csc/en/nav_date.do?iframe=true"): _RouteSpec(
        fixture="dd-565/scc-list.html"
    ),
    ("*", "/scc-csc/scc-csc/en/2026/nav_date.do?iframe=true"): _RouteSpec(
        fixture="dd-565/scc-list.html"
    ),
    ("*", "/judgments-jugements/cb/2026/40000/"): _RouteSpec(fixture="dd-565/scc-40000.html"),
    ("*", "/scc-csc/scc-csc/en/item/40000/index.do?iframe=true"): _RouteSpec(
        fixture="dd-565/scc-40000.html"
    ),
    ("*", "/judgments-jugements/cb/2026/40001/"): _RouteSpec(fixture="dd-565/scc-40001.html"),
    ("*", "/scc-csc/scc-csc/en/item/40001/index.do?iframe=true"): _RouteSpec(
        fixture="dd-565/scc-40001.html"
    ),
    ("*", "/cases-dossiers/search-recherche/40000/"): _RouteSpec(
        fixture="dd-565/scc-case-40000.html"
    ),
    # dd-570: New Zealand legislation
    ("*", "/act/public/2024/1/en/latest/"): _RouteSpec(fixture="dd-570/nz-act-2024-1.html"),
    ("*", "/act/public/2024/2/en/latest/"): _RouteSpec(fixture="dd-570/nz-act-2024-2.html"),
    # dd-572: Kenya Law AKN HTML
    ("*", "/legislation/"): _RouteSpec(fixture="dd-572/ke-legislation.html"),
    ("*", "/akn/ke/act/1963/718/eng@1963-12-10"): _RouteSpec(fixture="dd-572/ke-act-1963-718.html"),
    ("*", "/akn/ke/act/2016/31/eng@2022-12-31"): _RouteSpec(fixture="dd-572/ke-act-2016-31.html"),
    # dd-575: Vietnam National Assembly legislative drafts
    (
        "*",
        "/dt/luat-sua-doi-bo-sung-mot-so-dieu-cua-09-luat-ve-quan-su-quoc-phong/260805100059615084",
    ): _RouteSpec(fixture="dd-575/draft-1.html", template=True, requires_d1n_cookie=True),
    ("*", "/dt/luat-hoa-giai-o-co-so-sua-doi/260522145512086084"): _RouteSpec(
        fixture="dd-575/draft-2.html", template=True, requires_d1n_cookie=True
    ),
    # dd-997: Detail subrequest 503 contract test
    ("dd-997", "/dt"): _RouteSpec(fixture="dd-997/list.html"),
    ("dd-997", "/dt/law-draft/260805100059615084"): _RouteSpec(fixture="dd-997/detail.html"),
    ("dd-997", "/dt/api/dossier"): _RouteSpec(
        raw_body=b'{"error":"service_unavailable"}',
        content_type="application/json",
        status=503,
    ),
}

_PREFIX_ROUTES: list[tuple[str, _RouteSpec]] = [
    (
        "/atom.xml",
        _RouteSpec(fixture="dd-548/atom.xml", content_type="application/atom+xml; charset=utf-8"),
    ),
    (
        "/vanban/",
        _RouteSpec(
            binary_attr="sample_pdf", content_type="application/pdf", requires_d1n_cookie=True
        ),
    ),
    (
        "/api/v1/documents.json",
        _RouteSpec(
            fixture="dd-499/documents.json", content_type="application/json", template=True
        ),
    ),
    (
        "/api/decisions",
        _RouteSpec(
            fixture="dd-556/decisions.json", content_type="application/json", template=True
        ),
    ),
    (
        "/bills?jurisdiction=wa",
        _RouteSpec(fixture="dd-560/bills-wa.json", content_type="application/json", template=True),
    ),
    (
        "/api/rest/v4/search/",
        _RouteSpec(fixture="dd-561/search.json", content_type="application/json", template=True),
    ),
    (
        "/Pages/danh-sach-van-ban.aspx",
        _RouteSpec(fixture="dd-512/danh-sach-van-ban.html", requires_d1n_cookie=True),
    ),
    (
        "/Pages/vbpq-toan-van.aspx?ItemID=29895",
        _RouteSpec(fixture="dd-512/item-29895.html", requires_d1n_cookie=True),
    ),
    (
        "/Pages/vbpq-toan-van.aspx?ItemID=29894",
        _RouteSpec(fixture="dd-512/item-29894.html", requires_d1n_cookie=True),
    ),
    ("/items/", _RouteSpec(fixture="dd-570/nz-items.html")),
]


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
        "JURE100055034", "VIII ZR 13/25", "ECLI:DE:BGH:2026:150426UVIIIZR13.25.0"
    )
    gg_zip: bytes = _make_fixture_zip_bytes("BJNR000010949.xml", "dd-550/BJNR000010949.xml")
    bgb_zip: bytes = _make_fixture_zip_bytes("BJNR001950896.xml", "dd-550/BJNR001950896.xml")
    usc01_zip: bytes = _make_fixture_zip_bytes("usc01.xml", "dd-558/usc01.xml")
    usc02_zip: bytes = _make_fixture_zip_bytes("usc02.xml", "dd-558/usc02.xml")

    def log_message(self, format: str, *args: object) -> None:
        return

    def _resolve_route(self) -> _RouteSpec | None:
        spec = _EXACT_ROUTES.get((self.active_source_id, self.path)) or _EXACT_ROUTES.get(
            ("*", self.path)
        )
        if spec is not None:
            return spec
        for prefix, prefix_spec in _PREFIX_ROUTES:
            if self.path.startswith(prefix):
                return prefix_spec
        return None

    def _render_route(self, spec: _RouteSpec) -> tuple[bytes, str]:
        if spec.requires_d1n_cookie and "D1N=" not in self.headers.get("Cookie", ""):
            return _D1N_COOKIE_CHALLENGE, "text/html; charset=utf-8"
        if spec.raw_body is not None:
            return spec.raw_body, spec.content_type
        if spec.binary_attr is not None:
            payload: bytes = getattr(self, spec.binary_attr) or self.sample_pdf
            return payload, spec.content_type
        assert spec.fixture is not None
        text = (FIXTURES_DIR / spec.fixture).read_text(encoding="utf-8")
        if spec.template:
            host = self.headers.get("Host", "127.0.0.1")
            sec_host = self.secondary_netloc or host
            secondary_base = f"http://{self.secondary_netloc}" if self.secondary_netloc else ""
            text = (
                text.replace("{host}", host)
                .replace("{sec_host}", sec_host)
                .replace("{secondary_base}", secondary_base)
            )
        return text.encode(spec.encoding), spec.content_type

    def do_GET(self) -> None:
        spec = self._resolve_route()
        if spec is None:
            self.send_response(404)
            self.end_headers()
            return
        body, content_type = self._render_route(spec)
        self.send_response(spec.status)
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
                "dd-492",
                "dd-493",
                "dd-502",
                "dd-506",
                "dd-507",
                "dd-512",
                "dd-514",
                "dd-516",
                "dd-517",
                "dd-527",
                "dd-548",
                "dd-549",
                "dd-550",
                "dd-551",
                "dd-558",
                "dd-559",
                "dd-562",
                "dd-564",
                "dd-565",
                "dd-570",
                "dd-572",
                "dd-575",
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
                    if source_id in multi_resource_sources:
                        assert len({r["resource_id"] for r in rows}) >= 2, (
                            f"Expected at least 2 distinct resources discovered from list/index for {source_id}"
                        )
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
                        from wlh.engine.objects import Objects

                        objs = Objects()
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
                    if source_id == "dd-575":
                        assert "Bộ Quốc phòng" in (projected["issuer"] or "")
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
