"""Heuristic main-content selection, boilerplate pruning, and math recovery for unscoped HTML."""

from __future__ import annotations

import re
from typing import Any, cast
from urllib.parse import unquote

import lxml.etree as _etree  # type: ignore[import-untyped]
import lxml.html as lxml_html  # type: ignore[import-untyped]

etree: Any = _etree

_WS_RE = re.compile(r"\s+")

_FORUM_QA_ATTR_RE = re.compile(
    r"(?:^|[\s_-])(?:forum|thread|topic|showthread|viewtopic|discussion|qa-template|"
    r"qa-main|qa-q-view|messagelist|crawler-post|postbody|answercell|vbulletin|phpbb|"
    r"xenforo|discourse|flarum)(?:$|[\s_-])",
    re.I,
)

_BOILERPLATE_TOKEN_RE = re.compile(
    r"(?:^|[\s_-])(?:"
    r"cookie|cookies|consent|gdpr|onetrust|cybotcookiebot|cc-banner|cc-window|privacy-banner|"
    r"paywall-modal|paywall-banner|paywall-overlay|regwall|modal|popup|popover|lightbox|"
    r"newsletter|subscribe|subscription|mailing-list|promo-banner|app-banner|smart-banner|"
    r"navbar|nav-bar|main-nav|site-nav|sub-nav|footer-nav|mobile-nav|top-nav|"
    r"menubar|superfish|sf-menu|breadcrumb|breadcrumbs|pagination|pagenav|page-numbers|"
    r"pager|skip-link|skip-to|back-to-top|to-top|scroll-top|table-of-contents|"
    r"sidebar|side-bar|right-rail|left-rail|widget-area|widgets|facet-filters|filter-panel|"
    r"ad-slot|ad-unit|ad-wrapper|ad-container|ad-banner|advert|advertisement|adsense|"
    r"adsbygoogle|outbrain|taboola|sponsored|partner-content|"
    r"share-bar|social-share|sharedaddy|addtoany|sharethis|addthis|social-links|follow-us|"
    r"related-posts|related-articles|related-content|related-stories|related-news|"
    r"recommended|popular-posts|most-read|most-popular|trending|more-stories|more-articles|"
    r"more-posts|more-posts-view|archive-view|postbox|"
    r"read-more|read-next|up-next|also-like|you-may-like|further-reading|around-the-web|"
    r"post-navigation|nav-links|prev-next|"
    r"author-bio|author-box|author-info|about-author|about-the-author|"
    r"byline|entry-meta|post-meta|article-meta|meta-info|cat-links|tag-links|tags-links|"
    r"post-tags|article-tags|tagcloud|tag-cloud|"
    r"entry-footer|post-footer|article-footer|site-footer|page-footer|copyright|disclaimer"
    r")(?:$|[\s_-])",
    re.I,
)

_COMMENT_BOILERPLATE_RE = re.compile(
    r"(?:^|[\s_-])(?:"
    r"comments|comment-list|comment-area|comments-area|comment-section|comment-respond|"
    r"comment-form|disqus|disqus_thread|fb-comments|post-comments|article-comments|"
    r"user-comments|livefyre|coral-talk|respond"
    r")(?:$|[\s_-])",
    re.I,
)

_POSITIVE_CONTENT_RE = re.compile(
    r"(?:^|[\s_-])(?:"
    r"article|article-body|article-content|articlebody|entry-content|post-content|"
    r"post-body|story-body|story-content|main-content|main-outlet|page-content|"
    r"markdown-body|rst-content|doc-content|document-content|field--name-body|"
    r"node__content|content-body|text-body|detail-content|Messagelist|qa-main|"
    r"product-description|product-detail|service-content"
    r")(?:$|[\s_-])",
    re.I,
)

_PROTECTED_WRAPPER_RE = re.compile(
    r"(?:^|[\s_-])(?:"
    r"main|content|main-content|page|wrapper|container|layout|app|root|__next|"
    r"site-content|primary|body-content|article|post|hentry|entry|topic|thread|"
    r"forum|question|product"
    r")(?:$|[\s_-])",
    re.I,
)

_ACCORDION_TAB_RE = re.compile(
    r"(?:^|[\s_-])(?:"
    r"tab-pane|tabpanel|acd-a-content|accordion-content|accordion-body|faq-answer|"
    r"collapse|panel-collapse"
    r")(?:$|[\s_-])",
    re.I,
)

_NEGATIVE_FORM_RE = re.compile(
    r"(?:^|[\s_-])(?:"
    r"search|login|signin|sign-in|signup|sign-up|register|subscribe|newsletter|"
    r"comment-form|respond|facet|filter|cart|poll|share"
    r")(?:$|[\s_-])",
    re.I,
)

