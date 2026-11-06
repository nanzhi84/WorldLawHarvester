"""Office containers, structured direct extraction and Vietnamese TCVN3 recovery.

Core logic refactored from docs/reference/office_text_extract.py. Inputs are always files
materialized from verified CAS objects; LibreOffice runs headless in an isolated profile,
its own process group and a bounded timeout. Windows COM fallbacks are not supported.
"""

from __future__ import annotations

import codecs
import io
import os
import re
import shutil
import signal
import struct
import subprocess
import tempfile
import zipfile
import zlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from wlh.cleaners.text_cleaner import compact
from wlh.core import MAX_ORIGINAL_BYTES, WlhError
from wlh.processor.extractors.quirks.vi_tcvn3 import (
    legal_markdown,
    normalize_detected_tcvn3,
    rtf_bytes_to_tcvn3_raw,
    rtf_uses_tcvn3,
)
from wlh.processor.extractors.quirks.zip_salvage import (
    filter_redundant_zip_members,
    safe_member,
    salvage_zip_prefix,
)

OFFICE_CONTRACT = "wlh.office.v2"
OFFICE_KINDS = {"doc", "docx", "rtf", "xls", "xlsx"}
ZIP_MEMBER_KINDS = OFFICE_KINDS | {"pdf", "txt", "html", "json"}
MAX_ZIP_MEMBERS = 50
MAX_ZIP_DEPTH = 2

__all__ = [
    "MAX_ZIP_DEPTH",
    "MAX_ZIP_MEMBERS",
    "OFFICE_CONTRACT",
    "OFFICE_KINDS",
    "ZIP_MEMBER_KINDS",
    "bounded_run",
    "detect_real_format",
    "excel_value",
    "extract_docx",
    "extract_excel",
    "extract_libreoffice_text",
    "extract_msdoc_pure",
    "extract_office",
    "extract_ole2_data_images",
    "libreoffice_convert",
    "libreoffice_identity",
    "normalized_office_archive",
    "parse_ole2_streams",
    "safe_member",
    "salvage_zip_prefix",
    "soffice",
    "table_markdown",
    "unpack_zip",
]


def detect_real_format(data: bytes, suffix: str = "") -> str:
    hint = suffix.lstrip(".")
    """Real container type from magic bytes and structure, never from a URL suffix."""
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
    if head == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        if "WordDocument".encode("utf-16-le") in data:
            return "doc"
        if "Workbook".encode("utf-16-le") in data or "Book".encode("utf-16-le") in data:
            return "xls"
        return "unknown"
    stripped = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"{\\rtf"):
        return "rtf"
    lowered = re.sub(rb"^(?:<!--.*?-->\s*)+", b"", stripped[:2048], flags=re.S).lower()
    if (
        lowered.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))
        or b"<html" in lowered
        or b"<body" in lowered
        or (hint == "html" and lowered.startswith(b"<") and not lowered.startswith(b"<?xml"))
    ):
        return "html"
    if lowered.startswith(b"<?xml") or lowered.startswith(b"<"):
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


def soffice() -> str | None:
    configured = os.environ.get("WLH_SOFFICE")
    for candidate in (
        configured,
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    return None


def bounded_run(
    args: Sequence[str], timeout: float, cwd: str | Path | None = None, limit: int = 65536
) -> tuple[int, bytes]:
    """Own process group; stderr never forwarded (it can contain document text/paths)."""
    process: subprocess.Popen[bytes] = subprocess.Popen(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "HOME")},
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise WlhError("libreoffice_timeout") from None
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    return process.returncode, output[:limit]


def libreoffice_identity() -> dict[str, str]:
    executable = soffice()
    if not executable:
        return {"status": "unavailable"}
    try:
        with tempfile.TemporaryDirectory(prefix="wlh-office-id-") as temp:
            profile = (Path(temp) / "profile").as_uri()
            code, output = bounded_run(
                [executable, "-env:UserInstallation=" + profile, "--headless", "--version"], 60
            )
        if code != 0:
            raise ValueError
        version = output.decode("utf-8", errors="replace").strip().splitlines()[0][:200]
        return {"status": "available", "version": version}
    except (OSError, ValueError, IndexError, WlhError):
        return {"status": "unavailable"}


