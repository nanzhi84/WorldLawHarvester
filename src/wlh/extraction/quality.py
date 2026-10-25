"""Objective page/document quality gate (quality_check) and deterministic routing.

Refactored from docs/reference/production_document_parse.py. Vietnamese-specific checks
(legacy font glyphs, CJK/foreign diacritic garbage, signature block) apply only to the
``vi`` profile so that Chinese or English sources are not misjudged.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

QUALITY_CONTRACT = "wlh.quality.v2"
LEGACY_GARBLED = set("§©®µ¶¸¹º»¼½¾¿")
PRIMARY_TCVN3 = set("§©®µ¶¸¹º»¿")
FOREIGN_DIACRITICS = set("āēīōūǖǘǚǜǎěǐǒǔǹńň")
WARNING_REASONS = {
    "low_ink_blank_page_candidate",
    "isolated_replacement_character",
    "signature_block_missing",
    "reading_order_unstable",
}
SIGNATURE_MARKERS = ("TM.", "T.M.", "KT.", "KÝ TÊN", "THỦ TƯỚNG", "BỘ TRƯỞNG", "CHỦ TỊCH")
BLOCK_PATTERNS = (
    "bạn chưa đăng nhập thành viên",
    "văn bản này đang cập nhật nội dung",
    "đây là tiện ích dành cho tài khoản tiêu chuẩn hoặc nâng cao",
    "vui lòng xem nội dung chi tiết trong file đính kèm",
)


def json_value(value: Any, fallback: Any) -> Any:
    if isinstance(value, list):
        return cast(list[Any], value)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return fallback
    return fallback


def block_text(block: Mapping[str, Any]) -> str:
    values: list[Any] = [
        block.get("text"),
        block.get("content"),
        block.get("table_body"),
        block.get("table_caption"),
        block.get("table_footnote"),
    ]
    items = block.get("list_items")
    if isinstance(items, list):
        values.extend(cast(list[Any], items))
    return " ".join(str(v) for v in values if v)


def bbox_y0(block: Mapping[str, Any]) -> float:
    value = block.get("bbox") or [0, 0, 1, 1]
    if isinstance(value, (list, tuple)) and len(cast(Sequence[Any], value)) >= 2:
        try:
            return float(cast(Sequence[Any], value)[1])
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def route(document_reasons: Sequence[str], pages: Sequence[Mapping[str, Any]]) -> str:
    hard = [p for p in pages if p["route"] == "vision_reparse"]
    warnings = [p for p in pages if p["route"] == "accept_with_warning"]
    return (
        "legacy_font_encoding_recovery"
        if "legacy_vietnamese_font_encoding_suspect" in document_reasons
        else "vision_reparse_pages"
        if document_reasons or hard
        else "accept_with_warning"
        if warnings
        else "accept_ocr"
    )


def quality_check(
    analysis: Mapping[str, Any], result: Mapping[str, Any], profile: str = "generic"
) -> dict[str, Any]:
    """Route only objectively suspicious pages; never certify legal correctness."""
    vietnamese = profile == "vi"
    blocks = json_value(result.get("page_parse_json"), [])
    markdown = result.get("whole_text_md") or ""
    blocks = (
        [cast(dict[str, Any], b) for b in cast(list[Any], blocks) if isinstance(b, dict)]
        if isinstance(blocks, list)
        else []
    )
    raw_indices = [b["page_idx"] for b in blocks if isinstance(b.get("page_idx"), int)]
    zero_based = bool(raw_indices) and min(raw_indices) == 0
    by_page: dict[int, list[dict[str, Any]]] = {}
    for block in blocks:
        value = block.get("page_idx")
        if isinstance(value, int):
            by_page.setdefault(value + 1 if zero_based else value, []).append(block)
    has_native_pages = any(not p.get("suspected_scan") for p in analysis.get("pages", []))
    garbled = sum(char in LEGACY_GARBLED for char in markdown) if vietnamese else 0
    garbled_ratio = garbled / max(len(markdown), 1)
    document_reasons: list[str] = []
    expected = len(analysis.get("pages", []))
    if raw_indices and expected and (min(raw_indices) not in {0, 1} or max(by_page) > expected):
        document_reasons.append("page_number_mapping_unreliable")
    if (has_native_pages and garbled >= 8 and garbled_ratio >= 0.002) or (
        not has_native_pages and garbled >= 16 and garbled_ratio >= 0.03
    ):
        document_reasons.append("legacy_vietnamese_font_encoding_suspect")
    lowered_md = str(markdown).casefold()
    if any(pat in lowered_md for pat in BLOCK_PATTERNS):
        document_reasons.append("blocked_or_placeholder_text")
    pages: list[dict[str, Any]] = []
    for page in analysis.get("pages", []):
        number = page["page"]
        local = by_page.get(number, [])
        text_blocks: list[dict[str, Any]] = [
            b for b in local if b.get("type") in {"text", "header", "list", "table"}
        ]
        local_text = "\n".join(block_text(b) for b in text_blocks)
        ocr_chars = len(local_text.strip())
        reasons: list[str] = []
        ink = page.get("ink_ratio", 0)
        native = page.get("native_text_chars", 0)
        blank_candidate = ink < 0.012 and native < 20 and ocr_chars < 20
        if page.get("suspected_scan") and ocr_chars < 30 and not blank_candidate:
            reasons.append("scan_page_with_little_ocr_text")
        if blank_candidate:
            reasons.append("low_ink_blank_page_candidate")
        elif ink >= 0.006 and ocr_chars < 20:
            reasons.append("nonblank_page_without_ocr_text")
        if native >= 180 and ocr_chars < max(30, int(native * 0.15)):
            reasons.append("native_text_page_with_little_ocr_text")
        if ink < 0.0015 and ocr_chars > 50:
            reasons.append("blank_page_with_spurious_text")
        replacements = local_text.count("\ufffd")
        if replacements >= 3:
            reasons.append("replacement_character")
        elif replacements:
            reasons.append("isolated_replacement_character")
        if vietnamese:
            cjk = len(re.findall(r"[\u3400-\u9fff\uf900-\ufaff]", local_text))
            garbled_page = sum(char in LEGACY_GARBLED for char in local_text)
            has_primary_tcvn3 = any(char in PRIMARY_TCVN3 for char in local_text)
            if has_primary_tcvn3:
                if page.get("suspected_scan"):
                    if garbled_page >= 8 and garbled_page / max(ocr_chars, 1) >= 0.03:
                        reasons.append("legacy_vietnamese_font_garbled")
                elif garbled_page >= 4 and garbled_page / max(ocr_chars, 1) >= 0.005:
                    reasons.append("legacy_vietnamese_font_garbled")
            if cjk >= 12 and cjk / max(ocr_chars, 1) >= 0.02:
                reasons.append("non_vietnamese_garbage")
            if sum(char in FOREIGN_DIACRITICS for char in local_text) >= 3:
                reasons.append("foreign_diacritic_garbage")
        tables: list[dict[str, Any]] = [
            b for b in local if str(b.get("type", "")).lower() == "table"
        ]
        if tables and sum(len(block_text(b).strip()) for b in tables) < 20:
            reasons.append("table_content_missing")
        if any(
            len(block_text(b).strip()) >= 120
            and not (b.get("table_body") or "<table" in block_text(b).lower())
            for b in tables
        ):
            reasons.append("table_structure_missing")
        positions = [bbox_y0(b) for b in text_blocks if block_text(b).strip()]
        reversals = sum(c + 0.08 < p for p, c in zip(positions, positions[1:], strict=False))
        if len(positions) >= 8 and reversals >= 3:
            reasons.append("reading_order_unstable")
        if vietnamese:
            folded = local_text.upper()
            if ("NƠI NHẬN" in folded or "NOI NHAN" in folded) and not any(
                marker in folded for marker in SIGNATURE_MARKERS
            ):
                reasons.append("signature_block_missing")
        if number == 1 and "page_number_mapping_unreliable" in document_reasons and ocr_chars < 20:
            reasons.append("page_1_blocks_missing_with_unreliable_mapping")
        unique = list(dict.fromkeys(reasons))
        hard = [r for r in unique if r not in WARNING_REASONS]
        pages.append(
            {
                "page": number,
                "ocr_text_chars": ocr_chars,
                "ocr_text_blocks": len(text_blocks),
                "reasons": unique,
                "route": "vision_reparse"
                if hard
                else "accept_with_warning"
                if unique
                else "accept",
            }
        )
    return {
        "contract": QUALITY_CONTRACT,
        "profile": profile,
        "recommended_route": route(document_reasons, pages),
        "pages": pages,
        "document_reasons": document_reasons,
        "legacy_garbled_char_count": garbled,
        "legacy_garbled_char_ratio": round(garbled_ratio, 6),
        "reason_count": sum(len(p["reasons"]) for p in pages) + len(document_reasons),
    }


def text_check(markdown: str, profile: str = "generic") -> dict[str, Any]:
    """Document-level checks for direct text extraction (HTML, TXT, Office)."""
    reasons: list[str] = []
    replacements = markdown.count("\ufffd")
    if replacements >= 3:
        reasons.append("replacement_character")
    elif replacements:
        reasons.append("isolated_replacement_character")
    lowered = markdown.casefold()
    if any(pat in lowered for pat in BLOCK_PATTERNS):
        reasons.append("blocked_or_placeholder_text")
    garbled = 0
    if profile == "vi":
        garbled = sum(char in LEGACY_GARBLED for char in markdown)
        has_primary = any(char in PRIMARY_TCVN3 for char in markdown)
        if has_primary and garbled >= 8 and garbled / max(len(markdown), 1) >= 0.002:
            reasons.append("legacy_vietnamese_font_garbled")
    hard = [r for r in reasons if r not in WARNING_REASONS]
    return {
        "contract": QUALITY_CONTRACT,
        "profile": profile,
        "recommended_route": (
            "legacy_font_encoding_recovery"
            if "legacy_vietnamese_font_garbled" in hard
            else "vision_reparse_pages"
            if hard
            else "accept_with_warning"
            if reasons
            else "accept"
        ),
        "pages": [],
        "document_reasons": reasons,
        "legacy_garbled_char_count": garbled,
        "reason_count": len(reasons),
    }
