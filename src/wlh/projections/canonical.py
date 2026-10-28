"""Public English row contract; datetime values serialize as ISO-8601."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

COLUMNS = (
    "title",
    "markdown_file",
    "doc_type",
    "acquired_at",
    "original_file",
    "volume_order",
    "doc_number",
    "issuer",
    "published_at",
    "effective_at",
    "expired_at",
    "status",
    "source_id",
    "source_name",
    "jurisdiction",
    "detail_url",
    "source_record_hash",
    "markdown_hash",
    "original_hash",
)


def project_canonical(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, (date, datetime)) else value
        for key in COLUMNS
        for value in [row.get(key)]
    }
