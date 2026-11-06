"""Render only scan pages and run bounded local OCR (Apple Silicon Vision or Tesseract TSV) with raster table grid reconstruction."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf as _fitz  # type: ignore[import-untyped]

from wlh.core import WlhError
from wlh.processor.extractors.html.html_to_md import text_to_markdown


def clean_soft_hyphens(text: str) -> str:
    return re.sub("\u00ad[ \t]*\n[ \t]*", "", text).replace("\u00ad", "")


fitz: Any = _fitz
_TESSERACT_META: dict[str, tuple[set[str], str]] = {}
_IMAGE_OCR_CACHE: dict[tuple[str, str], str] = {}
_VISION_BUILD_LOCK = threading.Lock()
_VISION_STATE: dict[str, Path | None] = {"bin": None}

_TESS_TO_VISION_LANG: dict[str, str] = {
    "eng": "en-US",
    "chi_sim": "zh-Hans",
    "chi_tra": "zh-Hant",
    "vie": "vi-VT",
    "jpn": "ja-JP",
    "jpn_vert": "ja-JP",
    "kor": "ko-KR",
    "fra": "fr-FR",
    "deu": "de-DE",
    "spa": "es-ES",
    "por": "pt-BR",
    "ita": "it-IT",
    "rus": "ru-RU",
    "ukr": "uk-UA",
    "tha": "th-TH",
    "ara": "ar-SA",
    "tur": "tr-TR",
    "ind": "id-ID",
    "msa": "ms-MY",
    "nld": "nl-NL",
    "pol": "pl-PL",
    "ces": "cs-CZ",
    "ron": "ro-RO",
    "swe": "sv-SE",
    "dan": "da-DK",
    "nor": "no-NO",
    "fin": "fi-FI",
    "hin": "hi-IN",
}

_SWIFT_VISION_SOURCE = r"""
import Foundation
import Vision
import AppKit

struct LineBox: Codable {
    let text: String
    let confidence: Float
    let bbox: [Double]
}

struct PageOutput: Codable {
    let path: String
    let lines: [LineBox]
    let elapsed_ms: Double
}

let args = CommandLine.arguments
guard args.count >= 4 else {
    fputs("Usage: vision_ocr_cli <accurate|fast> <langs_comma> <img1> [img2 ...]\n", stderr)
    exit(1)
}
let mode = args[1]
let langs = args[2].split(separator: ",").map(String.init)
let imgPaths = Array(args[3...])

var outputs = Array<PageOutput?>(repeating: nil, count: imgPaths.count)
let lock = NSLock()

DispatchQueue.concurrentPerform(iterations: imgPaths.count) { idx in
    let p = imgPaths[idx]
    let t0 = CFAbsoluteTimeGetCurrent()
    guard let img = NSImage(contentsOfFile: p),
          let cgImg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        lock.lock()
        outputs[idx] = PageOutput(path: p, lines: [], elapsed_ms: 0)
        lock.unlock()
        return
    }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = (mode == "fast") ? .fast : .accurate
    req.usesLanguageCorrection = (mode != "fast")
    if !langs.isEmpty && langs[0] != "auto" {
        req.recognitionLanguages = langs
    }
    req.automaticallyDetectsLanguage = true
    let handler = VNImageRequestHandler(cgImage: cgImg, options: [:])
    try? handler.perform([req])
    var lines: [LineBox] = []
    if let results = req.results {
        for obs in results {
            if let cand = obs.topCandidates(1).first {
                let b = obs.boundingBox
                let x0 = Double(b.origin.x)
                let y0 = Double(1.0 - b.origin.y - b.size.height)
                let x1 = Double(b.origin.x + b.size.width)
                let y1 = Double(1.0 - b.origin.y)
                lines.append(LineBox(text: cand.string, confidence: cand.confidence, bbox: [x0, y0, x1, y1]))
            }
        }
    }
    let dt = (CFAbsoluteTimeGetCurrent() - t0) * 1000.0
    lock.lock()
    outputs[idx] = PageOutput(path: p, lines: lines, elapsed_ms: dt)
    lock.unlock()
}

