"""XML extractor exports (Stage 3b: Legal XML, GPO XML, XML tables & inline elements -> Markdown)."""

from __future__ import annotations

from wlh.processor.extractors.xml.inline import EMPHASIS_TAGS, INLINE_TAGS, inline_content
from wlh.processor.extractors.xml.markdown import (
    CONTAINER_TAGS,
    HEADING_TAGS,
    HEADINGS,
    PARAGRAPH_TAGS,
    SKIP,
    SKIP_TAGS,
    dokument_xml_to_markdown,
    egov_law_xml_to_markdown,
    gazette_html_to_markdown,
    gpo_xml_to_markdown,
    markdown,
    text_of,
    xml_to_markdown,
)
from wlh.processor.extractors.xml.tables import CELL_TAGS, ROW_TAGS, TABLE_TAGS, table_markdown

__all__ = [
    "CELL_TAGS",
    "CONTAINER_TAGS",
    "EMPHASIS_TAGS",
    "HEADINGS",
    "HEADING_TAGS",
    "INLINE_TAGS",
    "PARAGRAPH_TAGS",
    "ROW_TAGS",
    "SKIP",
    "SKIP_TAGS",
    "TABLE_TAGS",
    "dokument_xml_to_markdown",
    "egov_law_xml_to_markdown",
    "gazette_html_to_markdown",
    "gpo_xml_to_markdown",
    "inline_content",
    "markdown",
    "table_markdown",
    "text_of",
    "xml_to_markdown",
]
