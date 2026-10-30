"""Stage 3a XML parser package."""

from __future__ import annotations

from wlh.processor.parsers.xml.discovery import discover
from wlh.processor.parsers.xml.inline import INLINE_TAGS, inline_content
from wlh.processor.parsers.xml.markdown import markdown
from wlh.processor.parsers.xml.parser import (
    ERA_OFFSETS,
    Adapter,
    XmlParser,
    japan_records,
    japan_requests,
    verify_attachment_zip,
)
from wlh.processor.parsers.xml.tables import TABLE_TAGS, table_markdown
from wlh.processor.parsers.xml.values import select, text

Parser = XmlParser

__all__ = [
    "Adapter",
    "ERA_OFFSETS",
    "INLINE_TAGS",
    "Parser",
    "TABLE_TAGS",
    "XmlParser",
    "discover",
    "inline_content",
    "japan_records",
    "japan_requests",
    "markdown",
    "select",
    "table_markdown",
    "text",
    "verify_attachment_zip",
]