let finalOut = outputs.compactMap { $0 }
let data = try! JSONEncoder().encode(finalOut)
FileHandle.standardOutput.write(data)
"""


def _format_markdown_table(rows: list[list[str]]) -> str:
    width = max(len(r) for r in rows)
    norm_rows = [r + [""] * (width - len(r)) for r in rows]
    lines = [
        "| " + " | ".join(norm_rows[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(r) + " |" for r in norm_rows[1:])
    return "\n".join(lines)


def detect_raster_table_cells(
    gray_np: np.ndarray,
) -> list[tuple[float, float, float, float, int, int, int, int]] | None:
    """Fast pure-NumPy morphological rule detector for scanned bordered tables (~2-4 ms/page)."""
    h, w = gray_np.shape
    if h < 100 or w < 100:
        return None
    binary = gray_np < 200
    h_kernel = max(25, w // 12)
    cs_h = np.cumsum(binary, axis=1, dtype=np.int32)
    h_runs = (cs_h[:, h_kernel:] - cs_h[:, :-h_kernel]) >= int(h_kernel * 0.82)
    h_mask = np.any(h_runs, axis=1)
    y_indices = np.where(h_mask)[0]
    if len(y_indices) < 2:
        return None

    y_lines: list[int] = []
    cur_group = [int(y_indices[0])]
    for y in y_indices[1:]:
        if int(y) - cur_group[-1] <= max(4, h // 150):
            cur_group.append(int(y))
        else:
            y_lines.append(int(sum(cur_group) / len(cur_group)))
            cur_group = [int(y)]
    y_lines.append(int(sum(cur_group) / len(cur_group)))
    y_lines = [y for i, y in enumerate(y_lines) if i == 0 or y - y_lines[i - 1] >= max(12, h // 80)]
    if len(y_lines) < 3:
        return None

    y_top, y_bot = y_lines[0], y_lines[-1]
    if (y_bot - y_top) < h * 0.06:
        return None

    sub_bin = binary[y_top:y_bot, :]
    sh = sub_bin.shape[0]
    v_kernel = max(20, min(sh // 2, h // 20))
    if sh <= v_kernel:
        return None
    cs_v = np.cumsum(sub_bin, axis=0, dtype=np.int32)
    v_runs = (cs_v[v_kernel:, :] - cs_v[:-v_kernel, :]) >= int(v_kernel * 0.82)
    v_mask = np.any(v_runs, axis=0)
    x_indices = np.where(v_mask)[0]
    if len(x_indices) < 2:
        return None

    x_lines: list[int] = []
    cur_x = [int(x_indices[0])]
    for x in x_indices[1:]:
        if int(x) - cur_x[-1] <= max(4, w // 150):
            cur_x.append(int(x))
        else:
            x_lines.append(int(sum(cur_x) / len(cur_x)))
            cur_x = [int(x)]
    x_lines.append(int(sum(cur_x) / len(cur_x)))
    x_lines = [x for i, x in enumerate(x_lines) if i == 0 or x - x_lines[i - 1] >= max(20, w // 40)]
    if len(x_lines) < 3:
        return None

    nrows = len(y_lines) - 1
    ncols = len(x_lines) - 1
    if nrows < 2 or ncols < 2 or nrows > 60 or ncols > 25:
        return None

    cells: list[tuple[float, float, float, float, int, int, int, int]] = []
    for r in range(nrows):
        for c in range(ncols):
            cells.append(
                (
                    x_lines[c] / w,
                    y_lines[r] / h,
                    x_lines[c + 1] / w,
                    y_lines[r + 1] / h,
                    r,
                    c,
                    nrows,
                    ncols,
                )
            )
    return cells


def assemble_page_blocks_and_markdown(
    page_no: int,
    line_boxes: Sequence[Mapping[str, Any]],
    gray_np: np.ndarray,
) -> tuple[str, list[dict[str, Any]]]:
    """Combine OCR line bounding boxes + raster table grid into Markdown + page_parse_json blocks."""
    cells = detect_raster_table_cells(gray_np)
    sorted_lines = sorted(
        line_boxes,
        key=lambda item: (round(float(item["bbox"][1]), 2), float(item["bbox"][0])),
    )

    if not cells:
        lines_txt = [
            str(item["text"]).strip() for item in sorted_lines if str(item["text"]).strip()
        ]
        md = "\n".join(lines_txt)
        blocks = [{"type": "text", "page_idx": page_no, "text": md}] if md else []
        return md, blocks

    tx0 = min(c[0] for c in cells)
    ty0 = min(c[1] for c in cells)
    tx1 = max(c[2] for c in cells)
    ty1 = max(c[3] for c in cells)
    nrows, ncols = cells[0][6], cells[0][7]
    grid: list[list[list[str]]] = [[[] for _ in range(ncols)] for _ in range(nrows)]
    before_lines: list[str] = []
    after_lines: list[str] = []

    for item in sorted_lines:
        txt = str(item["text"]).strip()
        if not txt:
            continue
        bx0, by0, bx1, by1 = (
            float(item["bbox"][0]),
            float(item["bbox"][1]),
            float(item["bbox"][2]),
            float(item["bbox"][3]),
        )
        cx = (bx0 + bx1) * 0.5
        cy = (by0 + by1) * 0.5
        if (tx0 - 0.02) <= cx <= (tx1 + 0.02) and (ty0 - 0.01) <= cy <= (ty1 + 0.01):
            placed = False
            for cx0, cy0, cx1, cy1, r, c, _, _ in cells:
                if (cx0 - 0.01) <= cx <= (cx1 + 0.01) and (cy0 - 0.01) <= cy <= (cy1 + 0.01):
                    grid[r][c].append(txt.replace("|", "\\|"))
                    placed = True
                    break
            if not placed:
                before_lines.append(txt)
        elif cy < ty0:
            before_lines.append(txt)
        else:
            after_lines.append(txt)

    table_rows = [[" ".join(grid[r][c]).strip() for c in range(ncols)] for r in range(nrows)]
    nonempty_rows = [r for r in table_rows if any(cell for cell in r)]
    blocks_out: list[dict[str, Any]] = []
    md_parts: list[str] = []

    if before_lines:
        b_txt = "\n".join(before_lines)
        md_parts.append(b_txt)
        blocks_out.append({"type": "text", "page_idx": page_no, "text": b_txt})

    if len(nonempty_rows) >= 2:
        md_table = _format_markdown_table(nonempty_rows)
        md_parts.append(md_table)
        html_rows = "".join(
            "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in nonempty_rows
        )
        blocks_out.append(
            {
                "type": "table",
                "page_idx": page_no,
                "table_body": f"<table>{html_rows}</table>",
            }
        )
    else:
        flat = "\n".join(" ".join(c for c in r if c) for r in nonempty_rows)
        if flat.strip():
            md_parts.append(flat)
            blocks_out.append({"type": "text", "page_idx": page_no, "text": flat})

    if after_lines:
        a_txt = "\n".join(after_lines)
        md_parts.append(a_txt)
        blocks_out.append({"type": "text", "page_idx": page_no, "text": a_txt})

    return "\n\n".join(md_parts), blocks_out


def _is_vertical_cjk_fragmented(text: str) -> bool:
    rendered = text_to_markdown(clean_soft_hyphens(text))
    lines = [line.strip() for line in rendered.splitlines() if line.strip()]
    fragments = sum(bool(re.fullmatch(r"[\u3040-\u30ff\u3400-\u9fff]", line)) for line in lines)
    return fragments >= 12 and fragments / max(len(lines), 1) >= 0.25


def _tesseract_meta(executable: str) -> tuple[set[str], str]:
    cached = _TESSERACT_META.get(executable)
    if cached is not None:
        return cached
    available = set(command([executable, "--list-langs"], 65536, 5).splitlines()[1:])
    version = command([executable, "--version"], 65536, 5).splitlines()[0]
    _TESSERACT_META[executable] = (available, version)
    return available, version


def command(args: Sequence[str], limit: int, timeout: float) -> str:
    process: subprocess.Popen[bytes] = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise WlhError("pdf_ocr_timeout") from None
    if len(output) > limit:
        raise WlhError("pdf_output_too_large")
    if process.returncode:
        raise WlhError("pdf_ocr_failed")
    return output.decode("utf-8", errors="strict")


def _ocr_single_page(
    executable: str,
    img: Path,
    languages: str,
    dpi: int,
    limit: int,
    timeout: float,
) -> str:
    psm = "5" if languages.startswith("jpn_vert") else "3"
    try:
        raw = command(
            [
                executable,
                str(img),
                "stdout",
                "-l",
                languages,
                "--dpi",
                str(dpi),
                "--psm",
                psm,
            ],
            limit,
            timeout,
        )
        if psm == "5":
            raw = re.sub(
                r"(?<=[\u3000-\u9fff\uff00-\uffef])[ \t]+(?=[\u3000-\u9fff\uff00-\uffef])",
                "",
                raw,
            )
        return raw
    finally:
        img.unlink(missing_ok=True)


def _ocr_single_page_tsv(
    executable: str,
    img: Path,
    page_no: int,
    languages: str,
    dpi: int,
    limit: int,
    timeout: float,
) -> tuple[str, list[dict[str, Any]]]:
    psm = "5" if languages.startswith("jpn_vert") else "3"
    if psm == "5":
        text = _ocr_single_page(executable, img, languages, dpi, limit, timeout)
        blocks = [{"type": "text", "page_idx": page_no, "text": text}] if text.strip() else []
        return text, blocks
    try:
        pix = fitz.Pixmap(str(img))
        w = max(1, int(pix.width))
        h = max(1, int(pix.height))
        gray_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape(h, w).copy()
        tsv_raw = command(
            [
                executable,
                str(img),
                "stdout",
                "-l",
                languages,
                "--dpi",
                str(dpi),
                "--psm",
                psm,
                "tsv",
            ],
            max(limit * 4, 262144),
            timeout,
        )
        lines_map: dict[tuple[int, int, int], dict[str, Any]] = {}
        for row in tsv_raw.splitlines()[1:]:
            parts = row.split("\t")
            if len(parts) < 12 or parts[0] != "5":
                continue
            word = parts[11].strip()
            if not word:
                continue
            conf = float(parts[10]) if parts[10] != "-1" else 0.0
            left, top, bw, bh = int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9])
            key = (int(parts[2]), int(parts[3]), int(parts[4]))
            x0, y0, x1, y1 = left / w, top / h, (left + bw) / w, (top + bh) / h
            if key not in lines_map:
                lines_map[key] = {"words": [word], "confs": [conf], "bbox": [x0, y0, x1, y1]}
            else:
                entry = lines_map[key]
                entry["words"].append(word)
                entry["confs"].append(conf)
                b = entry["bbox"]
                entry["bbox"] = [min(b[0], x0), min(b[1], y0), max(b[2], x1), max(b[3], y1)]

        line_boxes: list[dict[str, Any]] = []
        for entry in lines_map.values():
            joined = " ".join(entry["words"])
            joined = re.sub(
                r"(?<=[\u3000-\u9fff\uff00-\uffef])[ \t]+(?=[\u3000-\u9fff\uff00-\uffef])",
                "",
                joined,
            )
            line_boxes.append(
                {
                    "text": joined,
                    "confidence": sum(entry["confs"]) / max(1, len(entry["confs"])),
                    "bbox": entry["bbox"],
                }
            )
        page_md, page_blocks = assemble_page_blocks_and_markdown(page_no, line_boxes, gray_np)
        if len(page_md.encode("utf-8")) > limit:
            raise WlhError("pdf_output_too_large")
        return page_md, page_blocks
    finally:
        img.unlink(missing_ok=True)


def _ensure_vision_binary() -> Path | None:
    if sys.platform != "darwin":
        return None
    cached_bin = _VISION_STATE.get("bin")
    if cached_bin is not None and cached_bin.is_file():
        return cached_bin
    with _VISION_BUILD_LOCK:
        cached_bin = _VISION_STATE.get("bin")
        if cached_bin is not None and cached_bin.is_file():
            return cached_bin
        repo_work = Path(__file__).resolve().parents[5] / "work"
        prebuilt = repo_work / "binary-extraction-pareto" / "vision_ocr_cli"
        if prebuilt.is_file() and os.access(prebuilt, os.X_OK):
            _VISION_STATE["bin"] = prebuilt
            return prebuilt
        target_dir = repo_work / "vision_ocr"
        target_dir.mkdir(parents=True, exist_ok=True)
        bin_path = target_dir / "vision_ocr_cli"
        if bin_path.is_file() and os.access(bin_path, os.X_OK):
            _VISION_STATE["bin"] = bin_path
            return bin_path
        swiftc = shutil.which("swiftc")
        if not swiftc:
            return None
        try:
            src_path = target_dir / "vision_ocr_cli.swift"
            tmp_bin = target_dir / f"vision_ocr_cli.{os.getpid()}"
            src_path.write_text(_SWIFT_VISION_SOURCE, encoding="utf-8")
            subprocess.run(
                [swiftc, "-O", str(src_path), "-o", str(tmp_bin)],
                check=True,
                capture_output=True,
                timeout=30,
            )
            tmp_bin.replace(bin_path)
            _VISION_STATE["bin"] = bin_path
            return bin_path
        except Exception:
            return None


def _map_vision_languages(languages: str) -> str:
    vision_langs: list[str] = []
    for part in languages.split("+"):
        mapped = _TESS_TO_VISION_LANG.get(part.strip())
        if mapped and mapped not in vision_langs:
            vision_langs.append(mapped)
    if not vision_langs:
        vision_langs = ["en-US"]
    return ",".join(vision_langs)


def vision_pages(
    path: str | Path,
    numbers: Sequence[int],
    cfg: Mapping[str, Any],
    deadline: float,
    *,
    nonblank_scans: set[int] | None = None,
) -> tuple[dict[int, str], dict[str, Any]]:
    """Run Apple Silicon native Vision (.accurate) + NumPy raster grid table layout."""
    vision_bin = _ensure_vision_binary()
    if vision_bin is None:
        raise WlhError("ocr_engine_unavailable")
    if len(numbers) > cfg["max_pages"]:
        raise WlhError("pdf_ocr_page_limit_exceeded")
    langs_csv = _map_vision_languages(str(cfg["languages"]))
    result: dict[int, str] = {}
    all_blocks: list[dict[str, Any]] = []
    size = 0
    with (
        fitz.open(path) as document,
        tempfile.TemporaryDirectory(
            prefix="wlh-vision-", dir=Path(path).resolve().parent
        ) as folder,
    ):
        scale = cfg["dpi"] / 72
        for number in numbers:
            page = document[number - 1]
            pixels = math.ceil(page.rect.width * scale) * math.ceil(page.rect.height * scale)
            if pixels <= 0 or pixels > cfg["max_page_pixels"]:
                raise WlhError("pdf_ocr_pixel_limit_exceeded")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WlhError("pdf_ocr_timeout")
        img_paths: list[str] = []
        gray_maps: dict[str, tuple[int, np.ndarray]] = {}
        for number in numbers:
            page = document[number - 1]
            img_p = Path(folder) / f"page-{number}.png"
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY)
            pix.save(str(img_p))
            img_paths.append(str(img_p))
            gray_maps[str(img_p)] = (
                number,
                np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy(),
            )
        try:
            proc = subprocess.run(
                [str(vision_bin), "accurate", langs_csv, *img_paths],
                capture_output=True,
                timeout=max(1.0, remaining),
                check=True,
            )
            vision_out = json.loads(proc.stdout.decode("utf-8"))
        except subprocess.TimeoutExpired:
            raise WlhError("pdf_ocr_timeout") from None
        except Exception:
            raise WlhError("pdf_ocr_failed") from None

        for item in vision_out:
            pno, gray_np = gray_maps[item["path"]]
            p_md, p_blocks = assemble_page_blocks_and_markdown(pno, item["lines"], gray_np)
            size += len(p_md.encode("utf-8"))
            if size > cfg["max_result_bytes"]:
                raise WlhError("pdf_output_too_large")
            result[pno] = p_md
            all_blocks.extend(p_blocks)
            if (nonblank_scans and pno in nonblank_scans and len(p_md.strip()) < 30) or (
                _is_vertical_cjk_fragmented(p_md)
            ):
                for rest in numbers:
                    if rest not in result:
                        result[rest] = ""
                break

    return (
        result,
        {
            "engine": "vision",
            "model": "VNRecognizeTextRequest.accurate",
            "languages": cfg["languages"],
            "blocks": all_blocks,
        },
    )


def tesseract_pages(
    path: str | Path,
    numbers: Sequence[int],
    cfg: Mapping[str, Any],
    deadline: float,
    *,
    nonblank_scans: set[int] | None = None,
) -> tuple[dict[int, str], dict[str, Any]]:
    executable = shutil.which(os.environ.get("WLH_TESSERACT", "tesseract"))
    if not executable:
        raise WlhError("ocr_engine_unavailable")
    available, version = _tesseract_meta(executable)
    if not set(cfg["languages"].split("+")).issubset(available):
        raise WlhError("ocr_language_unavailable")
    if len(numbers) > cfg["max_pages"]:
        raise WlhError("pdf_ocr_page_limit_exceeded")
    result: dict[int, str] = {}
    all_blocks: list[dict[str, Any]] = []
    size = 0
    with (
        fitz.open(path) as document,
        tempfile.TemporaryDirectory(prefix="wlh-ocr-", dir=Path(path).resolve().parent) as folder,
    ):
        scale = cfg["dpi"] / 72
        for number in numbers:
            page = document[number - 1]
            pixels = math.ceil(page.rect.width * scale) * math.ceil(page.rect.height * scale)
            if pixels <= 0 or pixels > cfg["max_page_pixels"]:
                raise WlhError("pdf_ocr_pixel_limit_exceeded")
        pos = 0
        is_vert_first = str(cfg["languages"]).startswith("jpn_vert")
        while pos < len(numbers):
            batch_len = min(12, len(numbers) - pos) if (pos > 0 or is_vert_first) else 1
            batch = numbers[pos : pos + batch_len]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WlhError("pdf_ocr_timeout")
            images: dict[int, Path] = {}
            for number in batch:
                page = document[number - 1]
                image = Path(folder) / f"page-{number}.png"
                page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY).save(
                    image
                )
                images[number] = image
            max_each = max(1, int(cfg["max_result_bytes"]) - size)
            lang_parts = str(cfg["languages"]).split("+")
            if lang_parts and lang_parts[0] == "jpn_vert":
                langs = "jpn_vert"
            elif "jpn" in lang_parts and len(lang_parts) > 1:
                langs = "+".join(part for part in lang_parts if part != "eng")
            else:
                langs = str(cfg["languages"])
            dpi_val = int(cfg["dpi"])
            batch_outputs: dict[int, tuple[str, list[dict[str, Any]]]]
            if len(batch) == 1:
                batch_outputs = {
                    batch[0]: _ocr_single_page_tsv(
                        executable, images[batch[0]], batch[0], langs, dpi_val, max_each, remaining
                    )
                }
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch)) as pool:
                    futs = {
                        num: pool.submit(
                            _ocr_single_page_tsv,
                            executable,
                            images[num],
                            num,
                            langs,
                            dpi_val,
                            max_each,
                            remaining,
                        )
                        for num in batch
                    }
                    batch_outputs = {num: futs[num].result() for num in batch}

            stopped = False
            for offset, number in enumerate(batch):
                text, page_blocks = batch_outputs[number]
                size += len(text.encode())
                if size > cfg["max_result_bytes"]:
                    raise WlhError("pdf_output_too_large")
                result[number] = text
                all_blocks.extend(page_blocks)
                if (
                    nonblank_scans and number in nonblank_scans and len(text.strip()) < 30
                ) or _is_vertical_cjk_fragmented(text):
                    for rest in numbers[pos + offset + 1 :]:
                        result[rest] = ""
                    stopped = True
                    break
            if stopped:
                break
            pos += batch_len
    return (
        result,
        {
            "engine": "tesseract",
            "version": version,
            "languages": cfg["languages"],
            "blocks": all_blocks,
        },
    )


def tesseract_image_bytes(
    data: bytes,
    languages: str = "jpn+jpn_vert",
    timeout: float = 15.0,
) -> str:
    """Run local OCR on an archived raster image (PNG/GIF/JPEG) and return normalized text."""
    if not data:
        return ""
    digest_key = (hashlib.sha256(data).hexdigest(), languages)
    cached = _IMAGE_OCR_CACHE.get(digest_key)
    if cached is not None:
        return cached
    executable = shutil.which(os.environ.get("WLH_TESSERACT", "tesseract"))
    if not executable:
        return ""
    available, _ = _tesseract_meta(executable)
    lang_parts = [p for p in languages.split("+") if p in available]
    if lang_parts and lang_parts[0] == "jpn_vert":
        langs = "jpn_vert"
    elif "jpn" in lang_parts and len(lang_parts) > 1:
        lang_parts = [p for p in lang_parts if p != "eng"]
        langs = "+".join(lang_parts) if lang_parts else "eng"
    else:
        langs = "+".join(lang_parts) if lang_parts else "eng"
    work_root = Path("work")
    work_root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="wlh-img-ocr-", dir=work_root.resolve()) as folder:
            png_path = Path(folder) / "image.png"
            pix = fitz.Pixmap(data)
            if pix.alpha or pix.n > 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            pix.save(png_path)
            raw_text = _ocr_single_page(executable, png_path, langs, 200, 65536, timeout)
    except Exception:
        _IMAGE_OCR_CACHE[digest_key] = ""
        return ""
    cleaned = re.sub(r"\s+", " ", clean_soft_hyphens(raw_text)).strip()
    _IMAGE_OCR_CACHE[digest_key] = cleaned
    return cleaned