_HEADER_NOISE_RE = re.compile(
    r"(?:^|[\s_-])(?:"
    r"cat-links|category|categories|byline|author|date|time|published|posted-on|"
    r"meta|entry-meta|post-meta|share|social|breadcrumb|breadcrumbs|tags|views|"
    r"reading-time|read-time|comment-count"
    r")(?:$|[\s_-])",
    re.I,
)

_TEX_IMG_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:mimetex|mathtex)\.cgi\?(.+)$", re.I),
    re.compile(r"codecogs\.com/(?:gif|png|svg)\.latex\?(.+)$", re.I),
    re.compile(r"chart\.apis\.google\.com/chart\?[^\"']*?\bchl=([^&\"']+)", re.I),
    re.compile(r"/latex\.php\?latex=([^&\"']+)", re.I),
)


def _local_tag(el: Any) -> str:
    if not isinstance(getattr(el, "tag", None), str):
        return ""
    return str(el.tag).split("}")[-1].split(":")[-1].lower()


def _clean_ws(text: str | None) -> str:
    return _WS_RE.sub(" ", text or "").strip()


def _remove_keep_tail(el: Any) -> None:
    parent = el.getparent()
    if parent is None:
        return
    if el.tail:
        prev = el.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def _replace_with_text(el: Any, replacement: str) -> None:
    parent = el.getparent()
    if parent is None:
        return
    text = replacement + (el.tail or "")
    prev = el.getprevious()
    if prev is not None:
        prev.tail = (prev.tail or "") + text
    else:
        parent.text = (parent.text or "") + text
    parent.remove(el)


def _attr_signature(el: Any) -> str:
    parts = [
        el.get("id") or "",
        el.get("class") or "",
        el.get("role") or "",
        el.get("aria-label") or "",
        el.get("data-testid") or "",
        el.get("data-component") or "",
        el.get("itemprop") or "",
    ]
    return " ".join(p for p in parts if p)


def recover_noscript_content(tree: Any) -> None:
    """Unwrap `<noscript>` blocks when they hold SSR/crawler main content (e.g. Discourse forums)."""
    body = tree.body if getattr(tree, "body", None) is not None else tree
    noscripts = list(cast(list[Any], body.xpath(".//noscript")))
    if not noscripts:
        return

    # Measure visible text outside <noscript>, <script>, <style>
    outside_len = 0
    for el in body.iter():
        tag = _local_tag(el)
        if tag in {"noscript", "script", "style", "template", "svg"}:
            continue
        if any(_local_tag(a) in {"noscript", "script", "style"} for a in el.iterancestors()):
            continue
        if el.text:
            outside_len += len(el.text.strip())
        if el.tail:
            outside_len += len(el.tail.strip())

    for ns in noscripts:
        parent = ns.getparent()
        if parent is None:
            continue
        inner_html = "".join(
            ([ns.text] if ns.text else [])
            + [etree.tostring(c, encoding="unicode", method="html") for c in ns]
        ).strip()
        if not inner_html:
            continue
        lower_html = inner_html.lower()
        has_semantic_outlet = any(
            marker in lower_html
            for marker in (
                "main-outlet",
                "crawler-post",
                "topic-body",
                "<article",
                "<main",
                'itemprop="articlebody"',
            )
        )
        if not has_semantic_outlet and outside_len >= 250:
            continue
        try:
            fragments = cast(Any, lxml_html).fragments_fromstring(inner_html)
        except Exception:
            continue
        frag_text = " ".join(
            _clean_ws(f.text_content()) if hasattr(f, "text_content") else _clean_ws(str(f))
            for f in fragments
        ).strip()
        if not has_semantic_outlet:
            if len(frag_text) < 120:
                continue
            if (
                re.search(r"enable\s+javascript|javascript\s+is\s+disabled", frag_text, re.I)
                and len(frag_text) < 300
            ):
                continue
        idx = parent.index(ns)
        for offset, item in enumerate(fragments):
            if isinstance(item, etree._Element):
                parent.insert(idx + offset, item)
        if ns.tail:
            prev = ns.getprevious()
            if prev is not None:
                prev.tail = (prev.tail or "") + ns.tail
            else:
                parent.text = (parent.text or "") + ns.tail
        parent.remove(ns)


