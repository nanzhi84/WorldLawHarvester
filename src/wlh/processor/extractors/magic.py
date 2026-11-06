"""Pure-function magic-byte sniffer: the single gateway deciding a payload's real format."""

from __future__ import annotations

import codecs
import io
import re
import zipfile

TEXT_KINDS: frozenset[str] = frozenset({"html", "xml", "json", "txt"})
OFFICE_KINDS: frozenset[str] = frozenset({"doc", "docx", "rtf", "xls", "xlsx"})
BINARY_KINDS: frozenset[str] = OFFICE_KINDS | {"pdf", "zip"}
ZIP_MEMBER_KINDS: frozenset[str] = OFFICE_KINDS | {"pdf", "txt", "html", "json"}

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_LEADING_COMMENTS = re.compile(rb"^(?:<!--.*?-->\s*)+", re.S)


def detect_real_format(data: bytes, suffix: str = "") -> str:
    """Real container type from magic bytes and structure, never from a URL suffix.

    ``suffix`` is only a tie-breaker for ambiguous markup/plain text, never an override.
    """
    hint = suffix.lstrip(".").lower()
    head = data[:8]
    if data[:5] == b"%PDF-" or b"%PDF-" in data[:1024]:
        return "pdf"
    if head[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = {n.replace("\\", "/") for n in archive.namelist()}
        except (zipfile.BadZipFile, OSError, ValueError):
            return "unknown"
        if "word/document.xml" in names:
            return "docx"
        if "xl/workbook.xml" in names:
            return "xlsx"
        return "zip"
    if head == _OLE2_MAGIC:
        if "WordDocument".encode("utf-16-le") in data:
            return "doc"
        if "Workbook".encode("utf-16-le") in data or "Book".encode("utf-16-le") in data:
            return "xls"
        return "unknown"
    stripped = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"{\\rtf"):
        return "rtf"
    lowered = _LEADING_COMMENTS.sub(b"", stripped[:2048]).lower()
    if (
        lowered.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))
        or b"<html" in lowered
        or b"<body" in lowered
        or (hint == "html" and lowered.startswith(b"<") and not lowered.startswith(b"<?xml"))
    ):
        return "html"
    if lowered.startswith(b"<"):
        return "xml"
    if lowered.startswith((b"{", b"[")):
        return "json"
    sample = data[:65536]
    if b"\x00" in sample:
        return "unknown"
    try:
        codecs.getincrementaldecoder("utf-8")().decode(sample, final=len(data) <= len(sample))
        return "txt"
    except UnicodeDecodeError:
        return hint if hint in {"txt", "html"} else "unknown"


__all__ = [
    "BINARY_KINDS",
    "OFFICE_KINDS",
    "TEXT_KINDS",
    "ZIP_MEMBER_KINDS",
    "detect_real_format",
]
