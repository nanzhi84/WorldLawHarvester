"""Legacy binary Word (``.doc``): pure-Python OLE2 + MS-DOC piece table, BLIP image OCR fallback.

Zero external processes: the OLE2 compound file is parsed in memory, text comes from the
FIB/Clx piece table and ``\\x07`` cell/row marks become Markdown tables. Image-only scans fall
back to the embedded OfficeArt BLIP bitmaps, rendered into an in-memory PDF for local OCR.
"""

from __future__ import annotations

import re
import struct
import zlib
from collections.abc import Mapping
from typing import Any

from wlh.core import WlhError
from wlh.processor.extractors.base import (
    EngineAdapter,
    ExtractionInput,
    ExtractionOptions,
    QuirkHookRunner,
)
from wlh.processor.extractors.office.tables import table_markdown
from wlh.processor.extractors.quirks.vi_tcvn3 import legal_markdown

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_END = 0xFFFFFFFE
MIN_TEXT_CHARS = 15

# [MS-DOC] 2.9.73 FcCompressed: 8-bit pieces are CP1252 for defined bytes; the five bytes CP1252
# leaves undefined (0x81, 0x8D, 0x8F, 0x90, 0x9D) keep their code point instead of U+FFFD so
# legacy font payloads (e.g. Vietnamese TCVN3) survive for later recovery.
_FC_COMPRESSED = "".join(bytes([b]).decode("cp1252", errors="ignore") or chr(b) for b in range(256))


def decode_fc_compressed(raw: bytes) -> str:
    """Lossless 8-bit piece decoding (one code point per byte)."""
    return raw.decode("latin-1").translate(dict(enumerate(_FC_COMPRESSED)))


def parse_ole2_streams(data: bytes) -> dict[str, bytes]:
    """Zero-dependency in-memory OLE2 Compound Document stream reader."""
    if len(data) < 512 or data[:8] != _OLE2_MAGIC:
        raise ValueError("not_ole2")
    sector_shift, mini_shift = struct.unpack_from("<HH", data, 30)
    sector_size = 1 << sector_shift
    mini_size = 1 << mini_shift
    (
        num_fat,
        first_dir,
        _,
        mini_cutoff,
        first_minifat,
        num_minifat,
        first_difat,
        num_difat,
    ) = struct.unpack_from("<IIIIIIII", data, 44)
    difat = [x for x in struct.unpack_from("<109I", data, 76) if x < _END]
    sec = first_difat
    for _ in range(num_difat):
        if sec >= _END:
            break
        off = (sec + 1) * sector_size
        vals = struct.unpack_from(f"<{sector_size // 4}I", data, off)
        difat.extend(x for x in vals[:-1] if x < _END)
        sec = vals[-1]
    fat: list[int] = []
    for s in difat[:num_fat]:
        off = (s + 1) * sector_size
        if off + sector_size <= len(data):
            fat.extend(struct.unpack_from(f"<{sector_size // 4}I", data, off))

    def read_chain(start_sec: int, size: int | None = None) -> bytes:
        out = bytearray()
        s = start_sec
        seen: set[int] = set()
        while s < _END and s < len(fat) and s not in seen:
            seen.add(s)
            off = (s + 1) * sector_size
            out.extend(data[off : off + sector_size])
            if size is not None and len(out) >= size:
                return bytes(out[:size])
            s = fat[s]
        return bytes(out if size is None else out[:size])

    dir_bytes = read_chain(first_dir)
    minifat: list[int] = []
    if first_minifat < _END and num_minifat > 0:
        mf_bytes = read_chain(first_minifat)
        minifat = list(struct.unpack(f"<{len(mf_bytes) // 4}I", mf_bytes))
    root_start, root_size = struct.unpack_from("<IQ", dir_bytes, 116)
    if sector_size == 512:
        root_size &= 0xFFFFFFFF
    mini_stream = read_chain(root_start, root_size) if root_start < _END else b""

    def read_mini_chain(start_sec: int, size: int) -> bytes:
        out = bytearray()
        s = start_sec
        seen: set[int] = set()
        while s < _END and s < len(minifat) and s not in seen and len(out) < size:
            seen.add(s)
            off = s * mini_size
            out.extend(mini_stream[off : off + mini_size])
            s = minifat[s]
        return bytes(out[:size])

    streams: dict[str, bytes] = {}
    for pos in range(0, len(dir_bytes), 128):
        entry = dir_bytes[pos : pos + 128]
        if len(entry) < 128:
            break
        name_len, obj_type = struct.unpack_from("<HB", entry, 64)
        if obj_type != 2 or name_len <= 2:
            continue
        name = entry[: name_len - 2].decode("utf-16-le", errors="ignore")
        start_sec, size = struct.unpack_from("<IQ", entry, 116)
        if sector_size == 512:
            size &= 0xFFFFFFFF
        if size < mini_cutoff and mini_stream:
            streams[name] = read_mini_chain(start_sec, size)
        else:
            streams[name] = read_chain(start_sec, size)
    return streams


