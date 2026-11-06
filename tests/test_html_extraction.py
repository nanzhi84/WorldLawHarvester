"""Scoped legal HTML conversion and visible boilerplate removal contracts."""

from __future__ import annotations


class TestHtmlToMarkdownConversion:
    def test_scoped_html_with_tables_and_relative_links_renders_deterministic_markdown(
        self,
    ) -> None:
        """HTML within `content_xpath` converts headings, relative links, and tables into Markdown."""
        from wlh.processor.extractors.html.html_to_md import html_to_markdown

        html = """
        <html>
          <body>
            <main id="legal-body">
              <h1>Act No. 42/2026</h1>
              <p>See <a href="/docs/annex1.pdf">Annex 1</a> for details.</p>
              <table>
                <thead>
                  <tr><th>Article</th><th>Subject</th></tr>
                </thead>
                <tbody>
                  <tr><td>Article 1</td><td>Scope of Application</td></tr>
                  <tr><td>Article 2</td><td>Definitions</td></tr>
                </tbody>
              </table>
            </main>
          </body>
        </html>
        """
        md = html_to_markdown(
            html,
            base_url="https://law.example.org/acts/42",
            content_xpath="//main[@id='legal-body']",
        )
        assert "# Act No. 42/2026" in md
        assert "[Annex 1](https://law.example.org/docs/annex1.pdf)" in md
        assert "| Article | Subject |" in md
        assert "| Article 1 | Scope of Application |" in md

    def test_html_with_ui_chrome_font_controls_print_back_buttons_and_share_bars_renders_clean_markdown(
        self,
    ) -> None:
        """HTML containing UI chrome (font widgets, print/back buttons, share bars, anchors without href, adjacent bolds) renders clean Markdown."""
        from wlh.processor.extractors.html.dom_tree import parse_html
        from wlh.processor.parsers.html.document_builder import WebDocumentBuilder

        html = """
        <html>
          <body>
            <div class="extend_font fl">【字体：<a href="javascript:fontZoom(26)">大</a> <a href="javascript:fontZoom(20)">中</a> <a href="javascript:fontZoom(14)">小</a>】</div>
            <div>2026-06-30 字体 [大 中 小]</div>
            <div class="eyd-page-header__share">
              <span>Share:</span>
              <ul>
                <li><a href="#" aria-label="Facebook"><span class="eyd-icon-facebook"></span></a></li>
                <li><a href="#" aria-label="X"><span class="eyd-icon-twitter"></span></a></li>
              </ul>
            </div>
            <div class="eyd-page-anchors">
              <span>On this page: </span>
              <nav><ul><li><a href="#sec1">Section 1</a></li></ul></nav>
            </div>
            <button type="button">Print this page</button>
            <button type="button">Show contents</button>
            <a id="main-content"></a>
            <a href="#NOT-2026-46" class="xref"></a>
            <p><b><span>第</span></b><b><span lang="EN-US">1</span></b><b><span>号修改单的通知</span></b></p>
            <p><a name="p1" id="p1">1.</a> For patients, the dose is <a name="_Hlk234317469">100 mg</a> daily.</p>
            <p>See <i>Lui Sai-yu </i><b>at </b><b>paragraphs </b>58.</p>
            <div class="document-tip target-hidden"><span>Tải biểu mẫu</span></div>
            <span class="tooltip-button target-hidden"><span>Đang theo dõi</span></span>
            <div class="zrbj">[责任编辑： 贾欣然]</div>
            <div id="xgxw" style="display: none;"><h3>相关新闻</h3></div>
            <a href="#" objid="135" onclick="javascript:window.print()">【打印】</a>
            <a href="#" objid="135" onclick="goSpecialURL()">【返回】</a>
            <section class="eyd-related-pages">
              <h2>Related Content</h2>
              <article><a href="/other">Related Guide</a></article>
            </section>
          </body>
        </html>
        """
        base_url = (
            "https://www.safe.gov.cn/www/illegal/listQuery?saffno=22d58c4edfbf401595848a52a3f91db6"
        )
        builder = WebDocumentBuilder(
            {
                "processor": {
                    "parser": {
                        "type": "html",
                        "documents": {
                            "exclude_content_xpath": [
                                './/*[contains(@class, "extend_font") '
                                'or contains(@class, "eyd-page-header__share") '
                                'or contains(@class, "eyd-page-anchors") '
                                'or contains(@class, "eyd-related-pages") '
                                'or contains(@class, "target-hidden") '
                                'or contains(@class, "document-tip") '
                                'or contains(@class, "tooltip-button") '
                                'or contains(@class, "zrbj")]'
                            ],
                        },
                    }
                },
                "cleaners": {
                    "strip_text_regexes": [
                        r"【字体[：:]\s*大\s*中\s*小\s*】",
                        r"\d{4}-\d{2}-\d{2}[ \t]+字体\s*\[\s*大\s+中\s+小\s*\]",
                        r"字体\s*\[\s*大\s+中\s+小\s*\]",
                        r"\[责任编辑[：:][^\]]*\]",
                    ]
                },
            }
        )
        tree, _, _ = parse_html(html.encode("utf-8"), "utf-8")
        md = builder.html_markdown(
            tree,
            {
                "actual_url": base_url,
                "request_url": base_url,
                "charset": "utf-8",
            },
        )
        for forbidden in (
            "【字体：",
            "字体 [大 中 小]",
            "相关新闻",
            "责任编辑",
            "【打印】",
            "【返回】",
            "Print this page",
            "Show contents",
            "Share:",
            "On this page:",
            "Related Content",
            "Đang theo dõi",
            "Tải biểu mẫu",
            "[](https://",
            "#)",
            "[1.](https://",
            "[100 mg](https://",
            "****",
        ):
            assert forbidden not in md, f"Unexpected noise {forbidden!r} found in Markdown:\n{md}"

        assert "1. For patients, the dose is 100 mg daily." in md
        assert "**第1号修改单的通知**" in md
        assert "*Lui Sai-yu* **at** **paragraphs** 58." in md

    def test_irs_bulletin_html_with_left_toc_column_and_named_anchors_excludes_front_toc_noise(
        self,
    ) -> None:
        """IRS IRB two-column layout strips the `col-md-4` table-of-contents sidebar and empty anchor links while keeping `col-md-8` body text."""
        from wlh.processor.extractors.html.dom_tree import parse_html
        from wlh.processor.parsers.html.document_builder import WebDocumentBuilder

        html = (
            '<div id="block-pup-irs-barrio-content">'
            '<div class="field--name-body">'
            '<div class="row">'
            '<div class="col-md-4">'
            "<ul>"
            '<li><a href="#id1">HIGHLIGHTS OF THIS ISSUE</a>'
            '<ul><li><a href="#HI1">INCOME TAX</a></li></ul>'
            "</li>"
            '<li><a href="#id3"></a>'
            '<ul><li><a href="#id5">The IRS Mission</a></li></ul>'
            "</li>"
            "</ul>"
            "</div>"
            '<div class="col-md-8">'
            '<div class="book">'
            '<div class="titlepage">'
            "<h1>Internal Revenue Bulletin: 2026-35</h1>"
            '<p class="pubdate">August 24, 2026</p>'
            "</div>"
            '<div class="part">'
            '<h1><a name="id1"></a>HIGHLIGHTS OF THIS ISSUE</h1>'
            "<p>Substantive bulletin text on Section 43 inflation adjustment.</p>"
            "</div>"
            "</div>"
            "</div>"
            "</div>"
            "</div>"
            "</div>"
        )
        base_url = "https://www.irs.gov/irb/2026-35_IRB"
        builder = WebDocumentBuilder(
            {
                "processor": {
                    "parser": {
                        "type": "html",
                        "documents": {
                            "content_xpath": '//div[@id="block-pup-irs-barrio-content"]',
                            "exclude_content_xpath": [
                                './/div[contains(@class, "col-md-4") and following-sibling::div[contains(@class, "col-md-8")]//div[contains(@class, "book")]]'
                            ],
                        },
                    }
                }
            }
        )
        tree, _, _ = parse_html(html.encode("utf-8"), "utf-8")
        md = builder.html_markdown(
            tree,
            {
                "actual_url": base_url,
                "request_url": base_url,
                "charset": "utf-8",
            },
        )
        for forbidden in (
            "[HIGHLIGHTS OF THIS ISSUE](",
            "[INCOME TAX](",
            "[The IRS Mission](",
            "[](https://www.irs.gov/irb/2026-35_IRB",
        ):
            assert forbidden not in md, (
                f"Unexpected TOC/anchor noise {forbidden!r} in Markdown:\n{md}"
            )

        assert "# Internal Revenue Bulletin: 2026-35" in md
        assert "Substantive bulletin text on Section 43 inflation adjustment." in md

    def test_complex_table_with_rowspan_colspan_and_multicalendar_dates_preserves_grid_and_normalizes_iso_dates(
        self,
    ) -> None:
        """Complex HTML tables with `rowspan`/`colspan` preserve cell alignment and multi-calendar dates (Japanese eras, ROC Minguo, localized formats) normalize to ISO `YYYY-MM-DD`."""
        from wlh.cleaners.dates import era_date, parse_date, parse_taiwan_date
        from wlh.processor.extractors.html.html_to_md import html_to_markdown

        html = """
        <html>
          <body>
            <main id="content">
              <table>
                <tr>
                  <th rowspan="2">Category</th>
                  <th colspan="2">Tax Rate Schedule</th>
                </tr>
                <tr>
                  <th>Standard</th>
                  <th>Reduced</th>
                </tr>
                <tr>
                  <td rowspan="2">Consumption Tax</td>
                  <td>10%</td>
                  <td>8%</td>
                </tr>
                <tr>
                  <td>National 7.8%</td>
                  <td>Local 2.2%</td>
                </tr>
              </table>
            </main>
          </body>
        </html>
        """
        md = html_to_markdown(
            html,
            base_url="https://www.nta.go.jp/taxes/shiraberu/taxanswer/shohi/6303.htm",
            content_xpath="//main[@id='content']",
        )
        assert "Consumption Tax" in md
        assert "Tax Rate Schedule" in md
        assert "10%" in md and "8%" in md
        assert "National 7.8%" in md and "Local 2.2%" in md

        # Japanese eras (Reiwa, Heisei, Showa, Gannen) and ROC Minguo dates
        assert era_date("令和元年5月1日") == "2019-05-01"
        assert era_date("令和6年10月25日") == "2024-10-25"
        assert era_date("平成三十一年四月三十日") == "2019-04-30"
        assert parse_taiwan_date("中華民國113年05月20日") == "2024-05-20"
        assert parse_taiwan_date("民國一百十二年十二月一日") == "2023-12-01"
        assert parse_date("24.10.2026") == "2026-10-24"
        assert parse_date("October 24, 2026") == "2026-10-24"

    def test_infer_effective_date_extracts_simplified_chinese_trial_and_execution_clauses(
        self,
    ) -> None:
        """`infer_effective_date` extracts effective dates from Simplified Chinese regulatory/practice guidance trial (`试行`) and execution (`执行`) clauses."""
        from wlh.cleaners.dates import infer_effective_date

        assert (
            infer_effective_date(
                "第八章 附 则\n第四十四条 本指引自2019年8月1日起试行。",
                published_at="2025-03-22",
            )
            == "2019-08-01"
        )
        assert (
            infer_effective_date(
                "第六章 附 则\n第四十四条 本指引自2019年8月1日之日起试行。",
                published_at="2025-03-22",
            )
            == "2019-08-01"
        )
        assert (
            infer_effective_date(
                "第八章 附 则\n第三十七条 本指引自2021年8月1日试行。",
                published_at="2025-03-22",
            )
            == "2021-08-01"
        )
        assert (
            infer_effective_date(
                "第六章 附 则\n第四十条 本规则自发布之日起执行。",
                published_at="2025-03-22",
            )
            == "2025-03-22"
        )

    def test_html_and_text_cleaners_with_download_and_error_correction_footer_strip_trailing_boilerplate(
        self,
    ) -> None:
        """HTML and text cleaners strip trailing `【下载】` and `纠错或建议` / `[纠错或建议](...jiucuo.html)` action footers across generic builders and `dd-504` / `dd-592` configs."""
        from pathlib import Path

        import yaml

        from wlh.cleaners.text_cleaner import apply_text_cleaners
        from wlh.processor.extractors.html.dom_tree import parse_html
        from wlh.processor.parsers.html.document_builder import WebDocumentBuilder

        cleaned_text = apply_text_cleaners(
            "正文第一条。\n\n【下载】\n\n[纠错或建议](http://hd.chinatax.gov.cn/fuwu/jiucuo.html)\n"
        )
        assert "正文第一条。" in cleaned_text
        for forbidden in ("【下载】", "纠错或建议", "jiucuo.html"):
            assert forbidden not in cleaned_text, (
                f"Expected {forbidden!r} to be stripped by apply_text_cleaners:\n{cleaned_text}"
            )

        snippet_html = (
            '<div class="article">'
            '<div class="arc_cont"><p>各省、自治区、直辖市税务局：现将有关事项通知如下。</p></div>'
            '<div class="bot-btns-box">'
            '<div class="print"><a href="javascript:void(0)" id="actdy">【打印】</a></div>'
            '<div class="xxgk-download-box"><a href="" class="xxgk-download-btn" id="zwxz">【下载】</a></div>'
            "</div>"
            '<div class="jcjy"><a href="http://hd.chinatax.gov.cn/fuwu/jiucuo.html" target="_blank">纠错或建议</a></div>'
            "</div>"
        )
        generic_builder = WebDocumentBuilder(
            {
                "processor": {
                    "parser": {
                        "type": "html",
                        "documents": {
                            "content_xpath": "//div[@class='article']",
                        },
                    }
                }
            }
        )
        snippet_url = "https://fgk.chinatax.gov.cn/zcfgk/c100012/c5242764/content.html"
        tree, _, _ = parse_html(snippet_html.encode("utf-8"), "utf-8")
        snippet_md = generic_builder.html_markdown(
            tree,
            {
                "actual_url": snippet_url,
                "request_url": snippet_url,
                "charset": "utf-8",
            },
        )
        assert "各省、自治区、直辖市税务局：现将有关事项通知如下。" in snippet_md
        for forbidden in ("【打印】", "【下载】", "纠错或建议", "jiucuo.html"):
            assert forbidden not in snippet_md, (
                f"Unexpected footer noise {forbidden!r} in generic HTML Markdown:\n{snippet_md}"
            )

        repo_root = Path(__file__).resolve().parents[1]
        source_fixtures = {
            "dd-504": [
                repo_root / "tests" / "fixtures" / "dd-504" / "02-content.html",
                repo_root / "tests" / "fixtures" / "dd-504" / "05-content.html",
            ],
            "dd-592": [
                repo_root / "tests" / "fixtures" / "dd-592" / "03-content.html",
                repo_root / "tests" / "fixtures" / "dd-592" / "04-content.html",
            ],
        }
        for source_id, fixture_paths in source_fixtures.items():
            cfg_path = repo_root / "sources" / f"{source_id}.yaml"
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            docs_cfg = cfg["processor"]["parser"]["documents"]
            assert docs_cfg.get("exclude_content_xpath"), (
                f"Expected exclude_content_xpath in {source_id}.yaml"
            )
            assert cfg.get("cleaners", {}).get("strip_text_regexes"), (
                f"Expected cleaners.strip_text_regexes in {source_id}.yaml"
            )
            prod_builder = WebDocumentBuilder(cfg)
            for fixture_path in fixture_paths:
                raw_html = fixture_path.read_text(encoding="utf-8")
                live_like_html = raw_html.replace(
                    'href="#unselected" target="_blank">纠错或建议</a>',
                    'href="http://hd.chinatax.gov.cn/fuwu/jiucuo.html" target="_blank">纠错或建议</a>',
                )
                for variant_html in (raw_html, live_like_html):
                    f_tree, _, _ = parse_html(variant_html.encode("utf-8"), "utf-8")
                    f_md = prod_builder.html_markdown(
                        f_tree,
                        {
                            "actual_url": snippet_url,
                            "request_url": snippet_url,
                            "charset": "utf-8",
                        },
                    )
                    for forbidden in ("【下载】", "纠错或建议", "jiucuo.html"):
                        assert forbidden not in f_md, (
                            f"Unexpected {forbidden!r} in {source_id} ({fixture_path.name}):\n{f_md}"
                        )

    def test_html_and_text_cleaners_with_attachment_and_related_articles_footer_strip_trailing_boilerplate(
        self,
    ) -> None:
        """HTML and text cleaners strip trailing `附件下载：` and `相关文章：` footer blocks across `apply_text_cleaners` and `dd-595` / `dd-596` configs while preserving main document text."""
        from pathlib import Path

        import yaml

        from wlh.cleaners.text_cleaner import apply_text_cleaners
        from wlh.processor.extractors.html.dom_tree import parse_html
        from wlh.processor.parsers.html.document_builder import WebDocumentBuilder

        for sample_text in (
            "正文第一条。\n\n附件下载：\n\n相关文章：\n",
            "正文第一条。\n\n附件下载：\n\n相关文章：\n\n- [某政策解读](https://szs.mof.gov.cn/zhengcejiedu/202409/t20240903_3943047.htm)\n",
            "正文第一条。\n\n**点击“阅读原文”快速投稿~**\n",
            "正文第一条。\n\n**点击“阅读原文”快速订刊~**\n",
        ):
            cleaned_text = apply_text_cleaners(sample_text)
            assert "正文第一条。" in cleaned_text
            for forbidden in (
                "附件下载：",
                "相关文章：",
                "点击“阅读原文”快速投稿~",
                "点击“阅读原文”快速订刊~",
            ):
                assert forbidden not in cleaned_text, (
                    f"Expected {forbidden!r} to be stripped by apply_text_cleaners:\n{cleaned_text}"
                )

        repo_root = Path(__file__).resolve().parents[1]
        source_fixtures = {
            "dd-595": [
                (
                    repo_root / "tests" / "fixtures" / "dd-595" / "51-t20260131_3983036.htm.html",
                    "https://szs.mof.gov.cn/zhengcefabu/202601/t20260131_3983036.htm",
                    "小规模纳税人发生应税交易",
                ),
                (
                    repo_root / "tests" / "fixtures" / "dd-595" / "52-t20230827_3904226.htm.html",
                    "https://szs.mof.gov.cn/zhengcefabu/202308/t20230827_3904226.htm",
                    "证券交易印花税实施减半征收",
                ),
            ],
            "dd-596": [
                (
                    repo_root / "tests" / "fixtures" / "dd-596" / "12-t20080815_63368.htm.html",
                    "https://szs.mof.gov.cn/zhengcejiedu/200808/t20080815_63368.htm",
                    "上半年税收收入增长的总体情况",
                ),
                (
                    repo_root / "tests" / "fixtures" / "dd-596" / "13-t20250827_3970905.htm.html",
                    "https://szs.mof.gov.cn/zhengcejiedu/202508/t20250827_3970905.htm",
                    "吉林省自2025年9月1日起",
                ),
            ],
        }
        for source_id, fixtures in source_fixtures.items():
            cfg_path = repo_root / "sources" / f"{source_id}.yaml"
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            docs_cfg = cfg["processor"]["parser"]["documents"]
            assert docs_cfg.get("exclude_content_xpath"), (
                f"Expected exclude_content_xpath in {source_id}.yaml"
            )
            assert cfg.get("cleaners", {}).get("strip_text_regexes"), (
                f"Expected cleaners.strip_text_regexes in {source_id}.yaml"
            )
            prod_builder = WebDocumentBuilder(cfg)
            for fixture_path, page_url, expected_body_phrase in fixtures:
                raw_html = fixture_path.read_text(encoding="utf-8")
                f_tree, _, _ = parse_html(raw_html.encode("utf-8"), "utf-8")
                f_md = prod_builder.html_markdown(
                    f_tree,
                    {
                        "actual_url": page_url,
                        "request_url": page_url,
                        "charset": "utf-8",
                    },
                )
                assert expected_body_phrase in f_md, (
                    f"Expected main body phrase {expected_body_phrase!r} in {source_id} ({fixture_path.name}):\n{f_md}"
                )
                for forbidden in ("附件下载：", "相关文章："):
                    assert forbidden not in f_md, (
                        f"Unexpected {forbidden!r} in {source_id} ({fixture_path.name}):\n{f_md}"
                    )

        dd605_cfg = yaml.safe_load(
            (repo_root / "sources" / "dd-605.yaml").read_text(encoding="utf-8")
        )
        assert dd605_cfg.get("cleaners", {}).get("strip_text_regexes"), (
            "Expected cleaners.strip_text_regexes in dd-605.yaml"
        )
        dd605_builder = WebDocumentBuilder(dd605_cfg)
        dd605_fixture = (
            repo_root / "tests" / "fixtures" / "dd-605" / "03-bVW3yqOBRMnoXFcO_K5Ejg.html"
        )
        dd605_url = "https://mp.weixin.qq.com/s/bVW3yqOBRMnoXFcO_K5Ejg"
        dd605_tree, _, _ = parse_html(
            dd605_fixture.read_text(encoding="utf-8").encode("utf-8"), "utf-8"
        )
        dd605_md = dd605_builder.html_markdown(
            dd605_tree,
            {
                "actual_url": dd605_url,
                "request_url": dd605_url,
                "charset": "utf-8",
            },
        )
        assert "对人工智能使用行为课税" in dd605_md
        for forbidden_wx in ("**点击“阅读原文”快速投稿~**", "点击“阅读原文”快速投稿~"):
            assert forbidden_wx not in dd605_md, (
                f"Unexpected {forbidden_wx!r} in dd-605 ({dd605_fixture.name}):\n{dd605_md}"
            )


