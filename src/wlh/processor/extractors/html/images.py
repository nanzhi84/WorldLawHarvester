"""Fail-closed handling for HTML body images that may carry normative text."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from wlh.core import WlhError
from wlh.processor.extractors.ocr.tesseract import tesseract_image_bytes
from wlh.processor.extractors.quirks.ja_kanpo_pua import inline_glyph_text

DECORATIVE = re.compile(
    r"(?:^|/|[._-])(logo|icon|banner|avatar|badge|button|btn|arrow|bullet|spacer|pixel|"
    r"blank|clear|1x1|loading|spinner|share|print|qrcode|qr|wechat|weibo|facebook|twitter|"
    r"linkedin|youtube|rss|search|close|menu|nav|header|footer|bg|background|seal|emblem|"
    r"coat|flag|favicon|back|top|next|prev|home|mail|phone|fax|gov|icp|beian|gongan)"
    r"(?:[._/-]|$)",
    re.I,
)


def small_dimension(node: Any) -> bool:
    for attr in ("width", "height"):
        raw = (node.get(attr) or "").strip().lower().removesuffix("px")
        if raw.isdigit() and int(raw) <= 64:
            return True
    style = node.get("style") or ""
    for match in re.finditer(r"\b(?:max-)?(?:width|height)\s*:\s*(\d+)\s*px", style, re.I):
        if int(match[1]) <= 64:
            return True
    return False


def textual_image_urls(
    root: Any, base_url: str = "", *, quirks: Sequence[str] | None = None
) -> list[str]:
    use_glyphs = quirks is None or "ja_inline_glyphs" in quirks or "ja_kanpo_pua" in quirks
    urls: list[str] = []
    for node in root.xpath(".//img"):
        role = (node.get("role") or "").strip().lower()
        hidden = (node.get("aria-hidden") or "").strip().lower()
        if role in {"presentation", "none"} or hidden == "true" or small_dimension(node):
            continue
        if use_glyphs and inline_glyph_text(node) is not None:
            continue
        raw = (node.get("src") or node.get("data-src") or node.get("data-original") or "").strip()
        if not raw or raw.lower().startswith(("javascript:", "about:", "#")):
            continue
        if raw.lower().startswith("data:"):
            if "svg" not in raw[:40].lower() and len(raw) > 2048:
                urls.append("data:")
            continue
        resolved = urljoin(base_url, raw) if base_url else raw
        path = unquote(urlsplit(resolved).path)
        if path.lower().endswith((".svg", ".ico")) or DECORATIVE.search(path):
            continue
        urls.append(resolved)
    return urls


_IMAGE_MAGIC_PREFIXES = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"BM",
    b"RIFF",
)


def _is_valid_raster_image(data: bytes) -> bool:
    return bool(data) and any(data.startswith(prefix) for prefix in _IMAGE_MAGIC_PREFIXES)


def require_textual_images(
    root: Any,
    base_url: str = "",
    *,
    image_loader: Callable[[str], bytes | None] | None = None,
    image_fetcher: Callable[[str], bytes | None] | None = None,
    ocr_languages: str = "jpn+jpn_vert",
    quirks: Sequence[str] | None = None,
) -> None:
    from wlh.core import public_url

    use_glyphs = quirks is None or "ja_inline_glyphs" in quirks or "ja_kanpo_pua" in quirks
    loader = image_loader or image_fetcher
    for img in root.xpath("descendant-or-self::img"):
        replacement = inline_glyph_text(img) if use_glyphs else None
        if replacement is None and base_url and loader is not None:
            src = str(img.get("src") or "").strip()
            if src:
                try:
                    full_url = public_url(urljoin(base_url, src))
                except WlhError:
                    full_url = ""
                img_bytes = loader(full_url) if full_url else None
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
