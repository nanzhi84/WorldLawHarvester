"""Render only scan pages and run a bounded local OCR process."""

import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pymupdf as fitz

from wlh.core import WlhError


def command(args, limit, timeout):
    process = subprocess.Popen(
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


def tesseract_pages(path, numbers, cfg, deadline):
    executable = shutil.which(os.environ.get("WLH_TESSERACT", "tesseract"))
    if not executable:
        raise WlhError("ocr_engine_unavailable")
    available = command([executable, "--list-langs"], 65536, 5).splitlines()[1:]
    if not set(cfg["languages"].split("+")).issubset(available):
        raise WlhError("ocr_language_unavailable")
    if len(numbers) > cfg["max_pages"]:
        raise WlhError("pdf_ocr_page_limit_exceeded")
    result, size = {}, 0
    # Homebrew Leptonica rejects /tmp paths; keep rendered pages with the extraction input.
    with (
        fitz.open(path) as document,
        tempfile.TemporaryDirectory(prefix="wlh-ocr-", dir=Path(path).resolve().parent) as folder,
    ):
        for number in numbers:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WlhError("pdf_ocr_timeout")
            page = document[number - 1]
            scale = cfg["dpi"] / 72
            pixels = math.ceil(page.rect.width * scale) * math.ceil(page.rect.height * scale)
            if pixels <= 0 or pixels > cfg["max_page_pixels"]:
                raise WlhError("pdf_ocr_pixel_limit_exceeded")
            image = Path(folder) / f"page-{number}.png"
            page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY).save(image)
            try:
                text = command(
                    [
                        executable,
                        str(image),
                        "stdout",
                        "-l",
                        cfg["languages"],
                        "--dpi",
                        str(cfg["dpi"]),
                        "--psm",
                        "3",
                    ],
                    cfg["max_result_bytes"] - size,
                    remaining,
                )
            finally:
                image.unlink(missing_ok=True)
            size += len(text.encode())
            result[number] = text
    version = command([executable, "--version"], 65536, 5).splitlines()[0]
    return result, {"engine": "tesseract", "version": version, "languages": cfg["languages"]}
