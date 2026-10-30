"""Render only scan pages and run a bounded local OCR process."""

from __future__ import annotations

import concurrent.futures
import hashlib
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pymupdf as _fitz  # type: ignore[import-untyped]

from wlh.core import WlhError
from wlh.processor.extractors.html.html_to_md import text_to_markdown


def clean_soft_hyphens(text: str) -> str:
    return re.sub("\u00ad[ \t]*\n[ \t]*", "", text).replace("\u00ad", "")


fitz: Any = _fitz
_TESSERACT_META: dict[str, tuple[set[str], str]] = {}
_IMAGE_OCR_CACHE: dict[tuple[str, str], str] = {}


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
            batch_len = (
                min(12, len(numbers) - pos)
                if (pos > 0 or is_vert_first)
                else 1
            )
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
            batch_texts: dict[int, str]
            if len(batch) == 1:
                batch_texts = {
                    batch[0]: _ocr_single_page(
                        executable, images[batch[0]], langs, dpi_val, max_each, remaining
                    )
                }
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch)) as pool:
                    futs = {
                        num: pool.submit(
                            _ocr_single_page,
                            executable,
                            images[num],
                            langs,
                            dpi_val,
                            max_each,
                            remaining,
                        )
                        for num in batch
                    }
                    batch_texts = {num: futs[num].result() for num in batch}

            stopped = False
            for offset, number in enumerate(batch):
                text = batch_texts[number]
                size += len(text.encode())
                if size > cfg["max_result_bytes"]:
                    raise WlhError("pdf_output_too_large")
                result[number] = text
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
    return result, {"engine": "tesseract", "version": version, "languages": cfg["languages"]}


def tesseract_image_bytes(
    data: bytes,
    languages: str = "jpn+jpn_vert",
    timeout: float = 15.0,
) -> str:
    """Run Tesseract OCR on an archived raster image (PNG/GIF/JPEG) and return normalized text."""
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
        with tempfile.TemporaryDirectory(
            prefix="wlh-img-ocr-", dir=work_root.resolve()
        ) as folder:
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