class TestUnscopedHtmlFallbackExtraction:
    def test_unscoped_article_page_with_chrome_sidebars_comments_and_related_links_extracts_clean_main_body(
        self,
    ) -> None:
        """Unscoped article HTML strips site chrome, cookie banners, comments, and related links while keeping clean main body prose."""
        from wlh.processor.extractors.html.html_to_md import html_to_markdown

        html = """
        <html>
          <body>
            <header class="site-header">
              <nav><a href="/home">Home</a> <a href="/politics">Politics</a></nav>
            </header>
            <div class="cookie-consent-banner">Accept all cookies to continue browsing our platform.</div>
            <main>
              <article>
                <header class="entry-header">
                  <div class="cat-links">Politics</div>
                  <h1>Regulatory Reform Act</h1>
                  <div class="byline">By Jane Doe | Shared 400 times</div>
                </header>
                <div class="entry-content">
                  <p>Article 1 establishes the new <a href="https://news.example.org/ref">supervisory authority</a> over financial institutions across all member states.</p>
                  <p>Article 2 sets out the transition period and compliance reporting obligations for regulated entities.</p>
                </div>
              </article>
              <section id="comments" class="comments-area">
                <h3>User Comments</h3>
                <p>Great article! Click here to subscribe to our newsletter.</p>
              </section>
              <div class="related-posts">
                <h3>Related Stories</h3>
                <ul>
                  <li><a href="https://news.example.org/a">Story A</a></li>
                  <li><a href="https://news.example.org/b">Story B</a></li>
                </ul>
              </div>
            </main>
            <aside class="sidebar">Popular Posts...</aside>
            <footer>Copyright 2026</footer>
          </body>
        </html>
        """
        md = html_to_markdown(
            html,
            base_url="https://news.example.org/articles/1",
            content_xpath=None,
        )
        assert "# Regulatory Reform Act" in md
        assert "Article 1 establishes the new supervisory authority" in md
        assert "Article 2 sets out the transition period" in md
        assert "(https://news.example.org/ref)" not in md
        for forbidden in (
            "Accept all cookies",
            "User Comments",
            "Click here to subscribe",
            "Related Stories",
            "Popular Posts",
            "Copyright 2026",
        ):
            assert forbidden not in md, f"Unexpected boilerplate {forbidden!r} in Markdown:\n{md}"

    def test_unscoped_aspnet_or_cms_form_wrapping_main_content_preserves_document_body_and_strips_form_controls(
        self,
    ) -> None:
        """Unscoped pages wrapped inside a root form preserve main content while stripping form controls and hidden state."""
        from wlh.processor.extractors.html.html_to_md import html_to_markdown

        html = """
        <html>
          <body>
            <form id="aspnetForm" method="post" action="/page.aspx">
              <input type="hidden" name="__VIEWSTATE" value="dDwxMjM0NTY3ODkwO3Q8O2w8aTwxPjs+O2w8dDw7bDxpPDA+Oz47bDx0PHA8cDxsPFRleHQ7PjtsPFZhbHVlOz4+Pjs7Pjs+Pjs+Pjs+" />
              <select><option>Filter</option></select>
              <div id="MainContent" class="post-content">
                <h1>Notice of Public Hearing</h1>
                <p>The commission will hold a public hearing on environmental standards on May 12.</p>
                <pre><code>CASE-ID: ENV-2026-09\nSTATUS: OPEN</code></pre>
              </div>
            </form>
          </body>
        </html>
        """
        md = html_to_markdown(
            html,
            base_url="https://portal.example.org/page.aspx",
            content_xpath=None,
        )
        assert "# Notice of Public Hearing" in md
        assert (
            "The commission will hold a public hearing on environmental standards on May 12." in md
        )
        assert "CASE-ID: ENV-2026-09" in md
        assert "Filter" not in md
        assert "__VIEWSTATE" not in md

    def test_unscoped_forum_thread_inside_noscript_crawler_outlet_and_inline_mod_form_extracts_all_posts(
        self,
    ) -> None:
        """Unscoped forum threads inside noscript crawler outlets or moderation forms retain all discussion posts."""
        from wlh.processor.extractors.html.html_to_md import html_to_markdown

        html_noscript_forum = """
        <html>
          <body>
            <section id="d-splash">Loading...</section>
            <noscript>
              <div id="main-outlet">
                <div id="post_1" class="topic-body crawler-post">
                  <div class="post">
                    <p>First post explaining the network bridge DNS resolution issue in detail.</p>
                  </div>
                </div>
                <div id="post_2" class="topic-body crawler-post">
                  <div class="post">
                    <p>Second post providing the custom bridge network workaround and verification steps.</p>
                  </div>
                </div>
              </div>
            </noscript>
          </body>
        </html>
        """
        md_a = html_to_markdown(
            html_noscript_forum,
            base_url="https://forum.example.org/t/10",
            content_xpath=None,
        )
        assert "First post explaining the network bridge" in md_a
        assert "Second post providing the custom bridge network workaround" in md_a

        html_inline_mod_forum = """
        <html>
          <body>
            <form action="inline-mod/post/switch" method="post" class="InlineModForm section">
              <ol class="messageList" id="messageList">
                <li id="post-101" class="message">
                  <div class="messageInfo primaryContent">
                    <div class="messageContent">
                      <article>
                        <blockquote class="messageText">
                          Question regarding municipal zoning variance application procedures for corner lots.
                        </blockquote>
                      </article>
                    </div>
                  </div>
                </li>
                <li id="post-102" class="message">
                  <div class="messageInfo primaryContent">
                    <div class="messageContent">
                      <article>
                        <blockquote class="messageText">
                          You must submit Form Z-14 along with a certified site survey at least 30 days prior to the hearing.
                        </blockquote>
                      </article>
                    </div>
                  </div>
                </li>
              </ol>
            </form>
          </body>
        </html>
        """
        md_b = html_to_markdown(
            html_inline_mod_forum,
            base_url="https://forum.example.org/threads/20",
            content_xpath=None,
        )
        assert "Question regarding municipal zoning variance application procedures" in md_b
        assert "You must submit Form Z-14 along with a certified site survey" in md_b

    def test_unscoped_scientific_html_with_mathml_latex_annotations_and_currency_amounts_formats_formulas_and_escapes_currency_dollars(
        self,
    ) -> None:
        """Unscoped scientific HTML converts MathML, KaTeX annotations, and formula images to LaTeX while escaping currency dollar signs."""
        from wlh.processor.extractors.html.html_to_md import html_to_markdown

        html = """
        <html>
          <body>
            <main>
              <h1> Quantitative Assessment of Statutory Penalties</h1>
              <p>The ratio is defined as <math xmlns="http://www.w3.org/1998/Math/MathML"><msub><mi>R</mi><mn>1</mn></msub><mo>=</mo><mfrac><msub><mi>t</mi><mi>j</mi></msub><msub><mi>t</mi><mn>2</mn></msub></mfrac></math> for all intervals.</p>
              <p>The threshold scale is <span class="katex"><span class="katex-mathml"><math><semantics><mrow><msup><mn>2</mn><mn>20</mn></msup><mo>+</mo><mn>1</mn></mrow><annotation encoding="application/x-tex">{2}^{20}+1</annotation></semantics></math></span><span class="katex-html" aria-hidden="true">220+1</span></span> units.</p>
              <p>Decay channel: <img src="http://latex.codecogs.com/gif.latex?W%20%5Cto%20e%20%5Cnu_e" /> in standard notation.</p>
              <p>The filing fee is $50 and the maximum statutory penalty is $5,000.00 per violation.</p>
            </main>
          </body>
        </html>
        """
        md = html_to_markdown(
            html,
            base_url="https://science.example.org/papers/1",
            content_xpath=None,
        )
        assert "${2}^{20}+1$" in md
        assert "220+1" not in md
        assert r"$W \to e \nu_e$" in md
        assert r"\frac" in md
        assert r"\$50" in md
        assert r"\$5,000.00" in md

    def test_unscoped_layout_and_empty_tables_do_not_emit_bogus_markdown_table_grids_while_data_tables_preserve_structure(
        self,
    ) -> None:
        """Unscoped extraction drops empty tables, unwraps single-cell layout tables into normal blocks, and preserves genuine data tables."""
        from wlh.processor.extractors.html.html_to_md import html_to_markdown

        html = """
        <html>
          <body>
            <main>
              <table><tr><td></td><td></td></tr></table>
              <table border="0">
                <tr>
                  <td>
                    <h2>Section Title</h2>
                    <p>Paragraph inside layout table.</p>
                    <pre>def run():\n    return True</pre>
                  </td>
                </tr>
              </table>
              <table>
                <thead>
                  <tr><th>Metric</th><th>Value</th></tr>
                </thead>
                <tbody>
                  <tr><td>Latency</td><td>12ms</td></tr>
                  <tr><td>Throughput</td><td>1000 rps</td></tr>
                </tbody>
              </table>
            </main>
          </body>
        </html>
        """
        md = html_to_markdown(
            html,
            base_url="https://docs.example.org/guide",
            content_xpath=None,
        )
        assert "|  |" not in md
        assert "## Section Title" in md
        assert "| ## Section Title" not in md
        assert "Paragraph inside layout table." in md
        assert "def run():\n    return True" in md
        assert "| Metric | Value |" in md
        assert "| Latency | 12ms |" in md

    def test_self_closing_iframe_inside_noscript_does_not_swallow_subsequent_body_content(
        self,
    ) -> None:
        """Self-closing iframe tags inside noscript blocks do not consume subsequent main body content."""
        from wlh.processor.extractors.html.html_to_md import html_to_markdown

        html = """
        <html>
          <body>
            <noscript><iframe src="https://tagmanager.example.org/ns.html" height="0" width="0" style="display:none;visibility:hidden" /></noscript>
            <main>
              <h1>Service Overview</h1>
              <p>Reliable cloud infrastructure for distributed engineering teams.</p>
            </main>
          </body>
        </html>
        """
        md = html_to_markdown(
            html,
            base_url="https://cloud.example.org/overview",
            content_xpath=None,
        )
        assert "# Service Overview" in md
        assert "Reliable cloud infrastructure for distributed engineering teams." in md
