"""Stage 3a JSON parser package."""

from __future__ import annotations

from wlh.processor.parsers.json.parser import (
    FIELDS,
    HEADINGS,
    SKIP,
    TYPES,
    Adapter,
    JsonParser,
    pointer,
    rii_xml,
    text_of,
    xml_to_markdown,
)

Parser = JsonParser

__all__ = [
    "Adapter",
    "FIELDS",
    "HEADINGS",
    "JsonParser",
    "Parser",
    "SKIP",
    "TYPES",
    "pointer",
    "rii_xml",
    "text_of",
    "xml_to_markdown",
]
