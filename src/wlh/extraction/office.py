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
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from wlh.core import MAX_ORIGINAL_BYTES, WlhError
from wlh.extraction.markdown import compact
from wlh.extraction.vi_tcvn3 import (
    legal_markdown,
    normalize_detected_tcvn3,
    rtf_bytes_to_tcvn3_raw,
    rtf_uses_tcvn3,
)

OFFICE_CONTRACT = "wlh.office.v2"
OFFICE_KINDS = {"doc", "docx", "rtf", "xls", "xlsx"}
ZIP_MEMBER_KINDS = OFFICE_KINDS | {"pdf", "txt", "html", "json"}
MAX_ZIP_MEMBERS = 50
MAX_ZIP_DEPTH = 2


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
    text = compact(raw.decode("utf-8", errors="replace"))
    if len(text) < 8:
        raise WlhError("libreoffice_text_unusable")
    return text + "\n"


def table_markdown(rows: list[list[str]]) -> str:
    """One Markdown table block; the first row is the header (source geometry unknown)."""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines: list[str] = [
        "| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"
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
        if not any(
            "\\" in name or name.startswith("[trash]/") for name in archive.namelist()
        ):
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
                            [excel_value(v) for v in row] for row in sheet.iter_rows(values_only=True)
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


def safe_member(name: str) -> str | None:
    parts = [p for p in Path(name.replace("\\", "/")).parts if p not in ("", ".", "/")]
    if not parts or any(p == ".." for p in parts) or name.startswith(("/", "\\")):
        return None
    return "/".join(parts)


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


def unpack_zip(path: str | Path, folder: str | Path, max_bytes: int) -> list[dict[str, Any]]:
    """Inner real documents with lineage paths; never hand the outer container to LibreOffice."""
    members: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(path) as archive:
            raw_infos = [i for i in archive.infolist() if not i.is_dir()]
            has_json_member = any(
                (sm := safe_member(i.filename)) is not None and sm.lower().endswith(".json")
                for i in raw_infos
            )
            infos = [
                i
                for i in raw_infos
                if not (
                    has_json_member
                    and (sm := safe_member(i.filename)) is not None
                    and sm.upper().endswith(".PDF")
                )
            ]
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
                if has_json_member and name.upper().endswith(".PDF"):
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
        from wlh.extraction.markdown import html_to_markdown

        return html_to_markdown(raw), {"kind": kind, "detected_format": kind}
    if kind in {"zip", "pdf"}:
        from wlh.config import OCRConfig
        from wlh.extraction.extract_worker import extract

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

    """Structured direct extraction first (office_text_policy=prefer), LibreOffice fallback."""
    timeout = cast(float, cfg["libreoffice_timeout_seconds"])
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
        if prefer:
            try:
                markdown_docx, info["extractor"] = extract_docx(path), "python_docx"
            except WlhError as exc:
                if exc.code != "quality_too_short":
                    raise
                info["attempts"].append(exc.code)
                from wlh.config import OCRConfig
                from wlh.extraction.pdf_scan import extract_pdf

                ocr_cfg = ocr if ocr is not None else OCRConfig().model_dump()
                try:
                    try:
                        data = libreoffice_convert(
                            path, "pdf:writer_pdf_Export", "pdf", timeout, kind
                        )
                        extractor_used = "libreoffice_pdf"
                    except WlhError as lo_exc:
                        info["attempts"].append(lo_exc.code)
                        import pymupdf as _fitz  # type: ignore[import-untyped]

                        fitz_mod: Any = _fitz
                        with normalized_office_archive(path) as handle, zipfile.ZipFile(handle) as zf:
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
                                    page.insert_image(
                                        fitz_mod.Rect(18, 18, 577, 824), stream=img_bytes
                                    )
                                data = bytes(pdf_doc.tobytes())
                            finally:
                                pdf_doc.close()
                        extractor_used = "docx_media_pdf_ocr"
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
                info.update(extractor=extractor_used, pdf=pdf_info)
        if markdown_docx is None:
            markdown_docx = legal_markdown(extract_libreoffice_text(path, timeout, kind))
            info["extractor"] = "libreoffice_text"
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
                extract_libreoffice_text(path, timeout, kind),
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
        try:
            text, info["extractor"] = (
                extract_libreoffice_text(path, timeout, kind),
                "libreoffice_text",
            )
        except WlhError as exc:
            info["attempts"].append(exc.code)
            from wlh.config import OCRConfig
            from wlh.extraction.pdf_scan import extract_pdf

            data = libreoffice_convert(path, "pdf:writer_pdf_Export", "pdf", timeout, kind)
            with tempfile.TemporaryDirectory(prefix="wlh-office-pdf-") as folder:
                pdf_path = Path(folder) / "input.pdf"
                pdf_path.write_bytes(data)
                text, pdf_info, quality, _ = extract_pdf(
                    pdf_path, OCRConfig().model_dump(), "generic"
                )
            if quality["recommended_route"] not in {"accept", "accept_ocr", "accept_with_warning"}:
                raise WlhError("quality_check_failed")
            info.update(extractor="libreoffice_pdf", pdf=pdf_info)
        text, converted = normalize_detected_tcvn3(text)
        if converted:
            info["legacy_encoding"] = "tcvn3"
        return legal_markdown(text), info
    raise WlhError("unsupported_office_type")