def _mathml_node_to_latex(node: Any) -> str:
    """Recursively convert a MathML element into LaTeX syntax."""
    tag = _local_tag(node)
    if tag == "semantics":
        for ann in node:
            if _local_tag(ann) == "annotation":
                enc = (ann.get("encoding") or "").lower()
                if "tex" in enc and _clean_ws(ann.text_content()):
                    return _clean_ws(ann.text_content())
        for child in node:
            if _local_tag(child) != "annotation" and _local_tag(child) != "annotation-xml":
                return _mathml_node_to_latex(child)

    children = [c for c in node if isinstance(getattr(c, "tag", None), str)]
    if tag in {"mi", "mn", "mo"}:
        txt = _clean_ws(node.text_content())
        return txt
    if tag == "mtext":
        txt = _clean_ws(node.text_content())
        return f"\\text{{{txt}}}" if txt else ""
    if tag == "mspace":
        return " "
    if tag == "msub" and len(children) >= 2:
        base = _mathml_node_to_latex(children[0])
        sub = _mathml_node_to_latex(children[1])
        return f"{{{base}}}_{{{sub}}}"
    if tag == "msup" and len(children) >= 2:
        base = _mathml_node_to_latex(children[0])
        sup = _mathml_node_to_latex(children[1])
        return f"{{{base}}}^{{{sup}}}"
    if tag == "msubsup" and len(children) >= 3:
        base = _mathml_node_to_latex(children[0])
        sub = _mathml_node_to_latex(children[1])
        sup = _mathml_node_to_latex(children[2])
        return f"{{{base}}}_{{{sub}}}^{{{sup}}}"
    if tag == "mfrac" and len(children) >= 2:
        num = _mathml_node_to_latex(children[0])
        den = _mathml_node_to_latex(children[1])
        return f"\\frac{{{num}}}{{{den}}}"
    if tag == "msqrt":
        inner = "".join(_mathml_node_to_latex(c) for c in children)
        return f"\\sqrt{{{inner}}}"
    if tag == "mroot" and len(children) >= 2:
        base = _mathml_node_to_latex(children[0])
        idx = _mathml_node_to_latex(children[1])
        return f"\\sqrt[{idx}]{{{base}}}"
    if tag == "mover" and len(children) >= 2:
        base = _mathml_node_to_latex(children[0])
        over = _mathml_node_to_latex(children[1])
        if over in {"¯", "‾", "_"}:
            return f"\\overline{{{base}}}"
        if over in {"^", "ˆ"}:
            return f"\\hat{{{base}}}"
        if over in {"~", "˜"}:
            return f"\\tilde{{{base}}}"
        if over in {"→", "⟶"}:
            return f"\\vec{{{base}}}"
        return f"\\overset{{{over}}}{{{base}}}"
    if tag == "munder" and len(children) >= 2:
        base = _mathml_node_to_latex(children[0])
        under = _mathml_node_to_latex(children[1])
        return f"\\underset{{{under}}}{{{base}}}"
    if tag == "munderover" and len(children) >= 3:
        base = _mathml_node_to_latex(children[0])
        under = _mathml_node_to_latex(children[1])
        over = _mathml_node_to_latex(children[2])
        return f"{{{base}}}_{{{under}}}^{{{over}}}"
    if tag == "mfenced":
        open_d = node.get("open", "(")
        close_d = node.get("close", ")")
        sep = node.get("separators", ",")
        inner = (sep[0] if sep else ",").join(_mathml_node_to_latex(c) for c in children)
        return f"\\left{open_d}{inner}\\right{close_d}"
    if tag == "mtable":
        rows: list[str] = []
        for tr in children:
            if _local_tag(tr) in {"mtr", "mlabeledtr"}:
                cells = [_mathml_node_to_latex(td) for td in tr if _local_tag(td) == "mtd"]
                if cells:
                    rows.append(" & ".join(cells))
        if rows:
            col_spec = "l" * max(1, rows[0].count("&") + 1)
            return f"\\begin{{array}}{{{col_spec}}}" + " \\\\ ".join(rows) + "\\end{array}"
    if children:
        return "".join(_mathml_node_to_latex(c) for c in children)
    return _clean_ws(node.text_content())


