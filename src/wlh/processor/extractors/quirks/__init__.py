"""Extractor quirks registry (`ja_kanpo_pua`, `ja_inline_glyphs`, `vi_tcvn3`, `zip_salvage`)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from wlh.processor.extractors.base import QuirkHook
from wlh.processor.extractors.quirks.ja_kanpo_pua import (
    _PUA_CHAR_MAP,
    _PUA_RESIDUAL_RE,
    decode_pua_text,
    inline_glyph_text,
    is_kanpo_pua_page,
)
from wlh.processor.extractors.quirks.vi_tcvn3 import (
    TCVN3_CONTRACT,
    legal_markdown,
    looks_like_tcvn3,
    normalize_detected_tcvn3,
    rtf_bytes_to_tcvn3_raw,
    rtf_uses_tcvn3,
    tcvn3_to_unicode,
)
from wlh.processor.extractors.quirks.zip_salvage import (
    filter_redundant_zip_members,
    salvage_zip_prefix,
)

QUIRKS: dict[str, Callable[..., Any]] = {
    "ja_kanpo_pua": decode_pua_text,
    "ja_inline_glyphs": inline_glyph_text,
    "vi_tcvn3": normalize_detected_tcvn3,
    "zip_salvage": salvage_zip_prefix,
}
REGISTRY = QUIRKS

# Declarative Stage Hooks (DSH): where each frozen quirk mounts in the extraction lifecycle.
QUIRK_HOOKS: tuple[QuirkHook, ...] = (
    QuirkHook("zip_salvage", "pre_container", frozenset({"zip"}), salvage_zip_prefix),
    QuirkHook("zip_salvage", "archive_members", frozenset({"zip"}), filter_redundant_zip_members),
    QuirkHook("ja_inline_glyphs", "ast_node", frozenset({"html"}), inline_glyph_text),
    QuirkHook("ja_kanpo_pua", "text_span", frozenset({"pdf", "html"}), decode_pua_text),
    # TCVN3 recovery only acts on glyph/font evidence, so Office output is always screened.
    QuirkHook(
        "vi_tcvn3",
        "post_markdown",
        frozenset({"doc", "docx", "rtf"}),
        normalize_detected_tcvn3,
        always=True,
    ),
    QuirkHook("vi_tcvn3", "post_markdown", frozenset({"pdf"}), normalize_detected_tcvn3),
)

__all__ = [
    "QUIRKS",
    "QUIRK_HOOKS",
    "REGISTRY",
    "TCVN3_CONTRACT",
    "_PUA_CHAR_MAP",
    "_PUA_RESIDUAL_RE",
    "decode_pua_text",
    "filter_redundant_zip_members",
    "inline_glyph_text",
    "is_kanpo_pua_page",
    "legal_markdown",
    "looks_like_tcvn3",
    "normalize_detected_tcvn3",
    "rtf_bytes_to_tcvn3_raw",
    "rtf_uses_tcvn3",
    "salvage_zip_prefix",
    "tcvn3_to_unicode",
]
