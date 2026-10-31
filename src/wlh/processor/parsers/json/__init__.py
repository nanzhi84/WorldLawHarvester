"""Stage 3a JSON parser package (navigation, pagination, record slicing, and metadata extraction)."""

from __future__ import annotations

from wlh.processor.parsers.json.parser import (
    FIELDS,
    TYPES,
    Adapter,
    JsonParser,
    pointer,
    rii_xml,
)

Parser = JsonParser

__all__ = [
    "Adapter",
    "FIELDS",
    "JsonParser",
    "Parser",
    "TYPES",
    "pointer",
    "rii_xml",
]
