#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Direct Unicode text extraction for DOCX, DOC and RTF legal documents.

DOCX is read structurally with python-docx. DOC/RTF use Word or WPS COM in
read-only mode. RTF files that declare legacy Vietnamese TCVN3 fonts such as
VnTime are converted to Unicode before they are used as searchable text.
"""
from __future__ import annotations

import json
import multiprocessing
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import fitz

TCVN3_SOURCE = "µ¸¶·¹¨»¾¼½Æ©ÇÊÈÉË®ÌÐÎÏÑªÒÕÓÔÖ×ÝØÜÞßãáâä«åèæçé¬êíëìîïóñòô-õøö÷ùúýûüþ¡¢§£¤¥¦"
TCVN3_TARGET = "àáảãạăằắẳẵặâầấẩẫậđèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵĂÂĐÊÔƠƯ"
_TCVN3_HYPHEN_INDEX = TCVN3_SOURCE.index("-")
TCVN3_TRANSLATION = str.maketrans({
    **dict(zip(
        TCVN3_SOURCE[:_TCVN3_HYPHEN_INDEX] + TCVN3_SOURCE[_TCVN3_HYPHEN_INDEX + 1:],
        TCVN3_TARGET[:_TCVN3_HYPHEN_INDEX] + TCVN3_TARGET[_TCVN3_HYPHEN_INDEX + 1:],
    )),
    "\u00ad": "ư",
})
LEGACY_TCVN3_FONT = re.compile(r"(?:^|[.\s])(vn(?:time|arial|helvet|book|courier)|vntimeh)\b", re.I)


def compact_text(value: str) -> str:
    return re.sub(r"[ \t]+", " ", value.replace("\r\n", "\n").replace("\r", "\n")).strip()


def tcvn3_to_unicode(value: str) -> str:
    return value.translate(TCVN3_TRANSLATION)


# These glyphs do not occur in normal Unicode Vietnamese prose.  The other
# TCVN3 codepoints overlap Unicode letters such as á and à, so converting a
# whole mixed-encoding document would corrupt already-correct headers.
TCVN3_STRONG_GLYPHS = frozenset("µ¸¶·¹¨»¾¼½Æ©ÇÊÈÉË®ÌÐÎÏÑªÒÕÓÔÖ×ÝØÜÞß«åæ¬ëîïñö÷ûüþ¡¢§£¤¥¦")


def normalize_detected_tcvn3(value: str) -> tuple[str, bool]:
    """Convert TCVN3 lines from a mixed Unicode/TCVN3 Office text export."""
    strong_count = sum(value.count(char) for char in TCVN3_STRONG_GLYPHS)
    if strong_count < 3:
        return value, False
    # LibreOffice retains paragraph line breaks.  Convert only lines carrying a
    # non-Unicode TCVN3 glyph, preserving already-Unicode headers and dates.
    converted = "".join(
        tcvn3_to_unicode(line) if any(char in TCVN3_STRONG_GLYPHS for char in line) else line
        for line in value.splitlines(keepends=True)
    )
    return converted, True


def rtf_font_names(path: Path) -> list[str]:
    raw = path.read_bytes().decode("latin-1", errors="ignore")
    # Font declarations end in a semicolon. This deliberately retains dot-prefixed
    # font names such as .VnTime used by TCVN3/ABC documents.
    return [item.strip() for item in re.findall(r"(?:\\f\d+[^;{}]*)?([^;{}]+);", raw[:20000])
            if "font" not in item.lower()]


def rtf_uses_tcvn3(path: Path) -> tuple[bool, list[str]]:
    fonts = rtf_font_names(path)
    matches = [font for font in fonts if LEGACY_TCVN3_FONT.search(font)]
    return bool(matches), matches


def find_soffice() -> str | None:
    candidates = [
        shutil.which("soffice"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    return next((value for value in candidates if value and Path(value).exists()), None)


def extract_libreoffice_text(path: Path) -> str:
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice soffice executable not found")
    with tempfile.TemporaryDirectory(prefix="vn-office-") as temp:
        output = Path(temp)
        completed = subprocess.run(
            [soffice, f"-env:UserInstallation={ (output / 'profile').as_uri() }", "--headless", "--convert-to", "txt:Text", "--outdir", str(output), str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90, check=False,
        )
        target = output / f"{path.stem}.txt"
        if completed.returncode != 0 or not target.exists():
            raise RuntimeError((completed.stderr or completed.stdout or b"LibreOffice conversion failed").decode("utf-8", errors="replace").strip())
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(compact_text(text)) < 8:
            raise RuntimeError("LibreOffice conversion produced no usable text")
        return compact_text(text) + "\n"



def extract_libreoffice_pdf_text(path: Path) -> tuple[str, str]:
    """Fallback for legacy binary DOC files that cannot export through txt:Text."""
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice soffice executable not found")
    with tempfile.TemporaryDirectory(prefix="vn-office-pdf-") as temp:
        output = Path(temp)
        completed = subprocess.run(
            [soffice, f"-env:UserInstallation={(output / 'profile').as_uri()}", "--headless",
             "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(output), str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False,
        )
        target = output / f"{path.stem}.pdf"
        if completed.returncode != 0 or not target.exists():
            raise RuntimeError((completed.stderr or completed.stdout or b"LibreOffice PDF conversion failed").decode("utf-8", errors="replace").strip())
        with fitz.open(target) as document:
            pages = [page.get_text("text").strip() for page in document]
        raw = "\n\n".join(f"<!-- page:{number} -->\n\n{text}" for number, text in enumerate(pages, 1) if text)
        if len(compact_text(raw)) < 8:
            raise RuntimeError("LibreOffice PDF conversion produced no usable text")
        normalized, is_tcvn3 = normalize_detected_tcvn3(raw)
        if is_tcvn3:
            return compact_text(normalized) + "\n", "libreoffice_pdf_text_tcvn3"
        return compact_text(raw) + "\n", "libreoffice_pdf_text"


RTF_DESTINATIONS = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object",
    "header", "footer", "headerl", "headerr", "footerl", "footerr",
    "annotation", "fldinst", "themedata", "datastore",
}
RTF_CONTROL_TEXT = {"par": "\n", "line": "\n", "page": "\n", "tab": "\t", "emdash": "—", "endash": "–", "bullet": "•", "lquote": "‘", "rquote": "’", "ldblquote": "“", "rdblquote": "”"}



LEGAL_TOP_HEADING = re.compile(r"^(?:phần|chương)\b", re.I)
LEGAL_SECTION_HEADING = re.compile(r"^(?:mục|tiểu mục)\b", re.I)
LEGAL_ARTICLE_HEADING = re.compile(r"^điều\s+\d+[a-zA-Z]?(?:\s*[.:]|$)", re.I)
LEGAL_TITLE = re.compile(r"^(?:luật|bộ luật|nghị định|thông tư|quyết định|nghị quyết|chỉ thị|pháp lệnh)\b", re.I)


def rtf_legal_markdown(value: str) -> str:
    """Restore safe legal Markdown hierarchy from RTF paragraph text.

    RTF soft line breaks frequently split a Vietnamese word mid-token. Only
    single breaks between letter characters are joined; paragraph boundaries
    remain intact. Heading conversion uses explicit Vietnamese legal markers.
    """
    value = re.sub(r"(?<=[A-Za-zÀ-ỹĐđ])\n[ \t]*(?=[a-zà-ỹđ])", "", value)
    paragraphs = [compact_text(item) for item in re.split(r"\n{2,}", value) if compact_text(item)]
    result = []
    title_budget = 2
    for paragraph in paragraphs:
        plain = paragraph.lstrip()
        if LEGAL_TOP_HEADING.match(plain):
            result.append("# " + plain)
        elif LEGAL_SECTION_HEADING.match(plain):
            result.append("## " + plain)
        elif LEGAL_ARTICLE_HEADING.match(plain):
            result.append("### " + plain)
        elif title_budget and LEGAL_TITLE.match(plain):
            result.append("# " + plain)
            title_budget -= 1
        elif title_budget and result and result[-1].startswith("# ") and len(plain) <= 180 and not plain.startswith(("(", "Căn cứ", "Để ", "Theo ")):
            result.append("# " + plain)
            title_budget -= 1
        else:
            result.append(plain)
    return "\n\n".join(result).strip() + "\n"

def extract_rtf_plain_text(path: Path) -> str:
    """Extract textual RTF content without launching LibreOffice, WPS or Word."""
    raw = path.read_bytes().decode("latin-1", errors="replace")
    token_re = re.compile(r"\\([a-zA-Z]{1,32})(-?\d{1,10})?[ ]?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|([{}])|([^\\{}]+)")
    output, stack = [], [False]
    unicode_skip = 1
    pending_skip = 0
    for match in token_re.finditer(raw):
        word, number, hex_byte, symbol, brace, text = match.groups()
        if brace == "{":
            stack.append(stack[-1])
            continue
        if brace == "}":
            if len(stack) > 1:
                stack.pop()
            continue
        if symbol:
            if symbol == "*":
                stack[-1] = True
            elif not stack[-1] and pending_skip <= 0:
                output.append({"~": " ", "_": "‑", "-": ""}.get(symbol, symbol))
            continue
        if hex_byte:
            if pending_skip:
                pending_skip -= 1
            elif not stack[-1]:
                output.append(bytes([int(hex_byte, 16)]).decode("cp1252", errors="replace"))
            continue
        if word:
            lower = word.lower()
            if lower in RTF_DESTINATIONS:
                stack[-1] = True
            elif lower == "uc" and number is not None:
                unicode_skip = max(0, int(number))
            elif lower == "u" and number is not None:
                value = int(number)
                if value < 0:
                    value += 65536
                if not stack[-1]:
                    output.append(chr(value))
                pending_skip = unicode_skip
            elif not stack[-1] and lower in RTF_CONTROL_TEXT:
                output.append(RTF_CONTROL_TEXT[lower])
            continue
        if text:
            if pending_skip:
                consumed = min(pending_skip, len(text))
                text = text[consumed:]
                pending_skip -= consumed
            if not stack[-1]:
                output.append(text)
    value = compact_text("".join(output))
    # Font tables and metadata should never survive the group parser. Treat a
    # result without substantive content as a parse failure, so fallback remains available.
    if len(value) < 8:
        raise RuntimeError("RTF direct parser produced no usable text")
    return value + "\n"



def _excel_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return compact_text(str(value))


def extract_excel_workbook(path: Path) -> tuple[str, str]:
    """Read XLS/XLSX content even when a crawler saved it with a DOC/DOCX name."""
    sheets = []
    try:
        import xlrd
        workbook = xlrd.open_workbook(path, formatting_info=False)
        for sheet in workbook.sheets():
            if not sheet.nrows or not sheet.ncols:
                continue
            rows = [[_excel_value(sheet.cell_value(row, col)) for col in range(sheet.ncols)]
                    for row in range(sheet.nrows)]
            sheets.append((sheet.name, rows))
        extractor = "mislabelled_xls_xlrd"
    except Exception as xls_error:
        try:
            from openpyxl import load_workbook
            workbook = load_workbook(path, read_only=True, data_only=True)
            for sheet in workbook.worksheets:
                rows = [[_excel_value(value) for value in row] for row in sheet.iter_rows(values_only=True)]
                if rows:
                    sheets.append((sheet.title, rows))
            extractor = "mislabelled_xlsx_openpyxl"
        except Exception as xlsx_error:
            raise RuntimeError(f"not an Excel workbook: xls={xls_error}; xlsx={xlsx_error}")
    if not sheets:
        raise RuntimeError("Excel workbook has no nonempty sheets")
    parts = []
    for name, rows in sheets:
        nonempty = [row for row in rows if any(cell for cell in row)]
        if not nonempty:
            continue
        width = max(len(row) for row in nonempty)
        rows = [row + [""] * (width - len(row)) for row in nonempty]
        # Preserve every visible cell. The first nonempty row becomes the
        # Markdown header; source table geometry remains unavailable for XLS.
        parts.append("# " + name)
        parts.append("| " + " | ".join(cell.replace("|", "\\|") for cell in rows[0]) + " |")
        parts.append("| " + " | ".join(["---"] * width) + " |")
        parts.extend("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in rows[1:])
    value = "\n\n".join(parts).strip()
    if len(value) < 8:
        raise RuntimeError("Excel workbook produced no usable text")
    return value + "\n", extractor

def extract_wrapped_office_archive(path: Path) -> tuple[str, str]:
    """Extract a mislabelled ZIP that contains one Office document payload."""
    with zipfile.ZipFile(path) as archive:
        candidates = [
            item for item in archive.infolist()
            if not item.is_dir() and Path(item.filename).suffix.lower() in {".doc", ".docx", ".rtf", ".xls", ".xlsx"}
        ]
        if not candidates:
            raise RuntimeError("no embedded Office file found")
        with tempfile.TemporaryDirectory(prefix="vn-wrapped-office-") as temp:
            parts, extractors, failures = [], [], []
            for item in candidates:
                embedded = Path(temp) / Path(item.filename).name
                embedded.write_bytes(archive.read(item))
                try:
                    result = extract_office_text(embedded)
                    # The wrapper's entries are normally a main decision plus
                    # annexes. Retain each filename as a Markdown boundary.
                    parts.append("## " + Path(item.filename).stem + "\n\n" + result["markdown"].strip())
                    extractors.append(result["extractor"])
                except Exception as exc:
                    failures.append(Path(item.filename).name + ": " + str(exc))
            if not parts:
                raise RuntimeError("all embedded Office files failed: " + " | ".join(failures))
    return "\n\n".join(parts).strip() + "\n", "wrapped_" + "+".join(dict.fromkeys(extractors))

def extract_docx(path: Path) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(path)
    parts = []
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            paragraph = Paragraph(child, document)
            text = compact_text(paragraph.text)
            if not text:
                continue
            style = (paragraph.style.name or "").lower()
            parts.append(("# " if style.startswith("heading") else "") + text)
        elif child.tag.endswith("}tbl"):
            table = Table(child, document)
            rows = []
            for row in table.rows:
                cells = [compact_text(cell.text).replace("|", "\\|") for cell in row.cells]
                # Word represents horizontal merges as repeated cell objects.
                cells = [cell for index, cell in enumerate(cells) if index == 0 or cell != cells[index - 1]]
                rows.append(cells)
            if rows:
                width = max(len(row) for row in rows)
                rows = [row + [""] * (width - len(row)) for row in rows]
                parts.append("| " + " | ".join(rows[0]) + " |")
                parts.append("| " + " | ".join(["---"] * width) + " |")
                parts.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n\n".join(parts).strip() + "\n"


def extract_com(path: Path) -> tuple[str, str]:
    import pythoncom
    import win32com.client

    # ThreadPoolExecutor workers are not COM apartments by default. Initialize
    # each worker before creating isolated Word/WPS automation instances.
    pythoncom.CoInitialize()
    failures = []
    try:
        for progid, label in (("Word.Application", "word_com"), ("Kwps.Application", "wps_com")):
            app = document = None
            try:
                app = win32com.client.DispatchEx(progid)
                app.Visible = False
                try:
                    app.DisplayAlerts = 0
                except Exception:
                    pass
                document = app.Documents.Open(str(path), ReadOnly=True, AddToRecentFiles=False, Visible=False)
                text = document.Content.Text.replace("\r\x07", "\n").replace("\r", "\n")
                return compact_text(text) + "\n", label
            except Exception as exc:
                failures.append(f"{label}: {exc}")
            finally:
                try:
                    if document is not None:
                        document.Close(False)
                except Exception:
                    pass
                try:
                    if app is not None:
                        app.Quit()
                except Exception:
                    pass
        raise RuntimeError("Office COM extraction failed: " + " | ".join(failures))
    finally:
        pythoncom.CoUninitialize()



def _com_extract_worker(source: str, target: str) -> None:
    try:
        markdown, extractor = extract_com(Path(source))
        Path(target).write_text(json.dumps({"markdown": markdown, "extractor": extractor}, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        Path(target).write_text(json.dumps({"error": str(exc)}, ensure_ascii=False), encoding="utf-8")


def extract_com_with_timeout(path: Path, timeout: int = 45) -> tuple[str, str]:
    """Run Word/WPS COM in an isolated process so damaged files cannot hang a worker."""
    with tempfile.TemporaryDirectory(prefix="vn-office-com-") as temp:
        target = Path(temp) / "result.json"
        context = multiprocessing.get_context("spawn")
        worker = context.Process(target=_com_extract_worker, args=(str(path), str(target)))
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            worker.terminate()
            worker.join(10)
            raise TimeoutError(f"Office COM fallback timed out after {timeout}s")
        if not target.exists():
            raise RuntimeError(f"Office COM fallback exited without a result (exit code {worker.exitcode})")
        result = json.loads(target.read_text(encoding="utf-8"))
        if result.get("error"):
            raise RuntimeError(result["error"])
        return result["markdown"], result["extractor"]

def extract_office_text(path: Path) -> dict:
    suffix = path.suffix.lower()
    # Crawlers occasionally use DOC/DOCX names for legacy XLS/XLSX files.
    # Probe the workbook container before launching an Office application.
    if suffix in {".doc", ".docx"}:
        try:
            markdown, extractor = extract_excel_workbook(path)
            return {"markdown": markdown, "extractor": extractor, "legacy_encoding": None}
        except Exception:
            pass
    if suffix == ".docx":
        try:
            markdown = extract_docx(path)
            return {"markdown": markdown, "extractor": "python_docx", "legacy_encoding": None}
        except (KeyError, zipfile.BadZipFile, ValueError) as docx_error:
            # Some crawled attachments use a .docx name for a ZIP wrapper
            # containing a legacy DOC/RTF payload, not an OOXML package.
            try:
                markdown, extractor = extract_wrapped_office_archive(path)
                return {"markdown": markdown, "extractor": extractor, "legacy_encoding": None}
            except Exception as wrapped_error:
                raise RuntimeError(f"DOCX package invalid: {docx_error} | embedded-file fallback: {wrapped_error}")
    if suffix in {".xls", ".xlsx"}:
        markdown, extractor = extract_excel_workbook(path)
        return {"markdown": markdown, "extractor": extractor, "legacy_encoding": None}
    if suffix not in {".doc", ".rtf"}:
        raise ValueError("supported Office text types: DOCX, DOC, RTF, XLS, XLSX")
    legacy_encoding = None
    legacy_fonts = []
    if suffix == ".rtf":
        # RTF is a text container. Parse it before trying any desktop office
        # application so malformed files cannot open a GUI dialog or stall a worker.
        try:
            markdown, extractor = extract_rtf_plain_text(path), "rtf_direct_text"
        except Exception as rtf_error:
            try:
                markdown, extractor = extract_libreoffice_text(path), "libreoffice_text"
            except Exception as libreoffice_error:
                try:
                    markdown, extractor = extract_com_with_timeout(path)
                except Exception as com_error:
                    raise RuntimeError(f"RTF direct parser: {rtf_error} | LibreOffice: {libreoffice_error} | COM fallback: {com_error}")
        uses_tcvn3, legacy_fonts = rtf_uses_tcvn3(path)
        if uses_tcvn3:
            markdown = tcvn3_to_unicode(markdown)
            legacy_encoding = "tcvn3"
        # Heading recognition must run after legacy-font conversion, otherwise
        # Vietnamese legal markers such as Luật and Điều cannot be recognized.
        markdown = rtf_legal_markdown(markdown)
    else:
        try:
            markdown, extractor = extract_libreoffice_text(path), "libreoffice_text"
            markdown, is_tcvn3 = normalize_detected_tcvn3(markdown)
            if is_tcvn3:
                legacy_encoding = "tcvn3"
                extractor = "libreoffice_text_tcvn3"
        except Exception as libreoffice_error:
            try:
                markdown, extractor = extract_libreoffice_pdf_text(path)
                legacy_encoding = "tcvn3" if extractor.endswith("_tcvn3") else None
            except Exception as pdf_error:
                try:
                    markdown, extractor = extract_com_with_timeout(path)
                except Exception as com_error:
                    raise RuntimeError(f"LibreOffice text: {libreoffice_error} | LibreOffice PDF text: {pdf_error} | COM fallback: {com_error}")
    return {"markdown": markdown, "extractor": extractor, "legacy_encoding": legacy_encoding,
            "legacy_fonts": legacy_fonts}