def convert_math_elements_to_latex(root: Any) -> None:
    """Convert MathML, KaTeX, MathJax script tags, and TeX formula images into `$...$` LaTeX."""
    # 1. Remove duplicate visual spans inside KaTeX / MathJax when MathML or TeX annotation exists
    for dup in list(
        cast(
            list[Any],
            root.xpath(
                './/*[contains(attribute::class, "katex-html") or contains(attribute::class, "MathJax_Preview")]'
            ),
        )
    ):
        _remove_keep_tail(dup)

    # 2. Convert <script type="math/tex"> before scripts are stripped
    for sc in list(cast(list[Any], root.xpath(".//script[@type]"))):
        stype = (sc.get("type") or "").lower()
        if "math/tex" in stype or "math/mml" in stype:
            tex = (sc.text or "").strip()
            if tex and "math/tex" in stype:
                wrapper = "$$" if "mode=display" in stype and "\n" not in tex else "$"
                _replace_with_text(sc, f" {wrapper}{tex}{wrapper} ")

    # 3. Convert elements with explicit data-tex / data-latex / data-formula attributes
    for el in list(cast(list[Any], root.xpath(".//*[@data-tex or @data-latex or @data-formula]"))):
        tex = (el.get("data-tex") or el.get("data-latex") or el.get("data-formula") or "").strip()
        if tex:
            _replace_with_text(el, f" ${tex}$ ")

    # 4. Convert <math> / <m:math> elements
    for math_el in list(cast(list[Any], root.xpath('.//*[local-name()="math"]'))):
        if math_el.getparent() is None:
            continue
        alt = (math_el.get("alttext") or "").strip()
        tex_ann = ""
        for ann in math_el.xpath('.//*[local-name()="annotation"]'):
            enc = (ann.get("encoding") or "").lower()
            if "tex" in enc or not enc:
                cand = _clean_ws(ann.text_content())
                if cand:
                    tex_ann = cand
                    break
        tex = tex_ann or alt or _mathml_node_to_latex(math_el).strip()
        if tex:
            tex = tex.strip("$").strip()
            _replace_with_text(math_el, f"${tex}$")

    # 5. Convert TeX formula <img> elements (e.g. codecogs, mimetex, mathtex)
    for img in list(cast(list[Any], root.xpath(".//img[@src or @alt]"))):
        src = (img.get("src") or "").strip()
        alt = (img.get("alt") or "").strip()
        cls = (img.get("class") or "").lower()
        tex = ""
        for pat in _TEX_IMG_PATTERNS:
            m = pat.search(src)
            if m:
                tex = unquote(m.group(1)).strip()
                tex = re.sub(r"^\\(?:dpi|bg|fg|large|small|tiny|huge)\b(?:\{[^}]*\})?\s*", "", tex)
                break
        if not tex and alt and re.search(r"\b(?:tex|latex|math|mwe-math)\b", cls):
            tex = alt.strip("$").strip()
        if tex:
            _replace_with_text(img, f"${tex}$")


def _is_genuine_inline_math(inner: str) -> bool:
    s = inner.strip()
    if not s or "\n\n" in s or "|" in s:
        return False
    if "\n" in s and "\\begin" not in s and "\\\\" not in s:
        return False
    if re.search(r"</?[a-zA-Z][^>]*>", s):
        return False
    if re.search(r"\\[a-zA-Z]+", s):
        return True
    if re.match(r"^\d[\d,]*(\.\d{2})?(\s|$|[-–—,;:\)\]])", s):
        if re.fullmatch(r"\d+(?:\.\d+)?(?:\s*[/^_*+\-=<>±×÷]\s*\d+(?:\.\d+)?)+", s):
            return True
        return False
    if re.search(r"\b[a-zA-Z]{3,}\s+[a-zA-Z]{2,}\s+[a-zA-Z]{3,}\b", s):
        return False
    if re.search(r"[\^_=<>≤≥±×÷∑∫∂√α-ωΑ-Ω]", s) and len(s) <= 120:
        return True
    if len(s) <= 15 and not re.search(r"\s", s) and not re.fullmatch(r"[\d,]+(?:\.\d+)?-?", s):
        return True
    return False


def escape_currency_dollars(md: str) -> str:
    """Escape literal currency `$` as `\\$` while preserving genuine `$...$` and `$$...$$` math."""
    if "$" not in md:
        return md
    out: list[str] = []
    i = 0
    n = len(md)
    while i < n:
        if md.startswith("```", i):
            end = md.find("```", i + 3)
            if end == -1:
                out.append(md[i:])
                break
            out.append(md[i : end + 3])
            i = end + 3
        elif md[i] == "`":
            end = md.find("`", i + 1)
            if end == -1 or "\n" in md[i + 1 : end]:
                out.append(md[i])
                i += 1
            else:
                out.append(md[i : end + 1])
                i = end + 1
        elif md.startswith("\\$", i):
            out.append("\\$")
            i += 2
        elif md.startswith("$$", i):
            end = md.find("$$", i + 2)
            if end != -1 and "\n\n" not in md[i + 2 : end]:
                out.append(md[i : end + 2])
                i = end + 2
            else:
                out.append("\\$\\$")
                i += 2
        elif md[i] == "$":
            end = i + 1
            found = -1
            while end < n:
                if md[end] == "\\" and end + 1 < n:
                    end += 2
                    continue
                if md[end] == "$":
                    if end + 1 < n and md[end + 1] == "$":
                        break
                    found = end
                    break
                end += 1
            if found != -1 and _is_genuine_inline_math(md[i + 1 : found]):
                out.append(md[i : found + 1])
                i = found + 1
            else:
                out.append("\\$")
                i += 1
        else:
            out.append(md[i])
            i += 1
    return "".join(out)