# OfficeArt BLIP record types -> recInstance values (odd instance = two 16-byte UIDs).
_BLIP_INSTANCES: dict[int, frozenset[int]] = {
    0xF01A: frozenset({0x3D4, 0x3D5}),  # EMF
    0xF01D: frozenset({0x46A, 0x46B, 0x6E2, 0x6E3}),  # JPEG (RGB / CMYK)
    0xF01E: frozenset({0x6E0, 0x6E1}),  # PNG
    0xF01F: frozenset({0x7A8, 0x7A9}),  # DIB
    0xF029: frozenset({0x6E4, 0x6E5}),  # TIFF
    0xF02A: frozenset({0x46A, 0x46B, 0x6E2, 0x6E3}),  # JPEG (CMYK)
}
_BLIP_HEADER = re.compile(rb"[\x1a\x1d\x1e\x1f\x29\x2a]\xf0", re.S)
_METAFILE_HEADER = 34  # cbSize, rcBounds, ptSize, cbSave, compression, filter


def _emf_bitmaps(emf: bytes) -> list[bytes]:
    """EMR_STRETCHDIBITS bitmaps of an EMF as standalone BMP files."""
    images: list[bytes] = []
    epos = 0
    while epos + 8 <= len(emf):
        rtype, rsize = struct.unpack_from("<II", emf, epos)
        if rsize < 8 or epos + rsize > len(emf):
            break
        if rtype == 81 and rsize >= 80:  # EMR_STRETCHDIBITS
            off_bmi, cb_bmi, off_bits, cb_bits = struct.unpack_from("<IIII", emf, epos + 48)
            if (
                cb_bmi > 0
                and cb_bits > 0
                and off_bmi + cb_bmi <= rsize
                and off_bits + cb_bits <= rsize
            ):
                bmi = emf[epos + off_bmi : epos + off_bmi + cb_bmi]
                bits = emf[epos + off_bits : epos + off_bits + cb_bits]
                images.append(_bmp(bmi, bits))
        epos += rsize
    return images


def _bmp(bmi: bytes, bits: bytes) -> bytes:
    return b"BM" + struct.pack("<III", 14 + len(bmi) + len(bits), 0, 14 + len(bmi)) + bmi + bits


def _dib_to_bmp(dib: bytes) -> bytes | None:
    if len(dib) < 40:
        return None
    header_size, _, _, _, bit_count, compression = struct.unpack_from("<IiiHHI", dib, 0)
    colors = struct.unpack_from("<I", dib, 32)[0] if header_size >= 36 else 0
    palette = (colors or (1 << bit_count if bit_count <= 8 else 0)) * 4
    masks = 12 if compression == 3 and header_size == 40 else 0
    offset = header_size + palette + masks
    if offset >= len(dib):
        return None
    return _bmp(dib[:offset], dib[offset:])