def libreoffice_convert(
    path: str | Path, target_format: str, extension: str, timeout: float, kind: str
) -> bytes:
    executable = soffice()
    if not executable:
        raise WlhError("libreoffice_unavailable")
    with tempfile.TemporaryDirectory(prefix="wlh-office-") as temp:
        folder = Path(temp)
        source = folder / ("input." + kind)
        shutil.copyfile(path, source)
        code, _ = bounded_run(
            [
                executable,
                "-env:UserInstallation=" + (folder / "profile").as_uri(),
                "--headless",
                "--norestore",
                "--convert-to",
                target_format,
                "--outdir",
                str(folder / "out"),
                str(source),
            ],
            timeout,
        )
        target = folder / "out" / ("input." + extension)
        if code != 0 or not target.is_file():
            raise WlhError("libreoffice_conversion_failed")
        return target.read_bytes()


def extract_libreoffice_text(path: str | Path, timeout: float, kind: str) -> str:
    raw = libreoffice_convert(path, "txt:Text (encoded):UTF8", "txt", timeout, kind)
    text = compact(raw.decode("utf-8", errors="replace")).lstrip("\ufeff")
    if len(re.sub(r"\s+", "", text)) < 15:
        raise WlhError("libreoffice_text_unusable")
    return text + "\n"


def table_markdown(rows: list[list[str]]) -> str:
    """One Markdown table block; the first row is the header (source geometry unknown)."""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines: list[str] = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(lines)


def excel_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return compact(str(value))


