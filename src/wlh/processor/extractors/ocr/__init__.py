"""OCR extractor exports."""

from __future__ import annotations

from wlh.processor.extractors.ocr.br_ocr import br_ocr
from wlh.processor.extractors.ocr.tesseract import tesseract_image_bytes, tesseract_pages

__all__ = [
    "br_ocr",
    "tesseract_image_bytes",
    "tesseract_pages",
]
