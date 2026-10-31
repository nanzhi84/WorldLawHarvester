"""Stage 3a XML parser package (navigation, record slicing, and metadata extraction)."""

from __future__ import annotations

from wlh.processor.parsers.xml.discovery import discover
from wlh.processor.parsers.xml.parser import (
    ERA_OFFSETS,
    Adapter,
    XmlParser,
    japan_records,
    japan_requests,
    verify_attachment_zip,
)
from wlh.processor.parsers.xml.values import select, text

Parser = XmlParser

__all__ = [
    "Adapter",
    "ERA_OFFSETS",
    "Parser",
    "XmlParser",
    "discover",
    "japan_records",
    "japan_requests",
    "select",
    "text",
    "verify_attachment_zip",
]