def _link_density(el: Any) -> tuple[float, int, int]:
    """Return (link_density, total_text_len, link_count) for an element."""
    total_text = _clean_ws(el.text_content())
    total_len = len(total_text)
    if total_len == 0:
        return (0.0, 0, 0)
    links = el.xpath(
        './/a[@href and not(starts-with(@href, "#")) and not(starts-with(@href, "javascript:"))]'
    )
    link_len = sum(len(_clean_ws(a.text_content())) for a in links)
    return (min(1.0, link_len / total_len), total_len, len(links))


def _is_forum_or_qa_page(tree: Any) -> bool:
    """Detect forum threads, discussion topics, and Q&A pages where user replies are main content."""
    root_sig = " ".join(
        f"{el.get('id') or ''} {el.get('class') or ''} {el.get('itemtype') or ''}"
        for el in (tree, getattr(tree, "body", None))
        if el is not None
    )
    if _FORUM_QA_ATTR_RE.search(root_sig):
        return True
    if tree.xpath(
        '//*[attribute::id="main-outlet" or attribute::id="messageList" or contains(attribute::class, "crawler-post") '
        'or contains(attribute::class, "messageList") or contains(attribute::class, "qa-main") '
        'or contains(attribute::class, "qa-q-view") or contains(attribute::class, "postbody") '
        'or contains(attribute::itemtype, "DiscussionForumPosting") or contains(attribute::itemtype, "QAPage")]'
    ):
        return True
    return False


