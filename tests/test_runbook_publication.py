"""Published verification-sample coverage and complete visible source content contracts."""

from __future__ import annotations

from pathlib import Path


class TestRunbookVerificationSamples:
    def test_published_runbook_samples_cover_all_active_and_partial_sources_without_reward_hacked_stubs(
        self,
    ) -> None:
        """Published `[x]`/`[~]` sections show real samples or explicit, unverified zero-sample states."""
        import csv
        import importlib
        import re
        from typing import Any

        import wlh.runbook_samples as runbook_samples
        from wlh.runbook_samples import (
            render_html_from_csv,
        )

        assert not hasattr(runbook_samples, "_restore_origin_url"), (
            "Rewriting 127.0.0.1 loopback fixture URLs via _restore_origin_url is forbidden"
        )

        lxml_html: Any = importlib.import_module("lxml.html")

        repo_root = Path(__file__).resolve().parents[1]
        matrix_csv = repo_root / "docs" / "runbooks" / "source-adaptation-matrix.csv"
        matrix_md = repo_root / "docs" / "runbooks" / "source-adaptation-matrix.md"
        samples_html = repo_root / "docs" / "runbooks" / "source-verification-samples.html"
        samples_md = repo_root / "docs" / "runbooks" / "source-verification-samples.md"
        if not matrix_csv.is_file() or not matrix_md.is_file() or not samples_html.is_file():
            import pytest

            pytest.skip("Private runbook matrix or verification samples not present")

        with matrix_csv.open(encoding="utf-8-sig", newline="") as handle:
            source_records = {row["Source ID"]: row for row in csv.DictReader(handle)}
            active_ids = [
                source_id
                for source_id, row in source_records.items()
                if row["状态"].startswith(("[x]", "[~]"))
            ]
        assert len(active_ids) >= 40
        assert samples_html.is_file(), (
            "docs/runbooks/source-verification-samples.html must exist on disk"
        )
        assert not samples_md.exists(), (
            "Obsolete docs/runbooks/source-verification-samples.md must not exist on disk"
        )

        content = samples_html.read_text(encoding="utf-8")
        assert render_html_from_csv(matrix_csv, samples_html) == content, (
            "source-verification-samples.html is out of sync with source-adaptation-matrix.csv"
        )

        tree: Any = lxml_html.fromstring(content)

        sidebar_nodes: list[Any] = tree.xpath(
            '//aside[contains(@class, "sidebar")] | //nav[contains(@class, "toc")]'
        )
        assert len(sidebar_nodes) >= 1, (
            "Missing GitBook left sidebar TOC in source-verification-samples.html"
        )
        sidebar_el = sidebar_nodes[0]
        sidebar_text = str(sidebar_el.text_content() or "")
        for expected_heading in (
            "1. 亚洲法域 (Asia)",
            "2. 欧洲法域 (Europe)",
            "3. 美洲法域 (Americas)",
            "4. 大洋洲法域 (Oceania)",
            "5. 非洲法域与国际组织 (Africa & International Organizations)",
            "1.1 中国大陆 (China Mainland)",
            "1.4 越南 (Vietnam)",
            "1.1.1 核心官方法律法规、公报与财税库 (dd-500–dd-504)",
        ):
            assert expected_heading in sidebar_text, (
                f"Expected hierarchy heading {expected_heading!r} in GitBook left sidebar TOC"
            )

        md_text = matrix_md.read_text(encoding="utf-8")
        md_active_order = [
            m.group(1)
            for line in md_text.splitlines()
            if ("| [x] |" in line or "| [~] |" in line)
            for m in [re.search(r'<a id="(dd-\d{3})"></a>', line)]
            if m is not None
        ]
        toc_ids = [
            str(href).lstrip("#")
            for href in sidebar_el.xpath('.//a[starts-with(@href, "#dd-")]/@href')
        ]
        assert toc_ids == md_active_order, (
            "Left sidebar TOC source order must match source-adaptation-matrix.md active row order"
        )

        main_nodes: list[Any] = tree.xpath('//main[contains(@class, "main")]')
        assert len(main_nodes) >= 1, (
            "Missing GitBook right <main> container in source-verification-samples.html"
        )
        main_ids = [
            str(sec_id)
            for sec_id in main_nodes[0].xpath('.//section[starts-with(@id, "dd-")]/@id')
        ]
        assert main_ids == md_active_order, (
            "Right main pane section order must match source-adaptation-matrix.md active row order"
        )
        idx_516 = toc_ids.index("dd-516")
        assert toc_ids[idx_516 + 1] == "dd-575", (
            "dd-575 must appear immediately after dd-516 under 1.4 越南 (Vietnam)"
        )
        assert "dd-517" in toc_ids[idx_516 + 2 : idx_516 + 4]

        source_rows_map: dict[str, list[Any]] = {}
        for source_id in active_ids:
            nodes: list[Any] = tree.xpath(f'//section[@id="{source_id}"]')
            assert len(nodes) == 1, (
                f'Expected one source section with id="{source_id}" in published HTML'
            )
            anchor_el = nodes[0]
            sample_rows: list[Any] = anchor_el.xpath(".//tr[count(td)=2]")
            source_rows_map[source_id] = sample_rows
            row_count = len(sample_rows)
            if source_id == "dd-375":
                assert row_count == 1, f"Expected 1 sample row for dd-375, got {row_count}"
            else:
                assert row_count == 10, (
                    f"Expected exactly 10 sample rows for {source_id}, got {row_count}"
                )
            for row_idx, row in enumerate(sample_rows, start=1):
                tds: list[Any] = row.xpath("./td")
                assert len(tds) == 2, f"{source_id} row #{row_idx} must have exactly 2 <td> cells"
                left_td, right_td = tds[0], tds[1]
                left_codes: list[Any] = left_td.xpath(".//pre/code")
                for code_el in left_codes:
                    code_text = str(code_el.text_content() or "").strip()
                    assert len(code_text) >= 20, (
                        f"{source_id} sample row #{row_idx} has empty/stub left-column "
                        f"<pre><code> payload ({len(code_text)} chars)"
                    )
                att_links: list[Any] = left_td.xpath(
                    './/a[contains(@href, ".pdf") or contains(@href, ".docx") or contains(@href, ".doc") or contains(@href, "/UCMServer/TAND") or contains(@class, "attachment")]'
                )
                if not att_links:
                    assert len(left_codes) >= 1, (
                        f"{source_id} sample row #{row_idx} left <td> has no attachment link and no <pre><code> payload"
                    )
                right_codes: list[Any] = right_td.xpath(
                    ".//pre/code | .//pre | .//*[contains(@class, 'markdown')]"
                )
                right_text = (
                    str(right_codes[0].text_content() or "").strip()
                    if right_codes
                    else str(right_td.text_content() or "").strip()
                )
                assert len(right_text) >= 20, (
                    f"{source_id} sample row #{row_idx} right <td> Markdown content too short ({len(right_text)} chars)"
                )

        assert "<code></code>" not in content, (
            "Empty <code></code> raw payload blocks must not appear in source-verification-samples.html"
        )
        assert (
            "official legal document full text body content for verification."
            not in content.lower()
        ), "Unit-test fixture stub text must not appear in source-verification-samples.html"
        assert "127.0.0.1" not in content, (
            "Loopback 127.0.0.1 URLs must not appear in source-verification-samples.html"
        )
        assert "localhost" not in content, (
            "Loopback localhost URLs must not appear in source-verification-samples.html"
        )

        att_href_re = re.compile(
            r"^https://[^)\s]+(?:\.(?:pdf|docx)(?:\?[^)\s]*)?|/webcenter/ShowProperty\?nodeId=/UCMServer/TAND\d+)$",
            re.I,
        )
        for vn_att_id in ("dd-492", "dd-493", "dd-512", "dd-514", "dd-575"):
            rows = source_rows_map.get(vn_att_id, [])
            hrefs: list[str] = [
                str(href)
                for row in rows
                for href in row.xpath(".//a/@href")
            ]
            assert any(att_href_re.search(h) for h in hrefs), (
                f"{vn_att_id} section must contain clickable .pdf, .docx, or UCM download <a href='https://...'> links"
            )

        forbidden_placeholders = (
            "Bạn chưa Đăng nhập thành viên",
            "Văn bản này đang cập nhật nội dung",
            "Nội dung tóm tắt đang được cập nhật",
            "Đây là tiện ích dành cho tài khoản Tiêu chuẩn hoặc Nâng cao",
            "Vui lòng xem nội dung chi tiết trong file đính kèm",
            "Danh sách bình luận",
            "uploadFiles/host/local/du-thao-01.pdf",
            "xaydungchinhsach.chinhphu.vn/cpp/files/vbpq/2026/04/105-nd.signed.pdf",
            "Thông tư 145/2026/TT-BTC quy định đăng ký, lưu ký, bù trừ và thanh toán chứng khoán",
            "Bản án số 1463/2026/DS-PT ngày 15/10/2026 của TAND TP. Cần Thơ",
            "hoidapcstc/home/cthoidap/166356",
            "各省、自治区、直辖市人民政府，国务院各部委、各直属机构：现将有关事项通知如下。",
            "指导性案例221号：丙公司诉丁公司算法商业秘密纠纷案",
        )
        for bad_str in forbidden_placeholders:
            assert bad_str not in content, (
                f"Reward-hacked placeholder {bad_str!r} must not appear in source-verification-samples.html"
            )

        def _right_cell_markdown(row_el: Any) -> str:
            tds_local: list[Any] = row_el.xpath("./td")
            right_td_local = tds_local[1]
            codes_local: list[Any] = right_td_local.xpath(
                ".//pre/code | .//pre | .//*[contains(@class, 'markdown')]"
            )
            return (
                str(codes_local[0].text_content() or "").strip()
                if codes_local
                else str(right_td_local.text_content() or "").strip()
            )

        for src_id in ("dd-040", "dd-041"):
            for r_idx, r_el in enumerate(source_rows_map.get(src_id, []), start=1):
                md_cell = _right_cell_markdown(r_el)
                assert "【字体：大 中 小】" not in md_cell, (
                    f"{src_id} row #{r_idx} right-column Markdown contains font widget noise"
                )
                assert "### 相关新闻" not in md_cell, (
                    f"{src_id} row #{r_idx} right-column Markdown contains hidden related news heading"
                )

        for r_idx, r_el in enumerate(source_rows_map.get("dd-102", []), start=1):
            md_cell = _right_cell_markdown(r_el)
            assert "【打印】" not in md_cell and "【返回】" not in md_cell, (
                f"dd-102 row #{r_idx} right-column Markdown contains print/back button noise"
            )

        dd375_rows = source_rows_map.get("dd-375", [])
        assert len(dd375_rows) == 1
        dd375_left_td = dd375_rows[0].xpath("./td")[0]
        dd375_hrefs = " ".join(str(h) for h in dd375_left_td.xpath(".//a/@href"))
        assert "P020200612583036030033.DOCX" in dd375_hrefs, (
            "dd-375 sample row #1 left-column <td> must include attachment link P020200612583036030033.DOCX"
        )
        assert "P020210608556900301740.docx" in dd375_hrefs, (
            "dd-375 sample row #1 left-column <td> must include attachment link P020210608556900301740.docx"
        )

        for r_idx, r_el in enumerate(source_rows_map.get("dd-527", []), start=1):
            md_cell = _right_cell_markdown(r_el)
            assert "Share:" not in md_cell, (
                f"dd-527 row #{r_idx} right-column Markdown contains Share bar noise"
            )
            assert "- [](https://www.iras.gov.sg/" not in md_cell, (
                f"dd-527 row #{r_idx} right-column Markdown contains empty social share link"
            )

        for r_idx, r_el in enumerate(source_rows_map.get("dd-563", []), start=1):
            md_cell = _right_cell_markdown(r_el)
            for toc_noise in (
                "- [HIGHLIGHTS OF THIS ISSUE](",
                "- [INCOME TAX](",
                "[](https://www.irs.gov/irb/",
            ):
                assert toc_noise not in md_cell, (
                    f"dd-563 row #{r_idx} right-column Markdown contains TOC/anchor noise {toc_noise!r}"
                )

        global_forbidden_md_tokens = (
            "[](https://",
            "[](http://",
            "【字体：大 中 小】",
            "字体 [大 中 小]",
            "[【打印】]",
            "[【返回】]",
            "Đang theo dõi",
        )
        for src_id, s_rows in source_rows_map.items():
            for r_idx, r_el in enumerate(s_rows, start=1):
                md_cell = _right_cell_markdown(r_el)
                for bad_tok in global_forbidden_md_tokens:
                    assert bad_tok not in md_cell, (
                        f"{src_id} row #{r_idx} right-column Markdown contains forbidden UI noise {bad_tok!r}"
                    )
