"""Quantitative cleanliness and accuracy metric algorithms."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any, Mapping, cast

from wlh.benchmark.models import (
    AccuracyResult,
    AccuracySubScores,
    CleanlinessResult,
    CleanlinessSubScores,
)

# Standard taxonomy mapping for legal document types
DOC_TYPE_TAXONOMY_MAP: dict[str, str] = {
    "by-law": "regulation",
    "decision": "regulation",
    "decree": "regulation",
    "standard": "standard",
    "determination": "regulation",
    "statute": "statute",
    "legislation": "statute",
    "act": "statute",
    "law": "statute",
    "ordinance": "statute",
    "code": "statute",
    "consolidated_document": "statute",
    "judgment": "judgment",
    "circular": "circular",
    "case_law": "judgment",
    "judicial_interpretation": "judgment",
    "precedent": "judgment",
    "ruling": "judgment",
    "decision_summary": "judgment",
    "guidance": "guidance",
    "tax_guidance": "guidance",
    "tax_material": "circular",
    "regulation": "regulation",
    "gazette": "regulation",
    "notice": "notice",
    "announcement": "notice",
    "public_notice": "notice",
    "政策文件": "regulation",
    "法律法规": "regulation",
    "税务资料": "circular",
    "通知公告": "notice",
    "通知": "notice",
    "公告": "notice",
    "公示": "notice",
    "招标公告": "notice",
    "案例": "judgment",
    "典型案例": "judgment",
    "裁判文书": "judgment",
    "通达": "circular",
    "行政法规": "regulation",
    "行政規則": "regulation",
    "地方性法规": "regulation",
    "quyết định": "regulation",
    "nghị quyết": "regulation",
    "quy định": "regulation",
}

# UI navigation, buttons, and boilerplate phrases
UI_BOILERPLATE_PATTERNS = [
    re.compile(r"【?(?:打印|打印本页|关闭|关闭窗口|分享|分享到微信)】?"),
    re.compile(r"字体\s*[:：]?\s*\[?\s*(?:大\s*中\s*小|小\s*中\s*大)\s*\]?"),
    re.compile(r"正文字体|浏览次数\s*[:：]|来源\s*[:：]|网站标识码\s*\d+|公网安备\d+号|相关链接\s*[:：]|相关文档\b|담당부서|조회수|微信\s*\*\*X\*\*"),
    re.compile(r"\b(?:Print|Print this page|Close window|Share this|Back to top|Breadcrumbs|Site Navigation|Search this site|Search Query)\b", re.IGNORECASE),
]

PORTAL_FOOTER_NOISE_PATTERNS = [
    re.compile(
        r"(?:版权所有|网站管理|网站地图|法律声明|隐私政策|联系我们|联系方式|邮政编码|邮编\s*[:：]|ICP备|主办单位\s*[:：]|承办单位\s*[:：]|技术支持\s*[:：])"
    ),
    re.compile(
        r"\b(?:All Rights Reserved|Privacy Policy|Terms of Use|Contact Us|Site Map|Copyright\s+©)\b",
        re.IGNORECASE,
    ),
]

# UUID regex pattern
UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{32}\b|\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

# HTML tags and entity patterns
HTML_TAG_PATTERN = re.compile(
    r"<(?:/?(?:div|span|p|a|img|table|thead|tbody|tfoot|tr|td|th|caption|colgroup|col|ul|ol|li|dl|dt|dd|br|hr|h[1-6]|script|style|link|meta|form|input|button|select|option|textarea|label|iframe|nav|header|footer|section|article|aside|main|figure|figcaption|blockquote|pre|code|font|center|strong|em|b|i|u|s|sub|sup|small|mark|del|ins|abbr|cite|q|time|html|head|body|title|base|noscript|canvas|svg|path|g|rect|circle|audio|video|source|track|embed|object|param|details|summary|dialog|menu|menuitem)\b[^>]*|!--.*?--)>",
    re.IGNORECASE | re.DOTALL,
)
HTML_ENTITY_PATTERN = re.compile(r"&(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);")
CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Mojibake & legacy font corruption patterns
MOJIBAKE_PATTERNS = [
    re.compile(r"[\u00a7\u00a9\u00ae\u00b5\u00b6\u00b8\u00b9\u00ba\u00bb\u00bf]{3,}"),  # Legacy TCVN3 corruption cluster
    re.compile(r"(?:Ã[©¼¤±]|â[€\u0080-\u009f]|ï¿½|dáº«n|Ãch)"),  # Corrupted UTF-8 as Latin-1
]


def levenshtein_distance(s1: str, s2: str) -> int:
    """Compute the Levenshtein edit distance between two strings."""
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1, 1):
        curr = [i] * (len(s2) + 1)
        for j, c2 in enumerate(s2, 1):
            cost = 0 if c1 == c2 else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[len(s2)]


def normalize_text(val: Any) -> str:
    """Normalize text using NFKC normalization, lowercase, and stripped whitespace."""
    if val is None:
        return ""
    text = str(val).strip()
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def strip_all_punct_and_whitespace(val: Any) -> str:
    """Strip all punctuation and whitespace for strict semantic comparison."""
    if val is None:
        return ""
    text = str(val)
    text = unicodedata.normalize("NFKC", text)
    # Remove all whitespace, markdown formatting markers, and ASCII/CJK punctuation symbols
    text = re.sub(
        r"[\s\-_—–()（）\[\]【】《》〈〉/\\.,:;\"'“”‘’#*|~`>、，。；：？！·…「」『』〔〕•◦▪]+",
        "",
        text,
    )
    return text.lower()


def _normalize_markdown_content(val: Any) -> str:
    """Normalize markdown prose by stripping link URLs, markdown syntax, punctuation, and whitespace."""
    if val is None:
        return ""
    text = str(val)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]+\)", r"\1", text)
    return strip_all_punct_and_whitespace(text)


def _line_has_ui_or_html_noise(line: str) -> bool:
    """Return True if a line contains explicit UI/portal boilerplate, UUIDs, or raw HTML tags."""
    if UUID_PATTERN.search(line) or HTML_TAG_PATTERN.search(line):
        return True
    if any(pat.search(line) for pat in PORTAL_FOOTER_NOISE_PATTERNS):
        return True
    return any(pat.search(line) for pat in UI_BOILERPLATE_PATTERNS)


# ==============================================================================
# Cleanliness Evaluation
# ==============================================================================


def evaluate_cleanliness(
    markdown: str,
    title: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> CleanlinessResult:
    """Evaluate text cleanliness across 5 quantitative dimensions.

    Dimensions:
    - noise_purity (weight: 25%)
    - encoding_purity (weight: 20%)
    - structural_integrity (weight: 20%)
    - boilerplate_freedom (weight: 20%)
    - deduplication_purity (weight: 15%)
    """
    text_content = markdown or ""
    combined_content = f"{title}\n\n{text_content}" if title else text_content
    penalties: list[dict[str, Any]] = []

    # 1. Noise Purity (HTML tags, entities, control characters)
    html_tags = HTML_TAG_PATTERN.findall(text_content)
    html_entities = HTML_ENTITY_PATTERN.findall(text_content)
    control_chars = CONTROL_CHAR_PATTERN.findall(text_content)

    noise_deduction = 0.0
    if html_tags:
        deduction = min(60.0, len(html_tags) * 8.0)
        noise_deduction += deduction
        penalties.append({
            "dimension": "noise_purity",
            "reason": f"Found {len(html_tags)} unescaped HTML tags (e.g. {html_tags[:3]})",
            "deduction": round(deduction, 2),
        })
    if html_entities:
        deduction = min(30.0, len(html_entities) * 3.0)
        noise_deduction += deduction
        penalties.append({
            "dimension": "noise_purity",
            "reason": f"Found {len(html_entities)} raw HTML entities",
            "deduction": round(deduction, 2),
        })
    if control_chars:
        deduction = min(30.0, len(control_chars) * 10.0)
        noise_deduction += deduction
        penalties.append({
            "dimension": "noise_purity",
            "reason": f"Found {len(control_chars)} non-printable control characters",
            "deduction": round(deduction, 2),
        })
    noise_score = round(max(0.0, 100.0 - noise_deduction), 2)

    # 2. Encoding Purity (\ufffd replacement chars, PUA, mojibake sequences)
    ufffd_count = combined_content.count("\ufffd")
    pua_count = len(re.findall(r"[\ue000-\uf8ff]", combined_content))
    mojibake_matches: list[str] = []
    for pattern in MOJIBAKE_PATTERNS:
        matches = [str(m) for m in pattern.findall(combined_content)]
        mojibake_matches.extend(matches)

    encoding_deduction = 0.0
    if ufffd_count > 0:
        deduction = min(70.0, ufffd_count * 20.0)
        encoding_deduction += deduction
        penalties.append({
            "dimension": "encoding_purity",
            "reason": f"Found {ufffd_count} Unicode replacement characters (\\ufffd)",
            "deduction": round(deduction, 2),
        })
    if pua_count > 0:
        deduction = min(40.0, pua_count * 5.0)
        encoding_deduction += deduction
        penalties.append({
            "dimension": "encoding_purity",
            "reason": f"Found {pua_count} Private Use Area (PUA) characters",
            "deduction": round(deduction, 2),
        })
    if mojibake_matches:
        deduction = min(50.0, len(mojibake_matches) * 15.0)
        encoding_deduction += deduction
        penalties.append({
            "dimension": "encoding_purity",
            "reason": f"Detected {len(mojibake_matches)} mojibake/corrupted encoding sequences",
            "deduction": round(deduction, 2),
        })
    encoding_score = round(max(0.0, 100.0 - encoding_deduction), 2)

    # 3. Structural Integrity (Graceful zero-table handling, broken tables, heading hierarchy)
    structural_deduction = 0.0

    # Table analysis
    lines = text_content.splitlines()
    table_blocks: list[list[str]] = []
    current_block: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            current_block.append(stripped)
        else:
            if current_block:
                table_blocks.append(current_block)
                current_block = []
    if current_block:
        table_blocks.append(current_block)

    for block_idx, block in enumerate(table_blocks, start=1):
        block_has_separator = any(re.match(r"^\|(?:\s*:?-+:?\s*\|)+$", row) for row in block)
        block_col_counts = [
            len([c for c in row.split("|")[1:-1]])
            for row in block
            if not re.match(r"^\|(?:\s*:?-+:?\s*\|)+$", row)
        ]
        suffix = f" (table block {block_idx})" if len(table_blocks) > 1 else ""
        if not block_has_separator:
            structural_deduction += 20.0
            penalties.append({
                "dimension": "structural_integrity",
                "reason": f"Markdown table missing standard header separator line (|---|){suffix}",
                "deduction": 20.0,
            })
        if block_col_counts and len(set(block_col_counts)) > 1:
            structural_deduction += 25.0
            penalties.append({
                "dimension": "structural_integrity",
                "reason": f"Table column count mismatch across rows: {set(block_col_counts)}{suffix}",
                "deduction": 25.0,
            })
    # If table_blocks is empty, no table deduction is made (graceful zero-table handling per M2 recommendation).

    # Heading hierarchy check
    heading_levels: list[int] = []
    for line in lines:
        match = re.match(r"^(#{1,6})\s+", line)
        if match:
            heading_levels.append(len(match.group(1)))

    heading_skips = 0
    for i in range(1, len(heading_levels)):
        if heading_levels[i] - heading_levels[i - 1] >= 3:
            heading_skips += 1

    if heading_skips > 0:
        deduction = min(30.0, heading_skips * 15.0)
        structural_deduction += deduction
        penalties.append({
            "dimension": "structural_integrity",
            "reason": f"Detected {heading_skips} drastic heading level jump(s) (e.g. h1 to h4+)",
            "deduction": round(deduction, 2),
        })

    # Unclosed code blocks check
    code_fence_count = len(re.findall(r"^```", text_content, re.MULTILINE))
    if code_fence_count % 2 != 0:
        structural_deduction += 20.0
        penalties.append({
            "dimension": "structural_integrity",
            "reason": "Unclosed markdown code fence (odd number of ``` markers)",
            "deduction": 20.0,
        })

    structural_score = round(max(0.0, 100.0 - structural_deduction), 2)

    # 4. Boilerplate Freedom (UI actions, navigation residues, UUIDs)
    boilerplate_deduction = 0.0
    ui_match_count = 0
    for pat in UI_BOILERPLATE_PATTERNS:
        ui_match_count += len(pat.findall(combined_content))

    uuid_matches = UUID_PATTERN.findall(combined_content)

    if ui_match_count > 0:
        deduction = min(60.0, ui_match_count * 12.0)
        boilerplate_deduction += deduction
        penalties.append({
            "dimension": "boilerplate_freedom",
            "reason": f"Detected {ui_match_count} UI action/navigation boilerplate elements",
            "deduction": round(deduction, 2),
        })

    if uuid_matches:
        deduction = min(40.0, len(uuid_matches) * 15.0)
        boilerplate_deduction += deduction
        penalties.append({
            "dimension": "boilerplate_freedom",
            "reason": f"Detected {len(uuid_matches)} raw UUID hashes in text/title",
            "deduction": round(deduction, 2),
        })

    boilerplate_score = round(max(0.0, 100.0 - boilerplate_deduction), 2)

    # 5. Deduplication Purity (Consecutive duplicate paragraphs and repeated blocks)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text_content) if len(p.strip()) >= 30]
    consec_dups = 0
    for i in range(1, len(paragraphs)):
        if paragraphs[i] == paragraphs[i - 1]:
            consec_dups += 1

    # Ratio of repeated paragraph characters
    seen_paras: set[str] = set()
    dup_chars = 0
    total_chars = sum(len(p) for p in paragraphs) or 1
    for p in paragraphs:
        if p in seen_paras:
            dup_chars += len(p)
        else:
            seen_paras.add(p)

    dup_ratio = dup_chars / total_chars
    dedup_deduction = 0.0
    if consec_dups > 0:
        deduction = min(50.0, consec_dups * 25.0)
        dedup_deduction += deduction
        penalties.append({
            "dimension": "deduplication_purity",
            "reason": f"Detected {consec_dups} consecutive duplicate paragraph(s)",
            "deduction": round(deduction, 2),
        })

    if dup_ratio > 0.2:
        deduction = min(50.0, (dup_ratio - 0.2) * 100.0)
        dedup_deduction += deduction
        penalties.append({
            "dimension": "deduplication_purity",
            "reason": f"High document text duplication ratio: {dup_ratio:.1%}",
            "deduction": round(deduction, 2),
        })

    dedup_score = round(max(0.0, 100.0 - dedup_deduction), 2)

    # 6. Apply Golden Set cleanliness_ground_truth annotations when provided via metadata
    cgt_raw = metadata.get("cleanliness_ground_truth") if isinstance(metadata, Mapping) else None
    if isinstance(cgt_raw, Mapping):
        cgt = cast(Mapping[str, Any], cgt_raw)
        if cgt.get("has_html_artifacts") is True and noise_score > 80.0:
            ded = round(noise_score - 80.0, 2)
            noise_score = 80.0
            penalties.append({
                "dimension": "noise_purity",
                "reason": "Annotated Golden Set: residual HTML artifacts in extracted markdown",
                "deduction": ded,
            })
        if cgt.get("has_navigation_boilerplate") is True and boilerplate_score > 75.0:
            ded = round(boilerplate_score - 75.0, 2)
            boilerplate_score = 75.0
            penalties.append({
                "dimension": "boilerplate_freedom",
                "reason": "Annotated Golden Set: navigation/UI/metadata boilerplate in extracted markdown",
                "deduction": ded,
            })
        if cgt.get("has_garbled_encoding") is True and encoding_score > 70.0:
            ded = round(encoding_score - 70.0, 2)
            encoding_score = 70.0
            penalties.append({
                "dimension": "encoding_purity",
                "reason": "Annotated Golden Set: garbled encoding or font corruption in extracted markdown",
                "deduction": ded,
            })
        if cgt.get("has_duplicated_body") is True and dedup_score > 75.0:
            ded = round(dedup_score - 75.0, 2)
            dedup_score = 75.0
            penalties.append({
                "dimension": "deduplication_purity",
                "reason": "Annotated Golden Set: duplicated content blocks in extracted markdown",
                "deduction": ded,
            })

    # 7. Compare extracted markdown against reference ground_truth.markdown & clean_markdown_excerpt
    gt_raw = metadata.get("ground_truth") if isinstance(metadata, Mapping) else None
    if isinstance(gt_raw, Mapping):
        gt_map = cast(Mapping[str, Any], gt_raw)
        clean_excerpt = str(gt_map.get("clean_markdown_excerpt") or "").strip()
        if len(clean_excerpt) >= 30:
            norm_text = _normalize_markdown_content(text_content)
            norm_excerpt = _normalize_markdown_content(clean_excerpt)
            if norm_excerpt and norm_excerpt not in norm_text:
                # Check 8-gram character window recall with stride 1
                gram_len = min(8, len(norm_excerpt))
                grams = [
                    norm_excerpt[i : i + gram_len]
                    for i in range(0, max(1, len(norm_excerpt) - gram_len + 1))
                ]
                hit_ratio = sum(1 for g in grams if g in norm_text) / max(len(grams), 1)
                if hit_ratio < 0.75:
                    excerpt_ded = round(min(25.0, (1.0 - hit_ratio) * 30.0), 2)
                    if excerpt_ded > 0.0:
                        structural_score = round(max(0.0, structural_score - excerpt_ded), 2)
                        penalties.append({
                            "dimension": "structural_integrity",
                            "reason": f"Reference clean_markdown_excerpt recall gap ({hit_ratio:.0%} match against ground truth)",
                            "deduction": excerpt_ded,
                        })

        ref_md = str(gt_map.get("markdown") or "").strip()
        if ref_md and not text_content.strip():
            empty_ded = round(min(60.0, structural_score), 2)
            if empty_ded > 0.0:
                structural_score = round(max(0.0, structural_score - empty_ded), 2)
                penalties.append({
                    "dimension": "structural_integrity",
                    "reason": "Reference ground_truth.markdown comparison: extracted markdown is empty (100.0% recall gap)",
                    "deduction": empty_ded,
                })
        elif ref_md and text_content:
            pred_lines = [ln.strip() for ln in text_content.splitlines() if ln.strip()]
            ref_lines = [ln.strip() for ln in ref_md.splitlines() if ln.strip()]
            norm_ref_full = _normalize_markdown_content(ref_md)
            norm_pred_full = _normalize_markdown_content(text_content)

            gram_k = 4
            full_pred_grams = [
                norm_pred_full[i : i + gram_k]
                for i in range(0, max(0, len(norm_pred_full) - gram_k + 1))
            ]
            full_ref_grams = [
                norm_ref_full[i : i + gram_k]
                for i in range(0, max(0, len(norm_ref_full) - gram_k + 1))
            ]
            full_ref_counts = Counter(full_ref_grams)
            full_pred_counts = Counter(full_pred_grams)
            raw_prec = (
                sum(min(cnt, full_ref_counts[g]) for g, cnt in full_pred_counts.items())
                / max(len(full_pred_grams), 1)
            )
            raw_rec = (
                sum(min(cnt, full_pred_counts[g]) for g, cnt in full_ref_counts.items())
                / max(len(full_ref_grams), 1)
            )

            pred_eval_lines = pred_lines
            ref_eval_lines = ref_lines

            # Span alignment when reference markdown is a bounded prefix excerpt of a longer document
            if (
                len(text_content) >= 1200
                and len(norm_ref_full) >= 120
                and len(norm_pred_full) - len(norm_ref_full) >= 250
                and len(norm_pred_full) > int(len(norm_ref_full) * 1.15)
                and raw_rec >= 0.70
            ):
                last_pred_idx = -1
                for idx, ln in enumerate(pred_lines):
                    nl = _normalize_markdown_content(ln)
                    if len(nl) >= 6 and nl in norm_ref_full:
                        last_pred_idx = idx
                if last_pred_idx >= 0:
                    tail_noise = [
                        ln for ln in pred_lines[last_pred_idx + 1 :] if _line_has_ui_or_html_noise(ln)
                    ]
                    pred_eval_lines = pred_lines[: last_pred_idx + 1] + tail_noise

            # Span alignment when extracted markdown hits the 4,000-character preview cap
            if (
                len(text_content) >= 3500
                and len(norm_ref_full) > int(len(norm_pred_full) * 1.10)
                and raw_prec >= 0.70
            ):
                last_ref_idx = -1
                for idx, ln in enumerate(ref_lines):
                    nl = _normalize_markdown_content(ln)
                    if len(nl) >= 6 and nl in norm_pred_full:
                        last_ref_idx = idx
                if last_ref_idx >= 0:
                    ref_eval_lines = ref_lines[: last_ref_idx + 1]

            norm_pred = _normalize_markdown_content("\n".join(pred_eval_lines))
            norm_ref = _normalize_markdown_content("\n".join(ref_eval_lines))
            ref_line_set = set(ref_eval_lines)
            pred_line_set = set(pred_eval_lines)

            extra_lines = [
                ln
                for ln in pred_eval_lines
                if ln not in ref_line_set
                and len(_normalize_markdown_content(ln)) >= 2
                and _normalize_markdown_content(ln) not in norm_ref_full
            ]
            missing_lines = [
                ln
                for ln in ref_eval_lines
                if ln not in pred_line_set
                and len(ln) >= 8
                and len(_normalize_markdown_content(ln)) >= 6
                and _normalize_markdown_content(ln) not in norm_pred_full
            ]

            if len(norm_pred) >= gram_k and len(norm_ref) >= gram_k:
                pred_grams = [
                    norm_pred[i : i + gram_k]
                    for i in range(0, len(norm_pred) - gram_k + 1)
                ]
                ref_grams = [
                    norm_ref[i : i + gram_k]
                    for i in range(0, len(norm_ref) - gram_k + 1)
                ]
                pred_counts = Counter(pred_grams)
                ref_counts = Counter(ref_grams)
                precision = (
                    sum(min(cnt, full_ref_counts[g]) for g, cnt in pred_counts.items())
                    / max(len(pred_grams), 1)
                )
                recall = (
                    sum(min(cnt, full_pred_counts[g]) for g, cnt in ref_counts.items())
                    / max(len(ref_grams), 1)
                )
            else:
                precision = 1.0 if not extra_lines else 0.5
                recall = 1.0 if not missing_lines else 0.5

            if extra_lines or precision < 0.92:
                extra_chars = sum(len(ln) for ln in extra_lines)
                line_extra_ratio = extra_chars / max(len(text_content), 1)
                extra_ratio = max(line_extra_ratio, max(0.0, 1.0 - precision))
                extra_ded = round(
                    min(30.0, max(5.0, len(extra_lines) * 4.0 + extra_ratio * 50.0)), 2
                )
                applied = round(min(extra_ded, max(0.0, boilerplate_score - 40.0)), 2)
                if applied > 0.0:
                    boilerplate_score = round(max(0.0, boilerplate_score - applied), 2)
                    penalties.append({
                        "dimension": "boilerplate_freedom",
                        "reason": (
                            f"Reference ground_truth.markdown comparison: {max(len(extra_lines), 1)} unstripped "
                            f"boilerplate/noise line(s) (precision={precision:.1%}, {extra_ratio:.1%} excess content)"
                        ),
                        "deduction": applied,
                    })

            if missing_lines or recall < 0.92:
                missing_chars = sum(len(ln) for ln in missing_lines)
                line_missing_ratio = missing_chars / max(len(ref_md), 1)
                missing_ratio = max(line_missing_ratio, max(0.0, 1.0 - recall))
                miss_ded = round(
                    min(30.0, max(5.0, len(missing_lines) * 4.0 + missing_ratio * 50.0)), 2
                )
                applied = round(min(miss_ded, structural_score), 2)
                if applied > 0.0:
                    structural_score = round(max(0.0, structural_score - applied), 2)
                    penalties.append({
                        "dimension": "structural_integrity",
                        "reason": (
                            f"Reference ground_truth.markdown comparison: {max(len(missing_lines), 1)} substantive "
                            f"line(s) missing from extracted markdown (recall={recall:.1%}, {missing_ratio:.1%} recall gap)"
                        ),
                        "deduction": applied,
                    })

    sub_scores = CleanlinessSubScores(
        noise_purity=noise_score,
        encoding_purity=encoding_score,
        structural_integrity=structural_score,
        boilerplate_freedom=boilerplate_score,
        deduplication_purity=dedup_score,
    )

    # Composite weighted cleanliness score
    overall_cleanliness = (
        0.25 * sub_scores.noise_purity
        + 0.20 * sub_scores.encoding_purity
        + 0.20 * sub_scores.structural_integrity
        + 0.20 * sub_scores.boilerplate_freedom
        + 0.15 * sub_scores.deduplication_purity
    )

    return CleanlinessResult(
        score=round(overall_cleanliness, 2),
        sub_scores=sub_scores,
        penalties=penalties,
    )


# ==============================================================================
# Accuracy Evaluation
# ==============================================================================


def _score_title_accuracy(pred: Any, gt: Any) -> tuple[float, dict[str, Any] | None]:
    """Score title alignment supporting CJK short titles and affixes."""
    if (pred is None or str(pred).strip() == "") and (gt is None or str(gt).strip() == ""):
        return 100.0, None
    if pred is None or str(pred).strip() == "":
        return 0.0, {"field": "title", "reason": "Missing or blank title in prediction", "predicted": None, "ground_truth": gt}
    if gt is None or str(gt).strip() == "":
        return 0.0, {"field": "title", "reason": "Missing title value in ground truth", "predicted": str(pred).strip(), "ground_truth": None}

    str_pred = str(pred).strip()
    str_gt = str(gt).strip()
    if str_pred == str_gt:
        return 100.0, None

    clean_pred = strip_all_punct_and_whitespace(str_pred)
    clean_gt = strip_all_punct_and_whitespace(str_gt)

    if clean_pred and clean_pred == clean_gt:
        return 100.0, None

    # Substring containment (e.g. order affixes or subheadings)
    if len(clean_gt) >= 3 and clean_gt in clean_pred:
        ratio = len(clean_gt) / len(clean_pred) if len(clean_pred) > 0 else 1.0
        score = round(80.0 + 20.0 * ratio, 2)
        return score, None

    if len(clean_pred) >= 3 and clean_pred in clean_gt:
        ratio = len(clean_pred) / len(clean_gt) if len(clean_gt) > 0 else 1.0
        score = round(70.0 + 20.0 * ratio, 2)
        return score, None

    # Levenshtein character similarity for CJK or general text
    dist = levenshtein_distance(clean_pred, clean_gt)
    max_len = max(len(clean_pred), len(clean_gt))
    if max_len == 0:
        dist = levenshtein_distance(str_pred, str_gt)
        max_len = max(len(str_pred), len(str_gt))
        if max_len == 0:
            return 100.0, None
    sim = max(0.0, 1.0 - (dist / max_len))
    score = round(sim * 100.0, 2)
    mismatch = None if score >= 90.0 else {
        "field": "title",
        "reason": f"Title divergence (similarity: {score}%)",
        "predicted": str_pred,
        "ground_truth": str_gt,
    }
    return score, mismatch


def _score_doc_number_accuracy(pred: Any, gt: Any) -> tuple[float, dict[str, Any] | None]:
    """Score doc_number with null-matching support and pollution penalties."""
    # Null matching per M2 reviewer recommendation
    if (pred is None or str(pred).strip() == "") and (gt is None or str(gt).strip() == ""):
        return 100.0, None

    if pred is None or str(pred).strip() == "":
        return 0.0, {"field": "doc_number", "reason": "Missing document number in prediction", "predicted": None, "ground_truth": gt}

    if gt is None or str(gt).strip() == "":
        return 0.0, {"field": "doc_number", "reason": "Extraneous document number extracted when ground truth is null", "predicted": pred, "ground_truth": None}

    str_pred = str(pred).strip()
    str_gt = str(gt).strip()

    if str_pred == str_gt:
        return 100.0, None

    clean_pred = strip_all_punct_and_whitespace(str_pred)
    clean_gt = strip_all_punct_and_whitespace(str_gt)

    if clean_pred == clean_gt:
        return 100.0, None

    # Extraneous bytes/file size pollution (e.g. Western Cape Provincial Gazette 9196 ... (372.7 KB))
    if clean_gt in clean_pred:
        ratio = len(clean_gt) / len(clean_pred)
        score = round(50.0 + 35.0 * ratio, 2)
        return score, {
            "field": "doc_number",
            "reason": "Document number contains extraneous text or byte pollution",
            "predicted": str_pred,
            "ground_truth": str_gt,
        }

    # Levenshtein fallback
    dist = levenshtein_distance(clean_pred, clean_gt)
    max_len = max(len(clean_pred), len(clean_gt))
    sim = max(0.0, 1.0 - (dist / max_len))
    score = round(sim * 100.0, 2)
    return score, {
        "field": "doc_number",
        "reason": "Document number mismatch",
        "predicted": str_pred,
        "ground_truth": str_gt,
    }


def _score_date_accuracy(field_name: str, pred: Any, gt: Any) -> tuple[float, dict[str, Any] | None]:
    """Score ISO date fields with null matching and temporal proximity."""
    if (pred is None or str(pred).strip() == "") and (gt is None or str(gt).strip() == ""):
        return 100.0, None

    if pred is None or str(pred).strip() == "":
        return 0.0, {"field": field_name, "reason": f"Missing {field_name} in prediction", "predicted": None, "ground_truth": gt}

    if gt is None or str(gt).strip() == "":
        return 0.0, {"field": field_name, "reason": f"Extraneous {field_name} extracted when ground truth is null", "predicted": pred, "ground_truth": None}

    str_pred = str(pred).strip()[:10]
    str_gt = str(gt).strip()[:10]

    if str_pred == str_gt:
        return 100.0, None

    # Check same year and month
    if len(str_pred) >= 7 and len(str_gt) >= 7 and str_pred[:7] == str_gt[:7]:
        return 60.0, {
            "field": field_name,
            "reason": f"Date mismatch on day component ({str_pred} vs {str_gt})",
            "predicted": str_pred,
            "ground_truth": str_gt,
        }

    # Check same year
    if len(str_pred) >= 4 and len(str_gt) >= 4 and str_pred[:4] == str_gt[:4]:
        return 30.0, {
            "field": field_name,
            "reason": f"Date mismatch on month/day ({str_pred} vs {str_gt})",
            "predicted": str_pred,
            "ground_truth": str_gt,
        }

    return 0.0, {
        "field": field_name,
        "reason": f"Completely mismatched date ({str_pred} vs {str_gt})",
        "predicted": str_pred,
        "ground_truth": str_gt,
    }


def _score_issuer_accuracy(pred: Any, gt: Any) -> tuple[float, dict[str, Any] | None]:
    """Score enacting/issuing authority with administrative division handling."""
    if (pred is None or str(pred).strip() == "") and (gt is None or str(gt).strip() == ""):
        return 100.0, None

    if pred is None or str(pred).strip() == "":
        return 0.0, {"field": "issuer", "reason": "Missing issuer in prediction", "predicted": None, "ground_truth": gt}

    if gt is None or str(gt).strip() == "":
        return 0.0, {"field": "issuer", "reason": "Extraneous issuer when ground truth is null", "predicted": pred, "ground_truth": None}

    str_pred = str(pred).strip()
    str_gt = str(gt).strip()

    if str_pred == str_gt:
        return 100.0, None

    clean_pred = strip_all_punct_and_whitespace(str_pred)
    clean_gt = strip_all_punct_and_whitespace(str_gt)

    if clean_pred == clean_gt:
        return 100.0, None

    # Substring containment (e.g. division suffix or parent authority)
    if clean_gt in clean_pred or clean_pred in clean_gt:
        min_len = min(len(clean_pred), len(clean_gt))
        max_len = max(len(clean_pred), len(clean_gt))
        ratio = min_len / max_len if max_len > 0 else 1.0
        score = round(50.0 + 45.0 * ratio, 2)
        return score, {
            "field": "issuer",
            "reason": "Issuer authority variation or administrative division difference",
            "predicted": str_pred,
            "ground_truth": str_gt,
        }

    # Levenshtein similarity
    dist = levenshtein_distance(clean_pred, clean_gt)
    max_len = max(len(clean_pred), len(clean_gt))
    sim = max(0.0, 1.0 - (dist / max_len)) if max_len > 0 else 0.0
    score = round(sim * 100.0, 2)
    return score, {
        "field": "issuer",
        "reason": "Mismatched issuer authority",
        "predicted": str_pred,
        "ground_truth": str_gt,
    }


def _score_doc_type_accuracy(pred: Any, gt: Any) -> tuple[float, dict[str, Any] | None]:
    """Score doc_type with canonical taxonomy normalization."""
    if pred is None and gt is None:
        return 100.0, None
    if pred is None or gt is None:
        return 0.0, {"field": "doc_type", "reason": "Missing doc_type", "predicted": pred, "ground_truth": gt}

    norm_pred = str(pred).strip().lower()
    norm_gt = str(gt).strip().lower()

    if norm_pred == norm_gt:
        return 100.0, None

    canon_pred = DOC_TYPE_TAXONOMY_MAP.get(norm_pred, norm_pred)
    canon_gt = DOC_TYPE_TAXONOMY_MAP.get(norm_gt, norm_gt)

    if canon_pred == canon_gt:
        return 85.0, None

    return 0.0, {
        "field": "doc_type",
        "reason": f"Mismatched document type ({norm_pred} vs {norm_gt})",
        "predicted": str(pred),
        "ground_truth": str(gt),
    }


def evaluate_accuracy(
    prediction: Mapping[str, Any],
    ground_truth: Mapping[str, Any],
) -> AccuracyResult:
    """Evaluate prediction accuracy against ground truth across 6 core fields.

    Weights:
    - title_accuracy (20%)
    - doc_number_accuracy (20%)
    - published_at_accuracy (15%)
    - effective_at_accuracy (15%)
    - issuer_accuracy (15%)
    - doc_type_accuracy (15%)
    """
    mismatches: list[dict[str, Any]] = []

    title_score, title_mis = _score_title_accuracy(prediction.get("title"), ground_truth.get("title"))
    if title_mis:
        mismatches.append(title_mis)

    doc_num_score, doc_num_mis = _score_doc_number_accuracy(prediction.get("doc_number"), ground_truth.get("doc_number"))
    if doc_num_mis:
        mismatches.append(doc_num_mis)

    pub_score, pub_mis = _score_date_accuracy("published_at", prediction.get("published_at"), ground_truth.get("published_at"))
    if pub_mis:
        mismatches.append(pub_mis)

    eff_score, eff_mis = _score_date_accuracy("effective_at", prediction.get("effective_at"), ground_truth.get("effective_at"))
    if eff_mis:
        mismatches.append(eff_mis)

    issuer_score, issuer_mis = _score_issuer_accuracy(prediction.get("issuer"), ground_truth.get("issuer"))
    if issuer_mis:
        mismatches.append(issuer_mis)

    doc_type_score, doc_type_mis = _score_doc_type_accuracy(prediction.get("doc_type"), ground_truth.get("doc_type"))
    if doc_type_mis:
        mismatches.append(doc_type_mis)

    sub_scores = AccuracySubScores(
        title_accuracy=title_score,
        doc_number_accuracy=doc_num_score,
        published_at_accuracy=pub_score,
        effective_at_accuracy=eff_score,
        issuer_accuracy=issuer_score,
        doc_type_accuracy=doc_type_score,
    )

    field_scores = {
        "title_accuracy": title_score,
        "doc_number_accuracy": doc_num_score,
        "published_at_accuracy": pub_score,
        "effective_at_accuracy": eff_score,
        "issuer_accuracy": issuer_score,
        "doc_type_accuracy": doc_type_score,
    }

    overall_accuracy = (
        0.20 * title_score
        + 0.20 * doc_num_score
        + 0.15 * pub_score
        + 0.15 * eff_score
        + 0.15 * issuer_score
        + 0.15 * doc_type_score
    )

    return AccuracyResult(
        score=round(overall_accuracy, 2),
        sub_scores=sub_scores,
        field_scores=field_scores,
        mismatches=mismatches,
    )