def select_fallback_nodes(tree: Any) -> list[Any]:
    """Clean boilerplate chrome and select the primary content root(s) when `content_xpath` is None."""
    recover_noscript_content(tree)
    body = tree.body if getattr(tree, "body", None) is not None else tree
    convert_math_elements_to_latex(body)

    is_forum = _is_forum_or_qa_page(tree)
    total_body_len = len(_clean_ws(body.text_content()))

    # 1. Remove hidden elements, protecting root FOUC wrappers and accordion/tab content panels
    for el in list(cast(list[Any], body.xpath(".//*[@style or @hidden]"))):
        if el is body or el.getparent() is None:
            continue
        sig = _attr_signature(el)
        if _ACCORDION_TAB_RE.search(sig):
            continue
        st = (el.get("style") or "").lower()
        is_hidden = "hidden" in el.attrib or bool(
            re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", st)
        )
        if not is_hidden:
            continue
        if el.xpath(".//main | .//article | .//*[@role='main']"):
            continue
        el_len = len(_clean_ws(el.text_content()))
        if total_body_len > 300 and el_len > total_body_len * 0.45:
            continue
        _remove_keep_tail(el)

    # 2. Strip non-content tags and form controls (while keeping content-bearing <form> wrappers)
    for bad in list(
        cast(
            list[Any],
            body.xpath(
                ".//script | .//style | .//noscript | .//template | .//svg | .//canvas | "
                ".//audio | .//video | .//source | .//track | .//map | .//area | "
                ".//object | .//embed | .//dialog | .//iframe | .//button | "
                ".//input | .//select | .//textarea | .//datalist | .//optgroup | .//option"
            ),
        )
    ):
        _remove_keep_tail(bad)

    for form in list(cast(list[Any], body.xpath(".//form"))):
        if form.getparent() is None:
            continue
        sig = f"{form.get('id') or ''} {form.get('class') or ''} {form.get('action') or ''} {form.get(' role') or ''}"
        form_len = len(_clean_ws(form.text_content()))
        has_main_child = bool(
            form.xpath(
                ".//main | .//article | .//h1 | .//h2 | .//pre | "
                './/*[contains(attribute::class, "content") or contains(attribute::class, "messageList") or contains(attribute::class, "post")]'
            )
        )
        if _NEGATIVE_FORM_RE.search(sig) and not has_main_child and form_len < 600:
            _remove_keep_tail(form)
        elif not has_main_child and form_len < 160 and not form.xpath(".//p"):
            _remove_keep_tail(form)

    # Short fragment fast-path: if minimal HTML without page chrome, return [body] directly
    has_page_chrome = bool(
        body.xpath(
            ".//header | .//footer | .//nav | .//aside | .//main | .//article | "
            './/*[contains(attribute::class, "sidebar") or contains(attribute::class, "footer") or contains(attribute::class, "cookie")]'
        )
    )
    if total_body_len < 400 and not has_page_chrome:
        return [body]

    # 3. Remove <nav>, <footer>, <aside>, and clean <header>
    for nav in list(
        cast(
            list[Any],
            body.xpath(
                './/nav | .//*[@role="navigation" or @role="search" or @role="contentinfo"]'
            ),
        )
    ):
        if nav is not body and not nav.xpath(".//main | .//article"):
            _remove_keep_tail(nav)

    for footer in list(cast(list[Any], body.xpath(".//footer"))):
        if footer is not body and not footer.xpath(".//main | .//article"):
            _remove_keep_tail(footer)

    for aside in list(cast(list[Any], body.xpath('.//aside | .//*[@role="complementary"]'))):
        if aside is body or aside.getparent() is None:
            continue
        if aside.xpath(".//main | .//article"):
            continue
        sig = _attr_signature(aside)
        ld, alen, _ = _link_density(aside)
        if (
            re.search(r"\b(?:note|warning|tip|admonition|callout|important|caution)\b", sig, re.I)
            and ld < 0.3
        ):
            continue
        if total_body_len > 0 and alen < total_body_len * 0.65:
            _remove_keep_tail(aside)

    all_h1s = body.xpath(".//h1")
    for hdr in list(cast(list[Any], body.xpath('.//header | .//*[@role="banner"]'))):
        if hdr is body or hdr.getparent() is None:
            continue
        if hdr.xpath(".//main | .//article"):
            continue
        in_article_or_main = any(_local_tag(a) in {"article", "main"} for a in hdr.iterancestors())
        hdr_sig = _attr_signature(hdr)
        is_site_header = (
            not in_article_or_main
            or hdr.get("role") == "banner"
            or bool(
                re.search(
                    r"\b(?:site-header|main-header|global-header|navbar|topbar|masthead)\b",
                    hdr_sig,
                    re.I,
                )
            )
        )
        if is_site_header:
            hdr_h1s = hdr.xpath(".//h1")
            if (
                len(all_h1s) == 1
                and len(hdr_h1s) == 1
                and len(_clean_ws(hdr_h1s[0].text_content())) > 8
            ):
                # Preserve the sole document H1, strip the rest of the site header
                h1_el = hdr_h1s[0]
                for child in list(hdr):
                    if child is not h1_el and h1_el not in child.iterdescendants():
                        _remove_keep_tail(child)
            else:
                _remove_keep_tail(hdr)
        else:
            # Article/entry header: keep H1/H2 title, strip category/byline/author/share clutter
            for sub in list(cast(list[Any], hdr.xpath(".//*"))):
                if sub.getparent() is None:
                    continue
                if _local_tag(sub) in {"h1", "h2"} or sub.xpath(".//h1 | .//h2"):
                    continue
                sub_sig = _attr_signature(sub)
                if _HEADER_NOISE_RE.search(sub_sig) or _local_tag(sub) in {
                    "ul",
                    "ol",
                    "time",
                    "figure",
                }:
                    _remove_keep_tail(sub)

    # 4. Prune boilerplate blocks by class/id/role/aria signatures
    remaining_len = max(1, len(_clean_ws(body.text_content())))
    for el in list(
        cast(
            list[Any],
            body.xpath(
                ".//*[@role or @aria-label or @data-testid or attribute::id or attribute::class]"
            ),
        )
    ):
        if el is body or el.getparent() is None:
            continue
        tag = _local_tag(el)
        if tag in {"body", "html", "main", "h1"}:
            continue
        if el.xpath(".//main | .//*[@role='main' or @itemprop='articleBody']"):
            continue
        sig = _attr_signature(el)
        if not sig:
            continue
        if _POSITIVE_CONTENT_RE.search(sig):
            continue
        is_comment_bp = not is_forum and bool(_COMMENT_BOILERPLATE_RE.search(sig))
        is_bp = is_comment_bp or bool(_BOILERPLATE_TOKEN_RE.search(sig))
        if not is_bp:
            continue
        if el.xpath(".//article"):
            # Only protect if the inner <article> is the primary long-form article, not teaser cards inside a related/sidebar block
            art_p_len = sum(len(_clean_ws(p.text_content())) for p in el.xpath(".//article//p"))
            if art_p_len > 300 and art_p_len > remaining_len * 0.40:
                continue
        el_len = len(_clean_ws(el.text_content()))
        if not is_comment_bp and remaining_len > 400 and el_len > remaining_len * 0.60:
            continue
        # Do not strip an <article> or protected wrapper unless it is comment/related/archive boilerplate or high link density
        if (tag == "article" or _PROTECTED_WRAPPER_RE.search(sig)) and el_len > 350:
            ld, _, _ = _link_density(el)
            if (
                ld < 0.35
                and not is_comment_bp
                and not re.search(r"\b(?:more-posts|archive-view|postbox|related)\b", sig, re.I)
            ):
                continue
        _remove_keep_tail(el)

    # 5. Select primary content container(s)
    # 5a. Forum / Q&A outlet
    if is_forum:
        forum_roots = cast(
            list[Any],
            body.xpath(
                './/*[attribute::id="main-outlet" or attribute::id="messageList" or contains(attribute::class, "messageList") '
                'or contains(attribute::class, "qa-main")] | .//main | .//*[@role="main"]'
            ),
        )
        if forum_roots:
            root_set = set(forum_roots)
            top_roots = [
                r for r in forum_roots if not any(a in root_set for a in r.iterancestors())
            ]
            if sum(len(_clean_ws(r.text_content())) for r in top_roots) >= 80:
                return top_roots

    # 5b. Explicit article / body containers vs outer <main>
    body_text_now = len(_clean_ws(body.text_content()))
    article_candidates = cast(
        list[Any],
        body.xpath(
            './/*[@itemprop="articleBody"] | .//article | '
            './/*[contains(attribute::class, "entry-content") or contains(attribute::class, "post-content") '
            'or contains(attribute::class, "article-body") or contains(attribute::class, "article__body") '
            'or contains(attribute::class, "articleBody") or contains(attribute::class, "article-content") '
            'or contains(attribute::class, "article-main") or contains(attribute::class, "story-body") '
            'or contains(attribute::class, "markdown-body") or contains(attribute::class, "rst-content") '
            'or contains(attribute::class, "doc-content") or attribute::class="article" or attribute::class="entry"]'
        ),
    )
    valid_art: list[Any] = []
    for cand in article_candidates:
        ld, clen, _ = _link_density(cand)
        if clen < 40:
            continue
        if ld > 0.55 and clen < 450 and len(article_candidates) > 1:
            continue
        valid_art.append(cand)

    if valid_art:
        art_set = set(valid_art)
        top_art = [c for c in valid_art if not any(a in art_set for a in c.iterancestors())]
        # If multiple <article> candidates exist on a non-forum page and one dominates in <p> prose, pick the dominant one
        if len(top_art) > 1 and not is_forum:
            scored_art = [
                (
                    sum(len(_clean_ws(p.text_content())) for p in c.xpath(".//p")),
                    len(_clean_ws(c.text_content())),
                    c,
                )
                for c in top_art
            ]
            scored_art.sort(key=lambda x: (x[0], x[1]), reverse=True)
            if scored_art[0][0] >= 250 and scored_art[0][0] >= scored_art[1][0] * 2.2:
                top_art = [scored_art[0][2]]

        art_len = sum(len(_clean_ws(c.text_content())) for c in top_art)
        main_nodes = cast(list[Any], body.xpath('.//main | .//*[@role="main"]'))
        main_len = sum(len(_clean_ws(m.text_content())) for m in main_nodes)
        main_p_len = sum(
            len(_clean_ws(p.text_content())) for m in main_nodes for p in m.xpath(".//p")
        )
        art_p_len = sum(len(_clean_ws(p.text_content())) for c in top_art for p in c.xpath(".//p"))
        if art_len >= min(120, int(body_text_now * 0.25)) and (
            not main_nodes or (art_p_len >= main_p_len * 0.65 and art_len >= main_len * 0.40)
        ):
            # Include document <h1> if it lies outside top_art
            h1s = body.xpath(".//h1")
            if len(h1s) == 1 and not any(
                h1s[0] is c or h1s[0] in c.iterdescendants() for c in top_art
            ):
                if len(_clean_ws(h1s[0].text_content())) > 3:
                    top_art = [h1s[0], *top_art]
            _prune_high_link_density_blocks(top_art, is_forum=is_forum)
            return top_art

    # 5c. Outer <main> / [role="main"] (e.g. multi-section service, listing, collection, docs pages)
    main_candidates = cast(list[Any], body.xpath('.//main | .//*[@role="main"]'))
    if main_candidates:
        m_set = set(main_candidates)
        top_main = [m for m in main_candidates if not any(a in m_set for a in m.iterancestors())]
        m_len = sum(len(_clean_ws(m.text_content())) for m in top_main)
        if m_len >= min(100, int(body_text_now * 0.20)):
            _prune_high_link_density_blocks(top_main, is_forum=is_forum)
            return top_main

    # 5d. Readability-style paragraph cluster scoring when no semantic container exists
    best_cluster = _score_best_paragraph_container(body)
    if best_cluster is not None and best_cluster is not body:
        result_nodes = [best_cluster]
        h1s = body.xpath(".//h1")
        if (
            len(h1s) == 1
            and h1s[0] is not best_cluster
            and h1s[0] not in best_cluster.iterdescendants()
        ):
            if len(_clean_ws(h1s[0].text_content())) > 3:
                result_nodes = [h1s[0], best_cluster]
        _prune_high_link_density_blocks(result_nodes, is_forum=is_forum)
        return result_nodes

    _prune_high_link_density_blocks([body], is_forum=is_forum)
    return [body]


