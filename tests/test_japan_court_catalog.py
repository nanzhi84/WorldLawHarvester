"""Public Japanese case search: finite recorded catalog, complete real judgments.

Catalog projection is deliberately four cases/three pages, not the live 67,890
case population. Complete original details and PDF bytes are unchanged.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Generator
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import fitz as pdf_library  # type: ignore[import-untyped]
import pytest
import yaml
from lxml import html as html_library  # type: ignore[import-untyped]

from .source_replay_support import (
    ROOT,
    assert_offline_reparse,
    collect_artifact,
    normalized,
    rendered_table_rows,
)

# These untyped libraries inspect fixture originals, never the application parser.
pdf: Any = pdf_library
html: Any = html_library
LEGACY = "/app/hanrei_jp/search1"
SEARCH = "/hanrei/search1/index.html"
FIXTURES = ROOT / "tests/fixtures/dd-519"
CASES = {
    "97209": ("courts-case.pdf", "2026-10-26", "令和8(行ツ)63"),
    "22226": ("courts-22226.pdf", "1947-09-15", "昭和22(オ)25"),
    "13034": ("courts-13034.pdf", "2000-07-28", "平成11(ネ)3237"),
    "15796": ("courts-15796.pdf", "2000-07-28", "平成11(行ウ)9"),
}


@contextmanager
def _court_server(fault: str = "") -> Generator[tuple[str, list[str]]]:
    if not (FIXTURES / "scope-manifest.json").is_file():
        pytest.skip("Private fixture directory tests/fixtures/dd-519 not present")
    manifest = json.loads((FIXTURES / "scope-manifest.json").read_text())
    routes = {r["path"]: ((FIXTURES / r["fixture"]).read_bytes(), r["content_type"]) for r in manifest["routes"]}
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            requests.append(self.path)
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            origin = f"http://127.0.0.1:{self.server.server_port}"  # type: ignore[attr-defined]
            if parts.path == LEGACY:
                self.send_response(301)
                self.send_header("Location", origin + SEARCH)
                self.end_headers()
                return
            route = parts.path
            if route == SEARCH and query.get("filter[judgeDateMode]") == ["2"]:
                route += "?offset=" + query.get("offset", ["0"])[0]
            found = routes.get(route)
            if fault == "missing_pdf" and parts.path.endswith("hanrei-pdf-97209.pdf"):
                found = None
            if found is None:
                self.send_response(404)
                self.end_headers()
                return
            payload, media = found
            if "text/html" in media:
                if (fault == "missing_next" and route.endswith("?offset=0")) or (
                    fault in {"repeated_offset", "range_mismatch", "duplicate_case"} and route.endswith("?offset=1")
                ):
                    doc = html.fromstring(payload.decode("utf-8"))
                    for anchor in doc.xpath('//a[contains(@href,"change_offset")][contains(.,"次へ")]'):
                        if fault == "missing_next":
                            anchor.getparent().remove(anchor)
                        elif fault == "repeated_offset":
                            anchor.set("href", "javascript:change_offset(1)")
                    if fault == "range_mismatch":
                        for p in doc.xpath('//p[contains(.,"4件中 2～3件を表示")]'):
                            p.text = "4件中 2～4件を表示"
                    if fault == "duplicate_case":
                        first = html.fromstring(routes[SEARCH + "?offset=0"][0].decode("utf-8"))
                        duplicate = first.xpath('//table[contains(@class,"search-result-table")]//tr')[0]
                        replaced = doc.xpath('//table[contains(@class,"search-result-table")]//tr')[0]
                        replaced.getparent().replace(replaced, deepcopy(duplicate))
                    payload = html.tostring(doc, encoding="utf-8", method="html")
                payload = payload.replace(b"https://www.courts.go.jp", origin.encode())
            self.send_response(200)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _config(tmp_path: Path, origin: str, *, request_budget: int = 40, adapter: str = "japan_courts") -> Path:
    if adapter == "web":
        config: dict[str, Any] = {
            "id": "dd-519",
            "scheduler": {
                "scope": "Four-case recorded official search protocol projection; full original judgments; not the entire published case population",
                "entry_urls": [origin + "/assets/hanrei/hanrei-pdf-97209.pdf"],
                "allowed_hosts": ["127.0.0.1"],
                "allowed_path_prefixes": ["/app/hanrei_jp/", "/hanrei/", "/assets/hanrei/"],
                "max_depth": 2,
                "budget": {"requests": request_budget, "documents": 70000, "seconds": 86400},
            },
            "fetcher": {"mode": "http", "delay": 0.0, "retries": 0, "robots": True},
            "processor": {
                "parser": {
                    "type": "html",
                    "documents": {"detail_url_patterns": [r"\.pdf$"]},
                }
            },
            "cleaners": {"require_complete_text": True},
            "storage": {
                "archive_approved": True,
                "metadata": {
                    "source_name": "裁判所 裁判例検索",
                    "document_type": "案例",
                    "language": "ja",
                    "jurisdiction": "JP",
                },
            },
        }
    else:
        config = yaml.safe_load((ROOT / "sources/dd-519.yaml").read_text())
        config["scheduler"]["scope"] = (
            "Four-case recorded official search protocol projection; full original judgments; not the entire published case population"
        )
        config["scheduler"]["entry_urls"] = [origin + LEGACY]
        config["scheduler"]["allowed_hosts"] = ["127.0.0.1"]
        config["scheduler"]["allowed_path_prefixes"] = [
            "/app/hanrei_jp/",
            "/hanrei/",
            "/assets/hanrei/",
        ]
        config["scheduler"]["max_depth"] = 2
        config["scheduler"]["budget"] = {
            "requests": request_budget,
            "documents": 70000,
            "seconds": 86400,
        }
        config["fetcher"]["delay"] = 0.0
        config["fetcher"]["retries"] = 0
        config["fetcher"]["robots"] = True
    path = tmp_path / "dd-519.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    return path


def _build_supreme_court_pdf() -> bytes:
    doc = pdf.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((285, 785), "- 1 -", fontname="japan", fontsize=11)
    p1.insert_text((108, 195), "主 文", fontname="japan", fontsize=12)
    p1.insert_text((96, 220), "本件上告を棄却する。上告費用は上告人の負担とする。", fontname="japan", fontsize=12)
    p1.draw_rect(pdf.Rect(85, 99, 510, 152), color=(0, 0, 0), width=1)
    p1.insert_text((96, 118), "令和６年（行ヒ）第３３９号 不当利得返還請求事件", fontname="japan", fontsize=11)
    p1.insert_text((96, 140), "令和８年１０月２３日 第二小法廷判決", fontname="japan", fontsize=11)
    data = bytes(doc.tobytes())
    doc.close()
    return data


def _build_ip_high_court_pdf() -> bytes:
    doc = pdf.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((90, 100), "令和7年（ネ）第10052号 特許権侵害差止請求控訴事件", fontname="japan", fontsize=11)
    parties = [("控訴人", "関ケ原町"), ("被控訴人", "東京特許"), ("代表者", "山田太郎"), ("代理人", "池田智洋")]
    for idx, (label, name) in enumerate(parties):
        y = 140 + idx * 24
        for i, ch in enumerate(label):
            p1.insert_text((90 + i * 36, y), ch, fontname="japan", fontsize=11)
        for j, ch in enumerate(name):
            p1.insert_text((260 + j * 36, y), ch, fontname="japan", fontsize=11)
    p1.insert_text((90, 260), "主文：本件控訴を棄却する。控訴費用は控訴人の負担とする。", fontname="japan", fontsize=11)
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((90, 100), "別紙 地形区分一覧表", fontname="japan", fontsize=11)
    p2.insert_text((90, 122), "上記一覧表のとおり、本件各土地は地形区分５の麓山地に該当するものと認められる。", fontname="japan", fontsize=11)
    for x in (75.0, 108.5, 240.0):
        p2.draw_line(pdf.Point(x, 140), pdf.Point(x, 200), color=(0, 0, 0), width=0.5)
    for y_line in (140.0, 170.0, 200.0):
        p2.draw_line(pdf.Point(75, y_line), pdf.Point(240, y_line), color=(0, 0, 0), width=0.5)
    p2.insert_text((82, 160), "番号", fontname="japan", fontsize=10)
    p2.insert_text((120, 160), "地形区分", fontname="japan", fontsize=10)
    p2.insert_text((90.72, 190), "５", fontname="japan", fontsize=10.5)
    p2.insert_text((102.96, 190), " ", fontname="helv", fontsize=10.5)
    p2.insert_text((113.88, 190), "麓山地", fontname="japan", fontsize=10.5)
    data = bytes(doc.tobytes())
    doc.close()
    return data


@contextmanager
def _regional_layout_server(
    live_total_increment: bool = False, *, thirty_row_pages: bool = False
) -> Generator[tuple[str, list[str]]]:
    sc_pdf = _build_supreme_court_pdf()
    ip_pdf = _build_ip_high_court_pdf()
    offset1_html = (
        "<html><body><main><p>3件中 2～3件を表示</p>"
        '<table class="search-result-table"><tbody>'
        '<tr><th><a href="/hanrei/97204/detail2/index.html">最高裁判例</a></th></tr>'
        '<tr><th><a href="/hanrei/97192/detail8/index.html">知的財産裁判例</a></th></tr>'
        "</tbody></table></main></body></html>".encode()
        if live_total_increment
        else (
            "<html><body><main><p>2件中 2～2件を表示</p>"
            '<table class="search-result-table"><tbody><tr><th>'
            '<a href="/hanrei/97192/detail8/index.html">知的財産裁判例</a>'
            "</th></tr></tbody></table></main></body></html>".encode()
        )
    )
    routes: dict[str, tuple[bytes, str]] = {
        "/robots.txt": (b"User-agent: *\nAllow: /\n", "text/plain"),
        SEARCH: (
            b'<html><body><form name="hanrei-search" method="GET" action="/hanrei/search1/index.html">'
            b'<input type="hidden" name="filter[judgeDateMode]" value="2"></form></body></html>',
            "text/html;charset=UTF-8",
        ),
        f"{SEARCH}?offset=0": (
            "<html><body><main><p>2件中 1～1件を表示</p>"
            '<table class="search-result-table"><tbody><tr><th>'
            '<a href="/hanrei/97204/detail2/index.html">最高裁判例</a>'
            '</th></tr></tbody></table><a href="javascript:change_offset(1)">次へ</a></main></body></html>'.encode(),
            "text/html;charset=UTF-8",
        ),
        f"{SEARCH}?offset=1": (offset1_html, "text/html;charset=UTF-8"),
        "/hanrei/97204/detail2/index.html": (
            "<html><body><main>"
            "<dl><dt>事件番号</dt><dd>令和6(行ヒ)339</dd></dl>"
            "<dl><dt>事件名</dt><dd>不当利得返還請求事件</dd></dl>"
            "<dl><dt>裁判年月日</dt><dd>令和8年10月23日</dd></dl>"
            "<dl><dt>法廷名</dt><dd>最高裁判所第二小法廷</dd></dl>"
            "<dl><dt>結果</dt><dd>棄却</dd></dl>"
            '<dl><dt>全文</dt><dd><a href="/saikosai/vc-files/saikosai/attachmentfile/hanrei-pdf-97204.pdf">全文</a></dd></dl>'
            "</main></body></html>".encode(),
            "text/html;charset=UTF-8",
        ),
        "/hanrei/97192/detail8/index.html": (
            "<html><body><main>"
            "<dl><dt>事件番号</dt><dd>令和7(ネ)10052</dd></dl>"
            "<dl><dt>事件名</dt><dd>特許権侵害差止請求控訴事件</dd></dl>"
            "<dl><dt>裁判年月日</dt><dd>令和8年10月15日</dd></dl>"
            "<dl><dt>裁判所名</dt><dd>知的財産高等裁判所</dd></dl>"
            "<dl><dt>部名</dt><dd>第２部</dd></dl>"
            "<dl><dt>判決結果</dt><dd>控訴棄却</dd></dl>"
            '<dl><dt>全文</dt><dd><a href="/ip/vc-files/ip/attachmentfile/hanrei-pdf-97192.pdf">全文</a></dd></dl>'
            "</main></body></html>".encode(),
            "text/html;charset=UTF-8",
        ),
        "/saikosai/vc-files/saikosai/attachmentfile/hanrei-pdf-97204.pdf": (sc_pdf, "application/pdf"),
        "/ip/vc-files/ip/attachmentfile/hanrei-pdf-97192.pdf": (ip_pdf, "application/pdf"),
    }
    if thirty_row_pages:
        for off, start, end, next_off in ((0, 1, 30, 30), (30, 31, 60, 60), (60, 61, 61, None)):
            trs = "".join(
                f'<tr><th><a href="/hanrei/{90000 + i}/detail2/index.html">最高裁判例</a></th></tr>'
                for i in range(start, end + 1)
            )
            nxt = f'<a href="javascript:change_offset({next_off})">次へ</a>' if next_off else ""
            routes[f"{SEARCH}?offset={off}"] = (
                f"<html><body><main><p>61件中 {start}～{end}件を表示</p>"
                f'<table class="search-result-table"><tbody>{trs}</tbody></table>{nxt}</main></body></html>'.encode(),
                "text/html;charset=UTF-8",
            )
        for i in range(1, 62):
            routes[f"/hanrei/{90000 + i}/detail2/index.html"] = (
                f"<html><body><main><dl><dt>事件番号</dt><dd>令和6(行ヒ){i}</dd></dl>"
                "<dl><dt>事件名</dt><dd>不当利得返還請求事件</dd></dl>"
                "<dl><dt>裁判年月日</dt><dd>令和8年10月23日</dd></dl>"
                "<dl><dt>法廷名</dt><dd>最高裁判所第二小法廷</dd></dl><dl><dt>結果</dt><dd>棄却</dd></dl>"
                '<dl><dt>全文</dt><dd><a href="/saikosai/vc-files/saikosai/attachmentfile/hanrei-pdf-97204.pdf">全文</a></dd></dl>'
                "</main></body></html>".encode(),
                "text/html;charset=UTF-8",
            )
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            requests.append(self.path)
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            origin = f"http://127.0.0.1:{self.server.server_port}"  # type: ignore[attr-defined]
            if parts.path == LEGACY:
                self.send_response(301)
                self.send_header("Location", origin + SEARCH)
                self.end_headers()
                return
            route = parts.path
            if route == SEARCH and query.get("filter[judgeDateMode]") == ["2"]:
                route += "?offset=" + query.get("offset", ["0"])[0]
            found = routes.get(route)
            if found is None:
                self.send_response(404)
                self.end_headers()
                return
            payload, media = found
            self.send_response(200)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class TestJapanCourtCatalog:
    def test_unfiltered_paginated_search_delivers_each_case_once_with_complete_judgment(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """A three-page cursor chain exceeds ordinary directory depth but remains complete."""
        name = "japan-court-catalog"
        with _court_server() as (origin, requests):
            config = _config(tmp_path, origin)
            report, rows = collect_artifact("dd-519", config, initialized_db, name, requests)
            assert LEGACY in requests and SEARCH in requests, requests
            catalogs = [parse_qs(urlsplit(p).query) for p in requests if urlsplit(p).path == SEARCH]
            searched = [q for q in catalogs if q.get("filter[judgeDateMode]") == ["2"]]
            assert {q.get("offset", ["0"])[0] for q in searched} == {"0", "1", "3"}, requests
            assert all(not values or key in {"filter[judgeDateMode]", "offset", "sort"}
                       for q in searched for key, values in q.items()), "The search must not add court, category, date, or keyword restrictions"
            assert len(rows) == 4 and len({r["resource_id"] for r in rows}) == 4, report
            by_hash = {r["original_hash"]: r for r in rows}
            for case_id, (filename, date, number) in CASES.items():
                payload = (FIXTURES / filename).read_bytes()
                row = by_hash[hashlib.sha256(payload).hexdigest()]
                assert normalized(number) in normalized(row["doc_number"] or "")
                assert str(row["published_at"]) == date
                assert row["effective_at"] is None
                assert "/" + case_id + "/" in row["detail_url"]
                assert "\ufffd" not in row["markdown"] and "サイトマップ" not in row["markdown"]
                with pdf.open(stream=payload, filetype="pdf") as original:
                    for page in original:
                        substantive = [normalized(block[4]) for block in page.get_text("blocks")
                                       if len(normalized(block[4])) >= 20]
                        assert substantive, (case_id, page.number + 1, "A nonempty judgment page must actually be checked")
                        for text in substantive:
                            assert text in normalized(row["markdown"]), (case_id, text[:120])
            ip_case = next(r for r in rows if "/13034/" in r["detail_url"])
            assert normalized("平成11(ネ)3237") in normalized(ip_case["title"])
            assert report["result"]["status"] == "complete", report
        assert_offline_reparse(config, initialized_db, name)

    @pytest.mark.parametrize("fault", ["missing_next", "repeated_offset", "range_mismatch", "duplicate_case"])
    def test_unreconciled_catalog_range_cannot_report_complete(
        self, initialized_db: dict[str, str], tmp_path: Path, fault: str
    ) -> None:
        """Protocol mutations include repeated caseID across pages despite valid ranges."""
        with _court_server(fault) as (origin, requests):
            config = _config(tmp_path, origin)
            report, _ = collect_artifact("dd-519", config, initialized_db, "japan-court-" + fault, requests)
            assert any("judgeDateMode" in p for p in requests), report
            assert report["result"].get("status") != "complete", report
            errors = str(report["result"].get("errors", []))
            assert any(term in errors for term in ("catalog", "pagination", "range", "offset")), report
            assert len(requests) < 30, "Invalid pagination must terminate rather than consume the whole budget"

    def test_unavailable_required_judgment_cannot_be_replaced_with_html_summary(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """The recorded summary still exists while its sole complete PDF returns 404."""
        with _court_server("missing_pdf") as (origin, requests):
            config = _config(tmp_path, origin)
            report, rows = collect_artifact("dd-519", config, initialized_db, "japan-court-required-pdf", requests)
            assert "/assets/hanrei/hanrei-pdf-97209.pdf" in requests
            assert len(rows) == 3, report
            assert not any("/97209/" in r["detail_url"] for r in rows)
            assert report["result"].get("status") != "complete", report

    def test_regional_court_attachments_ip_high_court_metadata_and_judgment_pdf_layouts_deliver_cleanly(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """Regional attachment prefixes, IP High Court detail8 fields, and PDF layouts deliver cleanly."""
        name = "japan-court-regional-and-layouts"
        if not (ROOT / "sources/dd-519.yaml").is_file():
            import pytest

            pytest.skip("Private source config dd-519.yaml not present")
        with _regional_layout_server() as (origin, requests):
            prod_cfg: dict[str, Any] = yaml.safe_load((ROOT / "sources/dd-519.yaml").read_text())
            prod_cfg["scheduler"]["entry_urls"] = [origin + LEGACY]
            prod_cfg["scheduler"]["allowed_hosts"] = ["127.0.0.1"]
            prod_cfg["fetcher"]["delay"] = 0.0
            prod_cfg["fetcher"]["retries"] = 0
            config = tmp_path / "dd-519-regional.yaml"
            config.write_text(yaml.safe_dump(prod_cfg, allow_unicode=True, sort_keys=False))

            report, rows = collect_artifact("dd-519", config, initialized_db, name, requests)

            offset1_idx = next((i for i, r in enumerate(requests) if "offset=1" in r), -1)
            detail1_idx = next((i for i, r in enumerate(requests) if "/hanrei/97204/detail2/index.html" in r), -1)
            assert 0 <= offset1_idx < detail1_idx, (
                f"Next catalog page offset=1 must be queued before detail pages: {requests}"
            )
            assert "/ip/vc-files/ip/attachmentfile/hanrei-pdf-97192.pdf" in requests, requests
            assert len(rows) == 2, (report, rows)
            assert report["result"]["status"] == "complete", report

            sc_row = next(r for r in rows if "/97204/" in r["detail_url"])
            sc_md = normalized(sc_row["markdown"])
            assert (
                sc_md.index(normalized("令和6年(行ヒ)第339号"))
                < sc_md.index(normalized("第二小法廷判決"))
                < sc_md.index(normalized("主文"))
                < sc_md.index(normalized("本件上告を棄却する"))
            ), sc_row["markdown"]

            ip_row = next(r for r in rows if "/97192/" in r["detail_url"])
            assert "令和7(ネ)10052" in (ip_row["doc_number"] or ""), ip_row
            assert str(ip_row["published_at"]) == "2026-10-15", ip_row
            assert "知的財産高等裁判所" in (ip_row["issuer"] or "") and "第２部" in (ip_row["issuer"] or ""), ip_row
            assert ip_row["status"] == "控訴棄却", ip_row

            ip_md = normalized(ip_row["markdown"])
            assert normalized("控訴人関ケ原町") in ip_md, ip_row["markdown"]
            assert normalized("代理人池田智洋") in ip_md, ip_row["markdown"]
            table_rows = rendered_table_rows(
                ip_row["markdown"],
                lambda cell: normalized(html.fromstring(cell).text_content()),
            )
            assert ["5", "麓山地"] in table_rows, (table_rows, ip_row["markdown"])
        assert_offline_reparse(config, initialized_db, name)

    def test_live_catalog_total_increment_during_pagination_continues_without_aborting(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """If a new judgment is published during pagination (total 2 -> 3, shifting 97204 onto offset=1), collection continues and completes cleanly."""
        name = "japan-court-live-total-increment"
        if not (ROOT / "sources/dd-519.yaml").is_file():
            import pytest

            pytest.skip("Private source config dd-519.yaml not present")
        with _regional_layout_server(live_total_increment=True) as (origin, requests):
            prod_cfg: dict[str, Any] = yaml.safe_load((ROOT / "sources/dd-519.yaml").read_text())
            prod_cfg["scheduler"]["entry_urls"] = [origin + LEGACY]
            prod_cfg["scheduler"]["allowed_hosts"] = ["127.0.0.1"]
            prod_cfg["fetcher"]["delay"] = 0.0
            prod_cfg["fetcher"]["retries"] = 0
            config = tmp_path / "dd-519-live-increment.yaml"
            config.write_text(yaml.safe_dump(prod_cfg, allow_unicode=True, sort_keys=False))

            report, rows = collect_artifact("dd-519", config, initialized_db, name, requests)
            errors = str(report["result"].get("errors", []))
            assert "court_catalog_total_changed" not in errors, report
            assert "court_catalog_duplicate_case_across_pages" not in errors, report
            assert len(rows) == 2, (report, rows)
            assert {r["detail_url"].split("/hanrei/")[1].split("/")[0] for r in rows} == {"97204", "97192"}
            assert report["result"]["status"] == "complete", report

    def test_thirty_row_catalog_prefetches_multi_page_window_and_prioritizes_page_discovery_ahead_of_attachments(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """A 30-row catalog page prefetches a multi-page offset window and fetches all page/detail HTML ahead of attachment PDFs."""
        name = "japan-court-thirty-row-prefetch"
        if not (ROOT / "sources/dd-519.yaml").is_file():
            import pytest

            pytest.skip("Private source config dd-519.yaml not present")
        with _regional_layout_server(thirty_row_pages=True) as (origin, requests):
            prod_cfg: dict[str, Any] = yaml.safe_load((ROOT / "sources/dd-519.yaml").read_text())
            prod_cfg["scheduler"]["entry_urls"] = [origin + LEGACY]
            prod_cfg["scheduler"]["allowed_hosts"] = ["127.0.0.1"]
            prod_cfg["scheduler"]["concurrency"] = 2
            prod_cfg["fetcher"]["delay"] = 0.0
            prod_cfg["fetcher"]["retries"] = 0
            config = tmp_path / "dd-519-prefetch.yaml"
            config.write_text(yaml.safe_dump(prod_cfg, allow_unicode=True, sort_keys=False))

            report, rows = collect_artifact("dd-519", config, initialized_db, name, requests)
            offset30_idx = next((i for i, r in enumerate(requests) if "offset=30" in r), -1)
            offset60_idx = next((i for i, r in enumerate(requests) if "offset=60" in r), -1)
            detail20_idx = next((i for i, r in enumerate(requests) if "/hanrei/90020/detail2/index.html" in r), -1)
            detail31_idx = next((i for i, r in enumerate(requests) if "/hanrei/90031/detail2/index.html" in r), -1)
            last_detail_idx = max((i for i, r in enumerate(requests) if "/detail2/index.html" in r), default=-1)
            first_pdf_idx = next((i for i, r in enumerate(requests) if r.endswith(".pdf")), -1)
            assert 0 <= offset30_idx < detail20_idx and 0 <= offset60_idx < min(detail20_idx, detail31_idx), (
                f"Both offset=30 and offset=60 must be prefetched in wave 1 from offset=0: {requests[:25]}"
            )
            assert 0 <= last_detail_idx < first_pdf_idx, (
                f"All role='page' detail requests must be fetched ahead of role='attachment' PDF requests: "
                f"last_detail_idx={last_detail_idx}, first_pdf_idx={first_pdf_idx}"
            )
            assert len(rows) == 61, (report, len(rows))
            assert report["result"]["status"] == "complete", report


class TestLargeCatalogBudgets:
    def test_explicit_two_hundred_thousand_request_budget_runs_a_finite_real_document(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """The real 67,890-case catalog needs >100,000 detail/PDF requests; a budget is a ceiling."""
        with _court_server() as (origin, requests):
            config = _config(tmp_path, origin, request_budget=200000, adapter="web")
            report, rows = collect_artifact("dd-519", config, initialized_db, "large-catalog-request-budget", requests)
            assert len(rows) == 1, report
            assert report["result"]["status"] == "complete", report
            assert len(requests) < 5
