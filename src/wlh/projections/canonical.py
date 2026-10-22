"""Public English row contract; datetime values serialize as ISO-8601."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

COLUMNS = (
    "source_id",
    "resource_id",
    "version_id",
    "row_index",
    "role",
    "volume_order",
    "title",
    "doc_type",
    "doc_number",
    "issuer",
    "published_at",
    "effective_at",
    "expired_at",
    "status",
    "source_name",
    "detail_url",
    "source_record_hash",
    "acquired_at",
    "markdown_file",
    "markdown_hash",
    "original_file",
    "original_hash",
    "original_media_type",
    "locator",
    "extensions",
)


def project_canonical(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, (date, datetime)) else value
        for key in COLUMNS
        for value in [row.get(key)]
    }
