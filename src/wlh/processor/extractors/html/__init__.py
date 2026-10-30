"""HTML extractor exports."""

from __future__ import annotations

from wlh.processor.extractors.html.dom_tree import decode, document_tree, first_text, parse_html
from wlh.processor.extractors.html.html_to_md import (
    MARKDOWN_CONTRACT,
    clean_html_tree,
    html_to_markdown,
    json_to_markdown,
    text_to_markdown,
)
from wlh.processor.extractors.html.images import require_textual_images, textual_image_urls
from wlh.processor.extractors.html.tables_and_lists import (
    expand_rows,
    literal_text,
    ordered_marker,
    span,
    spanning_table,
)

__all__ = [
    "MARKDOWN_CONTRACT",
    "clean_html_tree",
    "decode",
    "document_tree",
    "expand_rows",
    "first_text",
    "html_to_markdown",
    "json_to_markdown",
    "literal_text",
    "ordered_marker",
    "parse_html",
    "require_textual_images",
    "span",
    "spanning_table",
    "text_to_markdown",
    "textual_image_urls",
]
