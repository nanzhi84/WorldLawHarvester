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
from wlh.processor.parsers.json.schemas import (
    JSON_SCHEMA_SPECS,
    JsonSchemaSpec,
    extract_json_records,
    match_json_schema,
)

Parser = JsonParser

__all__ = [
    "Adapter",
    "FIELDS",
    "JSON_SCHEMA_SPECS",
    "JsonParser",
    "JsonSchemaSpec",
    "Parser",
    "TYPES",
    "extract_json_records",
    "match_json_schema",
    "pointer",
    "rii_xml",
]
