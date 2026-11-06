"""Stage 3a XML parser package (navigation, record slicing, and metadata extraction)."""

from __future__ import annotations

from wlh.processor.parsers.record_base import (
    japan_records,
    japan_requests,
    verify_attachment_zip,
)
from wlh.processor.parsers.xml.discovery import discover
from wlh.processor.parsers.xml.parser import (
    ERA_OFFSETS,
    Adapter,
    XmlParser,
)
from wlh.processor.parsers.xml.schemas import (
    XML_SCHEMA_SPECS,
    XmlSchemaSpec,
    extract_xml_records,
    match_xml_schema,
)
from wlh.processor.parsers.xml.values import select, text

Parser = XmlParser

__all__ = [
    "Adapter",
    "ERA_OFFSETS",
    "Parser",
    "XML_SCHEMA_SPECS",
    "XmlParser",
    "XmlSchemaSpec",
    "discover",
    "extract_xml_records",
    "japan_records",
    "japan_requests",
    "match_xml_schema",
    "select",
    "text",
    "verify_attachment_zip",
]