def _score_best_paragraph_container(body: Any) -> Any | None:
    """Bottom-up paragraph density scorer for legacy DOMs without `<article>` or `<main>`."""
    scores: dict[int, float] = {}
    nodes_by_id: dict[int, Any] = {}
    for p in cast(list[Any], body.xpath(".//p | .//pre | .//blockquote")):
        txt = _clean_ws(p.text_content())
        if len(txt) < 25:
            continue
        ld, _, _ = _link_density(p)
        if ld > 0.55:
            continue
        punct = len(re.findall(r"[.,;!?。，；！？]", txt))
        base_score = (1.0 + min(len(txt) / 100.0, 6.0) + punct * 0.5) * (1.0 - ld)
        parent = p.getparent()
        grand = parent.getparent() if parent is not None else None
        for anc, weight in ((parent, 1.0), (grand, 0.5)):
            if anc is None or _local_tag(anc) in {"body", "html"}:
                continue
            aid = id(anc)
            if aid not in scores:
                sig = _attr_signature(anc)
                bonus = 0.0
                if _POSITIVE_CONTENT_RE.search(sig) or re.search(
                    r"\b(?:content|article|post|entry|story|text|body|main|news|conteudo|texto|materia)\b",
                    sig,
                    re.I,
                ):
                    bonus += 8.0
                if _BOILERPLATE_TOKEN_RE.search(sig):
                    bonus -= 15.0
                scores[aid] = bonus
                nodes_by_id[aid] = anc
            scores[aid] += base_score * weight

    if not scores:
        return None
    best_id = max(scores, key=lambda k: scores[k])
    best = nodes_by_id[best_id]
    # If best container's parent holds substantially more low-link-density paragraph prose, step up one level
    parent = best.getparent()
    if parent is not None and _local_tag(parent) not in {"body", "html"}:
        best_p = sum(len(_clean_ws(p.text_content())) for p in best.xpath(".//p"))
        par_p = sum(len(_clean_ws(p.text_content())) for p in parent.xpath(".//p"))
        par_ld, _, _ = _link_density(parent)
        if par_p >= best_p * 1.35 and par_ld < 0.35:
            best = parent
    best_len = len(_clean_ws(best.text_content()))
    body_len = len(_clean_ws(body.text_content()))
    best_p_total = sum(len(_clean_ws(p.text_content())) for p in best.xpath(".//p"))
    body_p_total = max(1, sum(len(_clean_ws(p.text_content())) for p in body.xpath(".//p")))
    if best_len >= 160 and best_len >= body_len * 0.30 and best_p_total >= body_p_total * 0.55:
        return best
    return None


