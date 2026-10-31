"""Verification-sample rendering, one-way synchronization and visible text contracts."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import cast


class TestRunbookVerificationSamples:
    def test_sample_renderer_builds_two_column_payload_and_markdown_table_capped_at_ten_items(
        self,
    ) -> None:
        """Rendering verification samples produces a standalone HTML two-column Payload/Markdown comparison document with attachment links and a 10-item cap."""
        import re

        from wlh.runbook_samples import (
            render_source_verification_html,
        )

        items: list[dict[str, object]] = [
            {
                "detail_url": "https://vanban.chinhphu.vn/page/pagenew.aspx?docid=210001",
                "attachment_url": "https://vanban.chinhphu.vn/cpp/files/vbpq/2026/04/105-nd.signed.pdf",
                "original_file": "Nghị định 105_2026_NĐ-CP.pdf",
                "original_media_type": "application/pdf",
                "raw_payload": "<html><body><div>Chi tiết văn bản 105/2026/NĐ-CP</div></body></html>",
                "markdown": "# Nghị định 105/2026/NĐ-CP\n\nĐiều 1. Phạm vi điều chỉnh.",
                "title": "Nghị định 105/2026/NĐ-CP",
                "doc_number": "105/2026/NĐ-CP",
                "issuer": "Chính phủ",
                "published_at": "2026-04-15",
            },
            {
                "detail_url": "https://example.gov.vn/detail/2",
                "attachment_url": "https://example.gov.vn/files/doc2.docx",
                "original_file": "doc2.docx",
                "original_media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "raw_payload": "",
                "markdown": "# Quyết định 02/2026\n\nNội dung toàn văn.",
                "title": "Quyết định 02/2026",
                "doc_number": "02/2026/QĐ-TTg",
                "issuer": "Thủ tướng Chính phủ",
                "published_at": "2026-04-16",
            },
        ]
        for i in range(3, 13):
            items.append(
                {
                    "detail_url": f"https://example.gov.vn/detail/{i}",
                    "attachment_url": None,
                    "original_file": None,
                    "original_media_type": "text/html",
                    "raw_payload": f"<div class='law'>Raw HTML payload #{i}</div>",
                    "markdown": f"# Law #{i}\n\nArticle 1 of Law #{i}.",
                    "title": f"Law #{i}",
                    "doc_number": f"{i:02d}/2026/QH15",
                    "issuer": "Quốc hội",
                    "published_at": f"2026-04-{i:02d}",
                }
            )

        html_doc = render_source_verification_html(
            [
                {
                    "source_id": "dd-492",
                    "source_name": "越南政府文献门户",
                    "status": "[x] 已实网跑通",
                    "continent": "1. 亚洲法域 (Asia)",
                    "jurisdiction": "1.4 越南 (Vietnam)",
                    "subcategory": "—",
                    "items": items,
                }
            ],
            max_items_per_source=10,
        )

        assert "<!doctype html>" in html_doc.lower()
        assert "<html" in html_doc.lower()
        assert "<style>" in html_doc.lower()
        assert "<table" in html_doc.lower()
        assert "<tr" in html_doc.lower()
        assert "<td" in html_doc.lower()
        assert re.search(r'<aside\b[^>]*class="[^"]*sidebar[^"]*"', html_doc), (
            "Expected GitBook-style left <aside class='...sidebar...'> container"
        )
        assert re.search(r'<main\b[^>]*class="[^"]*main[^"]*"', html_doc), (
            "Expected GitBook-style right <main class='...main...'> container"
        )
        assert "1. 亚洲法域 (Asia)" in html_doc
        assert "1.4 越南 (Vietnam)" in html_doc
        assert 'href="#dd-492"' in html_doc
        assert 'id="dd-492"' in html_doc
        assert ("左栏" in html_doc or "请求 Payload" in html_doc) and (
            "右栏" in html_doc or "Markdown" in html_doc
        )
        assert re.search(
            r'<a\b[^>]*href="https://vanban\.chinhphu\.vn/cpp/files/vbpq/2026/04/105-nd\.signed\.pdf"',
            html_doc,
        ), "Expected clickable HTML <a> link for PDF attachment"
        assert re.search(
            r'<a\b[^>]*href="https://example\.gov\.vn/files/doc2\.docx"',
            html_doc,
        ), "Expected clickable HTML <a> link for DOCX attachment"
        assert "Raw HTML payload #3" in html_doc
        assert "Article 1 of Law #3" in html_doc
        assert "Law #10" in html_doc
        assert "Law #11" not in html_doc
        assert "Law #12" not in html_doc

    def test_csv_to_html_one_way_sync_updates_gitbook_toc_and_preserves_samples(
        self, tmp_path: Path
    ) -> None:
        """Syncing `source-adaptation-matrix.csv` -> `source-verification-samples.html` updates the GitBook TOC and metadata while preserving sample rows."""
        import importlib
        from typing import Any

        from wlh.runbook_samples import (
            render_html_from_csv,
            render_source_verification_html,
            sync_csv_to_html,
        )

        lxml_html: Any = importlib.import_module("lxml.html")

        csv_path = tmp_path / "source-adaptation-matrix.csv"
        html_path = tmp_path / "source-verification-samples.html"

        initial_html = render_source_verification_html(
            [
                {
                    "source_id": "dd-492",
                    "source_name": "Cổng Thông tin điện tử Chính phủ",
                    "status": "[x] 已实网跑通",
                    "continent": "1. 亚洲法域 (Asia)",
                    "jurisdiction": "1.4 越南 (Vietnam)",
                    "subcategory": "—",
                    "items": [
                        {
                            "detail_url": "https://vanban.chinhphu.vn/page/pagenew.aspx?docid=210001",
                            "attachment_url": "https://vanban.chinhphu.vn/cpp/files/vbpq/2026/04/105-nd.signed.pdf",
                            "original_file": "Nghị định 105_2026_NĐ-CP.pdf",
                            "original_media_type": "application/pdf",
                            "attachments": [
                                {
                                    "url": "https://vanban.chinhphu.vn/cpp/files/vbpq/2026/04/105-nd.signed.pdf",
                                    "name": "Nghị định 105_2026_NĐ-CP.pdf",
                                    "media_type": "application/pdf",
                                },
                                {
                                    "url": "https://vanban.chinhphu.vn/cpp/files/vbpq/2026/04/105-nd-phu-luc.docx",
                                    "name": "Phụ lục Nghị định 105.docx",
                                    "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                },
                            ],
                            "raw_payload": "<html><body><div>Chi tiết văn bản 105/2026/NĐ-CP</div></body></html>",
                            "markdown": "# Nghị định 105/2026/NĐ-CP\n\nĐiều 1. Phạm vi điều chỉnh.",
                            "title": "Nghị định 105/2026/NĐ-CP",
                            "doc_number": "105/2026/NĐ-CP",
                            "issuer": "Chính phủ",
                            "published_at": "2026-04-15",
                        }
                    ],
                },
                {
                    "source_id": "dd-495",
                    "source_name": "LuatVietnam 法规库（旧名称）",
                    "status": "[~] 部分跑通",
                    "continent": "1. 亚洲法域 (Asia)",
                    "jurisdiction": "1.4 越南 (Vietnam)",
                    "subcategory": "—",
                    "items": [
                        {
                            "detail_url": "https://thuvienphapluat.vn/van-ban/Thue-Phi-Le-Phi/Thong-tu-12-2026.aspx",
                            "attachment_url": None,
                            "original_file": None,
                            "original_media_type": "text/html",
                            "raw_payload": "<div id='toanvan'>Toàn văn Thông tư 12/2026/TT-BTC hướng dẫn chế độ kế toán.</div>",
                            "markdown": "# Thông tư 12/2026/TT-BTC\n\nĐiều 1. Quy định chung về kế toán.",
                            "title": "Thông tư 12/2026/TT-BTC",
                            "doc_number": "12/2026/TT-BTC",
                            "issuer": "Bộ Tài chính",
                            "published_at": "2026-03-20",
                        }
                    ],
                },
            ],
            max_items_per_source=10,
        )
        html_path.write_text(initial_html, encoding="utf-8")

        csv_path.write_text(
            "Source ID,大洲/区域,法域/国家,业务子分类,资料类型,来源名称,官方入口 URL,优先级,推荐适配器,状态,备注与实测结论\n"
            "dd-492,1. 亚洲法域 (Asia),1.4 越南 (Vietnam),1.4.1 越南核心法规,法律法规,Cổng Thông tin điện tử Chính phủ,https://vanban.chinhphu.vn/,P0,web (sources/dd-492.yaml),[x] 已实网跑通,实网已跑通（2026-10-25）：采集与离线重解析通过。\n"
            "dd-495,1. 亚洲法域 (Asia),1.4 越南 (Vietnam),1.4.1 越南核心法规,法律法规,LuatVietnam 法规库（已更新名称）,https://thuvienphapluat.vn/,P0,web (sources/dd-495.yaml),[~] 部分跑通,部分跑通（公开样本 5/10 篇）：已同步最新结论。\n"
            "dd-498,1. 亚洲法域 (Asia),1.4 越南 (Vietnam),1.4.1 越南核心法规,法律法规,Thư Viện Pháp Luật 待适配项,https://thuvienphapluat.vn/,P1,web,[ ] 待适配,待适配说明。\n",
            encoding="utf-8",
        )

        updated = sync_csv_to_html(csv_path, html_path)
        assert updated is True
        second = sync_csv_to_html(csv_path, html_path)
        assert second is False

        synced_text = html_path.read_text(encoding="utf-8")
        assert render_html_from_csv(csv_path, html_path) == synced_text
        assert "dd-498" not in synced_text
        assert "LuatVietnam 法规库（已更新名称）" in synced_text
        assert "1.4.1 越南核心法规" in synced_text
        assert "https://vanban.chinhphu.vn/cpp/files/vbpq/2026/04/105-nd.signed.pdf" in synced_text
        assert "https://vanban.chinhphu.vn/cpp/files/vbpq/2026/04/105-nd-phu-luc.docx" in synced_text
        assert "Phụ lục Nghị định 105.docx" in synced_text
        assert "Điều 1. Phạm vi điều chỉnh." in synced_text
        assert "Toàn văn Thông tư 12/2026/TT-BTC" in synced_text
        assert "Điều 1. Quy định chung về kế toán." in synced_text

        tree: Any = lxml_html.fromstring(synced_text)
        sidebar_text = str(tree.xpath('//aside[contains(@class, "sidebar")]')[0].text_content())
        main_text = str(tree.xpath('//main[contains(@class, "main")]')[0].text_content())
        assert "LuatVietnam 法规库（已更新名称）" in sidebar_text
        assert "1.4.1 越南核心法规" in sidebar_text
        assert "LuatVietnam 法规库（已更新名称）" in main_text

    def test_payload_preserves_multiline_structure_and_aligns_truncation_with_markdown(
        self,
    ) -> None:
        """Payload preview preserves multi-line structure and aligns truncation with right-column Markdown both in unit rendering and in the published HTML runbook."""
        import csv
        import importlib
        import re
        from typing import Any

        from wlh.runbook_samples import render_source_verification_html

        lxml_html: Any = importlib.import_module("lxml.html")

        paragraphs_a = [
            f"第{i}条 纳税人应当依照税收法律、行政法规的规定履行纳税义务，按期如实办理纳税申报并缴纳税款（段落编号 A-{i:02d}）。"
            for i in range(1, 12)
        ]
        markdown_a = "\n\n".join(paragraphs_a)
        assert 550 <= len(markdown_a) <= 700
        raw_payload_a = (
            '<div class="law-body" data-jurisdiction="cn">\n'
            + "\n".join(
                f'  <p class="legal-paragraph" style="margin-bottom: 1.5em; text-indent: 2em; line-height: 1.8;">{p}</p>'
                for p in paragraphs_a
            )
            + "\n</div>"
        )
        assert 1600 <= len(raw_payload_a) <= 2000

        paragraphs_b = [
            f"第{i}条 税务机关应当加强对纳税人发票使用情况的监督检查，依法查处虚开发票等涉税违法行为（段落编号 B-{i:02d}）。"
            for i in range(1, 45)
        ]
        markdown_b = "\n\n".join(paragraphs_b)
        assert 2300 <= len(markdown_b) <= 2700
        raw_payload_b = (
            '<div class="law-body" data-jurisdiction="cn">\n'
            + "\n".join(
                f'  <p style="margin-bottom: 1.5em; text-indent: 2em;">{p}</p>'
                for p in paragraphs_b
            )
            + "\n</div>"
        )
        assert 4500 <= len(raw_payload_b) <= 5500

        rendered_unit = render_source_verification_html(
            [
                {
                    "source_id": "dd-501",
                    "source_name": "国家税务总局法规库",
                    "status": "[x] 已实网跑通",
                    "continent": "1. 亚洲法域 (Asia)",
                    "jurisdiction": "1.1 中国大陆 (China Mainland)",
                    "subcategory": "—",
                    "items": [
                        {
                            "detail_url": "https://fgk.chinatax.gov.cn/zcfgk/c100012/c5200001/content.html",
                            "attachment_url": None,
                            "original_file": None,
                            "original_media_type": "text/html",
                            "raw_payload": raw_payload_a,
                            "markdown": markdown_a,
                            "title": "短篇完整法规样本",
                            "doc_number": "税总发〔2026〕1号",
                            "issuer": "国家税务总局",
                            "published_at": "2026-04-01",
                        },
                        {
                            "detail_url": "https://fgk.chinatax.gov.cn/zcfgk/c100012/c5200002/content.html",
                            "attachment_url": None,
                            "original_file": None,
                            "original_media_type": "text/html",
                            "raw_payload": raw_payload_b,
                            "markdown": markdown_b,
                            "title": "长篇截断法规样本",
                            "doc_number": "税总发〔2026〕2号",
                            "issuer": "国家税务总局",
                            "published_at": "2026-04-02",
                        },
                    ],
                }
            ],
            max_items_per_source=10,
        )

        unit_tree: Any = lxml_html.fromstring(rendered_unit)
        unit_rows: list[Any] = unit_tree.xpath('//section[@id="dd-501"]//tr[count(td)=2]')
        assert len(unit_rows) == 2

        payload_text_a = str(
            unit_rows[0].xpath('.//pre[contains(@class, "payload-box")]')[0].text_content() or ""
        ).strip()
        md_text_a = str(
            unit_rows[0].xpath('.//pre[contains(@class, "markdown-box")]')[0].text_content() or ""
        ).strip()
        assert "\n" in payload_text_a, "Case A payload-box must preserve multiple lines"
        assert "…(truncated)" not in payload_text_a, (
            "Case A payload-box must not be prematurely truncated when Markdown is complete"
        )
        assert "…(truncated)" not in md_text_a, (
            "Case A markdown-box must not be truncated when <= 1500 chars"
        )
        assert paragraphs_a[-1] in payload_text_a, (
            "Case A final paragraph of Markdown must also be present at the end of payload-box"
        )

        payload_text_b = str(
            unit_rows[1].xpath('.//pre[contains(@class, "payload-box")]')[0].text_content() or ""
        ).strip()
        md_text_b = str(
            unit_rows[1].xpath('.//pre[contains(@class, "markdown-box")]')[0].text_content() or ""
        ).strip()
        assert payload_text_b.count("\n") >= 5, (
            "Case B payload-box must preserve multi-line formatting (>= 5 newlines)"
        )
        assert payload_text_b.endswith("…(truncated)"), (
            "Case B payload-box must end with '…(truncated)'"
        )
        assert md_text_b.endswith("…(truncated)"), (
            "Case B markdown-box must end with '…(truncated)'"
        )
        payload_body_b = payload_text_b.removesuffix("…(truncated)").strip()
        md_body_b = md_text_b.removesuffix("…(truncated)").strip()
        payload_visible_b = re.sub(r"\s+", "", re.sub(r"<[^>]*>?", "", payload_body_b))
        md_visible_b = re.sub(r"\s+", "", md_body_b)
        assert len(md_visible_b) > 0
        diff_ratio = abs(len(payload_visible_b) - len(md_visible_b)) / len(md_visible_b)
        assert diff_ratio <= 0.15, (
            f"Case B visible text length of payload-box ({len(payload_visible_b)}) must match "
            f"markdown-box ({len(md_visible_b)}) within 15% (got {diff_ratio:.1%})"
        )

        repo_root = Path(__file__).resolve().parents[1]
        samples_html = repo_root / "docs" / "runbooks" / "source-verification-samples.html"
        matrix_csv = repo_root / "docs" / "runbooks" / "source-adaptation-matrix.csv"
        if not samples_html.is_file() or not matrix_csv.is_file():
            return
        published_tree: Any = lxml_html.fromstring(samples_html.read_text(encoding="utf-8"))

        all_payload_codes: list[Any] = published_tree.xpath(
            '//pre[contains(@class, "payload-box")]/code'
        )
        assert len(all_payload_codes) > 0
        for idx, code_el in enumerate(all_payload_codes, start=1):
            code_text = str(code_el.text_content() or "").strip()
            assert "\n" in code_text, (
                f"Published payload-box <code> #{idx} is compressed into a single line: {code_text[:80]!r}"
            )

        fulltext_html_sources = (
            "dd-501",
            "dd-040",
            "dd-041",
            "dd-102",
            "dd-375",
            "dd-506",
            "dd-517",
            "dd-520",
            "dd-527",
            "dd-563",
        )
        with (repo_root / "docs/runbooks/source-adaptation-matrix.csv").open(
            encoding="utf-8-sig", newline=""
        ) as handle:
            current_statuses = {row["Source ID"]: row["状态"] for row in csv.DictReader(handle)}
        quality_blocked_sources = {"dd-520": "PDF正文保真阻断"}
        for src_id in fulltext_html_sources:
            sec_nodes: list[Any] = published_tree.xpath(f'//section[@id="{src_id}"]')
            status = current_statuses[src_id]
            if status.startswith("[ ]"):
                assert src_id in quality_blocked_sources, (
                    f"Unexpected withdrawal of available fulltext source {src_id}"
                )
                assert quality_blocked_sources[src_id] in status
                assert not sec_nodes, (
                    f"{src_id} is quality-blocked and cannot present a delivered sample"
                )
                continue
            assert status.startswith(("[x]", "[~]"))
            assert len(sec_nodes) == 1, f"Missing or duplicate section #{src_id} in published HTML"
            rows: list[Any] = sec_nodes[0].xpath(".//tr[count(td)=2]")
            assert len(rows) >= 1, f"No sample rows found for {src_id}"
            for r_idx, row in enumerate(rows, start=1):
                p_nodes: list[Any] = row.xpath('.//pre[contains(@class, "payload-box")]')
                m_nodes: list[Any] = row.xpath('.//pre[contains(@class, "markdown-box")]')
                assert len(p_nodes) >= 1 and len(m_nodes) >= 1, (
                    f"{src_id} row #{r_idx} must contain both payload-box and markdown-box"
                )
                p_text = str(p_nodes[0].text_content() or "").strip()
                m_text = str(m_nodes[0].text_content() or "").strip()
                p_trunc = p_text.endswith("…(truncated)")
                m_trunc = m_text.endswith("…(truncated)")
                assert ("…(truncated)" in p_text) == ("…(truncated)" in m_text) and (
                    p_trunc == m_trunc
                ), (
                    f"{src_id} row #{r_idx} truncation mismatch: "
                    f"payload_truncated={p_trunc} (len={len(p_text)}), "
                    f"markdown_truncated={m_trunc} (len={len(m_text)})"
                )

    def test_anomaly_first_stratified_sampling_prioritizes_p5_p95_warning_and_missing_metadata_boundaries(
        self,
    ) -> None:
        """Anomaly-First Stratified Sampling prioritizes shortest (P5), longest (P95), accept_with_warning, and missing doc_number/effective_at boundary samples over fixed first-N items."""
        from wlh.runbook_samples import (
            render_source_verification_html,
            stratify_verification_samples,
        )

        assert stratify_verification_samples([], max_items=5) == []
        assert stratify_verification_samples([{"title": "T"}], max_items=0) == []

        candidates: list[dict[str, object]] = []
        # First 20 items: normal baseline documents (~260 visible chars, complete metadata, accept route)
        for i in range(1, 21):
            body = f"# 常规税法样本第{i:02d}号\n\n" + (
                f"第一条 纳税人应当依法按期办理纳税申报，税务机关依法核定税额（常规条目 {i:02d}）。\n"
                * 6
            )
            candidates.append(
                {
                    "detail_url": f"https://fgk.chinatax.gov.cn/normal/{i}.html",
                    "raw_payload": f"<div class='law'><p>{body}</p></div>",
                    "markdown": body,
                    "title": f"常规税法样本第{i:02d}号",
                    "doc_number": f"税总发〔2026〕{i}号",
                    "issuer": "国家税务总局",
                    "published_at": "2026-03-01",
                    "effective_at": "2026-04-01",
                    "quality_route": "accept",
                }
            )

        # Tail items (indices 20..24): anomaly & boundary samples that fixed first-N would drop
        candidates.append(
            {
                "detail_url": "https://fgk.chinatax.gov.cn/anomaly/p5-shortest.html",
                "raw_payload": "<div class='law'><p>第一条 极短批复全文。</p></div>",
                "markdown": "# 极短批复样本\n\n第一条 极短批复全文。",
                "title": "边界样本_P5最短正文",
                "doc_number": "税总函〔2026〕901号",
                "issuer": "国家税务总局",
                "published_at": "2026-03-10",
                "effective_at": "2026-03-10",
                "quality_route": "accept",
            }
        )
        long_body = "# 超长实施细则样本\n\n" + (
            "第一条 为全面规范跨境数字贸易税收征管流程、账簿凭证留存与信息报送义务，特制定本细则。\n\n"
            * 45
        )
        candidates.append(
            {
                "detail_url": "https://fgk.chinatax.gov.cn/anomaly/p95-longest.html",
                "raw_payload": f"<div class='law'><p>{long_body}</p></div>",
                "markdown": long_body,
                "title": "边界样本_P95最长正文",
                "doc_number": "税总发〔2026〕902号",
                "issuer": "国家税务总局",
                "published_at": "2026-03-11",
                "effective_at": "2026-05-01",
                "quality_route": "accept",
            }
        )
        candidates.append(
            {
                "detail_url": "https://fgk.chinatax.gov.cn/anomaly/warning-route.html",
                "raw_payload": "<div class='law'><p>含单字符替换警告\ufffd的OCR文本内容。</p></div>",
                "markdown": "# 警告路由样本\n\n"
                + ("第二条 含单字符替换警告\ufffd的OCR扫描件正文内容，用于抽检核验。\n" * 5),
                "title": "边界样本_警告路由",
                "doc_number": "税总发〔2026〕903号",
                "issuer": "国家税务总局",
                "published_at": "2026-03-12",
                "effective_at": "2026-05-01",
                "quality_route": "accept_with_warning",
            }
        )
        candidates.append(
            {
                "detail_url": "https://fgk.chinatax.gov.cn/anomaly/missing-doc-num.html",
                "raw_payload": "<div class='law'><p>无文号公告正文内容。</p></div>",
                "markdown": "# 缺失文号样本\n\n"
                + ("第三条 本公告无发文字号，专门用于检验缺失文号边界样本抽样。\n" * 6),
                "title": "边界样本_缺失文号",
                "doc_number": None,
                "issuer": "国家税务总局",
                "published_at": "2026-03-13",
                "effective_at": "2026-05-01",
                "quality_route": "accept",
            }
        )
        candidates.append(
            {
                "detail_url": "https://fgk.chinatax.gov.cn/anomaly/missing-effective-at.html",
                "raw_payload": "<div class='law'><p>无生效日通知正文内容。</p></div>",
                "markdown": "# 缺失生效日样本\n\n"
                + ("第四条 本通知未记载生效日期，专门用于检验缺失生效日边界样本抽样。\n" * 6),
                "title": "边界样本_缺失生效日",
                "doc_number": "税总发〔2026〕905号",
                "issuer": "国家税务总局",
                "published_at": "2026-03-14",
                "effective_at": None,
                "quality_route": "accept",
            }
        )

        sampled = stratify_verification_samples(candidates, max_items=6)
        assert len(sampled) == 6
        sampled_titles = [str(it.get("title")) for it in sampled]
        for expected_boundary_title in (
            "边界样本_P5最短正文",
            "边界样本_P95最长正文",
            "边界样本_警告路由",
            "边界样本_缺失文号",
            "边界样本_缺失生效日",
        ):
            assert expected_boundary_title in sampled_titles[:5], (
                f"Expected {expected_boundary_title!r} in top-5 anomaly-first strata, got {sampled_titles}"
            )

        all_strata: set[str] = {
            str(tag)
            for it in sampled
            for tag in cast(Sequence[object], it.get("sampling_strata") or ())
        }
        assert {
            "p5_shortest",
            "p95_longest",
            "accept_with_warning",
            "missing_doc_number",
            "missing_effective_at",
            "baseline",
        }.issubset(all_strata)

        # Even if most early candidates lack effective_at, per-stratum quota prevents starving complete baseline items
        sparse_eff_candidates: list[dict[str, object]] = []
        for idx, item in enumerate(candidates):
            cloned = dict(item)
            if idx < 15:
                cloned["effective_at"] = None
            sparse_eff_candidates.append(cloned)
        sparse_sampled = stratify_verification_samples(sparse_eff_candidates, max_items=6)
        sparse_strata: set[str] = {
            str(tag)
            for it in sparse_sampled
            for tag in cast(Sequence[object], it.get("sampling_strata") or ())
        }
        assert "baseline" in sparse_strata
        assert "missing_effective_at" in sparse_strata

        rendered_html = render_source_verification_html(
            [
                {
                    "source_id": "dd-501",
                    "source_name": "国家税务总局法规库",
                    "status": "[x] 已实网跑通",
                    "continent": "1. 亚洲法域 (Asia)",
                    "jurisdiction": "1.1 中国大陆 (China Mainland)",
                    "subcategory": "—",
                    "items": candidates,
                }
            ],
            max_items_per_source=6,
        )
        for expected_boundary_title in (
            "边界样本_P5最短正文",
            "边界样本_P95最长正文",
            "边界样本_警告路由",
            "边界样本_缺失文号",
            "边界样本_缺失生效日",
        ):
            assert expected_boundary_title in rendered_html

