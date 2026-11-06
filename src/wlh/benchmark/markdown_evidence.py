"""Visible Markdown output and its references, without executing active content."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from markdown_it import MarkdownIt

BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "main",
    "table",
    "tr",
    "td",
    "th",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "pre",
    "blockquote",
    "caption",
    "dt",
    "dd",
    "paragraph",
    "tablecolumn",
    "tablerow",
}
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


def has_hidden_attributes(attrs: list[tuple[str, str | None]]) -> bool:
    values = dict(attrs)
    return bool(
        "hidden" in values
        or (values.get("aria-hidden") or "").lower() == "true"
        or re.search(
            r"(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?:[;\s]|$))",
            values.get("style") or "",
            re.I,
        )
    )


@dataclass(frozen=True)
class MarkdownEvidence:
    text: str
    urls: tuple[str, ...]
    hidden_content: bool
    blocks: tuple[str, ...]


class RenderedOutput(HTMLParser):
    """Inspect the rendered fragment; escaped code remains literal visible text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.blocks: list[str] = []
        self.current: list[str] = []
        self.urls: list[str] = []
        self.hidden_content = False
        self.table_header = False

    def flush(self) -> None:
        text = "".join(self.current)
        # A DL rendered as a two-column table may need these generic headers.
        # They remain visible output, but are not factual body claims. Only a
        # complete header cell is exempt; invented headers/cell values are not.
        generic_header = self.table_header and text.strip() in {"項目", "内容"}
        if text.strip() and not generic_header:
            self.blocks.append(text)
        self.current = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in BLOCK_TAGS:
            self.flush()
        if tag == "th":
            self.table_header = True
        values = dict(attrs)
        if tag in {"script", "style", "template", "noscript", "head"} or has_hidden_attributes(
            attrs
        ):
            self.hidden_content = True
        if tag == "img" and values.get("alt"):
            # Distinct images have distinct alternative-text labels. Do not
            # fabricate continuous n-grams across adjacent download icons.
            self.flush()
            self.parts.append(values["alt"] or "")
            self.current.append(values["alt"] or "")
            self.flush()
        for name in ("href", "src", "data-src"):
            if values.get(name):
                self.urls.append(values[name] or "")

    def handle_endtag(self, tag: str) -> None:
        if tag in BLOCK_TAGS:
            self.flush()
        if tag == "th":
            self.table_header = False

    def handle_data(self, data: str) -> None:
        self.parts.append(data)
        self.current.append(data)

    def handle_comment(self, data: str) -> None:
        if data.strip():
            self.hidden_content = True


def markdown_evidence(markdown: str) -> MarkdownEvidence:
    rendered = MarkdownIt("commonmark", {"html": True}).enable("table").render(markdown)
    parser = RenderedOutput()
    parser.feed(rendered)
    parser.flush()
    return MarkdownEvidence(
        "\n".join(parser.parts), tuple(parser.urls), parser.hidden_content, tuple(parser.blocks)
    )