def _blip_image(rec_type: int, instance: int, payload: bytes) -> list[bytes]:
    uid_bytes = 32 if instance & 1 else 16
    if rec_type == 0xF01A:
        header = payload[uid_bytes : uid_bytes + _METAFILE_HEADER]
        if len(header) < _METAFILE_HEADER:
            return []
        cb_save = struct.unpack_from("<I", header, 28)[0]
        compressed = payload[uid_bytes + _METAFILE_HEADER : uid_bytes + _METAFILE_HEADER + cb_save]
        try:
            emf = zlib.decompress(compressed) if header[32] == 0 else compressed
        except zlib.error:
            return []
        return _emf_bitmaps(emf)
    image = payload[uid_bytes + 1 :]  # one-byte tag precedes bitmap BLIP data
    if rec_type == 0xF01F:
        bmp = _dib_to_bmp(image)
        return [bmp] if bmp else []
    signatures = {
        0xF01D: (b"\xff\xd8",),
        0xF02A: (b"\xff\xd8",),
        0xF01E: (b"\x89PNG\r\n\x1a\n",),
        0xF029: (b"II*\x00", b"MM\x00*"),
    }[rec_type]
    return [image] if image.startswith(signatures) else []


def _blip_records(buf: bytes) -> list[bytes]:
    """Every OfficeArt BLIP record, sliced exactly by its ``recLen`` (no cross-image bleed)."""
    images: list[bytes] = []
    resume = 0
    for match in _BLIP_HEADER.finditer(buf):
        start = match.start() - 2
        if start < resume or start < 0:
            continue
        ver_inst, rec_type, rec_len = struct.unpack_from("<HHI", buf, start)
        instance = ver_inst >> 4
        end = start + 8 + rec_len
        if (
            ver_inst & 0xF
            or instance not in _BLIP_INSTANCES.get(rec_type, frozenset())
            or rec_len <= 17
            or end > len(buf)
        ):
            continue
        images.extend(_blip_image(rec_type, instance, buf[start + 8 : end]))
        resume = end
    return images


