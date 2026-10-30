"""Streaming truncated ZIP central-directory recovery and redundant member filtering."""

from __future__ import annotations

import struct
import zipfile
import zlib
from collections.abc import Sequence
from pathlib import Path

from wlh.core import WlhError


def safe_member(name: str) -> str | None:
    parts = [p for p in Path(name.replace("\\", "/")).parts if p not in ("", ".", "/")]
    if not parts or any(p == ".." for p in parts) or name.startswith(("/", "\\")):
        return None
    return "/".join(parts)


def filter_redundant_zip_members(infos: Sequence[zipfile.ZipInfo]) -> list[zipfile.ZipInfo]:
    """Filter out redundant uppercase .PDF members when a .json dataset member is present."""
    has_json_member = any(
        (sm := safe_member(i.filename)) is not None and sm.lower().endswith(".json")
        for i in infos
    )
    if not has_json_member:
        return list(infos)
    return [
        i
        for i in infos
        if not (
            (sm := safe_member(i.filename)) is not None and sm.upper().endswith(".PDF")
        )
    ]


def salvage_zip_prefix(buf: bytes) -> bytes:
    """Rebuild Central Directory over complete CRC-verified PK\\x03\\x04 entries containing a JSON member."""
    offset = 0
    n = len(buf)
    cd_entries: list[bytes] = []
    has_json = False
    while offset + 30 <= n:
        if buf[offset : offset + 4] != b"PK\x03\x04":
            break
        _sig, ver, flags, method, mtime, mdate, crc, csize, usize, fn_len, ex_len = struct.unpack(
            "<4sHHHHHIIIHH", buf[offset : offset + 30]
        )
        if flags & 0x08:
            break
        header_end = offset + 30 + fn_len + ex_len
        entry_end = header_end + csize
        if entry_end > n:
            break
        raw_comp = buf[header_end:entry_end]
        try:
            raw_uncomp = (
                zlib.decompress(raw_comp, -15)
                if method == 8
                else raw_comp
                if method == 0
                else b""
            )
        except zlib.error:
            break
        if (
            method not in (0, 8)
            or (zlib.crc32(raw_uncomp) & 0xFFFFFFFF) != crc
            or len(raw_uncomp) != usize
        ):
            break
        fn_bytes = buf[offset + 30 : offset + 30 + fn_len]
        ex_bytes = buf[offset + 30 + fn_len : header_end]
        fn_str = fn_bytes.decode("utf-8", errors="replace")
        if (sm := safe_member(fn_str)) is not None and sm.lower().endswith(".json"):
            has_json = True
        cd_header = (
            struct.pack(
                "<4sHHHHHHIIIHHHHHII",
                b"PK\x01\x02",
                ver,
                ver,
                flags,
                method,
                mtime,
                mdate,
                crc,
                csize,
                usize,
                fn_len,
                ex_len,
                0,
                0,
                0,
                0,
                offset,
            )
            + fn_bytes
            + ex_bytes
        )
        cd_entries.append(cd_header)
        offset = entry_end
    if not cd_entries or not has_json:
        raise WlhError("zip_invalid")
    cd_blob = b"".join(cd_entries)
    eocd = struct.pack(
        "<4sHHHHIIH",
        b"PK\x05\x06",
        0,
        0,
        len(cd_entries),
        len(cd_entries),
        len(cd_blob),
        offset,
        0,
    )
    return buf[:offset] + cd_blob + eocd


__all__ = [
    "filter_redundant_zip_members",
    "safe_member",
    "salvage_zip_prefix",
]
