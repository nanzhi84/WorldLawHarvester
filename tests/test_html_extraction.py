"""Scoped legal HTML conversion and visible boilerplate removal contracts."""

from __future__ import annotations


class TestHtmlToMarkdownConversion:
    def test_scoped_html_with_tables_and_relative_links_renders_deterministic_markdown(
        self,
    ) -> None:
        """HTML within `content_xpath` converts headings, relative links, and tables into Markdown."""
        from wlh.extraction.markdown import html_to_markdown

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
        from wlh.extraction.markdown import html_to_markdown

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
        md = html_to_markdown(
            html,
            base_url="https://www.safe.gov.cn/www/illegal/listQuery?saffno=22d58c4edfbf401595848a52a3f91db6",
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
        from wlh.extraction.markdown import html_to_markdown

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
        md = html_to_markdown(
            html,
            base_url="https://www.irs.gov/irb/2026-35_IRB",
            content_xpath='//div[@id="block-pup-irs-barrio-content"]',
        )
        for forbidden in (
            "[HIGHLIGHTS OF THIS ISSUE](",
            "[INCOME TAX](",
            "[The IRS Mission](",
            "[](https://www.irs.gov/irb/2026-35_IRB",
        ):
            assert forbidden not in md, f"Unexpected TOC/anchor noise {forbidden!r} in Markdown:\n{md}"

        assert "# Internal Revenue Bulletin: 2026-35" in md
        assert "Substantive bulletin text on Section 43 inflation adjustment." in md
