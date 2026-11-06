"""Real binary Office/PDF fixture builders (OLE2 compound files, MS-DOC, BIFF8, OfficeArt BLIPs, scans)."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
FREESECT = 0xFFFFFFFF
OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MSDOC_TEXT_FC = 0x0800


def build_ole2_compound(streams: list[tuple[str, bytes]]) -> bytes:
    """Build a version-3 (512-byte sector) OLE2 compound file holding the given top-level streams.

    Every stream is padded to at least 4096 bytes so it lives in regular FAT sectors (no MiniFAT);
    the FAT spans as many sectors as needed (up to the 109 header DIFAT slots).
    """
    padded = [
        (name, data.ljust(max(4096, -(-len(data) // 512) * 512), b"\x00")) for name, data in streams
    ]
    stream_sectors = sum(len(data) // 512 for _, data in padded)
    dir_sectors = -(-(len(padded) + 1) // 4)
    fat_sectors = 1
    while fat_sectors * 128 < fat_sectors + dir_sectors + stream_sectors:
        fat_sectors += 1
    if fat_sectors > 109:
        raise ValueError("fixture too large for header DIFAT")

    fat = [FREESECT] * (fat_sectors * 128)
    for index in range(fat_sectors):
        fat[index] = FATSECT
    dir_start = fat_sectors
    for index in range(dir_sectors):
        fat[dir_start + index] = dir_start + index + 1 if index + 1 < dir_sectors else ENDOFCHAIN
    next_sector = dir_start + dir_sectors
    starts: list[int] = []
    for _, data in padded:
        count = len(data) // 512
        starts.append(next_sector)
        for index in range(count):
            sector = next_sector + index
            fat[sector] = sector + 1 if index + 1 < count else ENDOFCHAIN
        next_sector += count

    def dir_entry(name: str, obj_type: int, right: int, child: int, start: int, size: int) -> bytes:
        entry = bytearray(128)
        if name:
            name_bytes = name.encode("utf-16le") + b"\x00\x00"
            entry[: len(name_bytes)] = name_bytes
            struct.pack_into("<H", entry, 64, len(name_bytes))
        struct.pack_into("<BB", entry, 66, obj_type, 1)
        struct.pack_into("<III", entry, 68, FREESECT, right, child)
        struct.pack_into("<I", entry, 116, start)
        struct.pack_into("<Q", entry, 120, size)
        return bytes(entry)

    entries = [dir_entry("Root Entry", 5, FREESECT, 1 if padded else FREESECT, ENDOFCHAIN, 0)]
    for sid, ((name, data), start) in enumerate(zip(padded, starts, strict=True), start=1):
        right = sid + 1 if sid < len(padded) else FREESECT
        entries.append(dir_entry(name, 2, right, FREESECT, start, len(data)))
    while len(entries) < dir_sectors * 4:
        entries.append(dir_entry("", 0, FREESECT, FREESECT, FREESECT, 0))

    header = bytearray(512)
    header[0:8] = OLE2_SIGNATURE
    struct.pack_into("<HHHHH", header, 24, 0x003E, 0x0003, 0xFFFE, 9, 6)
    struct.pack_into("<III", header, 40, 0, fat_sectors, dir_start)
    struct.pack_into("<IIIIII", header, 52, 0, 4096, ENDOFCHAIN, 0, ENDOFCHAIN, 0)
    for slot in range(109):
        struct.pack_into("<I", header, 76 + slot * 4, slot if slot < fat_sectors else FREESECT)
    return (
        bytes(header)
        + struct.pack(f"<{len(fat)}I", *fat)
        + b"".join(entries)
        + b"".join(data for _, data in padded)
    )


def build_msdoc_bytes(
    text: str = "", *, compressed_text: bytes | None = None, data_stream: bytes = b""
) -> bytes:
    """Build a Word 97 `.doc` (FIB + 1Table Clx piece table) with one text piece.

    ``text`` is stored as a UTF-16LE piece; ``compressed_text`` instead stores raw 8-bit bytes in an
    ``FcCompressed`` piece (``fc & 0x40000000``).  ``data_stream`` becomes the OLE2 ``Data`` stream.
    """
    if compressed_text is not None:
        body = compressed_text
        ccp_text = len(compressed_text)
        fc_raw = 0x40000000 | (MSDOC_TEXT_FC * 2)
    else:
        body = text.encode("utf-16le")
        ccp_text = len(text)
        fc_raw = MSDOC_TEXT_FC
    plc_pcd = struct.pack("<II", 0, ccp_text) + struct.pack("<HIH", 0, fc_raw, 0)
    clx = b"\x02" + struct.pack("<I", len(plc_pcd)) + plc_pcd

    word = bytearray(MSDOC_TEXT_FC + len(body))
    struct.pack_into("<HH", word, 0x0000, 0xA5EC, 0x00C1)  # wIdent, nFib (Word 97)
    struct.pack_into("<H", word, 0x000A, 0x0200)  # fWhichTblStm -> 1Table
    struct.pack_into("<H", word, 0x0020, 14)  # csw
    struct.pack_into("<H", word, 0x003E, 22)  # cslw
    struct.pack_into("<I", word, 0x004C, ccp_text)  # ccpText
    struct.pack_into("<H", word, 0x0096, 0x005D)  # cbRgFcLcb
    struct.pack_into("<II", word, 0x01A2, 0, len(clx))  # fcClx, lcbClx
    word[MSDOC_TEXT_FC : MSDOC_TEXT_FC + len(body)] = body

    streams = [("WordDocument", bytes(word)), ("1Table", clx)]
    if data_stream:
        streams.append(("Data", data_stream))
    return build_ole2_compound(streams)


def officeart_blip_in_fbse(rec_type: int, rec_instance: int, image: bytes) -> bytes:
    """One OfficeArtFBSE (0xF007) record wrapping an OfficeArt BLIP record holding ``image``."""
    blip_payload = bytes(range(16)) + b"\xff" + image
    blip = struct.pack("<HHI", rec_instance << 4, rec_type, len(blip_payload)) + blip_payload
    bt = 6 if rec_type == 0xF01E else 5
    fbse_body = (
        bytes([bt, bt])
        + bytes(range(16, 32))
        + struct.pack("<HIIIBBBB", 0, len(blip), 1, 0, 0, 0, 0, 0)
    )
    header = struct.pack("<HHI", 0x2 | (bt << 4), 0xF007, len(fbse_body) + len(blip))
    return header + fbse_body + blip


def render_text_image(text: str, image_format: str, *, height: int = 320) -> bytes:
    """Rasterize ``text`` (Helvetica 18pt) to PNG/JPEG bytes at 150 dpi."""
    import fitz  # type: ignore[import-untyped]

    fitz_mod: Any = fitz
    doc = fitz_mod.open()
    page = doc.new_page(width=595, height=height)
    page.insert_textbox(fitz_mod.Rect(30, 40, 565, height - 40), text, fontsize=18, fontname="helv")
    image = bytes(page.get_pixmap(dpi=150).tobytes(image_format))
    doc.close()
    return image


def _biff_record(rec_type: int, data: bytes) -> bytes:
    return struct.pack("<HH", rec_type, len(data)) + data


def build_biff8_xls_bytes(sheet_name: str, cells: dict[tuple[int, int], str | float]) -> bytes:
    """Build a single-sheet BIFF8 `.xls` workbook (OLE2 ``Workbook`` stream) with LABEL/NUMBER cells."""
    bof_globals = _biff_record(0x0809, struct.pack("<HHHHII", 0x0600, 0x0005, 0x0DBB, 0x07CC, 0, 6))
    prelude = _biff_record(0x0042, struct.pack("<H", 1200))  # CODEPAGE: UTF-16
    prelude += b"".join(  # 15 style XFs + 1 cell XF (index 15)
        _biff_record(0x00E0, struct.pack("<HHH", 0, 0, 0xFFF5 if i < 15 else 0x0001) + bytes(14))
        for i in range(16)
    )
    eof = _biff_record(0x000A, b"")
    name_bytes = struct.pack("<BB", len(sheet_name), 1) + sheet_name.encode("utf-16le")
    boundsheet_size = 4 + 6 + len(name_bytes)
    sheet_offset = len(bof_globals) + len(prelude) + boundsheet_size + len(eof)
    boundsheet = _biff_record(0x0085, struct.pack("<IBB", sheet_offset, 0, 0) + name_bytes)

    nrows = max(r for r, _ in cells) + 1
    ncols = max(c for _, c in cells) + 1
    sheet = _biff_record(0x0809, struct.pack("<HHHHII", 0x0600, 0x0010, 0x0DBB, 0x07CC, 0, 6))
    sheet += _biff_record(0x0200, struct.pack("<IIHHH", 0, nrows, 0, ncols, 0))
    for (row, col), value in sorted(cells.items()):
        if isinstance(value, str):
            label = struct.pack("<HB", len(value), 1) + value.encode("utf-16le")
            sheet += _biff_record(0x0204, struct.pack("<HHH", row, col, 15) + label)
        else:
            sheet += _biff_record(0x0203, struct.pack("<HHHd", row, col, 15, value))
    sheet += eof
    workbook = bof_globals + prelude + boundsheet + eof + sheet
    return build_ole2_compound([("Workbook", workbook)])


def build_ruled_table_scan_pdf(path: Path) -> Path:
    """Image-only (scanned) PDF page: a heading plus a 3x3 ruled table, rasterized at 150 dpi."""
    import fitz  # type: ignore[import-untyped]

    fitz_mod: Any = fitz
    src_doc = fitz_mod.open()
    page = src_doc.new_page(width=595, height=842)
    page.insert_textbox(
        fitz_mod.Rect(50, 80, 545, 220),
        "TREASURY DECISION 9988 CORPORATE MINIMUM TAX REGULATIONS\n"
        "Section 1. Applicable corporations shall compute tax using the schedule below.",
        fontsize=15,
        fontname="helv",
    )
    x_cols = [50, 200, 410, 545]
    y_rows = [240, 290, 340, 390]
    shape = page.new_shape()
    for x in x_cols:
        shape.draw_line(fitz_mod.Point(x, y_rows[0]), fitz_mod.Point(x, y_rows[-1]))
    for y in y_rows:
        shape.draw_line(fitz_mod.Point(x_cols[0], y), fitz_mod.Point(x_cols[-1], y))
    shape.finish(color=(0, 0, 0), width=2.0)
    shape.commit()
    rows = [
        ["Form Code", "Taxable Category", "Rate"],
        ["Form 1120", "Domestic Corp", "15%"],
        ["Form 1120-F", "Foreign Corp", "21%"],
    ]
    for r_idx, row in enumerate(rows):
        for c_idx, cell_text in enumerate(row):
            page.insert_text(
                fitz_mod.Point(x_cols[c_idx] + 10, y_rows[r_idx] + 30),
                cell_text,
                fontsize=13,
                fontname="helv",
            )
    png_bytes = page.get_pixmap(dpi=150).tobytes("png")
    src_doc.close()

    scan_doc = fitz_mod.open()
    scan_page = scan_doc.new_page(width=595, height=842)
    scan_page.insert_image(fitz_mod.Rect(0, 0, 595, 842), stream=png_bytes)
    scan_doc.save(str(path))
    scan_doc.close()
    return path


def markdown_table_rows(markdown: str) -> list[list[str]]:
    """Cells of every Markdown pipe-table line (``| a | b |``), separator rows included."""
    return [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in markdown.splitlines()
        if line.strip().startswith("|") and line.strip().endswith("|")
    ]