def _jpeg_end(buf: bytes, start: int) -> int:
    """Offset just past the EOI of the JPEG starting at ``start`` (marker walk), or -1."""
    pos = start + 2
    while pos + 4 <= len(buf):
        if buf[pos] != 0xFF:
            return -1
        marker = buf[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        length = struct.unpack_from(">H", buf, pos + 2)[0]
        if marker != 0xDA:  # SOS: entropy-coded data follows the header
            pos += 2 + length
            continue
        scan = pos + 2 + length
        while True:
            scan = buf.find(b"\xff", scan)
            if scan == -1 or scan + 1 >= len(buf):
                return -1
            follower = buf[scan + 1]
            if follower == 0xD9:
                return scan + 2
            scan += 2 if follower == 0x00 or 0xD0 <= follower <= 0xD7 else 1
    return -1


def _signature_images(buf: bytes) -> list[bytes]:
    """Raw PNG/JPEG signature scan for streams without OfficeArt BLIP headers."""
    images: list[bytes] = []
    pos = 0
    while (start := buf.find(b"\x89PNG\r\n\x1a\n", pos)) != -1:
        end = buf.find(b"IEND\xaeB`\x82", start)
        if end == -1:
            break
        images.append(buf[start : end + 8])
        pos = end + 8
    pos = 0
    while (start := buf.find(b"\xff\xd8\xff", pos)) != -1:
        end = _jpeg_end(buf, start)
        if end == -1:
            pos = start + 3
            continue
        images.append(buf[start:end])
        pos = end
    if not images and (start := buf.find(b"II*\x00\x08\x00\x00\x00")) != -1:
        images.append(buf[start:])
    return images


def extract_ole2_data_images(streams: Mapping[str, bytes]) -> list[bytes]:
    """All embedded page images of a ``.doc`` (``Data`` then ``WordDocument`` stream)."""
    images: list[bytes] = []
    for name in ("Data", "WordDocument"):
        buf = streams.get(name, b"")
        if buf:
            images.extend(_blip_records(buf) or _signature_images(buf))
    return images


def _format_msdoc_paragraphs_and_tables(raw_text: str) -> tuple[str, int]:
    """Convert a raw MS-DOC character stream (``\\x07`` cell/row marks) into Markdown + tables.

    A run of ``k`` consecutive ``\\x07`` closes the current cell; for ``k >= 2`` the next
    ``k - 2`` marks close empty cells and the last one is the row end (TTP).
    """
    text = re.sub(r"\x13[^\x14\x15]*\x14", "", raw_text)
    text = re.sub(r"\x13[^\x15]*\x15", "", text)
    text = text.replace("\x15", "").replace("\x0b", "\n").replace("\x0c", "\n\n")
    text = re.sub(r"[\x00-\x06\x08\x0e-\x12\x16-\x1f]", "", text)

    blocks: list[str] = []
    current_table: list[list[str]] = []
    current_row: list[str] = []
    tables_recovered = 0

    def end_row() -> None:
        nonlocal current_row
        if any(c.strip() for c in current_row):
            current_table.append(current_row)
        current_row = []

    def flush_table() -> None:
        nonlocal current_table, tables_recovered
        if current_row:
            end_row()
        if current_table:
            if len(current_table) >= 2 and max(len(r) for r in current_table) >= 2:
                blocks.append(table_markdown(current_table))
                tables_recovered += 1
            else:
                for r in current_table:
                    line = " | ".join(c for c in r if c)
                    if line.strip():
                        blocks.append(line.strip())
            current_table = []

    idx = 0
    n = len(text)
    buf: list[str] = []
    while idx < n:
        ch = text[idx]
        if ch == "\x07":
            run = 1
            while idx + run < n and text[idx + run] == "\x07":
                run += 1
            cell = re.sub(r"\s+", " ", "".join(buf)).strip().replace("|", "\\|")
            buf.clear()
            idx += run
            if run == 1 and not cell and current_row and (idx == n or text[idx] in "\r\n"):
                # A lone empty mark right before a paragraph end is the row end (TTP).
                end_row()
            elif run >= 2:
                current_row.append(cell)
                current_row.extend([""] * (run - 2))
                end_row()
            else:
                current_row.append(cell)
                continue
            if idx < n and text[idx] == "\r":
                idx += 1
        elif ch == "\r":
            if current_row:
                buf.append(" ")
            else:
                flush_table()
                para = re.sub(r"[ \t]+", " ", "".join(buf)).strip()
                buf.clear()
                if para:
                    blocks.append(para)
            idx += 1
        else:
            buf.append(ch)
            idx += 1

    if buf:
        rem = re.sub(r"[ \t]+", " ", "".join(buf)).strip()
        if current_row:
            current_row.append(rem)
        elif rem:
            blocks.append(rem)
    flush_table()
    return "\n\n".join(blocks).strip(), tables_recovered


def _extract_fc_min_mac_text(wdoc: bytes, ccp_text: int) -> str:
    """Fallback for early Word 6.0/95 (no 1Table/0Table) using FIB fcMin..fcMac (0x0018..0x0020)."""
    if len(wdoc) < 0x0020:
        return ""
    fc_min, fc_mac = struct.unpack_from("<II", wdoc, 0x0018)
    if not (0 < fc_min < fc_mac <= len(wdoc)):
        return ""
    raw_slice = wdoc[fc_min:fc_mac]
    if ccp_text > 0 and len(raw_slice) == ccp_text * 2:
        return raw_slice.decode("utf-16-le", errors="replace")
    return decode_fc_compressed(raw_slice)


def extract_msdoc_pure(data: bytes) -> tuple[str, dict[str, Any]]:
    """Pure-Python in-memory MS-DOC (Word 6.0/95 and 97-2003) extractor with table recovery."""
    streams = parse_ole2_streams(data)
    wdoc = streams.get("WordDocument")
    if not wdoc or len(wdoc) < 0x0020:
        raise ValueError("missing_word_document_stream")
    flags = struct.unpack_from("<H", wdoc, 0x000A)[0]
    ccp_text = struct.unpack_from("<I", wdoc, 0x004C)[0] if len(wdoc) >= 0x0050 else 0
    tbl_name = "1Table" if (flags & 0x0200) else "0Table"
    tbl = streams.get(tbl_name) or streams.get("1Table") or streams.get("0Table")
    pieces: list[str] = []
    if tbl and len(wdoc) >= 0x01AA:
        fc_clx, lcb_clx = struct.unpack_from("<II", wdoc, 0x01A2)
        clx = tbl[fc_clx : fc_clx + lcb_clx] if fc_clx < len(tbl) and lcb_clx > 0 else b""
        pos = 0
        plc = b""
        while pos < len(clx):
            clxt = clx[pos]
            if clxt == 1:
                if pos + 3 > len(clx):
                    break
                cb = struct.unpack_from("<H", clx, pos + 1)[0]
                pos += 3 + cb
            elif clxt == 2:
                if pos + 5 > len(clx):
                    break
                lcb = struct.unpack_from("<I", clx, pos + 1)[0]
                plc = clx[pos + 5 : pos + 5 + lcb]
                break
            else:
                break
        if len(plc) >= 16 and (len(plc) - 4) % 12 == 0:
            n = (len(plc) - 4) // 12
            cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
            for i in range(n):
                cp_start, cp_end = cps[i], cps[i + 1]
                if ccp_text and cp_start >= ccp_text:
                    break
                if ccp_text:
                    cp_end = min(cp_end, ccp_text)
                char_count = cp_end - cp_start
                if char_count <= 0:
                    continue
                pcd_off = (n + 1) * 4 + i * 8
                _, fc_raw, _ = struct.unpack_from("<HIH", plc, pcd_off)
                if fc_raw & 0x40000000:
                    byte_off = (fc_raw & ~0x40000000) // 2
                    if byte_off < len(wdoc):
                        pieces.append(decode_fc_compressed(wdoc[byte_off : byte_off + char_count]))
                else:
                    byte_off = fc_raw & 0x3FFFFFFF
                    if byte_off < len(wdoc):
                        raw_slice = wdoc[byte_off : byte_off + char_count * 2]
                        pieces.append(raw_slice.decode("utf-16-le", errors="replace"))
    raw_text = "".join(pieces)
    if not raw_text.strip():
        raw_text = _extract_fc_min_mac_text(wdoc, ccp_text)
    if not raw_text.strip() and not tbl:
        raise ValueError("missing_table_stream")
    if not raw_text.strip():
        raise ValueError("missing_pcdt")
    md, tables_count = _format_msdoc_paragraphs_and_tables(raw_text)
    return md, {"extractor": "pure_python_ole2_msdoc", "tables_recovered": tables_count}


class Ole2PieceTableEngine(EngineAdapter):
    kind = "doc"
    engine_name = "pure_python_ole2_msdoc"
    parser_tag = "office"

    def extract(
        self, inp: ExtractionInput, opts: ExtractionOptions, hooks: QuirkHookRunner
    ) -> tuple[str, dict[str, Any]]:
        try:
            text, info = extract_msdoc_pure(inp.data)
        except (ValueError, struct.error, IndexError):
            raise WlhError("doc_stream_missing") from None
        if len(re.sub(r"\s+", "", text)) < MIN_TEXT_CHARS:
            raise WlhError("doc_text_too_short")
        return text, {"tables_recovered": info["tables_recovered"]}

    def finalize(self, markdown: str) -> str:
        return legal_markdown(markdown)


class Ole2BlipOcrEngine(EngineAdapter):
    kind = "doc"
    engine_name = "msdoc_embedded_image_ocr"
    parser_tag = "office"

    def extract(
        self, inp: ExtractionInput, opts: ExtractionOptions, hooks: QuirkHookRunner
    ) -> tuple[str, dict[str, Any]]:
        from wlh.processor.extractors.pdf.scan_detector import ocr_image_pages

        try:
            images = extract_ole2_data_images(parse_ole2_streams(inp.data))
        except (ValueError, struct.error, IndexError):
            images = []
        if not images:
            raise WlhError("doc_text_unusable")
        text, pdf_info = ocr_image_pages(images, opts.ocr, opts.profile)
        return text, {"pdf": pdf_info}

    def finalize(self, markdown: str) -> str:
        return legal_markdown(markdown)


def extract_doc(
    raw: bytes, *, ocr: Mapping[str, Any] | None = None, script_hint: str | None = None
) -> tuple[str, dict[str, Any]]:
    from wlh.processor.extractors.office import extract_office

    return extract_office(raw, "doc", ocr=ocr, script_hint=script_hint)


__all__ = [
    "Ole2BlipOcrEngine",
    "Ole2PieceTableEngine",
    "decode_fc_compressed",
    "extract_doc",
    "extract_msdoc_pure",
    "extract_ole2_data_images",
    "parse_ole2_streams",
]
