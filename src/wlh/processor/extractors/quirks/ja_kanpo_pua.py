"""National Printing Bureau (Kanpo) & tax circular Private Use Area and inline glyph decoders."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

_PUA_CHAR_MAP: dict[str, str] = {
    "\ue88a": "0",
    "\ue88b": "1",
    "\ue88c": "2",
    "\ue88d": "3",
    "\ue88e": "4",
    "\ue88f": "5",
    "\ue890": "6",
    "\ue891": "7",
    "\ue892": "8",
    "\ue893": "9",
    "\ue0a8": "・",
    "\ue61c": "－",
    "\uea75": ",",
    "\ue05a": "⑴",
    "\ue000": "𠮟",
    "\ue003": "𠮷",
    "\ue005": "",
    "\ue00b": "麴",
    "\ue090": "𦥑",
    "\ue7b2": "〔",
    "\ue7b3": "〕",
}
_PUA_RESIDUAL_RE = re.compile(r"[\ue000-\uf8ff]+")
_NO_IMG_RE = re.compile(r"(?:^|/)parts/no_img/(?P<num>\d{1,2})\.gif(?:$|[?#])", re.I)
_INLINE_PART_RE = re.compile(
    r"(?:^|/)(?:img/parts/[^/?#]+/[^/?#]+|parts/no_img/\d{1,2})\.gif(?:$|[?#])", re.I
)
_INLINE_GLYPH_PATHS = {
    "parts/ten.gif": "・",
    "img/kan_ho.gif": "〔法〕",
    "img/kan_rei.gif": "〔令〕",
    "img/kan_ki.gif": "〔規〕",
    "img/kan_so.gif": "〔措〕",
    "img/kan_tsu.gif": "〔通〕",
}


def decode_pua_text(text: str) -> str:
    """Decode National Printing Bureau (Kanpo) Private Use Area and vertical font glyphs."""
    orig = text
    for src, dst in _PUA_CHAR_MAP.items():
        if src in text:
            text = text.replace(src, dst)
    if "\u617a" in text:
        text = text.replace("\u617a", "－")
    if "\u5a23" in text:
        text = text.replace("\u5a23", "")
    if text != orig and _PUA_RESIDUAL_RE.search(text):
        text = _PUA_RESIDUAL_RE.sub("", text)
    return text


def is_kanpo_pua_page(raw_text: str) -> bool:
    """Return True when a PDF page exhibits Kanpo PUA glyph encoding with gazette header markers."""
    return bool(re.search(r"[\ue000-\uf8ff]", raw_text)) and (
        "官" in raw_text or "号外" in raw_text
    )


def inline_glyph_text(node: Any) -> str | None:
    """Recognize inline bullet, circled-number, and cross-reference glyph images."""
    alt = re.sub(r"\s+", " ", str(node.get("alt") or "")).strip()
    raw_src = unquote(str(node.get("src") or "").strip()).replace("\\", "/")
    lower_src = raw_src.lower()
    width, height = str(node.get("width") or ""), str(node.get("height") or "")
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
    is_inline_part = (
        bool(_INLINE_PART_RE.search(raw_src))
        or lower_src.endswith("parts/ten.gif")
        or ("parts/" in lower_src and small_explicit)
    )
    if is_inline_part and small_or_unbounded:
        if alt:
            return alt + " "
        m = _NO_IMG_RE.search(raw_src)
        if m:
            return f"({int(m.group('num'))}) "
        if lower_src.endswith("parts/ten.gif"):
            return "・"
    for suffix, replacement in _INLINE_GLYPH_PATHS.items():
        if lower_src.endswith(suffix) and small_or_unbounded:
            if alt:
                return alt + " "
            return replacement
    return None


__all__ = [
    "_PUA_CHAR_MAP",
    "_PUA_RESIDUAL_RE",
    "decode_pua_text",
    "inline_glyph_text",
    "is_kanpo_pua_page",
]
