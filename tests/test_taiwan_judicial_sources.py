"""Black-box discovery and extraction contracts for Taiwan Judicial Yuan sources (dd-510 and dd-580)."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from wlh.cleaners.extraction_quality import text_check
from wlh.config import SourceConfig
from wlh.core import digest
from wlh.processor.extractors.router import run_extraction
from wlh.processor.extractors.word.office import unpack_zip
from wlh.processor.parsers import load as load_adapter

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
        language = str(self.config.get("storage", {}).get("metadata", {}).get("language") or "")
        return text_check(markdown, "vi" if language.startswith("vi") else "generic")

    def store(self, data: bytes) -> str:
        ref = digest(data)
        self.stored[ref] = data
        return ref

    def follow_links(self, task: Mapping[str, Any]) -> bool:
        return self.runtime.mode == "collect" and int(task["depth"]) < int(
            self.config["scheduler"]["max_depth"]
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
                "scheduler": {
                    "scope": "中国台湾司法院法学资料检索系统（裁判书查询 judgment.judicial.gov.tw）各级法院民事、刑事、行政及宪政裁判书列表发现、详情正文与 PDF 原件有界采集",
                    "entry_urls": [
                        "https://judgment.judicial.gov.tw/FJUD/qryresult.aspx?jud_court=TPS&jud_year=113&jud_case=%E5%8F%B0%E4%B8%8A&jud_no=1&judtype=JUDBOOK&sys=V"
                    ],
                    "allowed_hosts": ["judgment.judicial.gov.tw"],
                    "allowed_path_prefixes": ["/FJUD/", "/FILES/", "/controls/"],
                    "max_depth": 2,
                    "budget": {
                        "requests": 60,
                        "documents": 10,
                        "seconds": 300,
                        "response_bytes": 67108864,
                    },
                },
                "fetcher": {
                    "mode": "rendered_dom",
                    "robots": False,
                    "delay": 1.2,
                    "retries": 3,
                    "page_timeout_ms": 60000,
                },
                "processor": {
                    "parser": {
                        "type": "html",
                        "documents": {
                            "detail_url_patterns": [
                                r"^https://judgment\.judicial\.gov\.tw/FJUD/data\.aspx\?.*id=[^&]+"
                            ],
                            "detail_body": "always",
                            "attachment_url_pattern": r"^https://judgment\.judicial\.gov\.tw/FILES/.*\.pdf$",
                            "content_xpath": '//div[@id="jud"][normalize-space(.) != ""]',
                            "link_scope_xpath": "//iframe[@id='iframe-data' and @src] | //table[@id='jud']//a[@href]",
                            "next_page_xpath": "//a[@id='hlNext' and @href]",
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
                },
                "cleaners": {},
                "storage": {
                    "archive_approved": True,
                    "metadata": {
                        "source_name": "司法院法学资料检索系统（裁判书查询）",
                        "document_type": "案例",
                        "language": "zh",
                        "jurisdiction": "TW",
                    },
                },
            }
        ).model_dump()
        adapter: Any = load_adapter("html")(config)

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
                "scheduler": {
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
                    "max_depth": 1,
                    "budget": {
                        "requests": 50,
                        "documents": 10,
                        "seconds": 300,
                        "response_bytes": 134217728,
                    },
                },
                "fetcher": {
                    "mode": "http",
                    "robots": False,
                    "delay": 1.0,
                    "retries": 3,
                    "page_timeout_ms": 120000,
                },
                "processor": {
                    "parser": {
                        "type": "json",
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
                    },
                    "extractor": {
                        "quirks": ["zip_salvage"],
                    },
                },
                "cleaners": {},
                "storage": {
                    "archive_approved": True,
                    "metadata": {
                        "source_name": "司法院资料开放平台（Judicial Yuan Open Data API）",
                        "document_type": "案例",
                        "language": "zh",
                        "jurisdiction": "TW",
                    },
                },
            }
        ).model_dump()

    def test_opendata_catalog_json_discovers_open_category_a_datasets_and_skips_unauthenticated_category_b(
        self,
    ) -> None:
        """Parsing OpenData catalog JSON discovers categoryDataset 'A' detail endpoints and skips member-gated 'B' items when unauthenticated."""
        config = self._dd580_config()
        adapter: Any = load_adapter("json")(config)

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

    def test_opendata_catalog_json_pagination_discovers_next_page_enforces_max_pages_and_stops_when_all_items_member_gated(
        self, monkeypatch: Any
    ) -> None:
        """OpenData pagedList catalog JSON discovers next page when open datasets exist and hasNextPage is true, returns page_limit_exceeded at max_pages, and stops paging when a page contains only unauthenticated category B items."""
        monkeypatch.delenv("JUDICIAL_OPENDATA_BEARER_TOKEN", raising=False)
        config = self._dd580_config()
        config["processor"]["parser"]["options"] = {"max_pages": 2}
        adapter: Any = load_adapter("json")(config)

        page1_url = "https://opendata.judicial.gov.tw/api/Datasets?categoryTheme4Sys%5B0%5D=052&sort.publishedDate.order=desc&page=1"
        page2_url = "https://opendata.judicial.gov.tw/api/Datasets?categoryTheme4Sys%5B0%5D=052&sort.publishedDate.order=desc&page=2"
        page3_url = "https://opendata.judicial.gov.tw/api/Datasets?categoryTheme4Sys%5B0%5D=052&sort.publishedDate.order=desc&page=3"

        page1_payload = {
            "pagedList": {
                "totalCount": 30,
                "pageSize": 10,
                "currentPage": 1,
                "lastPage": 3,
                "hasPreviousPage": False,
                "hasNextPage": True,
                "items": [
                    {
                        "datasetId": 24557,
                        "title": "釋字第813號解釋",
                        "categoryDataset": "A",
                        "dataProvider": "司法院",
                        "publishedDate": "2021-12-24T00:00:00+08:00",
                    }
                ],
            }
        }
        page2_payload = {
            "pagedList": {
                "totalCount": 30,
                "pageSize": 10,
                "currentPage": 2,
                "lastPage": 3,
                "hasPreviousPage": True,
                "hasNextPage": True,
                "items": [
                    {
                        "datasetId": 24556,
                        "title": "釋字第812號解釋",
                        "categoryDataset": "A",
                        "dataProvider": "司法院",
                        "publishedDate": "2021-12-10T00:00:00+08:00",
                    }
                ],
            }
        }
        gated_page1_url = "https://opendata.judicial.gov.tw/api/Datasets?categoryTheme4Sys%5B0%5D=051&sort.publishedDate.order=desc&page=1"
        gated_page2_url = "https://opendata.judicial.gov.tw/api/Datasets?categoryTheme4Sys%5B0%5D=051&sort.publishedDate.order=desc&page=2"
        gated_page1_payload = {
            "pagedList": {
                "totalCount": 50,
                "pageSize": 10,
                "currentPage": 1,
                "lastPage": 5,
                "hasPreviousPage": False,
                "hasNextPage": True,
                "items": [
                    {
                        "datasetId": 59372,
                        "title": "202608裁判書--(20261016Update)",
                        "categoryDataset": "B",
                        "dataProvider": "司法院",
                        "publishedDate": "2026-10-16T07:54:53.173+08:00",
                    }
                ],
            }
        }

        page1_bytes = json.dumps(page1_payload, ensure_ascii=False).encode("utf-8")
        page2_bytes = json.dumps(page2_payload, ensure_ascii=False).encode("utf-8")
        gated_page1_bytes = json.dumps(gated_page1_payload, ensure_ascii=False).encode("utf-8")

        ctx = _InMemoryAdapterContext(
            config,
            {
                page1_url: page1_bytes,
                page2_url: page2_bytes,
                gated_page1_url: gated_page1_bytes,
            },
        )

        # Case 1: Page 1 < max_pages (2) and hasNextPage=True with open dataset A -> discovers detail + page 2
        parsed_p1: dict[str, Any] = adapter.parse(
            ctx,
            {"depth": 0, "payload": {"url": page1_url, "role": "page", "depth": 0}},
            _acquisition(page1_url, page1_bytes, mime_type="application/json", acq_id=1),
        )
        assert parsed_p1.get("truncated") is None
        assert {"url": "https://opendata.judicial.gov.tw/api/Datasets/24557", "role": "page", "depth": 1} in parsed_p1["requests"]
        assert {
            "url": page2_url,
            "role": "page",
            "depth": 0,
            "meta": {"page": 2},
        } in parsed_p1["requests"]

        # Case 2: Page 2 == max_pages (2) and hasNextPage=True -> discovers detail, stops page 3, marks truncated
        parsed_p2: dict[str, Any] = adapter.parse(
            ctx,
            {
                "depth": 0,
                "payload": {"url": page2_url, "role": "page", "depth": 0, "meta": {"page": 2}},
            },
            _acquisition(page2_url, page2_bytes, mime_type="application/json", acq_id=2),
        )
        assert parsed_p2.get("truncated") == "page_limit_exceeded"
        p2_urls = [str(req["url"]) for req in parsed_p2["requests"]]
        assert "https://opendata.judicial.gov.tw/api/Datasets/24556" in p2_urls
        assert page3_url not in p2_urls

        # Case 3: Page 1 contains only unauthenticated category B items -> discovers no datasets and does not paginate
        parsed_gated: dict[str, Any] = adapter.parse(
            ctx,
            {"depth": 0, "payload": {"url": gated_page1_url, "role": "page", "depth": 0}},
            _acquisition(gated_page1_url, gated_page1_bytes, mime_type="application/json", acq_id=3),
        )
        gated_urls = [str(req["url"]) for req in parsed_gated["requests"]]
        assert gated_urls == []
        assert gated_page2_url not in gated_urls

    def test_opendata_dataset_detail_json_and_fileset_zip_extract_structured_judgment_and_skip_redundant_ocr_scan_pdf(
        self, tmp_path: Path
    ) -> None:
        """Dataset detail JSON and FilesetLists ZIP extract structured Constitutional Court judgment JSON and native PDF while skipping redundant *_OCR.pdf scan overlays."""
        config = self._dd580_config()
        adapter: Any = load_adapter("json")(config)

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

    def test_opendata_zip_with_json_and_over_fifty_pdf_members_extracts_json_and_config_accepts_large_archive_budgets(
        self, tmp_path: Path
    ) -> None:
        """OpenData mega-dataset ZIP (1 JSON + 57 PDFs exceeding member and byte limits) extracts JSON member cleanly and SourceConfig accepts 512 MiB response_bytes and 30-minute page_timeout_ms."""
        zip_path = tmp_path / "jy_791.zip"
        out_dir = tmp_path / "unpacked"
        json_member_bytes = json.dumps(
            {"title": "釋字第791號", "content": "通姦罪違憲"},
            ensure_ascii=False,
        ).encode("utf-8")
        pdf_payload = b"%PDF-1.4\n" + (b"0123456789abcdef" * 1920)  # > 30 KiB per PDF

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("大法官解釋-釋字第791號.json", json_member_bytes)
            for idx in range(1, 58):
                zf.writestr(f"{idx}_意見書.pdf", pdf_payload)

        members = unpack_zip(zip_path, out_dir, max_bytes=1024 * 1024)
        assert [m["path"] for m in members] == ["大法官解釋-釋字第791號.json"]
        assert members[0]["kind"] == "json"

        raw_cfg = self._dd580_config()
        raw_cfg["scheduler"]["budget"]["response_bytes"] = 536870912
        raw_cfg["fetcher"]["page_timeout_ms"] = 1800000
        validated = SourceConfig.model_validate(raw_cfg)
        assert validated.scheduler.budget.response_bytes == 536870912
        assert validated.fetcher.page_timeout_ms == 1800000

    def test_salvage_truncated_opendata_zip_stream_preserves_crc_verified_json_member_and_builds_valid_zip(
        self, tmp_path: Path
    ) -> None:
        """Truncated OpenData ZIP stream salvages intact CRC-verified prefix entries (including the structured JSON dataset) into a valid ZIP archive."""
        import os

        import pytest

        from wlh.processor.extractors.quirks.zip_salvage import salvage_zip_prefix

        json_bytes = json.dumps(
            {"data": {"interpretNo": "793", "interpretContent": "黨產條例合憲"}},
            ensure_ascii=False,
        ).encode("utf-8")
        pdf1_bytes = b"%PDF-1.4\n" + os.urandom(50 * 1024)
        pdf2_bytes = b"%PDF-1.4\n" + os.urandom(100 * 1024)

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("大法官解釋-釋字第793號.json", json_bytes)
            zf.writestr("1_意見書.pdf", pdf1_bytes)
            zf.writestr("2_聲請書.pdf", pdf2_bytes)
            info3 = zf.getinfo("2_聲請書.pdf")
            cut_offset = info3.header_offset + 64 + (info3.compress_size // 2)

        full_zip_bytes = zip_buf.getvalue()
        truncated_bytes = full_zip_bytes[:cut_offset]

        with pytest.raises(zipfile.BadZipFile):
            zipfile.ZipFile(io.BytesIO(truncated_bytes))

        repaired = salvage_zip_prefix(truncated_bytes)
        with zipfile.ZipFile(io.BytesIO(repaired)) as repaired_zf:
            assert repaired_zf.testzip() is None
            assert repaired_zf.namelist() == ["大法官解釋-釋字第793號.json", "1_意見書.pdf"]

        repaired_path = tmp_path / "repaired.zip"
        repaired_path.write_bytes(repaired)
        members = unpack_zip(repaired_path, tmp_path / "out", max_bytes=10 * 1024 * 1024)
        assert [m["path"] for m in members] == ["大法官解釋-釋字第793號.json"]


