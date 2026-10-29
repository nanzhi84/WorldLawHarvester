"""Verify that a selected legal HTML region has no unrepresented image content."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from wlh.core import WlhError, public_url
from wlh.extraction.ocr_tesseract import tesseract_image_bytes

_IMAGE_MAGIC_PREFIXES = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"BM",
    b"RIFF",
)


def inline_glyph_text(img: Any) -> str | None:
    alt = str(img.get("alt") or "").strip()
    src = str(img.get("src") or "").strip()
    src_lower = src.lower()
    width, height = str(img.get("width") or ""), str(img.get("height") or "")
    small_explicit = (
        width.isdigit()
        and height.isdigit()
        and 0 < int(width) <= 32
        and 0 < int(height) <= 32
    )
    small_or_unbounded = (
        (not width or (width.isdigit() and 0 < int(width) <= 64))
        and (not height or (height.isdigit() and 0 < int(height) <= 64))
    )
    if re.fullmatch(r"[0-9０-９]{1,3}", alt) and small_explicit:
        return alt + " "
    if "parts/" in src_lower and small_or_unbounded:
        if alt:
            return alt + " "
        m_num = re.search(r"parts/no_img/0*([0-9]+)\.gif$", src_lower)
        if m_num:
            return f"({m_num.group(1)}) "
        if src_lower.endswith("parts/ten.gif"):
            return "・"
    if src_lower.endswith("img/kan_ho.gif") and alt == "○保" and small_or_unbounded:
        return alt + " "
    return None


def _is_valid_raster_image(data: bytes) -> bool:
    return bool(data) and any(data.startswith(prefix) for prefix in _IMAGE_MAGIC_PREFIXES)


def require_textual_images(
    node: Any,
    *,
    base_url: str = "",
    image_loader: Callable[[str], bytes | None] | None = None,
    ocr_languages: str = "jpn+jpn_vert",
) -> None:
    """Replace explicit numbered bullets or archived OCR-verified images; reject unverified images.

    The caller chooses and cleans the document region first. Site logos outside
    that region are not document content and must not determine this decision.
    """
    for img in node.xpath("descendant-or-self::img"):
        replacement = inline_glyph_text(img)
        if replacement is None and base_url and image_loader is not None:
            src = str(img.get("src") or "").strip()
            if src:
                try:
                    full_url = public_url(urljoin(base_url, src))
                except WlhError:
                    full_url = ""
                img_bytes = image_loader(full_url) if full_url else None
                if img_bytes is not None and _is_valid_raster_image(img_bytes):
                    alt = re.sub(r"\s+", " ", str(img.get("alt") or "")).strip()
                    ocr_text = (
                        tesseract_image_bytes(img_bytes, languages=ocr_languages)
                        if (len(alt) < 12 or "kankei" in src.lower() or len(img_bytes) <= 4096)
                        else ""
                    )
                    if alt and ocr_text and ocr_text not in alt:
                        label = f"{alt} {ocr_text}"
                    elif alt or ocr_text:
                        label = alt or ocr_text
                    else:
                        label = src
                    if len(re.sub(r"\s+", "", label)) < 12:
                        replacement = f"[図表 ({src}): {label}] "
                    else:
                        replacement = f"[図表: {label}] "
        if replacement is not None:
            img.tag = "span"
            img.text = replacement
            img.attrib.clear()
        else:
            raise WlhError("html_images_unverified")
