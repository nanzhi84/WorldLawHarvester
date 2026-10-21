"""Public English row contract; datetime values serialize as ISO-8601."""

from datetime import date, datetime

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


def project_canonical(row):
    return {
        key: value.isoformat() if isinstance(value, (date, datetime)) else value
        for key in COLUMNS
        for value in [row.get(key)]
    }
