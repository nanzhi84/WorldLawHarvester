"""Shared OOXML (docx/xlsx) container normalization."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path


def normalized_office_archive(source: bytes | str | Path) -> io.BytesIO:
    """Normalize Windows-signed Office ZIPs in memory, preserving the archived original.

    Electronic signature tools write backslash member paths and ``[trash]/`` leftovers that
    python-docx/openpyxl cannot open; they are rewritten in memory only.
    """
    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if not any("\\" in name or name.startswith("[trash]/") for name in archive.namelist()):
            return io.BytesIO(raw)
        normalized = io.BytesIO()
        with zipfile.ZipFile(normalized, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for member in archive.infolist():
                name = member.filename.replace("\\", "/")
                if not name.startswith("[trash]/"):
                    output.writestr(name, archive.read(member))
    normalized.seek(0)
    return normalized


__all__ = ["normalized_office_archive"]
