"""Deterministic legal HTML conversion; no browser, network or implicit caches."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Sequence
from typing import Any, cast
from urllib.parse import urljoin, urlsplit

import lxml.etree as _etree  # type: ignore[import-untyped]

from wlh.cleaners.text_cleaner import UI_ACTION_LABELS, normalized
from wlh.core import WlhError
from wlh.processor.extractors.base import (
    EngineAdapter,
    ExtractionInput,
    ExtractionOptions,
    QuirkHookRunner,
)
from wlh.processor.extractors.html.dom_tree import document_tree
from wlh.processor.extractors.html.fallback import (
    escape_currency_dollars,
    select_fallback_nodes,
)
from wlh.processor.extractors.html.tables_and_lists import (
    expand_rows,
    ordered_marker,
    span,
    spanning_table,
)

etree: Any = _etree

MARKDOWN_CONTRACT = "wlh.markdown.v6"


def _remove_element(el: Any) -> None:
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


def clean_html_tree(node: Any, *, quirks: Sequence[str] | None = None) -> None:
    """In-place strip of standard non-content HTML tags and hidden inline styles."""
    total_len = len(re.sub(r"\s+", " ", node.text_content() or "").strip())
    for el in list(node.xpath(".//*[@style]")):
        if el is node or el.getparent() is None:
            continue
        st = (el.get("style") or "").lower()
        if re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", st):
            if el.xpath(".//main | .//article | .//*[@role='main']"):
                continue
            cls_id = f"{el.get('id') or ''} {el.get('class') or ''} {el.get('role') or ''}".lower()
            if re.search(
                r"\b(?:tab-pane|tabpanel|acd-a-content|accordion-content|accordion-body|faq-answer)\b",
                cls_id,
            ):
                continue
            el_len = len(re.sub(r"\s+", " ", el.text_content() or "").strip())
            if total_len > 300 and el_len > total_len * 0.45:
                continue
            _remove_element(el)

    for bad_tag in list(
        node.xpath(".//script|.//style|.//noscript|.//template|.//svg|.//button|.//nav")
    ):
        _remove_element(bad_tag)

    if quirks is None or "ja_inline_glyphs" in quirks or "ja_kanpo_pua" in quirks:
        from wlh.processor.extractors.quirks.ja_kanpo_pua import inline_glyph_text

        for img_el in list(node.xpath(".//img")):
            glyph = inline_glyph_text(img_el)
            if glyph is not None:
                parent = img_el.getparent()
                if parent is not None:
                    prev = img_el.getprevious()
                    if prev is not None:
                        prev.tail = (prev.tail or "") + glyph + (img_el.tail or "")
                    else:
                        parent.text = (parent.text or "") + glyph + (img_el.tail or "")
                    parent.remove(img_el)


def text_to_markdown(value: str, *, title: str | None = None) -> str:
    body = normalized(value)
    if not body.strip():
        return body
    return normalized(f"# {title}\n\n{body}") if title else body


def json_to_markdown(value: str) -> str:
    try:
        parsed = json.loads(value)
    except ValueError:
        return text_to_markdown(value)
    if not isinstance(parsed, dict):
        return text_to_markdown(value)
    top = cast(dict[str, Any], parsed)
    inner: dict[str, Any] = (
        cast(dict[str, Any], next(iter(top.values())))
        if len(top) == 1 and isinstance(next(iter(top.values())), dict)
        else next(
            (cast(dict[str, Any], v) for v in top.values() if isinstance(v, dict)),
            top,
        )
    )
    sections: list[str] = []
    seen_values: set[str] = set()
    for key, val in inner.items():
        if val is None or isinstance(val, bool):
            continue
        if isinstance(val, (str, int, float)):
            sval = str(val).strip()
            if not sval or sval in seen_values:
                continue
            seen_values.add(sval)
            if len(sval) >= 40 or "\n" in sval:
                sections.append(f"### {key}\n\n{sval}")
            else:
                sections.append(f"- **{key}**: {sval}")
    if not sections:
        return text_to_markdown(value)
    return normalized("\n\n".join(sections))


def html_to_markdown(
    html: str | bytes | Any,
    base_url: str | None = None,
    content_xpath: str | None = None,
    *,
    quirks: Sequence[str] | set[str] | None = None,
    title: str | None = None,
    already_cleaned: bool = False,
) -> str:
    quirks = tuple(quirks) if quirks is not None else None
    scoped = bool(content_xpath)
    try:
        if isinstance(html, (str, bytes)):
            if not html.strip():
                raise WlhError("html_empty")
            tree = document_tree(html)
            already_cleaned = False
        else:
            tree = html if already_cleaned else copy.deepcopy(html)
            if hasattr(tree, "getroot"):
                tree = tree.getroot()
        for comment in list(tree.xpath("//comment()")):
            _remove_element(comment)
        nodes: list[Any] = (
            tree.xpath(content_xpath) if content_xpath else select_fallback_nodes(tree)
        )
    except (etree.Error, ValueError):
        raise WlhError("html_unparseable") from None
    if not nodes or any(not isinstance(n, etree._Element) for n in nodes):
        raise WlhError("content_region_missing")
    if not already_cleaned:
        for n in nodes:
            clean_html_tree(n, quirks=quirks)

    def inline(text: str | None) -> str:
        return re.sub(r"\s+", " ", text or "")

    def escape_literal_order(text: str) -> str:
        return re.sub(r"^(\s*\d{1,9})([.)])(?=\s)", r"\1\\\2", text)

    def children(node: Any) -> str:
        has_br = any(isinstance(c.tag, str) and c.tag.lower() == "br" for c in node)
        head = escape_literal_order(inline(node.text)) if has_br else inline(node.text)
        chunks: list[str] = [head]
        for c in node:
            chunks.append(render(c))
            tail = inline(c.tail)
            if isinstance(c.tag, str) and c.tag.lower() == "br":
                tail = escape_literal_order(tail)
            chunks.append(tail)
        return "".join(chunks)

    def _is_layout_or_noise_table(node: Any) -> str | None:
        """Return 'skip' for empty/noise tables, 'unwrap' for layout tables, or None for data tables."""
        if not scoped:
            sig = f"{node.get('id') or ''} {node.get('class') or ''}".lower()
            if "calendar" in sig:
                return "skip"
            if node.get("role") in {"presentation", "none"}:
                return "unwrap"
            has_th = bool(node.xpath("./tr/th | ./thead/tr/th | ./tbody/tr/th"))
            if not has_th and node.xpath(".//h1 | .//h2 | .//h3 | .//h4 | .//pre | .//article"):
                return "unwrap"
        return None

    def table(node: Any) -> str:
        disposition = _is_layout_or_noise_table(node)
        if disposition == "skip":
            return ""
        if disposition == "unwrap":
            return "\n\n" + children(node).strip() + "\n\n"
        try:
            raw_rows: list[list[tuple[str, int, int]]] = []
            for tr in node.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr"):
                cells = [
                    (
                        re.sub(r"\s+", " ", children(c)).strip().replace("|", "\\|"),
                        span(c.get("colspan")),
                        span(c.get("rowspan")),
                    )
                    for c in tr.xpath("./th|./td")
                ]
                if cells:
                    raw_rows.append(cells)
            raw_rows = [
                [
                    (text, colspan, min(rowspan, len(raw_rows) - r_idx))
                    for text, colspan, rowspan in row
                ]
                for r_idx, row in enumerate(raw_rows)
            ]
            rows = expand_rows(raw_rows)
            caption = " ".join(children(c).strip() for c in node.xpath("./caption")).strip()
            if not rows or not any(cell.strip() for row in rows for cell in row):
                return f"\n\n{caption}\n\n" if caption else ""
            if not scoped:
                has_th = bool(node.xpath("./tr/th | ./thead/tr/th | ./tbody/tr/th"))
                non_empty_rows = [r for r in rows if any(c.strip() for c in r)]
                non_empty_cols = sum(
                    1 for col_idx in range(len(rows[0])) if any(r[col_idx].strip() for r in rows)
                )
                if not has_th and (len(non_empty_rows) <= 1 or non_empty_cols <= 1):
                    return "\n\n" + children(node).strip() + "\n\n"
            if any(
                colspan > 1 or rowspan > 1 for row in raw_rows for _, colspan, rowspan in row
            ) or node.xpath(".//table | .//th[@scope]"):
                return spanning_table(node, base_url if scoped else None, set(UI_ACTION_LABELS))
            caption = " ".join(children(c).strip() for c in node.xpath("./caption")).strip()
            width = max(map(len, rows))
            rows = [row + [""] * (width - len(row)) for row in rows]
            rows.insert(1, ["---"] * width)
            return (
                "\n\n"
                + (caption + "\n\n" if caption else "")
                + "\n".join("| " + " | ".join(row) + " |" for row in rows)
                + "\n\n"
            )
        except WlhError:
            if not scoped:
                return "\n\n" + children(node).strip() + "\n\n"
            raise

    def render(node: Any) -> str:
        if not isinstance(node.tag, str):
            return ""
        tag = node.tag.split("}")[-1].split(":")[-1].lower()
        if tag in {
            "script",
            "style",
            "noscript",
            "template",
            "head",
            "nav",
            "footer",
            "button",
            "input",
            "select",
            "textarea",
            "datalist",
            "optgroup",
            "option",
            "svg",
        }:
            return ""
        if tag == "form" and scoped:
            return ""
        if tag == "table":
            return table(node)
        if tag == "pre":
            for br in list(node.xpath(".//br")):
                br.tail = "\n" + (br.tail or "")
                _remove_element(br)
            pre_text = node.text_content().strip()
            if not pre_text:
                return ""
            return "\n\n```\n" + pre_text + "\n```\n\n"
        value = children(node)
        if tag == "a":
            label = value.strip()
            if not label:
                return ""
            raw_href = (node.get("href") or "").strip()
            onclick = (node.get("onclick") or "").lower()
            if label.lower() in UI_ACTION_LABELS and (
                not raw_href
                or raw_href.startswith(("#", "javascript:"))
                or raw_href.endswith("#")
                or bool(onclick)
            ):
                return ""
            if not scoped:
                return value
            if (
                not raw_href
                or raw_href.lower().startswith(("#", "javascript:"))
                or raw_href.endswith("#")
            ):
                return value
            try:
                href = urljoin(base_url or "", raw_href)
                scheme = urlsplit(href).scheme if href else ""
            except ValueError:
                href, scheme = "", ""
            if href and scheme in {"http", "https"}:
                lead = " " if value[:1].isspace() else ""
                trail = " " if value[-1:].isspace() else ""
                return f"{lead}[{label}]({href}){trail}"
            return value
        if tag in {"strong", "b"}:
            stripped = value.strip()
            if not stripped:
                return ""
            lead = " " if value[:1].isspace() else ""
            trail = " " if value[-1:].isspace() else ""
            return f"{lead}**{stripped}**{trail}"
        if tag in {"em", "i"}:
            stripped = value.strip()
            if not stripped:
                return ""
            lead = " " if value[:1].isspace() else ""
            trail = " " if value[-1:].isspace() else ""
            return f"{lead}*{stripped}*{trail}"
        if tag == "code":
            code_val = value.strip()
            if not code_val:
                return ""
            if "\n" in code_val:
                return "\n\n```\n" + code_val + "\n```\n\n"
            return "`" + code_val + "`"
        if tag == "br":
            return "\n\n"
        if re.fullmatch(r"h[1-6]", tag):
            heading = re.sub(r"\s+", " ", value).strip()
            return "\n\n" + "#" * int(tag[1]) + " " + heading + "\n\n" if heading else ""
        if tag == "li":
            if not value.strip():
                return ""
            marker = ordered_marker(node)
            if not marker:
                literal = re.sub(r"(?m)^(\s*\d{1,9})([.)])(?=\s)", r"\1\\\2", value.strip())
                return "\n\n" + literal + "\n\n"
            return "\n" + marker + re.sub(r"\n+", "\n  ", value.strip()) + "\n"
        if tag == "blockquote":
            return "\n\n" + "\n".join("> " + line for line in value.strip().splitlines()) + "\n\n"
        if tag in {
            "p",
            "div",
            "section",
            "article",
            "main",
            "ul",
            "ol",
            "header",
            "body",
            "hr",
            "form",
        }:
            body_text = value.strip()
            if tag == "p" and "\n" in body_text:
                body_text = re.sub(r"(?m)^(\s*\d{1,9})([.)])(?=\s)", r"\1\\\2", body_text)
            return "\n\n" + body_text + "\n\n"
        return value

    selected = {id(n) for n in nodes}
    parts: list[str] = []
    seen_parents: set[int] = set()
    for node in nodes:
        parent = node.getparent()
        if parent is not None and id(parent) not in selected and id(parent) not in seen_parents:
            seen_parents.add(id(parent))
            if node.getprevious() is None and inline(parent.text).strip():
                parts.append(escape_literal_order(inline(parent.text).strip()))
        parts.append(render(node))
        if parent is not None and id(parent) not in selected and inline(node.tail).strip():
            parts.append(escape_literal_order(inline(node.tail).strip()))
    joined = "\n\n".join(parts)
    if not scoped:
        joined = escape_currency_dollars(joined)
    md = normalized(joined)
    if not md:
        raise WlhError("html_empty")
    return normalized(f"# {title}\n\n{md}") if title else md


def _utf8_text(inp: ExtractionInput) -> str:
    try:
        return inp.data.decode("utf-8-sig")
    except UnicodeError:
        raise WlhError("zip_text_encoding_invalid") from None


class HtmlDomEngine(EngineAdapter):
    kind = "html"
    engine_name = "html_dom"
    parser_tag = "markdown"
    native_stages = frozenset({"ast_node", "text_span"})

    def extract(
        self, inp: ExtractionInput, opts: ExtractionOptions, hooks: QuirkHookRunner
    ) -> tuple[str, dict[str, Any]]:
        # Inline glyph images (ast_node) are resolved while the DOM is cleaned; an empty
        # source quirk list keeps the historical "all glyph quirks on" default.
        quirks = tuple(hooks.enabled_names()) if opts.quirks else None
        return html_to_markdown(_utf8_text(inp), inp.base_url, quirks=quirks), {}


class JsonOutlineEngine(EngineAdapter):
    kind = "json"
    engine_name = "json_outline"
    parser_tag = "markdown"

    def extract(
        self, inp: ExtractionInput, opts: ExtractionOptions, hooks: QuirkHookRunner
    ) -> tuple[str, dict[str, Any]]:
        return json_to_markdown(_utf8_text(inp)), {}


class PlainTextEngine(EngineAdapter):
    kind = "txt"
    engine_name = "plain_text"
    parser_tag = "markdown"

    def extract(
        self, inp: ExtractionInput, opts: ExtractionOptions, hooks: QuirkHookRunner
    ) -> tuple[str, dict[str, Any]]:
        return text_to_markdown(_utf8_text(inp)), {}
