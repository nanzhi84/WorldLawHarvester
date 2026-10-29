"""Black-box discovery and extraction contracts for Taiwan Judicial Yuan sources (dd-510 and dd-580)."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from wlh.adapters import load as load_adapter
from wlh.config import SourceConfig
from wlh.core import digest
from wlh.extraction.process import run_extraction
from wlh.extraction.quality import text_check

from .conftest import make_native_pdf


class _RuntimeStub:
    def __init__(self, mode: str = "collect") -> None:
        self.mode = mode


class _InMemoryAdapterContext:
    """In-memory implementation of the public Adapter Context contract backed by real extraction."""

    def __init__(
        self,
        config: dict[str, Any],
        bodies: Mapping[str, bytes],
        *,
        mode: str = "collect",
    ) -> None:
        self.config = config
        self._bodies = dict(bodies)
        self.runtime = _RuntimeStub(mode=mode)
        self.stored: dict[str, bytes] = {}

    def read(self, acquisition: Mapping[str, Any]) -> bytes:
        ref = str(
            acquisition.get("archive_ref")
            or acquisition.get("actual_url")
            or acquisition.get("request_url")
            or ""
        )
        return self._bodies[ref]

    def extract(self, data: bytes, kind: str) -> dict[str, Any]:
        return run_extraction(data, kind, self.config)

    def text_quality(self, markdown: str) -> dict[str, Any]:
        language = str(self.config["metadata"].get("language") or "")
        return text_check(markdown, "vi" if language.startswith("vi") else "generic")

    def store(self, data: bytes) -> str:
        ref = digest(data)
        self.stored[ref] = data
        return ref

    def follow_links(self, task: Mapping[str, Any]) -> bool:
        return self.runtime.mode == "collect" and int(task["depth"]) < int(
            self.config["fetch"]["max_depth"]
        )


def _acquisition(
    url: str,
    body: bytes,
    *,
    mime_type: str = "text/html",
    charset: str = "utf-8",
    acq_id: int = 1,
) -> dict[str, Any]:
    return {
        "id": acq_id,
        "state": "archived",
        "outcome": "ok",
        "request_url": url,
        "actual_url": url,
        "archive_ref": url,
        "mime_type": mime_type,
        "charset": charset,
        "byte_size": len(body),
    }


def _make_hybrid_native_and_image_pdf(path: Path, native_text: str) -> bytes:
    """Create a PDF with both a substantial native text layer and an embedded full-page image."""
    import fitz  # type: ignore[import-untyped]

    fitz_mod: Any = fitz
    img_doc = fitz_mod.open()
    img_page = img_doc.new_page(width=595, height=842)
    img_page.insert_textbox(
        fitz_mod.Rect(40, 60, 555, 780),
        "CONSTITUTIONAL COURT JUDGMENT SCAN LAYER\nFull page rasterized scan image with ink.",
        fontsize=20,
        fontname="helv",
    )
    png_bytes = img_page.get_pixmap(dpi=120).tobytes("png")
    img_doc.close()

    doc = fitz_mod.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(fitz_mod.Rect(20, 20, 575, 822), stream=png_bytes)
    page.insert_textbox(
        fitz_mod.Rect(30, 30, 565, 800),
        native_text,
        fontsize=11,
        fontname="helv",
    )
    doc.save(str(path))
    doc.close()
    return path.read_bytes()


class TestTaiwanJudicialJudgmentDiscoveryAndExtraction:
    def test_search_wrapper_iframe_and_result_list_discover_judgment_detail_and_pdf_attachment(
        self, tmp_path: Path
    ) -> None:
        """dd-510 search wrapper iframe (depth 0 -> 1) and result list (depth 1 -> 2) discover judgment detail and official PDF attachment with enriched non-generic title."""
        config = SourceConfig.model_validate(
            {
                "id": "dd-510",
                "adapter": "web",
                "scope": "中国台湾司法院法学资料检索系统（裁判书查询 judgment.judicial.gov.tw）各级法院民事、刑事、行政及宪政裁判书列表发现、详情正文与 PDF 原件有界采集",
                "entry_urls": [
                    "https://judgment.judicial.gov.tw/FJUD/qryresult.aspx?jud_court=TPS&jud_year=113&jud_case=%E5%8F%B0%E4%B8%8A&jud_no=1&judtype=JUDBOOK&sys=V"
                ],
                "allowed_hosts": ["judgment.judicial.gov.tw"],
                "allowed_path_prefixes": ["/FJUD/", "/FILES/", "/controls/"],
                "archive_approved": True,
                "robots": False,
                "delay": 1.2,
                "fetch": {
                    "mode": "rendered_dom",
                    "max_depth": 2,
                    "retries": 3,
                    "page_timeout_ms": 60000,
                },
                "budget": {
                    "requests": 60,
                    "documents": 10,
                    "seconds": 300,
                    "response_bytes": 67108864,
                },
                "metadata": {
                    "source_name": "司法院法学资料检索系统（裁判书查询）",
                    "document_type": "案例",
                    "language": "zh",
                    "jurisdiction": "TW",
                },
                "documents": {
                    "detail_url_patterns": [
                        r"^https://judgment\.judicial\.gov\.tw/FJUD/data\.aspx\?.*id=[^&]+"
                    ],
                    "detail_body": "always",
                    "attachment_url_pattern": r"^https://judgment\.judicial\.gov\.tw/FILES/.*\.pdf$",
                    "content_xpath": '//div[@id="jud"][normalize-space(.) != ""]',
                    "fields": {
                        "title": {
                            "xpath": '//div[@id="jud"]//div[contains(@class,"row")][div[contains(@class,"col-th") and contains(.,"裁判字號")]]/div[contains(@class,"col-td")]'
                        },
                        "document_number": {
                            "xpath": '//div[@id="jud"]//div[contains(@class,"row")][div[contains(@class,"col-th") and contains(.,"裁判字號")]]/div[contains(@class,"col-td")]',
                            "regex": r"(\d+\s*年(?:度)?\s*[^\s]+?字\s*第\s*[^\s]+?\s*號)",
                        },
                        "published_at": {
                            "xpath": '//div[@id="jud"]//div[contains(@class,"row")][div[contains(@class,"col-th") and contains(.,"裁判日期")]]/div[contains(@class,"col-td")]'
                        },
                        "issuer": {
                            "xpath": '//div[@id="jud"]//div[contains(@class,"row")][div[contains(@class,"col-th") and contains(.,"裁判字號")]]/div[contains(@class,"col-td")]',
                            "regex": r"^([^\s]+法院(?:[^\s]*分院)?|憲法法庭|懲戒法院)",
                        },
                    },
                },
            }
        ).model_dump()
        adapter: Any = load_adapter("web")(config)

        search_url = "https://judgment.judicial.gov.tw/FJUD/qryresult.aspx?jud_court=TPS&jud_year=113&jud_case=%E5%8F%B0%E4%B8%8A&jud_no=1&judtype=JUDBOOK&sys=V"
        list_url = "https://judgment.judicial.gov.tw/FJUD/qryresultlst.aspx?ty=JUDBOOK&q=abc123"
        detail_url = "https://judgment.judicial.gov.tw/FJUD/data.aspx?ty=JD&id=TPSV%2c113%2c%e5%8f%b0%e4%b8%8a%2c1%2c20250826%2c1&ot=in"
        pdf_url = "https://judgment.judicial.gov.tw/FILES/TPSV/113%2c%e5%8f%b0%e4%b8%8a%2c1%2c20250826%2c1.pdf"

        search_html = (
            "<html><head><meta charset='utf-8'><title>查詢結果</title></head>"
            "<body>"
            '<iframe id="iframe-data" src="qryresultlst.aspx?ty=JUDBOOK&amp;q=abc123"></iframe>'
            "</body></html>"
        ).encode("utf-8")

        list_html = (
            "<html><head><meta charset='utf-8'><title>裁判書列表</title></head>"
            "<body><table id='jud' class='jub-table'><tr><td>"
            '<a class="hlTitle_scroll" href="data.aspx?ty=JD&amp;id=TPSV%2c113%2c%e5%8f%b0%e4%b8%8a%2c1%2c20250826%2c1&amp;ot=in">113年度台上字第1號</a>'
            "</td></tr></table></body></html>"
        ).encode("utf-8")

        detail_html = (
            "<html><head><meta charset='utf-8'><title>最高法院 113 年度台上字第 1 號民事判決</title></head>"
            "<body>"
            '<div id="jud" class="container-fluid">'
            '<div class="row"><div class="col-th">裁判字號：</div><div class="col-td">最高法院 113 年度台上字第 1 號民事判決</div></div>'
            '<div class="row"><div class="col-th">裁判日期：</div><div class="col-td">民國 114 年 08 月 26 日</div></div>'
            '<div class="row"><div class="col-th">裁判案由：</div><div class="col-td">侵害商標權有關財產權爭議等</div></div>'
            '<div class="jud_content">'
            "<p>主文：原判決廢棄，發回智慧財產及商業法院。</p>"
            "<p>理由：本件上訴人主張被上訴人未經同意使用相同或近似於其註冊商標之圖樣於同一或類似商品，構成商標法第68條第1項之侵害商標權行為，爰依商標法第69條第1項及第3項規定請求排除侵害並賠償損害。</p>"
            "</div>"
            "</div>"
            '<div class="panel-body">'
            '<a id="hlExportPDF" href="/FILES/TPSV/113%2c%e5%8f%b0%e4%b8%8a%2c1%2c20250826%2c1.pdf">轉存PDF</a>'
            "</div>"
            "</body></html>"
        ).encode("utf-8")

        pdf_bytes = make_native_pdf(
            tmp_path / "judgment.pdf",
            [
                "Supreme Court Civil Judgment 113-Tai-Shang-1\n"
                "Official PDF full text of the Supreme Court judgment regarding trademark infringement dispute."
            ],
        ).read_bytes()

        ctx = _InMemoryAdapterContext(
            config,
            {
                search_url: search_html,
                list_url: list_html,
                detail_url: detail_html,
                pdf_url: pdf_bytes,
            },
        )

        # Step 1: depth=0 wrapper page discovers iframe result list at depth=1
        parsed_wrapper: dict[str, Any] = adapter.parse(
            ctx,
            {"depth": 0, "payload": {"url": search_url, "role": "page", "depth": 0}},
            _acquisition(search_url, search_html, acq_id=1),
        )
        assert parsed_wrapper["targets"] == []
        assert parsed_wrapper["requests"] == [
            {"url": list_url, "role": "page", "depth": 1}
        ]

        # Step 2: depth=1 result list discovers detail page at depth=2
        parsed_list: dict[str, Any] = adapter.parse(
            ctx,
            {"depth": 1, "payload": {"url": list_url, "role": "page", "depth": 1}},
            _acquisition(list_url, list_html, acq_id=2),
        )
        assert parsed_list["targets"] == []
        assert parsed_list["requests"] == [
            {"url": detail_url, "role": "page", "depth": 2}
        ]

        # Step 3: depth=2 detail page produces target with detail + PDF attachment inputs
        parsed_detail: dict[str, Any] = adapter.parse(
            ctx,
            {"depth": 2, "payload": {"url": detail_url, "role": "page", "depth": 2}},
            _acquisition(detail_url, detail_html, acq_id=3),
        )
        assert len(parsed_detail["targets"]) == 1
        target: dict[str, Any] = parsed_detail["targets"][0]
        assert len(target["inputs"]) == 2
        assert target["inputs"][0]["role"] == "detail"
        assert target["inputs"][0]["request"]["url"] == detail_url
        assert target["inputs"][1]["role"] == "attachment"
        assert target["inputs"][1]["request"]["url"] == pdf_url

        # Step 4: building target extracts metadata and both deliverables with enriched PDF title
        built_candidates: list[dict[str, Any]] = adapter.build(
            ctx,
            target,
            [
                {
                    **target["inputs"][0],
                    "acquisition": _acquisition(detail_url, detail_html, acq_id=3),
                },
                {
                    **target["inputs"][1],
                    "acquisition": _acquisition(
                        pdf_url, pdf_bytes, mime_type="application/pdf", acq_id=4
                    ),
                },
            ],
        )
        assert len(built_candidates) == 1
        candidate: dict[str, Any] = built_candidates[0]
        assert candidate["metadata"]["title"] == "最高法院 113 年度台上字第 1 號民事判決"
        assert candidate["metadata"]["document_number"] == "113 年度台上字第 1 號"
        assert candidate["dates"]["published_at"] == "2025-08-26"
        assert candidate["metadata"]["issuer"] == "最高法院"

        deliverables: list[dict[str, Any]] = candidate["deliverables"]
        assert len(deliverables) == 2
        assert deliverables[0]["role"] == "primary"
        assert "侵害商標權有關財產權爭議等" in deliverables[0]["markdown"]
        assert deliverables[1]["role"] == "independent_attachment"
        assert deliverables[1]["title"] != "轉存PDF"
        assert "最高法院 113 年度台上字第 1 號民事判決" in (deliverables[1]["title"] or "")


class TestTaiwanJudicialOpenDataApiAndZipExtraction:
    @staticmethod
    def _dd580_config() -> dict[str, Any]:
        return SourceConfig.model_validate(
            {
                "id": "dd-580",
                "adapter": "web",
                "scope": "中国台湾司法院资料开放平台（opendata.judicial.gov.tw/api/）宪法法庭判决（053）、大法官解释（052）及各级法院裁判书（051）开放资料集 API 枚举及 FilesetLists 压缩包归档",
                "entry_urls": [
                    "https://opendata.judicial.gov.tw/api/Datasets?categoryTheme4Sys%5B0%5D=053&sort.publishedDate.order=desc&page=1"
                ],
                "allowed_hosts": ["opendata.judicial.gov.tw"],
                "allowed_path_prefixes": [
                    "/api/Datasets",
                    "/api/FilesetLists",
                    "/data/api/rest/categories",
                ],
                "archive_approved": True,
                "robots": False,
                "delay": 1.0,
                "fetch": {
                    "mode": "http",
                    "max_depth": 1,
                    "retries": 3,
                    "page_timeout_ms": 120000,
                },
                "budget": {
                    "requests": 50,
                    "documents": 10,
                    "seconds": 300,
                    "response_bytes": 134217728,
                },
                "metadata": {
                    "source_name": "司法院资料开放平台（Judicial Yuan Open Data API）",
                    "document_type": "案例",
                    "language": "zh",
                    "jurisdiction": "TW",
                },
                "documents": {
                    "detail_url_patterns": [
                        r"^https://opendata\.judicial\.gov\.tw/api/Datasets/\d+$"
                    ],
                    "attachment_url_pattern": r"^https://opendata\.judicial\.gov\.tw/api/FilesetLists/\d+/file$",
                    "detail_body": "always",
                    "fields": {
                        "title": {"regex": r'"title"\s*:\s*"([^"]+)"'},
                        "document_number": {"regex": r'"datasetId"\s*:\s*(\d+)'},
                        "published_at": {
                            "regex": r'"publishedDate"\s*:\s*"(\d{4}-\d{2}-\d{2})'
                        },
                        "issuer": {"regex": r'"dataProvider"\s*:\s*"([^"]+)"'},
                    },
                },
            }
        ).model_dump()

    def test_opendata_catalog_json_discovers_open_category_a_datasets_and_skips_unauthenticated_category_b(
        self,
    ) -> None:
        """Parsing OpenData catalog JSON discovers categoryDataset 'A' detail endpoints and skips member-gated 'B' items when unauthenticated."""
        config = self._dd580_config()
        adapter: Any = load_adapter("web")(config)

        catalog_url = "https://opendata.judicial.gov.tw/api/Datasets?categoryTheme4Sys%5B0%5D=053&sort.publishedDate.order=desc&page=1"
        catalog_payload = {
            "pagedList": {
                "total": 2,
                "page": 1,
                "pageSize": 10,
                "lastPage": 1,
                "items": [
                    {
                        "datasetId": 58978,
                        "title": "111年憲判字第10號",
                        "categoryDataset": "A",
                        "dataProvider": "司法院",
                        "publishedDate": "2026-10-02T00:00:00+08:00",
                        "filesetLists": [
                            {
                                "fileSetId": 71543,
                                "resourceDescription": "111年憲判字第10號",
                                "resourceFormat": "ZIP",
                            }
                        ],
                    },
                    {
                        "datasetId": 59372,
                        "title": "202608裁判書--(20261016Update)",
                        "categoryDataset": "B",
                        "dataProvider": "司法院",
                        "publishedDate": "2026-10-16T07:54:53.173+08:00",
                        "filesetLists": [
                            {
                                "fileSetId": 72011,
                                "resourceDescription": "202608裁判書",
                                "resourceFormat": "RAR",
                            }
                        ],
                    },
                ],
            }
        }
        catalog_bytes = json.dumps(catalog_payload, ensure_ascii=False).encode("utf-8")
        ctx = _InMemoryAdapterContext(config, {catalog_url: catalog_bytes})

        parsed: dict[str, Any] = adapter.parse(
            ctx,
            {"depth": 0, "payload": {"url": catalog_url, "role": "page", "depth": 0}},
            _acquisition(
                catalog_url,
                catalog_bytes,
                mime_type="application/json",
                acq_id=1,
            ),
        )
        requests: list[dict[str, Any]] = parsed["requests"]
        discovered_urls: list[str] = [str(req["url"]) for req in requests]
        assert "https://opendata.judicial.gov.tw/api/Datasets/58978" in discovered_urls
        assert "https://opendata.judicial.gov.tw/api/Datasets/59372" not in discovered_urls

    def test_opendata_dataset_detail_json_and_fileset_zip_extract_structured_judgment_and_skip_redundant_ocr_scan_pdf(
        self, tmp_path: Path
    ) -> None:
        """Dataset detail JSON and FilesetLists ZIP extract structured Constitutional Court judgment JSON and native PDF while skipping redundant *_OCR.pdf scan overlays."""
        config = self._dd580_config()
        adapter: Any = load_adapter("web")(config)

        detail_url = "https://opendata.judicial.gov.tw/api/Datasets/58978"
        fileset_url = "https://opendata.judicial.gov.tw/api/FilesetLists/71543/file"

        detail_payload = {
            "dataset": {
                "datasetId": 58978,
                "title": "111年憲判字第10號",
                "dataProvider": "司法院",
                "publishedDate": "2026-10-02T00:00:00+08:00",
                "filesetLists": [
                    {
                        "fileSetId": 71543,
                        "resourceDescription": "111年憲判字第10號",
                        "resourceFormat": "ZIP",
                    }
                ],
            }
        }
        detail_bytes = json.dumps(detail_payload, ensure_ascii=False).encode("utf-8")

        judgment_json_bytes = json.dumps(
            {
                "data": {
                    "ruling_name": "警消人員獎懲累積達二大過免職案",
                    "ruling_content": "警察人員人事條例第三十一條第一項第十一款規定違憲。",
                    "ruling_reason": "本件聲請人因免職事件聲請憲法審查，本庭判決理由如下：憲法第十八條保障人民服公職之權利，旨在保障人民有依法令從事於公務之權利。",
                }
            },
            ensure_ascii=False,
        ).encode("utf-8")

        native_pdf_bytes = make_native_pdf(
            tmp_path / "1_111年憲判字第10號判決主文立場表.pdf",
            [
                "Constitutional Court Judgment 111-Hsien-Pan-10 Voting Table\n"
                "Summary of Justices' concurrences and dissents on the police disciplinary dismissal provision."
            ],
        ).read_bytes()

        ocr_pdf_bytes = _make_hybrid_native_and_image_pdf(
            tmp_path / "2_憲法法庭111年憲判字第10號判決_OCR.pdf",
            (
                "Constitutional Court Judgment 111-Hsien-Pan-10 OCR Layer. "
                "This PDF contains over four hundred characters of selectable text layered on top of "
                "a full-page raster scan image, which would trigger pdf_images_unverified if not skipped "
                "as a redundant _OCR.pdf companion in the Judicial Yuan Open Data ZIP archive. "
            )
            * 3,
        )

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("憲法法庭判決-111年憲判字第10號.json", judgment_json_bytes)
            zf.writestr("1_111年憲判字第10號判決主文立場表.pdf", native_pdf_bytes)
            zf.writestr("2_憲法法庭111年憲判字第10號判決_OCR.pdf", ocr_pdf_bytes)
        zip_bytes = zip_buf.getvalue()

        ctx = _InMemoryAdapterContext(
            config,
            {
                detail_url: detail_bytes,
                fileset_url: zip_bytes,
            },
        )

        parsed: dict[str, Any] = adapter.parse(
            ctx,
            {"depth": 1, "payload": {"url": detail_url, "role": "page", "depth": 1}},
            _acquisition(
                detail_url,
                detail_bytes,
                mime_type="application/json",
                acq_id=10,
            ),
        )
        assert len(parsed["targets"]) == 1
        target: dict[str, Any] = parsed["targets"][0]
        assert len(target["inputs"]) == 2
        assert target["inputs"][0]["role"] == "detail"
        assert target["inputs"][0]["request"]["url"] == detail_url
        assert target["inputs"][1]["role"] == "attachment"
        assert target["inputs"][1]["request"]["url"] == fileset_url

        built_candidates: list[dict[str, Any]] = adapter.build(
            ctx,
            target,
            [
                {
                    **target["inputs"][0],
                    "acquisition": _acquisition(
                        detail_url,
                        detail_bytes,
                        mime_type="application/json",
                        acq_id=10,
                    ),
                },
                {
                    **target["inputs"][1],
                    "acquisition": _acquisition(
                        fileset_url,
                        zip_bytes,
                        mime_type="application/zip",
                        acq_id=11,
                    ),
                },
            ],
        )
        assert len(built_candidates) == 1
        candidate: dict[str, Any] = built_candidates[0]
        assert candidate["metadata"]["title"] == "111年憲判字第10號"
        assert candidate["metadata"]["document_number"] == "58978"
        assert candidate["dates"]["published_at"] == "2026-10-02"
        assert candidate["metadata"]["issuer"] == "司法院"

        deliverables: list[dict[str, Any]] = candidate["deliverables"]
        zip_deliverables: list[dict[str, Any]] = [
            d
            for d in deliverables
            if cast(dict[str, Any], d.get("original") or {}).get("kind") == "zip"
        ]
        assert len(zip_deliverables) == 1
        zip_markdown = str(zip_deliverables[0]["markdown"])
        assert "警消人員獎懲累積達二大過免職案" in zip_markdown
        assert "警察人員人事條例" in zip_markdown
