#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production OCR pipeline: analyze input, parse with br-ocr-v1, and quality-check.

Examples:
  python scripts/production_document_parse.py file.pdf --output-dir output
  python scripts/production_document_parse.py input-dir --recursive --output-dir output
"""
import argparse, base64, hashlib, json, os, re, shutil, subprocess, sys, time, uuid
from pathlib import Path

import fitz
import requests

try:
    from scripts.office_text_extract import extract_office_text
except ModuleNotFoundError:  # Direct invocation: python scripts/production_document_parse.py
    from office_text_extract import extract_office_text

DEFAULT_OCR_URL = "http://192.168.160.67:7555/api/v1"
DEFAULT_MODEL = "br-ocr-v1"
OFFICE_SUFFIXES = {".doc", ".docx", ".rtf"}


def utf8_stdout():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def analyze_pdf(path):
    pages = []
    with fitz.open(path) as pdf:
        for number, page in enumerate(pdf, 1):
            native = page.get_text().strip()
            pix = page.get_pixmap(matrix=fitz.Matrix(.5, .5), colorspace=fitz.csGRAY)
            ink = sum(value < 210 for value in pix.samples) / len(pix.samples)
            pages.append({
                "page": number,
                "native_text_chars": len(native),
                "embedded_images": len(page.get_images(full=True)),
                "ink_ratio": round(ink, 5),
                "suspected_scan": len(native) < 30 and ink > .003,
            })
        return {
            "page_count": len(pdf),
            "native_text_chars": sum(p["native_text_chars"] for p in pages),
            "suspected_scan_pages": [p["page"] for p in pages if p["suspected_scan"]],
            "producer": pdf.metadata.get("producer", ""),
            "pages": pages,
        }


def converter_executables(names, roots):
    found = []
    for name in names:
        value = shutil.which(name)
        if value:
            found.append(Path(value))
    for root in roots:
        root = Path(root)
        if root.exists():
            for name in names:
                found.extend(root.rglob(name))
    unique = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return unique


def word_com_convert(source, target):
    # Word's SaveAs PDF format is 17. Escape apostrophes for PowerShell literals.
    def ps(value):
        return "'" + str(value).replace("'", "''") + "'"
    command = (
        "$word = New-Object -ComObject Word.Application; "
        "$word.Visible = $false; "
        "try { $doc = $word.Documents.Open(" + ps(source) + "); "
        "$doc.SaveAs([ref]" + ps(target) + ", [ref]17); $doc.Close() } "
        "finally { $word.Quit() }"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                   check=True, capture_output=True, text=True, errors="replace", timeout=180)


def convert_to_pdf(source, work_dir):
    if source.suffix.lower() == ".pdf":
        return source, {"converter": "native_pdf"}
    if source.suffix.lower() not in OFFICE_SUFFIXES:
        raise ValueError("supported input types: PDF, DOC, DOCX, RTF")
    work_dir.mkdir(parents=True, exist_ok=True)
    target = work_dir / (source.stem + ".pdf")
    attempts = []

    libreoffice = converter_executables(
        ["soffice.exe", "soffice"],
        [r"C:\Program Files\LibreOffice", r"C:\Program Files (x86)\LibreOffice"])
    for executable in libreoffice:
        try:
            subprocess.run([str(executable), "--headless", "--convert-to", "pdf",
                            "--outdir", str(work_dir), str(source)],
                           check=True, capture_output=True, text=True, errors="replace", timeout=180)
            if target.exists():
                return target, {"converter": "libreoffice", "executable": str(executable)}
        except Exception as exc:
            attempts.append("LibreOffice: " + str(exc))

    wps = converter_executables(
        ["wps.exe", "wps"],
        [r"C:\Program Files\Kingsoft", r"C:\Program Files (x86)\Kingsoft",
         r"C:\Program Files\WPS Office", r"C:\Program Files (x86)\WPS Office",
         Path(os.environ.get("LOCALAPPDATA", "")) / "Kingsoft",
         Path(os.environ.get("APPDATA", "")) / "Kingsoft"])
    for executable in wps:
        try:
            subprocess.run([str(executable), "--headless", "--convert-to", "pdf",
                            "--outdir", str(work_dir), str(source)],
                           check=True, capture_output=True, text=True, errors="replace", timeout=180)
            if target.exists():
                return target, {"converter": "wps", "executable": str(executable)}
        except Exception as exc:
            attempts.append("WPS: " + str(exc))

    try:
        word_com_convert(source, target)
        if target.exists():
            return target, {"converter": "microsoft_word_com"}
    except Exception as exc:
        attempts.append("Microsoft Word: " + str(exc))
    raise RuntimeError("Office conversion failed; attempted LibreOffice, WPS, Microsoft Word. "
                       + " | ".join(attempts[-3:]))


def api_data(response, operation):
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 200 or not isinstance(payload.get("data"), dict):
        raise RuntimeError(operation + " failed: " + json.dumps(payload, ensure_ascii=False)[:1000])
    return payload["data"]


def parse_pdf(pdf_path, server_url, model, timeout, poll_interval):
    with pdf_path.open("rb") as stream:
        content = base64.b64encode(stream.read()).decode("ascii")
    task_id = str(uuid.uuid4())
    request = {
        "task_id": task_id, "task_uuid": task_id, "task_type": "parse",
        "file_name": pdf_path.name, "original_file_name": pdf_path.name,
        "file_base64": content, "file_dir": None, "file_suffix": ".pdf",
        "model_name": model, "split_setting": None, "extra": None, "priority": 1,
    }
    base = server_url.rstrip("/")
    with requests.Session() as session:
        task_uuid = api_data(session.post(base + "/file_parse", json=request, timeout=60), "submit").get("task_uuid")
        if not task_uuid:
            raise RuntimeError("OCR did not return task_uuid")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            status = api_data(session.get(base + "/status/" + task_uuid, timeout=60), "status")
            if status.get("task_status") == "SUCCESS":
                return api_data(session.get(base + "/result/" + task_uuid, timeout=60), "result")
            if status.get("task_status") in {"FAILED", "ERROR", "CANCELLED", "CANCELED"}:
                raise RuntimeError(status.get("detail_msg") or "OCR task failed")
    raise TimeoutError("OCR timeout")


def json_value(value, fallback):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return fallback


def _raw_block_page(block, zero_based):
    value = block.get("page_idx")
    if not isinstance(value, int):
        return None
    return value + 1 if zero_based else value


def _raw_block_text(block):
    values = [
        block.get("text"), block.get("content"), block.get("table_body"),
        block.get("table_caption"), block.get("table_footnote"),
    ]
    items = block.get("list_items")
    if isinstance(items, list):
        values.extend(items)
    return " ".join(str(value) for value in values if value)


def _bbox_y0(block):
    value = block.get("bbox") or [0, 0, 1, 1]
    return float(value[1]) if isinstance(value, (list, tuple)) and len(value) >= 2 else 0.0


def quality_check(analysis, result):
    """Route only objectively suspicious OCR pages to visual reparse.

    The returned structure stays within existing quality pages reasons and route.
    """
    blocks = json_value(result.get("page_parse_json"), [])
    markdown = result.get("whole_text_md") or ""
    page_markdown = json_value(result.get("page_content_json"), [])
    if not isinstance(blocks, list):
        blocks = []
    if not isinstance(page_markdown, list):
        page_markdown = []

    raw_indices = [block.get("page_idx") for block in blocks if isinstance(block, dict)
                   and isinstance(block.get("page_idx"), int)]
    zero_based = bool(raw_indices) and min(raw_indices) == 0
    by_page = {}
    for block in (item for item in blocks if isinstance(item, dict)):
        page = _raw_block_page(block, zero_based)
        if page is not None:
            by_page.setdefault(page, []).append(block)

    legacy_garbled_chars = set("§©®µ¶¸¹º»¼½¾¿")
    garbled_count = sum(char in legacy_garbled_chars for char in markdown)
    garbled_ratio = garbled_count / max(len(markdown), 1)
    document_reasons = []
    expected_pages = len(analysis.get("pages", []))
    # OCR services normally use either 0..n-1 or 1..n. Other ranges prove
    # that the blocks cannot be safely joined to original PDF pages.
    if raw_indices and expected_pages and (
        min(raw_indices) not in {0, 1} or max(raw_indices) > expected_pages
    ):
        document_reasons.append("page_number_mapping_unreliable")
    if garbled_count >= 8 and garbled_ratio >= .002:
        document_reasons.append("legacy_vietnamese_font_encoding_suspect")

    page_quality = []
    for page in analysis["pages"]:
        number = page["page"]
        local = by_page.get(number, [])
        text_blocks = [block for block in local if block.get("type") in {"text", "header", "list", "table"}]
        local_text = "\n".join(_raw_block_text(block) for block in text_blocks)
        ocr_chars = len(local_text.strip())
        cjk_chars = len(re.findall(r"[㐀-鿿豈-﫿]", local_text))
        cjk_ratio = cjk_chars / max(ocr_chars, 1)
        local_garbled = sum(char in legacy_garbled_chars for char in local_text)
        foreign_diacritics = sum(char in "āēīōūǖǘǚǜǎěǐǒǔǹńň" for char in local_text)
        reasons = []

        ink_ratio = page.get("ink_ratio", 0)
        native_chars = page.get("native_text_chars", 0)
        low_ink_blank_candidate = ink_ratio < .012 and native_chars < 20 and ocr_chars < 20
        if page["suspected_scan"] and ocr_chars < 30 and not low_ink_blank_candidate:
            reasons.append("scan_page_with_little_ocr_text")
        # Low-ink scanned backsides and separator pages are warnings, not content loss.
        if low_ink_blank_candidate:
            reasons.append("low_ink_blank_page_candidate")
        elif ink_ratio >= .006 and ocr_chars < 20:
            reasons.append("nonblank_page_without_ocr_text")
        # A born-digital page can have a usable PDF text layer even when its
        # raster/OCR result is empty. This catches missed attachment/form
        # pages without treating table reading-order differences as failures.
        if native_chars >= 180 and ocr_chars < max(30, int(native_chars * .15)):
            reasons.append("native_text_page_with_little_ocr_text")
        if page.get("ink_ratio", 0) < .0015 and ocr_chars > 50:
            reasons.append("blank_page_with_spurious_text")
        replacement_count = local_text.count("�")
        if replacement_count >= 3:
            reasons.append("replacement_character")
        elif replacement_count:
            reasons.append("isolated_replacement_character")
        if local_garbled >= 4:
            reasons.append("legacy_vietnamese_font_garbled")
        if cjk_chars >= 12 and cjk_ratio >= .02:
            reasons.append("non_vietnamese_garbage")
        if foreign_diacritics >= 3:
            reasons.append("foreign_diacritic_garbage")

        table_blocks = [block for block in local if str(block.get("type", "")).lower() == "table"]
        if table_blocks and sum(len(_raw_block_text(block).strip()) for block in table_blocks) < 20:
            reasons.append("table_content_missing")
        # Preserve a table only when the parser supplied table markup/rows.
        # Long undelimited table text cannot safely support cell-level indexing.
        flattened_tables = [block for block in table_blocks if len(_raw_block_text(block).strip()) >= 120
                            and not (block.get("table_body") or "<table" in _raw_block_text(block).lower())]
        if flattened_tables:
            reasons.append("table_structure_missing")

        y_positions = [_bbox_y0(block) for block in text_blocks if _raw_block_text(block).strip()]
        reversals = sum(current + .08 < previous for previous, current in zip(y_positions, y_positions[1:]))
        if len(y_positions) >= 8 and reversals >= 3:
            reasons.append("reading_order_unstable")

        folded = local_text.upper()
        has_recipient_block = "NƠI NHẬN" in folded or "NOI NHAN" in folded
        signature_markers = ("TM.", "T.M.", "KT.", "KÝ TÊN", "THỦ TƯỚNG", "BỘ TRƯỞNG", "CHỦ TỊCH")
        if has_recipient_block and not any(marker in folded for marker in signature_markers):
            reasons.append("signature_block_missing")

        if number == 1 and "page_number_mapping_unreliable" in document_reasons and ocr_chars < 20:
            reasons.append("page_1_blocks_missing_with_unreliable_mapping")
        warning_reasons = {"low_ink_blank_page_candidate", "isolated_replacement_character", "signature_block_missing", "reading_order_unstable"}
        unique_reasons = list(dict.fromkeys(reasons))
        hard_reasons = [reason for reason in unique_reasons if reason not in warning_reasons]
        page_quality.append({
            "page": number,
            "ocr_text_chars": ocr_chars,
            "ocr_text_blocks": len(text_blocks),
            "reasons": unique_reasons,
            "route": "vision_reparse" if hard_reasons else "accept_with_warning" if unique_reasons else "accept",
        })

    hard = [item for item in page_quality if item["route"] == "vision_reparse"]
    warnings = [item for item in page_quality if item["route"] == "accept_with_warning"]
    return {
        "recommended_route": (
            "legacy_font_encoding_recovery" if document_reasons
            else "vision_reparse_pages" if hard
            else "accept_with_warning" if warnings
            else "accept_ocr"
        ),
        "pages": page_quality,
        "document_reasons": document_reasons,
        "legacy_garbled_char_count": garbled_count,
        "legacy_garbled_char_ratio": round(garbled_ratio, 6),
        "reason_count": sum(len(item["reasons"]) for item in page_quality) + len(document_reasons),
    }

def process(source, output_dir, args):
    direct_text = None
    direct_error = None
    if source.suffix.lower() in OFFICE_SUFFIXES and args.office_text_policy == "prefer":
        try:
            direct_text = extract_office_text(source)
        except Exception as exc:
            direct_error = str(exc)

    converted, conversion = convert_to_pdf(source, output_dir / "_converted")
    analysis = analyze_pdf(converted)
    started = time.perf_counter()
    result = parse_pdf(converted, args.server_url, args.model, args.timeout, args.poll_interval)
    elapsed = round(time.perf_counter() - started, 3)
    ocr_markdown = result.get("whole_text_md")
    if not isinstance(ocr_markdown, str):
        raise RuntimeError("OCR result has no whole_text_md")

    stem = source.stem
    ocr_quality = quality_check(analysis, result)
    (output_dir / (stem + ".ocr.md")).write_text(ocr_markdown, encoding="utf-8")
    canonical_markdown = ocr_markdown
    canonical = {"source": "br_ocr_v1", "markdown_file": stem + ".ocr.md"}
    if direct_text:
        direct_markdown = direct_text.pop("markdown")
        (output_dir / (stem + ".source.md")).write_text(direct_markdown, encoding="utf-8")
        canonical_markdown = direct_markdown
        canonical = {"source": "direct_office_text", "markdown_file": stem + ".source.md", **direct_text}
    (output_dir / (stem + ".md")).write_text(canonical_markdown, encoding="utf-8")

    artifact = {
        "source": {"path": str(source), "sha256": sha256(source),
                   "converted_pdf": str(converted), "conversion": conversion},
        "analysis": analysis, "ocr": result, "quality": ocr_quality,
        "canonical": canonical, "office_direct_extraction_error": direct_error,
        "model": args.model, "ocr_seconds": elapsed,
    }
    (output_dir / (stem + ".ocr.json")).write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact


def main():
    utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--server-url", default=DEFAULT_OCR_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--poll-interval", type=float, default=1)
    parser.add_argument("--office-text-policy", choices={"prefer", "off"}, default="prefer",
                        help="Use direct DOCX/DOC/RTF extraction as final Markdown when available.")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    files = [args.input] if args.input.is_file() else list(args.input.glob("**/*" if args.recursive else "*"))
    files = [p for p in files if p.is_file() and p.suffix.lower() in OFFICE_SUFFIXES | {".pdf"}]
    if not files:
        parser.error("no supported files found")
    report = []
    for source in files:
        try:
            artifact = process(source, args.output_dir, args)
            report.append({"file": source.name, "status": "success", "route": artifact["quality"]["recommended_route"]})
        except Exception as exc:
            report.append({"file": source.name, "status": "failed", "error": str(exc)})
        print(json.dumps(report[-1], ensure_ascii=False), flush=True)
    (args.output_dir / "production_run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()


