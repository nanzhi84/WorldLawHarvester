"""Render only scan pages and run a bounded local OCR process."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pymupdf as _fitz  # type: ignore[import-untyped]

from wlh.core import WlhError

fitz: Any = _fitz
_TESSERACT_META: dict[str, tuple[set[str], str]] = {}


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
    # Homebrew Leptonica rejects /tmp paths; keep rendered pages with the extraction input.
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
        for idx, number in enumerate(numbers):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WlhError("pdf_ocr_timeout")
            page = document[number - 1]
            image = Path(folder) / f"page-{number}.png"
            page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY).save(image)
            try:
                text: str = command(
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
            if nonblank_scans and number in nonblank_scans and len(text.strip()) < 30:
                for rest in numbers[idx + 1 :]:
                    result[rest] = ""
                break
    return result, {"engine": "tesseract", "version": version, "languages": cfg["languages"]}
