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