def _prune_high_link_density_blocks(roots: list[Any], *, is_forum: bool) -> None:
    """Remove peripheral high-link-density navigation/related lists inside selected content roots."""
    total_non_link = 0
    for r in roots:
        ld, tlen, _ = _link_density(r)
        total_non_link += int(tlen * (1.0 - ld))
    if total_non_link < 250:
        return

    for r in roots:
        for block in list(cast(list[Any], r.xpath(".//ul | .//ol | .//div | .//section"))):
            if block is r or block.getparent() is None:
                continue
            if block.xpath(".//table | .//pre | .//h1 | .//article"):
                continue
            sig = _attr_signature(block)
            if _POSITIVE_CONTENT_RE.search(sig):
                continue
            if is_forum and _FORUM_QA_ATTR_RE.search(sig):
                continue
            ld, blen, lcount = _link_density(block)
            non_link_chars = int(blen * (1.0 - ld))
            tag = _local_tag(block)
            if tag in {"ul", "ol"} and lcount >= 3 and ld > 0.72 and non_link_chars < 60:
                _remove_keep_tail(block)
            elif (
                tag in {"div", "section"}
                and lcount >= 4
                and ld > 0.78
                and non_link_chars < 50
                and not block.xpath(".//p")
            ):
                _remove_keep_tail(block)
