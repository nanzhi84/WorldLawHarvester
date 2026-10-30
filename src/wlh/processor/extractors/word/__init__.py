"""Word/Office extractor exports."""

from __future__ import annotations

from wlh.processor.extractors.word.office import (
    MAX_ZIP_DEPTH,
    MAX_ZIP_MEMBERS,
    OFFICE_CONTRACT,
    OFFICE_KINDS,
    ZIP_MEMBER_KINDS,
    detect_real_format,
    extract_docx,
    extract_excel,
    extract_libreoffice_text,
    extract_office,
    libreoffice_convert,
    libreoffice_identity,
    normalized_office_archive,
    safe_member,
    salvage_zip_prefix,
    soffice,
    table_markdown,
    unpack_zip,
)

__all__ = [
    "MAX_ZIP_DEPTH",
    "MAX_ZIP_MEMBERS",
    "OFFICE_CONTRACT",
    "OFFICE_KINDS",
    "ZIP_MEMBER_KINDS",
    "detect_real_format",
    "extract_docx",
    "extract_excel",
    "extract_libreoffice_text",
    "extract_office",
    "libreoffice_convert",
    "libreoffice_identity",
    "normalized_office_archive",
    "safe_member",
    "salvage_zip_prefix",
    "soffice",
    "table_markdown",
    "unpack_zip",
]
