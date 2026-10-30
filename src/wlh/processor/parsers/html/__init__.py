"""Stage 3a HTML parser package."""

from __future__ import annotations

from wlh.processor.parsers.html.document_builder import (
    BINARY_KINDS,
    GENERIC_ATTACHMENT_LABELS,
    TEXT_KINDS,
    WebDocumentBuilder,
)
from wlh.processor.parsers.html.dom_cleanup import (
    clean_site_dom,
    court_id,
    next_page_url,
    parse_detail_fields,
    parse_list_rows,
    result_range,
    verify_detail_fields,
)
from wlh.processor.parsers.html.fields import (
    DATE_FIELDS,
    clean_url,
    decode,
    enrich_bare_title,
    extract_detail_fields,
    extract_label_value_fields,
    field_value,
    file_name,
    first_text,
    parse_date,
    parse_html,
)
from wlh.processor.parsers.html.pagination import aspnet_next_page, pagination_requests
from wlh.processor.parsers.html.parser import Adapter, HtmlParser
from wlh.processor.parsers.html.titles import directory_title

Parser = HtmlParser

__all__ = [
    "Adapter",
    "BINARY_KINDS",
    "DATE_FIELDS",
    "GENERIC_ATTACHMENT_LABELS",
    "HtmlParser",
    "Parser",
    "TEXT_KINDS",
    "WebDocumentBuilder",
    "aspnet_next_page",
    "clean_site_dom",
    "clean_url",
    "court_id",
    "decode",
    "directory_title",
    "enrich_bare_title",
    "extract_detail_fields",
    "extract_label_value_fields",
    "field_value",
    "file_name",
    "first_text",
    "next_page_url",
    "pagination_requests",
    "parse_date",
    "parse_detail_fields",
    "parse_html",
    "parse_list_rows",
    "result_range",
    "verify_detail_fields",
]