def normalized_office_archive(path: str | Path) -> io.BytesIO:
    """Normalize Windows-signed Office ZIPs in memory, preserving the archived original."""
    with io.BytesIO(Path(path).read_bytes()) as source, zipfile.ZipFile(source) as archive:
        if not any("\\" in name or name.startswith("[trash]/") for name in archive.namelist()):
            return io.BytesIO(source.getvalue())
        normalized = io.BytesIO()
        with zipfile.ZipFile(normalized, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for member in archive.infolist():
                name = member.filename.replace("\\", "/")
                if not name.startswith("[trash]/"):
                    output.writestr(name, archive.read(member))
    normalized.seek(0)
    return normalized


def extract_excel(path: str | Path, kind: str) -> tuple[str, str]:
    """XLS/XLSX workbooks (including spreadsheets disguised as DOC/DOCX) as Markdown tables."""
    sheets: list[tuple[Any, list[list[str]]]] = []
    try:
        if kind == "xls":
            import xlrd  # type: ignore[import-untyped]

            workbook: Any = xlrd.open_workbook(str(path), formatting_info=False, on_demand=True)
            for sheet in workbook.sheets():
                rows: list[list[str]] = [
                    [excel_value(sheet.cell_value(r, c)) for c in range(sheet.ncols)]
                    for r in range(sheet.nrows)
                ]
                sheets.append((sheet.name, rows))
            extractor = "xlrd"
        else:
            from openpyxl import load_workbook  # type: ignore[import-untyped]

            with normalized_office_archive(path) as handle:
                workbook = load_workbook(handle, read_only=True, data_only=True)
                try:
                    for sheet in workbook.worksheets:
                        rows = [
                            [excel_value(v) for v in row]
                            for row in sheet.iter_rows(values_only=True)
                        ]
                        sheets.append((sheet.title, rows))
                finally:
                    workbook.close()
            extractor = "openpyxl"
    except Exception:
        raise WlhError("spreadsheet_invalid") from None
    parts: list[str] = []
    for name, rows in sheets:
        nonempty = [row for row in rows if any(cell for cell in row)]
        if not nonempty:
            continue
        width = max(len(row) for row in nonempty)
        rows = [row + [""] * (width - len(row)) for row in nonempty]
        parts.append("# " + compact(str(name)))
        parts.append(table_markdown([[c.replace("|", "\\|") for c in row] for row in rows]))
    value = "\n\n".join(parts).strip()
    if len(value) < 8:
        raise WlhError("spreadsheet_empty")
    return value + "\n", extractor


def extract_docx(path: str | Path) -> str:
    from docx import Document  # type: ignore[import-untyped]
    from docx.table import Table  # type: ignore[import-untyped]
    from docx.text.paragraph import Paragraph  # type: ignore[import-untyped]

    has_media_images = False
    try:
        with normalized_office_archive(path) as handle:
            with zipfile.ZipFile(handle) as zf:
                has_media_images = any(
                    name.startswith("word/media/")
                    and name.lower().endswith(
                        (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif")
                    )
                    for name in zf.namelist()
                )
            handle.seek(0)
            document: Any = Document(handle)
    except Exception:
        raise WlhError("docx_invalid") from None
    parts: list[str] = []
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            paragraph: Any = Paragraph(child, document)
            text = compact(paragraph.text)
            if not text:
                continue
            style = ((paragraph.style.name if paragraph.style else "") or "").lower()
            level = re.match(r"heading\s*([1-6])", style)
            if style == "title":
                parts.append("# " + text)
            elif level:
                parts.append("#" * int(level[1]) + " " + text)
            else:
                parts.append(text)
        elif child.tag.endswith("}tbl"):
            rows: list[list[str]] = []
            for row in Table(child, document).rows:
                cells = [compact(cell.text).replace("|", "\\|") for cell in row.cells]
                # Word represents horizontal merges as repeated cell objects.
                cells = [c for i, c in enumerate(cells) if i == 0 or c != cells[i - 1]]
                rows.append(cells)
            if rows:
                parts.append(table_markdown(rows))
    value = "\n\n".join(parts).strip()
    min_chars = 200 if has_media_images else 8
    if len(compact(value)) < min_chars:
        raise WlhError("quality_too_short" if has_media_images else "docx_text_unusable")
    return value + "\n"


def unpack_zip(
    path: str | Path,
    folder: str | Path,
    max_bytes: int,
    *,
    quirks: Sequence[str] = ("zip_salvage",),
) -> list[dict[str, Any]]:
    """Inner real documents with lineage paths; never hand the outer container to LibreOffice."""
    members: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(path) as archive:
            raw_infos = [i for i in archive.infolist() if not i.is_dir()]
            infos = (
                filter_redundant_zip_members(raw_infos) if "zip_salvage" in quirks else raw_infos
            )
            if len(infos) > MAX_ZIP_MEMBERS:
                raise WlhError("zip_member_limit_exceeded")
            if sum(i.file_size for i in infos) > max_bytes:
                raise WlhError("zip_expanded_size_exceeded")
            total = 0
            for index, info in enumerate(sorted(infos, key=lambda i: i.filename)):
                name = safe_member(info.filename)
                if name is None:
                    raise WlhError("zip_member_path_invalid")
                if name.startswith("__MACOSX/") or Path(name).name.startswith("._"):
                    continue
                with archive.open(info) as stream:
                    data = stream.read(max_bytes - total + 1)
                total += len(data)
                if total > max_bytes:
                    raise WlhError("zip_expanded_size_exceeded")
                kind = detect_real_format(data)
                if kind not in ZIP_MEMBER_KINDS and kind != "zip":
                    continue
                target = Path(folder) / f"member-{index}"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                members.append({"path": name, "file": target, "kind": kind})
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
        raise WlhError("zip_invalid") from None
    if not members:
        raise WlhError("zip_without_documents")
    return members


def parse_ole2_streams(data: bytes) -> dict[str, bytes]:
    """Zero-dependency in-memory OLE2 Compound Document stream reader."""
    if len(data) < 512 or data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
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
    difat = [x for x in struct.unpack_from("<109I", data, 76) if x < 0xFFFFFFFE]
    sec = first_difat
    for _ in range(num_difat):
        if sec >= 0xFFFFFFFE:
            break
        off = (sec + 1) * sector_size
        vals = struct.unpack_from(f"<{sector_size // 4}I", data, off)
        difat.extend(x for x in vals[:-1] if x < 0xFFFFFFFE)
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
        while s < 0xFFFFFFFE and s < len(fat) and s not in seen:
            seen.add(s)
            off = (s + 1) * sector_size
            out.extend(data[off : off + sector_size])
            if size is not None and len(out) >= size:
                return bytes(out[:size])
            s = fat[s]
        return bytes(out if size is None else out[:size])

    dir_bytes = read_chain(first_dir)
    minifat: list[int] = []
    if first_minifat < 0xFFFFFFFE and num_minifat > 0:
        mf_bytes = read_chain(first_minifat)
        minifat = list(struct.unpack(f"<{len(mf_bytes) // 4}I", mf_bytes))
    root_start, root_size = struct.unpack_from("<IQ", dir_bytes, 116)
    if sector_size == 512:
        root_size &= 0xFFFFFFFF
    mini_stream = read_chain(root_start, root_size) if root_start < 0xFFFFFFFE else b""

    def read_mini_chain(start_sec: int, size: int) -> bytes:
        out = bytearray()
        s = start_sec
        seen: set[int] = set()
        while s < 0xFFFFFFFE and s < len(minifat) and s not in seen and len(out) < size:
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


def extract_ole2_data_images(streams: Mapping[str, bytes]) -> list[bytes]:
    """Extract embedded PNG/JPEG/TIFF or OfficeArtBlipEMF (0xF01A EMR_STRETCHDIBITS) bitmaps from OLE2 streams."""
    images: list[bytes] = []
    for sname in ("Data", "WordDocument"):
        buf = streams.get(sname, b"")
        if not buf:
            continue
        pos = 0
        while pos + 58 <= len(buf):
            idx = buf.find(b"\x1a\xf0", pos)
            if idx == -1 or idx < 2 or idx + 56 > len(buf):
                break
            _, rec_type, rec_len = struct.unpack_from("<HHI", buf, idx - 2)
            if rec_type == 0xF01A and 60 < rec_len <= len(buf) - (idx - 2):
                rec_data = buf[idx + 6 : idx + 6 + rec_len]
                for z_off in (50, 66):
                    if z_off < len(rec_data):
                        try:
                            emf = zlib.decompress(rec_data[z_off:])
                            epos = 0
                            while epos + 8 <= len(emf):
                                rtype, rsize = struct.unpack_from("<II", emf, epos)
                                if rsize < 8 or epos + rsize > len(emf):
                                    break
                                if rtype == 81 and rsize >= 80:  # EMR_STRETCHDIBITS
                                    off_bmi, cb_bmi, off_bits, cb_bits = struct.unpack_from(
                                        "<IIII", emf, epos + 48
                                    )
                                    if (
                                        cb_bmi > 0
                                        and cb_bits > 0
                                        and off_bmi + cb_bmi <= rsize
                                        and off_bits + cb_bits <= rsize
                                    ):
                                        bmi = emf[epos + off_bmi : epos + off_bmi + cb_bmi]
                                        bits = emf[epos + off_bits : epos + off_bits + cb_bits]
                                        bmp_size = 14 + len(bmi) + len(bits)
                                        bmp = (
                                            b"BM"
                                            + struct.pack("<III", bmp_size, 0, 14 + len(bmi))
                                            + bmi
                                            + bits
                                        )
                                        images.append(bmp)
                                epos += rsize
                            break
                        except zlib.error:
                            continue
            pos = idx + 2
        p_idx = buf.find(b"\x89PNG\r\n\x1a\n")
        if p_idx != -1:
            end_idx = buf.find(b"IEND\xaeB`\x82", p_idx)
            if end_idx != -1:
                images.append(buf[p_idx : end_idx + 8])
        t_idx = buf.find(b"II*\x00\x08\x00\x00\x00")
        if t_idx != -1:
            images.append(buf[t_idx:])
        j_idx = buf.find(b"\xff\xd8\xff")
        if j_idx != -1:
            end_j = buf.rfind(b"\xff\xd9")
            if end_j > j_idx:
                images.append(buf[j_idx : end_j + 2])
    return images


def _format_msdoc_paragraphs_and_tables(raw_text: str) -> tuple[str, int]:
    """Convert raw MS-DOC character stream (with \\x07 cell/row marks) into Markdown + tables."""
    text = re.sub(r"\x13[^\x14\x15]*\x14", "", raw_text)
    text = re.sub(r"\x13[^\x15]*\x15", "", text)
    text = text.replace("\x15", "").replace("\x0b", "\n").replace("\x0c", "\n\n")
    text = re.sub(r"[\x00-\x06\x08\x0e-\x12\x16-\x1f]", "", text)

    blocks: list[str] = []
    current_table: list[list[str]] = []
    current_row: list[str] = []
    tables_recovered = 0

    def flush_table() -> None:
        nonlocal current_table, current_row, tables_recovered
        if current_row:
            if any(c.strip() for c in current_row):
                current_table.append(current_row)
            current_row = []
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
            cell_text = re.sub(r"\s+", " ", "".join(buf)).strip().replace("|", "\\|")
            buf.clear()
            if idx + 1 < n and text[idx + 1] == "\x07":
                current_row.append(cell_text)
                if any(c.strip() for c in current_row):
                    current_table.append(current_row)
                current_row = []
                idx += 2
                if idx < n and text[idx] == "\r":
                    idx += 1
                continue
            elif not cell_text and current_row and (idx + 1 == n or text[idx + 1] in ("\r", "\n")):
                if any(c.strip() for c in current_row):
                    current_table.append(current_row)
                current_row = []
                idx += 1
                if idx < n and text[idx] == "\r":
                    idx += 1
                continue
            else:
                current_row.append(cell_text)
                idx += 1
                continue
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


def extract_msdoc_pure(data: bytes) -> tuple[str, dict[str, Any]]:
    """Pure-Python in-memory MS-DOC (Word 97-2003) extractor with table recovery."""
    streams = parse_ole2_streams(data)
    wdoc = streams.get("WordDocument")
    if not wdoc or len(wdoc) < 0x200:
        raise ValueError("missing_word_document_stream")
    flags = struct.unpack_from("<H", wdoc, 0x000A)[0]
    ccp_text = struct.unpack_from("<I", wdoc, 0x004C)[0]
    tbl_name = "1Table" if (flags & 0x0200) else "0Table"
    tbl = streams.get(tbl_name) or streams.get("1Table") or streams.get("0Table")
    if not tbl:
        raise ValueError("missing_table_stream")
    fc_clx, lcb_clx = struct.unpack_from("<II", wdoc, 0x01A2)
    clx = tbl[fc_clx : fc_clx + lcb_clx]
    pos = 0
    plc = b""
    while pos < len(clx):
        clxt = clx[pos]
        if clxt == 1:
            cb = struct.unpack_from("<H", clx, pos + 1)[0]
            pos += 3 + cb
        elif clxt == 2:
            lcb = struct.unpack_from("<I", clx, pos + 1)[0]
            plc = clx[pos + 5 : pos + 5 + lcb]
            break
        else:
            raise ValueError(f"unexpected_clxt_{clxt}")
    if not plc:
        raise ValueError("missing_pcdt")
    n = (len(plc) - 4) // 12
    cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
    pieces: list[str] = []
    for i in range(n):
        cp_start, cp_end = cps[i], cps[i + 1]
        if cp_start >= ccp_text:
            break
        cp_end = min(cp_end, ccp_text)
        char_count = cp_end - cp_start
        if char_count <= 0:
            continue
        pcd_off = (n + 1) * 4 + i * 8
        _, fc_raw, _ = struct.unpack_from("<HIH", plc, pcd_off)
        compressed = bool(fc_raw & 0x40000000)
        if compressed:
            byte_off = (fc_raw & ~0x40000000) // 2
            raw_slice = wdoc[byte_off : byte_off + char_count]
            pieces.append(raw_slice.decode("cp1252", errors="replace"))
        else:
            byte_off = fc_raw & 0x3FFFFFFF
            raw_slice = wdoc[byte_off : byte_off + char_count * 2]
            pieces.append(raw_slice.decode("utf-16-le", errors="replace"))
    raw_text = "".join(pieces)
    md, tables_count = _format_msdoc_paragraphs_and_tables(raw_text)
    return md, {"extractor": "pure_python_ole2_msdoc", "tables_recovered": tables_count}


def extract_office(
    path: str | Path,
    kind: str | None = None,
    timeout: float | dict[str, Any] = 120,
    ocr: dict[str, Any] | None = None,
    script_hint: str | None = None,
) -> tuple[str, dict[str, Any]]:
    path = Path(path)
    cfg: dict[str, Any] = (
        timeout
        if isinstance(timeout, dict)
        else {"libreoffice_timeout_seconds": timeout, "office_text_policy": "prefer"}
    )
    raw = path.read_bytes()
    kind = detect_real_format(raw, kind or path.suffix)
    if kind == "html":
        from wlh.processor.extractors.html.html_to_md import html_to_markdown

        return html_to_markdown(raw), {"kind": kind, "detected_format": kind}
    if kind in {"zip", "pdf"}:
        from wlh.config import OCRConfig
        from wlh.processor.extractors.extract_worker import extract

        result = extract(
            path,
            kind,
            {
                "ocr": ocr if ocr is not None else OCRConfig().model_dump(),
                "parser": cfg,
                "profile": script_hint or "generic",
                "max_bytes": MAX_ORIGINAL_BYTES,
            },
        )
        return result[0], {**result[1], "detected_format": kind}

    """Structured direct extraction first (pure-Python / python-docx / xlrd / openpyxl) without LibreOffice."""
    timeout_val = cast(float, cfg["libreoffice_timeout_seconds"])
    prefer = cfg["office_text_policy"] == "prefer"
    info: dict[str, Any] = {
        "kind": kind,
        "detected_format": kind,
        "legacy_encoding": None,
        "legacy_fonts": [],
        "attempts": [],
    }
    if kind in {"xls", "xlsx"}:
        markdown, info["extractor"] = extract_excel(path, kind)
        return markdown, info
    if kind == "docx":
        markdown_docx: str | None = None
        try:
            markdown_docx, info["extractor"] = extract_docx(path), "python_docx"
        except WlhError as exc:
            if exc.code != "quality_too_short":
                raise
            info["attempts"].append(exc.code)
            import pymupdf as _fitz  # type: ignore[import-untyped]

            from wlh.config import OCRConfig
            from wlh.processor.extractors.pdf.scan_detector import extract_pdf

            fitz_mod: Any = _fitz
            ocr_cfg = ocr if ocr is not None else OCRConfig().model_dump()
            try:
                with (
                    normalized_office_archive(path) as handle,
                    zipfile.ZipFile(handle) as zf,
                ):
                    media_names = sorted(
                        name
                        for name in zf.namelist()
                        if name.startswith("word/media/")
                        and name.lower().endswith(
                            (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif")
                        )
                    )
                    if not media_names:
                        raise WlhError("quality_too_short") from None
                    pdf_doc = fitz_mod.open()
                    try:
                        for mname in media_names:
                            img_bytes = zf.read(mname)
                            page = pdf_doc.new_page(width=595, height=842)
                            page.insert_image(fitz_mod.Rect(18, 18, 577, 824), stream=img_bytes)
                        data = bytes(pdf_doc.tobytes())
                    finally:
                        pdf_doc.close()
                with tempfile.TemporaryDirectory(prefix="wlh-office-pdf-") as folder:
                    pdf_path = Path(folder) / "input.pdf"
                    pdf_path.write_bytes(data)
                    markdown_docx, pdf_info, quality, _ = extract_pdf(
                        pdf_path, ocr_cfg, script_hint or "generic"
                    )
            except WlhError as pdf_exc:
                info["attempts"].append(pdf_exc.code)
                raise WlhError("quality_too_short") from None
            if (
                quality["recommended_route"]
                not in {
                    "accept",
                    "accept_ocr",
                    "accept_with_warning",
                }
                or len(compact(markdown_docx)) < 80
            ):
                raise WlhError("quality_too_short")
            info.update(extractor="docx_media_pdf_ocr", pdf=pdf_info)
        markdown_docx, changed = normalize_detected_tcvn3(markdown_docx)
        if changed:
            info["legacy_encoding"] = "tcvn3"
        return markdown_docx, info
    raw = Path(path).read_bytes()
    if kind == "rtf":
        markdown = None
        if prefer:
            try:
                uses, _ = rtf_uses_tcvn3(raw)
                if uses:
                    markdown = rtf_bytes_to_tcvn3_raw(raw)
                else:
                    from striprtf.striprtf import rtf_to_text  # type: ignore[import-untyped]

                    markdown = cast(str, rtf_to_text(raw.decode("latin-1")))
                info["extractor"] = "rtf_direct_text"
            except WlhError as exc:
                info["attempts"].append(exc.code)
        if markdown is None:
            markdown, info["extractor"] = (
                extract_libreoffice_text(path, timeout_val, kind),
                "libreoffice_text",
            )
        uses, fonts = rtf_uses_tcvn3(raw)
        info["legacy_fonts"] = fonts
        # Line-level conversion protects Unicode headers and dates in mixed documents.
        markdown, converted = normalize_detected_tcvn3(markdown, font_evidence=uses)
        if converted:
            info["legacy_encoding"] = "tcvn3"
        return legal_markdown(markdown), info
    if kind == "doc":
        text = ""
        try:
            text, msdoc_info = extract_msdoc_pure(raw)
            if len(re.sub(r"\s+", "", text)) >= 15:
                info["extractor"] = msdoc_info["extractor"]
                info["tables_recovered"] = msdoc_info["tables_recovered"]
            else:
                text = ""
        except Exception:
            text = ""

        if not text:
            try:
                streams = parse_ole2_streams(raw)
                images = extract_ole2_data_images(streams)
            except Exception:
                images = []
            if images:
                import pymupdf as _fitz  # type: ignore[import-untyped]

                from wlh.config import OCRConfig
                from wlh.processor.extractors.pdf.scan_detector import extract_pdf

                fitz_mod = _fitz
                ocr_cfg = ocr if ocr is not None else OCRConfig().model_dump()
                pdf_doc = fitz_mod.open()
                try:
                    for img_bytes in images:
                        page = pdf_doc.new_page(width=595, height=842)
                        page.insert_image(fitz_mod.Rect(18, 18, 577, 824), stream=img_bytes)
                    pdf_data: bytes = pdf_doc.tobytes()
                finally:
                    pdf_doc.close()
                with tempfile.TemporaryDirectory(prefix="wlh-msdoc-img-") as folder:
                    pdf_path = Path(folder) / "input.pdf"
                    pdf_path.write_bytes(pdf_data)
                    text, pdf_info, quality, _ = extract_pdf(
                        pdf_path, ocr_cfg, script_hint or "generic"
                    )
                if quality["recommended_route"] not in {
                    "accept",
                    "accept_ocr",
                    "accept_with_warning",
                }:
                    raise WlhError("quality_check_failed")
                info.update(extractor="msdoc_embedded_image_ocr", pdf=pdf_info)
            elif Path("/usr/bin/textutil").is_file():
                try:
                    proc = subprocess.run(
                        [
                            "/usr/bin/textutil",
                            "-stdin",
                            "-format",
                            "doc",
                            "-convert",
                            "txt",
                            "-stdout",
                        ],
                        input=raw,
                        capture_output=True,
                        timeout=min(15.0, timeout_val),
                        check=True,
                    )
                    cand = compact(proc.stdout.decode("utf-8", errors="replace"))
                    if len(re.sub(r"\s+", "", cand)) >= 15:
                        text = cand
                        info["extractor"] = "textutil_txt"
                except Exception:
                    pass

        if not text:
            text, info["extractor"] = (
                extract_libreoffice_text(path, timeout_val, kind),
                "libreoffice_text",
            )
        text, converted = normalize_detected_tcvn3(text)
        if converted:
            info["legacy_encoding"] = "tcvn3"
        return legal_markdown(text), info
    raise WlhError("unsupported_office_type")
