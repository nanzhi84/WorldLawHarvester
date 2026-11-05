"""Generate and synchronize the standalone GitBook-style HTML two-column Source Verification Samples runbook."""

from __future__ import annotations

import argparse
import contextlib
import csv
import fcntl
import hashlib
import html as std_html
import io
import json
import os
import re
import select
import threading
import zipfile
from collections.abc import Generator, Mapping, Sequence
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, unquote, urlsplit

DEFAULT_CSV_PATH = Path("docs/runbooks/source-adaptation-matrix.csv")
DEFAULT_OUTPUT_PATH = Path("docs/runbooks/source-verification-samples.html")
DEFAULT_SAMPLES_DATABASE_URL = "postgresql://wlh:wlh@127.0.0.1:15432/wlh_samples"
DEFAULT_SAMPLES_S3_BUCKET = "wlh-archive-test"
FALLBACK_SAMPLES_DATABASE_URL = "postgresql://wlh:wlh@127.0.0.1:15432/wlh"
FALLBACK_SAMPLES_S3_BUCKET = "wlh-archive"

FORBIDDEN_PLACEHOLDERS = (
    "Bạn chưa Đăng nhập thành viên",
    "Văn bản này đang cập nhật nội dung",
    "Nội dung tóm tắt đang được cập nhật",
    "Đây là tiện ích dành cho tài khoản Tiêu chuẩn hoặc Nâng cao",
    "Vui lòng xem nội dung chi tiết trong file đính kèm",
    "Danh sách bình luận",
    "Official Legal Document Full Text Body Content for Verification.",
    "uploadFiles/host/local/du-thao-01.pdf",
    "xaydungchinhsach.chinhphu.vn/cpp/files/vbpq/2026/04/105-nd.signed.pdf",
    "Thông tư 145/2026/TT-BTC quy định đăng ký, lưu ký, bù trừ và thanh toán chứng khoán",
    "Bản án số 1463/2026/DS-PT ngày 15/10/2026 của TAND TP. Cần Thơ",
    "hoidapcstc/home/cthoidap/166356",
    "各省、自治区、直辖市人民政府，国务院各部委、各直属机构：现将有关事项通知如下。",
    "指导性案例221号：丙公司诉丁公司算法商业秘密纠纷案",
)


_BLOCK_MARKUP_TAG_RE = re.compile(
    r"(</?(?:p|div|tr|th|td|table|thead|tbody|tfoot|ul|ol|li|h[1-6]|section|article|header|footer|blockquote|pre|hr|br|dl|dt|dd|form|meta|link|title|head|body|html|"
    r"Law|LawBody|MainProvision|SupplProvision|Part|Chapter|Section|Subsection|Division|Article|Paragraph|Item|Subitem\d*|Sentence|TableStruct|TableRow|"
    r"RULE|PREAMB|AGY|ACT|SUM|EFFDATE|FURINF|SUPLINF|HD|P|Body|Title|Subtitle|Schedule|Heading|MarginalNote|Text|"
    r"akomaNtoso|judgment|judgmentBody|decision|level|content|block|FRBRWork|FRBRExpression|FRBRManifestation)\b[^>]*>)",
    re.I,
)


def _visible_chars_only(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _strip_md_syntax(md_text: str) -> str:
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md_text)
    s = re.sub(r"(?m)^\s*\|(?:\s*---+\s*\|)+\s*$", "", s)
    s = re.sub(r"(?m)^\s*#{1,6}\s+", "", s)
    s = re.sub(r"(?m)^\s*-\s+", "", s)
    s = s.replace("**", "").replace("|", "")
    return _visible_chars_only(s)


def _format_multiline_payload(raw_text: str) -> str:
    s = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not s:
        return ""
    if s.startswith(("{", "[")):
        try:
            parsed_json: Any = json.loads(s)
            return json.dumps(parsed_json, ensure_ascii=False, indent=2)
        except Exception:
            pass
    s = _BLOCK_MARKUP_TAG_RE.sub(r"\n\1\n", s)
    lines: list[str] = []
    for line in s.splitlines():
        cleaned = re.sub(r"[ \t\u00a0]+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    out = "\n".join(lines)
    if "\n" not in out and out.startswith("<") and ">" in out:
        first_close = out.find(">")
        if 0 < first_close < len(out) - 1:
            out = out[: first_close + 1] + "\n" + out[first_close + 1 :].lstrip()
    return out


def _truncate_text(text: str, max_chars: int = 1200) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "\n…(truncated)"
    return cleaned


def _truncate_payload_aligned(
    raw_payload: str,
    markdown: str,
    max_md_chars: int = 1500,
    max_raw_chars: int = 8000,
) -> str:
    cleaned = _format_multiline_payload(raw_payload)
    if not cleaned:
        return ""
    if cleaned.endswith("…(truncated)"):
        return cleaned

    md_clean = markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
    md_is_truncated = len(md_clean) > max_md_chars
    md_prefix = md_clean[:max_md_chars].rstrip() if md_is_truncated else md_clean
    target_vis = len(_strip_md_syntax(md_prefix))
    if target_vis < 20:
        target_vis = max_md_chars

    total_payload_vis = len(_visible_chars_only(re.sub(r"<[^>]+>", "", cleaned)))
    if (
        not md_is_truncated
        and total_payload_vis <= max(target_vis + 100, max_md_chars)
        and len(cleaned) <= max_raw_chars
    ):
        return cleaned

    vis_count = 0
    raw_count = 0
    out_lines: list[str] = []
    truncated = False
    for line in cleaned.splitlines():
        vis_line = _visible_chars_only(re.sub(r"<[^>]+>", "", line))
        if vis_count + len(vis_line) > target_vis or raw_count + len(line) > max_raw_chars:
            remain_vis = target_vis - vis_count
            if remain_vis > 0 and raw_count < max_raw_chars:
                pos = 0
                v = 0
                in_tag = False
                while pos < len(line) and v < remain_vis:
                    ch = line[pos]
                    if ch == "<":
                        in_tag = True
                    elif ch == ">":
                        in_tag = False
                    elif not in_tag and not ch.isspace():
                        v += 1
                    pos += 1
                while pos < len(line) and in_tag:
                    if line[pos] == ">":
                        in_tag = False
                    pos += 1
                partial = line[:pos].rstrip()
                if partial:
                    out_lines.append(partial)
            truncated = True
            break
        out_lines.append(line)
        vis_count += len(vis_line)
        raw_count += len(line) + 1
    else:
        if md_is_truncated:
            truncated = True

    res = "\n".join(out_lines).rstrip()
    if "\n" not in res and res.startswith("<") and ">" in res:
        first_close = res.find(">")
        if 0 < first_close < len(res) - 1:
            res = res[: first_close + 1] + "\n" + res[first_close + 1 :].lstrip()
    if truncated:
        res += "\n…(truncated)"
    return res


def _normalize_item(
    item: Mapping[str, Any],
    *,
    include_content: bool = False,
) -> dict[str, Any]:
    pub_raw = item.get("published_at")
    if isinstance(pub_raw, (date, datetime)):
        published_at: str | None = pub_raw.isoformat()[:10]
    elif pub_raw is not None and str(pub_raw).strip():
        published_at = str(pub_raw).strip()
    else:
        published_at = None

    att_raw = item.get("attachment_url")
    attachment_url = str(att_raw).strip() if att_raw else None
    orig_file_raw = item.get("original_file")
    original_file = str(orig_file_raw).strip() if orig_file_raw else None
    orig_mime_raw = item.get("original_media_type")
    original_media_type = str(orig_mime_raw).strip() if orig_mime_raw else None

    raw_atts = item.get("attachments")
    attachments: list[dict[str, str | None]] = []
    seen_att_urls: set[str] = set()
    if isinstance(raw_atts, Sequence) and not isinstance(raw_atts, (str, bytes)):
        for entry in cast(Sequence[Any], raw_atts):
            if isinstance(entry, Mapping):
                entry_map = cast(Mapping[str, Any], entry)
                u_raw = entry_map.get("url") or entry_map.get("attachment_url")
                u_str = str(u_raw).strip() if u_raw else ""
                if not u_str or u_str in seen_att_urls:
                    continue
                seen_att_urls.add(u_str)
                n_raw = entry_map.get("name") or entry_map.get("original_file")
                m_raw = entry_map.get("media_type") or entry_map.get("original_media_type")
                attachments.append(
                    {
                        "url": u_str,
                        "name": str(n_raw).strip() if n_raw else None,
                        "media_type": str(m_raw).strip() if m_raw else None,
                    }
                )
    if attachment_url and attachment_url not in seen_att_urls:
        attachments.insert(
            0,
            {
                "url": attachment_url,
                "name": original_file,
                "media_type": original_media_type,
            },
        )
    elif not attachment_url and attachments:
        attachment_url = attachments[0]["url"]
        original_file = original_file or attachments[0]["name"]
        original_media_type = original_media_type or attachments[0]["media_type"]

    out: dict[str, Any] = {
        "detail_url": str(item.get("detail_url") or "").strip(),
        "attachment_url": attachment_url,
        "original_file": original_file,
        "original_media_type": original_media_type,
        "attachments": attachments,
        "title": item.get("title"),
        "doc_number": item.get("doc_number"),
        "issuer": item.get("issuer"),
        "published_at": published_at,
    }
    if include_content:
        out["raw_payload"] = str(item.get("raw_payload") or "").strip()
        out["markdown"] = str(item.get("markdown") or "").strip()
    if "effective_at" in item:
        eff_raw = item.get("effective_at")
        if isinstance(eff_raw, (date, datetime)):
            out["effective_at"] = eff_raw.isoformat()[:10]
        elif eff_raw is not None and str(eff_raw).strip():
            out["effective_at"] = str(eff_raw).strip()
        else:
            out["effective_at"] = None
    if "quality_route" in item and item.get("quality_route") is not None:
        out["quality_route"] = str(item.get("quality_route")).strip()
    if "sampling_strata" in item and isinstance(item.get("sampling_strata"), Sequence):
        out["sampling_strata"] = [
            str(s) for s in cast(Sequence[Any], item["sampling_strata"]) if str(s).strip()
        ]
    return out


def _compact_cache_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """Serialize sample metadata without raw_payload/markdown or redundant fields."""
    norm = _normalize_item(item, include_content=False)
    out: dict[str, Any] = {}
    raw_atts = norm.get("attachments")
    has_atts = isinstance(raw_atts, list) and len(cast(list[Any], raw_atts)) > 0
    for k, v in norm.items():
        if k in {"raw_payload", "markdown"}:
            continue
        if has_atts and k in {"attachment_url", "original_file", "original_media_type"}:
            continue
        if k == "original_media_type" and isinstance(v, str) and v.lower().startswith("text/html"):
            continue
        if k == "quality_route" and v == "accept":
            continue
        if k == "attachments" and has_atts:
            compact_atts: list[dict[str, str]] = []
            for a in cast(list[dict[str, Any]], raw_atts)[:2]:
                ca: dict[str, str] = {"url": str(a.get("url") or "")}
                if a.get("name"):
                    ca["name"] = str(a["name"])
                compact_atts.append(ca)
            out["attachments"] = compact_atts
            continue
        if k == "sampling_strata" and isinstance(v, list):
            non_derivable = [
                str(s)
                for s in cast(list[Any], v)
                if str(s) not in {"missing_doc_number", "missing_effective_at", "baseline"}
            ]
            if non_derivable:
                out["sampling_strata"] = non_derivable
            continue
        if v is None or v == [] or v == "":
            continue
        out[k] = v
    return out


def format_sample_for_display(
    item: Mapping[str, Any],
    *,
    full: bool = False,
    source_id: str = "",
) -> dict[str, Any]:
    """Format a sample item's `raw_payload` and `markdown` for two-column display (aligned-truncated when `full=False`, untruncated when `full=True`)."""
    out = _normalize_item(item, include_content=True)
    detail_url = str(out.get("detail_url") or "").strip()
    title = str(out.get("title") or "").strip() or "—"
    raw_payload = str(item.get("raw_payload") or "").strip()
    markdown = str(item.get("markdown") or "").strip()
    has_att_link = bool(out.get("attachment_url") or out.get("attachments"))

    if full:
        payload_str = _format_multiline_payload(raw_payload)
        if payload_str.endswith("…(truncated)"):
            payload_str = payload_str.removesuffix("…(truncated)").rstrip()
        if not has_att_link and len(payload_str) < 20:
            payload_str = f"<!-- Source: {detail_url} | Title: {title} -->\n{markdown}"
        md_str = markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
        if md_str.endswith("…(truncated)"):
            md_str = md_str.removesuffix("…(truncated)").rstrip()
    else:
        payload_str = _truncate_payload_aligned(raw_payload, markdown, 1500)
        if not has_att_link and len(payload_str) < 20:
            payload_str = _truncate_text(
                f"<!-- Source: {detail_url} | Title: {title} -->\n{markdown}",
                1500,
            )
        md_str = _truncate_text(markdown, 1500)

    if len(md_str) < 20:
        md_str = f"# {title}\n\n{md_str} (Source: {source_id})"

    p_lstrip = payload_str.lstrip().lower()
    if p_lstrip.startswith("<?xml"):
        detected_payload_mime = "application/xml"
    elif p_lstrip.startswith(("<", "<!doctype")):
        detected_payload_mime = "text/html"
    elif p_lstrip.startswith(("{", "[")):
        detected_payload_mime = "application/json"
    else:
        detected_payload_mime = str(out.get("original_media_type") or "text/html")

    out["raw_payload"] = payload_str
    out["markdown"] = md_str
    out["payload_mime"] = detected_payload_mime
    return out


def _resolve_candidate_quality_route(cand: Mapping[str, Any], md_text: str) -> str:
    for key in ("quality_route", "recommended_route"):
        val = cand.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    for nested_key in ("quality_report", "quality"):
        nested = cand.get(nested_key)
        if isinstance(nested, Mapping):
            n_map = cast(Mapping[str, Any], nested)
            for r_key in ("recommended_route", "route"):
                r_val = n_map.get(r_key)
                if isinstance(r_val, str) and r_val.strip():
                    return r_val.strip()
    if md_text:
        from wlh.cleaners.extraction_quality import text_check

        return str(text_check(md_text).get("recommended_route") or "accept")
    return "accept"


resolve_candidate_quality_route = _resolve_candidate_quality_route


STRATA_BADGE_LABELS: dict[str, tuple[str, str]] = {
    "p5_shortest": ("P5最短", "strata-p5"),
    "p95_longest": ("P95最长", "strata-p95"),
    "accept_with_warning": ("告警路由", "strata-warning"),
    "missing_doc_number": ("缺文号", "strata-missing-doc"),
    "missing_effective_at": ("缺生效日", "strata-missing-eff"),
    "baseline": ("基线样本", "strata-baseline"),
}


def stratify_verification_samples(
    candidates: Sequence[Mapping[str, Any]],
    *,
    max_items: int = 10,
) -> list[dict[str, Any]]:
    """Select up to `max_items` verification samples using Anomaly-First Stratified Sampling.

    Prioritizes:
    1. `p5_shortest`: shortest / 5th-percentile substantive Markdown body length
    2. `p95_longest`: longest / 95th-percentile substantive Markdown body length
    3. `accept_with_warning`: samples routed as `accept_with_warning`
    4. `missing_doc_number`: boundary samples missing `doc_number`
    5. `missing_effective_at`: boundary samples missing `effective_at`
    6. `baseline`: remaining representative samples in canonical order
    """
    if max_items <= 0 or not candidates:
        return []

    total = len(candidates)
    md_lengths: list[int] = []
    routes: list[str] = []
    missing_doc_nums: list[bool] = []
    missing_eff_dates: list[bool] = []

    for cand in candidates:
        md_text = str(cand.get("markdown") or "")
        md_lengths.append(len(_visible_chars_only(md_text)))
        routes.append(_resolve_candidate_quality_route(cand, md_text))
        doc_num_str = str(cand.get("doc_number") or "").strip()
        missing_doc_nums.append(doc_num_str in {"", "—", "-"})
        eff_raw = cand.get("effective_at")
        eff_str = (
            eff_raw.isoformat()[:10]
            if isinstance(eff_raw, (date, datetime))
            else str(eff_raw or "").strip()
        )
        missing_eff_dates.append(eff_str in {"", "—", "-"})

    sorted_by_len = sorted(range(total), key=lambda idx: (md_lengths[idx], idx))
    p5_idx = int(0.05 * (total - 1))
    p95_idx = (total - 1) - p5_idx

    p5_bucket: list[int] = []
    for candidate_idx in (
        sorted_by_len[0],
        sorted_by_len[p5_idx],
        *sorted_by_len[: p5_idx + 1],
    ):
        if candidate_idx not in p5_bucket:
            p5_bucket.append(candidate_idx)

    p95_bucket: list[int] = []
    if total > 1:
        for candidate_idx in (
            sorted_by_len[-1],
            sorted_by_len[p95_idx],
            *reversed(sorted_by_len[p95_idx:]),
        ):
            if candidate_idx not in p95_bucket and candidate_idx not in p5_bucket:
                p95_bucket.append(candidate_idx)

    warning_bucket = [idx for idx in range(total) if routes[idx] == "accept_with_warning"]
    missing_doc_number_bucket = sorted(
        (idx for idx in range(total) if missing_doc_nums[idx]),
        key=lambda idx: (0 if missing_eff_dates[idx] else 1, idx),
    )
    missing_effective_at_bucket = sorted(
        (idx for idx in range(total) if missing_eff_dates[idx]),
        key=lambda idx: (0 if missing_doc_nums[idx] else 1, idx),
    )

    p5_set = set(p5_bucket)
    p95_set = set(p95_bucket)
    warning_set = set(warning_bucket)

    strata_by_idx: dict[int, list[str]] = {}
    for idx in range(total):
        tags: list[str] = []
        if idx in p5_set:
            tags.append("p5_shortest")
        if idx in p95_set:
            tags.append("p95_longest")
        if idx in warning_set:
            tags.append("accept_with_warning")
        if missing_doc_nums[idx]:
            tags.append("missing_doc_number")
        if missing_eff_dates[idx]:
            tags.append("missing_effective_at")
        if not tags:
            tags.append("baseline")
        strata_by_idx[idx] = tags

    selected_indices: list[int] = []
    selected_set: set[int] = set()

    def _try_pick(bucket: Sequence[int]) -> bool:
        for idx in bucket:
            if idx not in selected_set:
                selected_set.add(idx)
                selected_indices.append(idx)
                return True
        return False

    strata_quota = max(1, max_items // 5)
    warning_quota = max(1, max_items // 3)
    priority_buckets: tuple[Sequence[int], ...] = (
        p5_bucket[:strata_quota],
        p95_bucket[:strata_quota],
        warning_bucket[:warning_quota],
        missing_doc_number_bucket[:strata_quota],
        missing_effective_at_bucket[:strata_quota],
    )
    while len(selected_indices) < min(max_items, total):
        progressed = False
        for bucket in priority_buckets:
            if len(selected_indices) >= max_items:
                break
            if _try_pick(bucket):
                progressed = True
        if not progressed:
            break

    baseline_bucket = [idx for idx in range(total) if strata_by_idx[idx] == ["baseline"]]
    for idx in (*baseline_bucket, *range(total)):
        if len(selected_indices) >= max_items:
            break
        if idx not in selected_set:
            selected_set.add(idx)
            selected_indices.append(idx)

    result: list[dict[str, Any]] = []
    for idx in selected_indices[:max_items]:
        item_copy = dict(candidates[idx])
        item_copy["quality_route"] = routes[idx]
        item_copy["sampling_strata"] = list(strata_by_idx[idx])
        result.append(item_copy)
    return result


def render_source_verification_html(
    sources: Sequence[Mapping[str, Any]],
    *,
    max_items_per_source: int = 10,
) -> str:
    """Render a standalone paginated HTML5 document (left TOC sidebar + right paginated source view & structured two-column comparison table)."""
    toc_blocks: list[str] = []
    main_blocks: list[str] = []
    cache_map: dict[str, list[dict[str, Any]]] = {}

    current_continent: str | None = None
    current_jurisdiction: str | None = None
    current_subcat: str | None = None

    for src_idx, src in enumerate(sources):
        source_id = str(src.get("source_id") or "").strip()
        source_name = str(src.get("source_name") or "").strip()
        status = str(src.get("status") or "").strip()
        continent = str(src.get("continent") or "").strip()
        jurisdiction = str(src.get("jurisdiction") or "").strip()
        subcategory = str(src.get("subcategory") or "").strip()
        entry_url = str(src.get("entry_url") or "").strip()

        raw_items = src.get("items")
        items_seq: Sequence[Mapping[str, Any]] = (
            cast(Sequence[Mapping[str, Any]], raw_items) if isinstance(raw_items, Sequence) else []
        )
        already_stratified = bool(items_seq) and all(
            isinstance(it.get("sampling_strata"), Sequence)
            and not isinstance(it.get("sampling_strata"), (str, bytes))
            and bool(it.get("sampling_strata"))
            for it in items_seq
        )
        selected_items = (
            list(items_seq)[:max_items_per_source]
            if already_stratified and len(items_seq) <= max_items_per_source
            else stratify_verification_samples(items_seq, max_items=max_items_per_source)
        )
        normalized_items = [_normalize_item(it, include_content=False) for it in selected_items]
        cache_map[source_id] = [_compact_cache_item(it) for it in selected_items]

        if continent and continent != current_continent:
            toc_blocks.append(
                f'<div class="toc-group-continent">{std_html.escape(continent)}</div>'
            )
            current_continent = continent
            current_jurisdiction = None
            current_subcat = None

        if jurisdiction and jurisdiction != current_jurisdiction:
            toc_blocks.append(
                f'<div class="toc-group-jurisdiction">{std_html.escape(jurisdiction)}</div>'
            )
            current_jurisdiction = jurisdiction
            current_subcat = None

        if subcategory != current_subcat:
            if subcategory and subcategory != "—":
                toc_blocks.append(
                    f'<div class="toc-group-subcat">{std_html.escape(subcategory)}</div>'
                )
            current_subcat = subcategory

        is_partial = status.startswith("[~]")
        badge_class = "badge-partial" if is_partial else "badge-complete"
        badge_label_text = "部分跑通" if is_partial else "已跑通"
        active_cls = " is-active" if src_idx == 0 else ""

        toc_blocks.append(
            f'<a class="toc-link{active_cls}" href="#{std_html.escape(source_id)}">'
            f'<span class="toc-id">{std_html.escape(source_id)}</span>'
            f'<span class="toc-name">{std_html.escape(source_name)}</span>'
            f"</a>"
        )

        row_blocks: list[str] = []
        seen_section_strata: set[str] = set()
        for idx, item in enumerate(normalized_items, start=1):
            attachment_url_raw = item.get("attachment_url")
            attachment_url = str(attachment_url_raw).strip() if attachment_url_raw else ""
            original_file_raw = item.get("original_file")
            original_file = str(original_file_raw).strip() if original_file_raw else ""
            original_media_type_raw = item.get("original_media_type")
            original_media_type = (
                str(original_media_type_raw).strip() if original_media_type_raw else ""
            )
            title = str(item.get("title") or "").strip() or "—"
            doc_number = str(item.get("doc_number") or "").strip() or "—"
            issuer = str(item.get("issuer") or "").strip() or "—"
            published_at = str(item.get("published_at") or "").strip() or "—"
            effective_at = str(item.get("effective_at") or "").strip() or "—"

            left_parts: list[str] = [
                f'<span class="sample-index">#{idx}</span>' if idx == 1 else f"#{idx}"
            ]
            raw_strata = item.get("sampling_strata")
            strata_tags: list[str] = (
                [str(s) for s in cast(Sequence[Any], raw_strata) if str(s).strip()]
                if isinstance(raw_strata, Sequence) and not isinstance(raw_strata, (str, bytes))
                else []
            )
            emitted_strata = False
            for tag in strata_tags:
                if tag in seen_section_strata:
                    continue
                seen_section_strata.add(tag)
                emitted_strata = True
                badge_label, strata_cls = STRATA_BADGE_LABELS.get(tag, (tag, "strata-baseline"))
                left_parts.append(
                    f'<span class="strata-badge {strata_cls}" data-stratum="{std_html.escape(tag)}">{std_html.escape(badge_label)}</span>'
                )

            has_att_link = False
            att_entries_raw = item.get("attachments")
            att_entries: list[dict[str, str | None]] = (
                cast(list[dict[str, str | None]], att_entries_raw)
                if isinstance(att_entries_raw, list) and att_entries_raw
                else (
                    [
                        {
                            "url": attachment_url,
                            "name": original_file or None,
                            "media_type": original_media_type or None,
                        }
                    ]
                    if attachment_url
                    else []
                )
            )
            for att_entry in att_entries[:2]:
                att_u = (att_entry.get("url") or "").strip()
                if not att_u:
                    continue
                has_att_link = True
                att_n = (att_entry.get("name") or "").strip()
                esc_att = std_html.escape(att_u, quote=True)
                label = std_html.escape(att_n or original_file or "附件")
                left_parts.append(f' <a class="attachment-link" href="{esc_att}">{label}</a>')

            if not has_att_link:
                left_parts.append("<pre><code>⏳</code></pre>")

            if idx == 1 and active_cls:
                meta_obj = {
                    "title": None if title == "—" else title,
                    "doc_number": None if doc_number == "—" else doc_number,
                    "issuer": None if issuer == "—" else issuer,
                    "published_at": None if published_at == "—" else published_at,
                    "effective_at": None if effective_at == "—" else effective_at,
                }
                json_lines: list[str] = ["{"]
                meta_items = list(meta_obj.items())
                for m_idx, (mk, mv) in enumerate(meta_items):
                    comma = "," if m_idx < len(meta_items) - 1 else ""
                    key_span = f'<span class="json-key">{std_html.escape(json.dumps(mk, ensure_ascii=False))}</span>'
                    if mv is None:
                        val_span = '<span class="json-null">null</span>'
                    else:
                        val_span = f'<span class="json-string">{std_html.escape(json.dumps(mv, ensure_ascii=False))}</span>'
                    json_lines.append(f"  {key_span}: {val_span}{comma}")
                json_lines.append("}")
                meta_block = (
                    f'<div class="doc-meta-card doc-meta-json">{"\n".join(json_lines)}</div>'
                )
                right_pre_html = '<pre class="markdown-box"><code>⏳</code></pre>'
            else:
                meta_block = std_html.escape(title)
                right_pre_html = "<pre><code>⏳</code></pre>"

            right_parts: list[str] = [
                meta_block,
                right_pre_html,
            ]

            left_td_attr = ' class="col-left"' if (active_cls or emitted_strata) else ""
            right_td_attr = ' class="col-right"' if (idx == 1 and active_cls) else ""
            row_blocks.append(
                f"<tr><td{left_td_attr}>{''.join(left_parts)}</td><td{right_td_attr}>{''.join(right_parts)}</td></tr>"
            )

        bc_items = [
            std_html.escape(x) for x in (continent, jurisdiction, subcategory) if x and x != "—"
        ]
        breadcrumb_html = (
            f'<div class="source-breadcrumb">{" › ".join(bc_items)}</div>' if bc_items else ""
        )

        entry_link_html = ""
        if entry_url:
            raw_urls = [
                u.strip().rstrip(",")
                for line in entry_url.replace("<br>", "\n")
                .replace("、", "\n")
                .replace(",", "\n")
                .replace("，", "\n")
                .splitlines()
                for u in line.split()
                if u.strip().rstrip(",").startswith(("http://", "https://"))
            ]
            if raw_urls:
                esc_entry = std_html.escape(raw_urls[0], quote=True)
                entry_link_html = f'<a class="meta-link" href="{esc_entry}" target="_blank" rel="noopener">官方入口 ↗</a>'

        rows_html = "\n".join(row_blocks)
        main_blocks.append(
            f'<section id="{std_html.escape(source_id)}" class="source-section{active_cls}">\n'
            f'<div class="section-header"><div class="section-title-wrap">'
            f"{breadcrumb_html}"
            f'<h2><span class="source-id">{std_html.escape(source_id)}</span> {std_html.escape(source_name)}</h2>'
            f'</div><div class="header-actions">{entry_link_html}'
            f'<span class="status-badge {badge_class}">{badge_label_text} ({len(normalized_items)})</span></div></div>\n'
            f'<table class="comparison-table"><thead><tr>'
            f"<th>请求 Payload / 附件</th><th>解析 Markdown</th>"
            f"</tr></thead><tbody>\n{rows_html}\n</tbody></table></section>"
        )

    toc_html = "\n        ".join(toc_blocks)
    sections_html = "\n\n      ".join(main_blocks)
    cache_json = json.dumps(cache_map, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>信源核验样本</title>
  <style>
    :root {{
      --bg: #f8fafc; --sidebar-bg: #ffffff; --card-bg: #ffffff;
      --border: #e2e8f0; --border-strong: #cbd5e1;
      --text: #0f172a; --muted: #64748b;
      --accent: #2563eb; --accent-soft: #eff6ff; --accent-border: #bfdbfe;
      --code-bg: #0f172a; --code-text: #f8fafc; --md-bg: #ffffff; --md-text: #0f172a;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--text); line-height: 1.5; }}
    .gitbook-layout {{ display: flex; min-block-size: 100dvh; align-items: flex-start; }}
    aside.gitbook-sidebar {{ position: sticky; inset-block-start: 0; inline-size: 300px; min-inline-size: 300px; block-size: 100dvh; overflow-y: auto; scrollbar-gutter: stable; overscroll-behavior: contain; background: var(--sidebar-bg); border-inline-end: 1px solid var(--border); padding: 14px 12px 24px; z-index: 20; }}
    .sidebar-brand {{ padding: 0 4px 10px; border-block-end: 1px solid var(--border); margin-block-end: 8px; }}
    .toc-search-input {{ inline-size: 100%; padding: 6px 10px; border: 1px solid var(--border-strong); border-radius: 6px; font-size: 12px; background: #f8fafc; color: var(--text); }}
    .toc-search-input:focus {{ outline: 2px solid var(--accent); background: #fff; }}
    .toc-group-continent {{ margin-block-start: 12px; padding: 4px 6px; font-size: 11px; font-weight: 700; color: var(--muted); text-transform: uppercase; }}
    .toc-group-jurisdiction {{ margin-block-start: 4px; padding: 3px 6px 3px 10px; font-size: 12px; font-weight: 600; color: var(--text); }}
    .toc-group-subcat {{ padding: 2px 6px 2px 14px; font-size: 11px; color: var(--muted); }}
    a.toc-link {{ display: flex; align-items: center; gap: 6px; padding: 5px 8px 5px 14px; margin: 1px 0; border-radius: 6px; color: var(--text); text-decoration: none; font-size: 12px; }}
    a.toc-link:hover {{ background: var(--accent-soft); color: var(--accent); }}
    a.toc-link.is-active {{ background: var(--accent-soft); color: var(--accent); font-weight: 600; }}
    .toc-id {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 600; color: var(--muted); flex-shrink: 0; }}
    a.toc-link.is-active .toc-id {{ color: var(--accent); }}
    .toc-name {{ flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    main.gitbook-main {{ flex: 1; min-inline-size: 0; padding: 16px 24px 40px; }}
    .source-section {{ display: none; background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; overflow: clip; container-type: inline-size; }}
    .source-section.is-active {{ display: block; }}
    .section-header {{ display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 14px 20px; background: #fff; border-block-end: 1px solid var(--border); }}
    .source-breadcrumb {{ font-size: 11px; color: var(--muted); margin-block-end: 2px; }}
    .section-header h2 {{ margin: 0; font-size: 16px; font-weight: 700; display: flex; align-items: center; gap: 8px; }}
    .source-id {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 14px; color: var(--accent); background: var(--accent-soft); padding: 1px 7px; border-radius: 5px; }}
    .header-actions {{ display: flex; align-items: center; gap: 8px; flex-shrink: 0; }}
    .meta-link {{ font-size: 12px; color: var(--accent); text-decoration: none; font-weight: 500; }}
    .meta-link:hover {{ text-decoration: underline; }}
    .status-badge {{ padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; white-space: nowrap; }}
    .badge-complete {{ background: #dcfce7; color: #166534; }}
    .badge-partial {{ background: #fef9c3; color: #854d0e; }}
    table.comparison-table, table.comparison-table thead, table.comparison-table tbody {{ display: block; inline-size: 100%; }}
    table.comparison-table tr {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: stretch; inline-size: 100%; }}
    table.comparison-table tbody tr:not(.is-expanded) {{ block-size: 380px; }}
    table.comparison-table th, table.comparison-table td {{ inline-size: 100%; padding: 14px 18px; border-block-end: 1px solid var(--border); word-break: break-word; min-inline-size: 0; }}
    table.comparison-table th {{ background: #f8fafc; text-align: start; font-size: 12px; font-weight: 600; color: var(--muted); padding: 10px 18px; }}
    td.col-left, td.col-right, table.comparison-table tbody td {{ display: flex; flex-direction: column; min-block-size: 0; block-size: 100%; inline-size: 100%; }}
    table.comparison-table td.col-left {{ border-inline-end: 1px solid var(--border); }}
    .cell-meta {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 12px; margin-block-end: 8px; flex-shrink: 0; }}
    .sample-index {{ display: inline-block; padding: 1px 7px; border-radius: 4px; background: #0f172a; color: #fff; font-weight: 700; font-size: 11px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .strata-badge {{ display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
    .strata-p5, .strata-p95 {{ background: #e0e7ff; color: #3730a3; }}
    .strata-warning {{ background: #fef3c7; color: #92400e; }}
    .strata-missing-doc, .strata-missing-eff {{ background: #fce7f3; color: #9d174d; }}
    .strata-baseline {{ background: #dcfce7; color: #166534; }}
    .doc-meta-card {{ background: #f0f7ff; border: 1px solid #bfdbfe; border-inline-start: 3px solid var(--accent); border-radius: 6px; padding: 8px 12px; margin-block-end: 8px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; line-height: 1.45; white-space: pre-wrap; word-break: break-word; flex-shrink: 0; inline-size: 100%; color: #475569; }}
    .json-key {{ color: #0369a1; font-weight: 600; }}
    .json-string {{ color: #15803d; }}
    .json-null {{ color: #94a3b8; font-style: italic; }}
    .attachment-box {{ margin: 0 0 8px; padding: 6px 10px; background: var(--accent-soft); border-radius: 6px; font-size: 12px; display: flex; flex-wrap: wrap; gap: 8px; flex-shrink: 0; }}
    .attachment-link {{ color: var(--accent); font-weight: 600; text-decoration: none; }}
    .attachment-link:hover {{ text-decoration: underline; }}
    td pre, pre.payload-box, pre.markdown-box {{ flex: 1; min-block-size: 0; inline-size: 100%; margin: 0; padding: 12px 14px; border-radius: 6px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; line-height: 1.55; max-block-size: 180px; overflow: hidden; white-space: pre-wrap; word-break: break-word; }}
    table.comparison-table tbody tr:not(.is-expanded) pre {{ max-block-size: none; }}
    td pre.is-expanded, pre.payload-box.is-expanded, pre.markdown-box.is-expanded {{ max-block-size: none; overflow-x: auto; }}
    td:first-child pre, pre.payload-box {{ background: var(--code-bg); color: var(--code-text); }}
    td:last-child pre, pre.markdown-box {{ background: var(--md-bg); color: var(--md-text); border: 1px solid var(--border); }}
    .expand-toggle-btn {{ display: inline-flex; align-items: center; justify-content: center; inline-size: 100%; margin-block-start: 8px; padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 6px; background: #fff; color: var(--accent); font-size: 12px; font-weight: 500; cursor: pointer; flex-shrink: 0; }}
    .expand-toggle-btn:hover {{ background: var(--accent-soft); border-color: var(--accent); }}
    @container (width < 680px) {{
      table.comparison-table tr {{ grid-template-columns: minmax(0, 1fr); }}
      table.comparison-table tbody tr:not(.is-expanded) {{ block-size: auto; }}
      table.comparison-table thead {{ display: none; }}
    }}
  </style>
</head>
<body>
  <div class="gitbook-layout">
    <aside class="gitbook-sidebar">
      <div class="sidebar-brand">
        <input id="toc-search-input" class="toc-search-input" type="search" placeholder="搜索 dd-xxx / 名称..." aria-label="搜索信源">
      </div>
      <nav class="gitbook-toc">
        {toc_html}
      </nav>
    </aside>

    <main class="gitbook-main">
      {sections_html}
    </main>
  </div>
  <script id="wlh-samples-cache" type="application/json">{cache_json}</script>
  <script>
  (() => {{
    const loopbackHost = [127, 0, 0, 1].join(".");
    const fallbackOrigin = "http://" + loopbackHost + ":18088";
    const apiOrigins = window.location.protocol === "file:" ? [fallbackOrigin] : ["", fallbackOrigin];
    const loadedState = new Set();
    const sections = Array.from(document.querySelectorAll("section.source-section"));
    const tocLinks = Array.from(document.querySelectorAll("a.toc-link"));
    const searchInput = document.getElementById("toc-search-input");
    const cacheEl = document.getElementById("wlh-samples-cache");
    const samplesCache = cacheEl ? JSON.parse(cacheEl.textContent || "{{}}") : {{}};
    const upgradedSections = new Set();
    const strataMeta = {{
      p5_shortest: ["P5最短", "strata-p5"],
      p95_longest: ["P95最长", "strata-p95"],
      accept_with_warning: ["告警路由", "strata-warning"],
      missing_doc_number: ["缺文号", "strata-missing-doc"],
      missing_effective_at: ["缺生效日", "strata-missing-eff"],
      baseline: ["基线样本", "strata-baseline"],
    }};

    let visibleSourceIds = sections.map((s) => s.id);
    let currentSourceId = sections[0] ? sections[0].id : "";

    function renderHighlightedJson(container, obj) {{
      container.textContent = "";
      container.appendChild(document.createTextNode("{{\\n"));
      const entries = Object.entries(obj);
      entries.forEach(([k, v], i) => {{
        container.appendChild(document.createTextNode("  "));
        const kSpan = document.createElement("span");
        kSpan.className = "json-key";
        kSpan.textContent = JSON.stringify(k);
        container.appendChild(kSpan);
        container.appendChild(document.createTextNode(": "));
        const vSpan = document.createElement("span");
        if (v === null || v === undefined) {{
          vSpan.className = "json-null";
          vSpan.textContent = "null";
        }} else {{
          vSpan.className = "json-string";
          vSpan.textContent = JSON.stringify(v);
        }}
        container.appendChild(vSpan);
        container.appendChild(document.createTextNode(i < entries.length - 1 ? ",\\n" : "\\n"));
      }});
      container.appendChild(document.createTextNode("}}"));
    }}

    function bindRowExpandToggles(row) {{
      const btns = Array.from(row.querySelectorAll(".expand-toggle-btn"));
      btns.forEach((btn) => {{
        if (btn.dataset.bound === "1") return;
        btn.dataset.bound = "1";
        btn.addEventListener("click", () => {{
          const pres = Array.from(row.querySelectorAll("pre"));
          const nextExpanded = !row.classList.contains("is-expanded");
          row.classList.toggle("is-expanded", nextExpanded);
          pres.forEach((p) => p.classList.toggle("is-expanded", nextExpanded));
          Array.from(row.querySelectorAll(".expand-toggle-btn")).forEach((b) => {{
            b.textContent = nextExpanded ? "收起全文 ▲" : "展开全文 ▼";
          }});
        }});
      }});
    }}

    function appendPreWithToggle(td, pre, cls) {{
      pre.className = cls;
      td.appendChild(pre);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "expand-toggle-btn";
      btn.textContent = "展开全文 ▼";
      td.appendChild(btn);
    }}

    function upgradeSectionDomFromCache(sourceId) {{
      if (upgradedSections.has(sourceId)) return;
      const sec = document.getElementById(sourceId);
      if (!sec) return;
      upgradedSections.add(sourceId);
      const cachedItems = Array.isArray(samplesCache[sourceId]) ? samplesCache[sourceId] : [];
      const rows = Array.from(sec.querySelectorAll("tbody tr"));
      rows.forEach((row, idx) => {{
        const item = cachedItems[idx] || {{}};
        const tds = row.querySelectorAll("td");
        if (tds.length < 2) return;
        const [leftTd, rightTd] = tds;
        leftTd.classList.add("col-left");
        rightTd.classList.add("col-right");

        const leftPre = leftTd.querySelector("pre");
        const attLinks = Array.from(leftTd.querySelectorAll("a.attachment-link"));
        const cellMeta = document.createElement("div");
        cellMeta.className = "cell-meta";
        const idxBadge = document.createElement("span");
        idxBadge.className = "sample-index";
        idxBadge.textContent = `#${{idx + 1}}`;
        cellMeta.appendChild(idxBadge);

        const strata = Array.isArray(item.sampling_strata) ? [...item.sampling_strata] : [];
        if (!item.doc_number && !strata.includes("missing_doc_number")) strata.push("missing_doc_number");
        if (!item.effective_at && !strata.includes("missing_effective_at")) strata.push("missing_effective_at");
        if (strata.length === 0) strata.push("baseline");
        strata.forEach((tag) => {{
          const [label, cls] = strataMeta[tag] || [tag, "strata-baseline"];
          const b = document.createElement("span");
          b.className = `strata-badge ${{cls}}`;
          b.setAttribute("data-stratum", tag);
          b.textContent = label;
          cellMeta.appendChild(b);
        }});

        if (item.detail_url) {{
          const dLink = document.createElement("a");
          dLink.className = "meta-link";
          dLink.href = item.detail_url;
          dLink.target = "_blank";
          dLink.rel = "noopener";
          dLink.textContent = "源站详情 ↗";
          cellMeta.appendChild(dLink);
        }}

        leftTd.textContent = "";
        leftTd.appendChild(cellMeta);
        if (attLinks.length > 0) {{
          const attBox = document.createElement("div");
          attBox.className = "attachment-box";
          attLinks.forEach((a) => {{
            a.target = "_blank";
            a.rel = "noopener";
            attBox.appendChild(a);
          }});
          leftTd.appendChild(attBox);
        }}
        if (leftPre) {{
          appendPreWithToggle(leftTd, leftPre, "payload-box");
        }}

        const rightPre = rightTd.querySelector("pre");
        const card = document.createElement("div");
        card.className = "doc-meta-card doc-meta-json";
        const metaObj = {{
          title: item.title ?? null,
          doc_number: item.doc_number ?? null,
          issuer: item.issuer ?? null,
          published_at: item.published_at ?? null,
          effective_at: item.effective_at ?? null,
        }};
        renderHighlightedJson(card, metaObj);

        rightTd.textContent = "";
        rightTd.appendChild(card);
        if (rightPre) {{
          appendPreWithToggle(rightTd, rightPre, "markdown-box");
        }}
        bindRowExpandToggles(row);
      }});
    }}

    async function fetchSamplesJson(sourceId) {{
      let lastErr = null;
      for (const origin of apiOrigins) {{
        try {{
          const resp = await fetch(`${{origin}}/api/samples?source_id=${{encodeURIComponent(sourceId)}}&full=1`);
          if (resp.ok) return await resp.json();
          lastErr = new Error(`HTTP ${{resp.status}}`);
        }} catch (e) {{
          lastErr = e;
        }}
      }}
      throw lastErr || new Error("fetch_failed");
    }}

    async function loadSourceSamples(sourceId) {{
      const sec = document.getElementById(sourceId);
      if (!sec) return;
      upgradeSectionDomFromCache(sourceId);
      if (loadedState.has(sourceId)) return;
      loadedState.add(sourceId);
      try {{
        const data = await fetchSamplesJson(sourceId);
        const items = Array.isArray(data.items) ? data.items : [];
        const rows = sec.querySelectorAll("tbody tr");
        rows.forEach((row, idx) => {{
          const item = items[idx];
          if (!item) return;
          const tds = row.querySelectorAll("td");
          let pCode = tds[0] ? tds[0].querySelector("pre code") : null;
          if (!pCode && tds[0] && item.raw_payload) {{
            const pre = document.createElement("pre");
            pCode = document.createElement("code");
            pre.appendChild(pCode);
            appendPreWithToggle(tds[0], pre, "payload-box");
            bindRowExpandToggles(row);
          }}
          const mCode = tds[1] ? tds[1].querySelector("pre code") : null;
          if (pCode) pCode.textContent = item.raw_payload || "（纯附件样本，请点击上方附件链接查看）";
          if (mCode) mCode.textContent = item.markdown || "";
        }});
      }} catch (err) {{
        loadedState.delete(sourceId);
        const rows = sec.querySelectorAll("tbody tr");
        rows.forEach((row) => {{
          const tds = row.querySelectorAll("td");
          const pCode = tds[0] ? tds[0].querySelector("pre code") : null;
          const mCode = tds[1] ? tds[1].querySelector("pre code") : null;
          const hint = "⚠️ 未连接实时样本服务，请运行: uv run python -m wlh.runbook_samples --serve";
          if (pCode && pCode.textContent.startsWith("⏳")) pCode.textContent = hint;
          if (mCode && mCode.textContent.startsWith("⏳")) mCode.textContent = hint;
        }});
      }}
    }}

    function selectSourcePage(sourceId, updateHash = true) {{
      const targetSec = document.getElementById(sourceId);
      if (!targetSec) return;
      currentSourceId = sourceId;
      sections.forEach((sec) => {{
        sec.classList.toggle("is-active", sec.id === sourceId);
      }});
      tocLinks.forEach((link) => {{
        const isMatch = link.getAttribute("href") === `#${{sourceId}}`;
        link.classList.toggle("is-active", isMatch);
        if (isMatch) {{
          link.scrollIntoView({{ block: "nearest" }});
        }}
      }});
      if (updateHash && window.history && window.history.replaceState) {{
        window.history.replaceState(null, "", `#${{sourceId}}`);
      }}
      window.scrollTo({{ top: 0, behavior: "instant" }});
      loadSourceSamples(sourceId);
    }}

    function applySidebarFilter() {{
      const q = (searchInput ? searchInput.value : "").trim().toLowerCase();
      visibleSourceIds = [];
      tocLinks.forEach((link) => {{
        const rawHref = (link.getAttribute("href") || "").replace(/^#/, "");
        const sid = rawHref.toLowerCase();
        const text = (link.textContent || "").toLowerCase();
        const show = !q || sid.includes(q) || text.includes(q);
        link.style.display = show ? "" : "none";
        if (show && rawHref) {{
          visibleSourceIds.push(rawHref);
        }}
      }});
      if (visibleSourceIds.length > 0 && !visibleSourceIds.includes(currentSourceId)) {{
        selectSourcePage(visibleSourceIds[0], true);
      }}
    }}

    if (searchInput) {{
      searchInput.addEventListener("input", applySidebarFilter);
    }}

    tocLinks.forEach((link) => {{
      link.addEventListener("click", (ev) => {{
        const href = link.getAttribute("href") || "";
        if (href.startsWith("#")) {{
          ev.preventDefault();
          selectSourcePage(href.slice(1), true);
        }}
      }});
    }});

    window.addEventListener("hashchange", () => {{
      const h = (window.location.hash || "").replace(/^#/, "");
      if (h && document.getElementById(h)) {{
        selectSourcePage(h, false);
      }}
    }});

    const initialHash = (window.location.hash || "").replace(/^#/, "");
    if (initialHash && document.getElementById(initialHash)) {{
      selectSourcePage(initialHash, false);
    }} else if (currentSourceId) {{
      selectSourcePage(currentSourceId, false);
    }}
  }})();
  </script>
</body>
</html>
"""


render_source_verification_markdown = render_source_verification_html


def _parse_existing_html_samples(html_path: Path) -> dict[str, list[dict[str, Any]]]:
    """Load existing sample items per `source_id` from `source-verification-samples.html`."""
    if not html_path.is_file():
        return {}
    text = html_path.read_text(encoding="utf-8")
    m = re.search(
        r'<script\s+id="wlh-samples-cache"\s+type="application/json">(.*?)</script>',
        text,
        flags=re.S,
    )
    if m:
        try:
            raw_data: Any = json.loads(m.group(1))
            if isinstance(raw_data, dict):
                out: dict[str, list[dict[str, Any]]] = {}
                for k, v in cast(dict[str, Any], raw_data).items():
                    if isinstance(v, list):
                        restored_list: list[dict[str, Any]] = []
                        for item in cast(list[Any], v):
                            if not isinstance(item, Mapping):
                                continue
                            norm = _normalize_item(cast(Mapping[str, Any], item))
                            strata = list(cast(list[str], norm.get("sampling_strata") or []))
                            doc_num_s = str(norm.get("doc_number") or "").strip()
                            if doc_num_s in {"", "—", "-"} and "missing_doc_number" not in strata:
                                strata.append("missing_doc_number")
                            eff_s = str(norm.get("effective_at") or "").strip()
                            if eff_s in {"", "—", "-"} and "missing_effective_at" not in strata:
                                strata.append("missing_effective_at")
                            if not strata:
                                strata.append("baseline")
                            norm["sampling_strata"] = strata
                            restored_list.append(norm)
                        out[k] = restored_list
                return out
        except Exception:
            pass
    return {}


def render_html_from_csv(
    csv_path: Path = DEFAULT_CSV_PATH,
    html_path: Path = DEFAULT_OUTPUT_PATH,
    *,
    max_items_per_source: int = 10,
) -> str:
    """Render `source-verification-samples.html` from `source-adaptation-matrix.csv` preserving sample items."""
    existing_items_map = _parse_existing_html_samples(html_path)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        active_rows = [
            row for row in csv.DictReader(handle) if row["状态"].strip().startswith(("[x]", "[~]"))
        ]

    sources_payload: list[dict[str, Any]] = []
    for csv_row in active_rows:
        source_id = csv_row["Source ID"].strip()
        sources_payload.append(
            {
                "source_id": source_id,
                "continent": csv_row.get("大洲/区域", "").strip(),
                "jurisdiction": csv_row.get("法域/国家", "").strip(),
                "subcategory": csv_row.get("业务子分类", "").strip(),
                "document_type": csv_row.get("资料类型", "").strip(),
                "source_name": csv_row.get("来源名称", "").strip(),
                "entry_url": csv_row.get("官方入口 URL", "").strip(),
                "priority": csv_row.get("优先级", "").strip(),
                "adapter": csv_row.get("推荐适配器", "").strip(),
                "status": csv_row.get("状态", "").strip(),
                "note": csv_row.get("备注与实测结论", "").strip(),
                "items": existing_items_map.get(source_id, []),
            }
        )

    return render_source_verification_html(
        sources_payload, max_items_per_source=max_items_per_source
    )


def sync_csv_to_html(
    csv_path: Path = DEFAULT_CSV_PATH,
    html_path: Path = DEFAULT_OUTPUT_PATH,
) -> bool:
    """One-way synchronize `source-adaptation-matrix.csv` -> `source-verification-samples.html`."""
    lock_path = html_path.with_suffix(".html.lock")
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            rendered = render_html_from_csv(csv_path, html_path)
            current = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
            if rendered == current:
                return False
            tmp_path = html_path.with_suffix(f".html.{os.getpid()}.tmp")
            tmp_path.write_text(rendered, encoding="utf-8")
            tmp_path.replace(html_path)
            return True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass


def _extract_payload_snippet(
    raw_bytes: bytes,
    mime_type: str | None,
    content_xpath: str | None,
    charset: str | None = None,
    max_chars: int = 12000,
) -> str:
    if raw_bytes.startswith(b"%PDF"):
        return ""
    if raw_bytes.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                for name in zf.namelist():
                    if name.lower().endswith(
                        (".xml", ".html", ".htm", ".json", ".txt")
                    ) and not name.startswith("word/"):
                        inner = zf.read(name).decode("utf-8", errors="replace")
                        snippet = _format_multiline_payload(inner)[:max_chars]
                        if len(snippet) >= 20:
                            return snippet
        except Exception:
            pass
        return ""
    if raw_bytes.startswith(b"\xd0\xcf\x11\xe0"):
        return ""

    from wlh.processor.parsers.html.fields import decode

    text, enc = decode(raw_bytes, charset)
    if enc == "utf-8-replace":
        for fallback_enc in ("cp932", "gb18030"):
            try:
                text = raw_bytes.decode(fallback_enc)
                break
            except UnicodeDecodeError:
                continue
    lower_mime = (mime_type or "").lower()
    if "html" in lower_mime or text.lstrip().lower().startswith(("<!doctype html", "<html")):
        try:
            import lxml.etree as etree
            from lxml import html as lxml_html  # type: ignore[import-untyped]

            from wlh.processor.extractors.html.html_to_md import clean_html_tree

            etree_mod: Any = etree
            lxml_html_mod: Any = lxml_html
            tree = lxml_html_mod.document_fromstring(text)
            for el in list(tree.xpath("//*")):
                t_val = (el.text or "") + " " + (el.tail or "")
                if any(bad in t_val for bad in FORBIDDEN_PLACEHOLDERS):
                    parent = el.getparent()
                    if parent is not None:
                        parent.remove(el)
            if content_xpath:
                nodes: Any = tree.xpath(content_xpath)
                if isinstance(nodes, list):
                    nodes_list = [
                        n for n in cast(list[Any], nodes) if isinstance(n, etree_mod._Element)
                    ]
                    node_set = set(nodes_list)
                    nodes_list = [
                        n
                        for n in nodes_list
                        if not any(anc in node_set for anc in n.iterancestors())
                    ]
                    if nodes_list:
                        for n in nodes_list:
                            clean_html_tree(n)
                        frag = "\n".join(
                            str(etree_mod.tostring(n, encoding="unicode", method="html"))
                            for n in nodes_list
                        )
                        if len(frag) > 15000:
                            frag = re.sub(
                                r'\s+(?:style|data-[a-z0-9_-]+)=["\'][^"\']*["\']',
                                "",
                                frag,
                                flags=re.I,
                            )
                        cleaned_frag = _format_multiline_payload(frag)[:max_chars]
                        if len(cleaned_frag) >= 20 and not any(
                            b in cleaned_frag for b in FORBIDDEN_PLACEHOLDERS
                        ):
                            return cleaned_frag
            clean_html_tree(tree)
            for head_el in list(tree.xpath("//head")):
                parent = head_el.getparent()
                if parent is not None and len(tree.xpath("//body//*")) > 0:
                    parent.remove(head_el)
            cleaned_tree = str(etree_mod.tostring(tree, encoding="unicode", method="html"))
            cleaned_all = _format_multiline_payload(cleaned_tree)[:max_chars]
            if not any(b in cleaned_all for b in FORBIDDEN_PLACEHOLDERS):
                return cleaned_all
        except Exception:
            pass
    text_no_toc = re.sub(
        r"<(?:TOC|ImageData)\b[^>]*>.*?</(?:TOC|ImageData)>",
        "",
        text,
        flags=re.S,
    )
    out_txt = _format_multiline_payload(text_no_toc)[:max_chars]
    for bad in FORBIDDEN_PLACEHOLDERS:
        out_txt = out_txt.replace(bad, "")
    return out_txt.strip()


def _make_objects_for_bucket(bucket: str) -> Any:
    from wlh.storage.cas import Objects

    return Objects(bucket=bucket)


_CONTENT_XPATH_CACHE: dict[str, str | None] = {}
_HYDRATED_SOURCE_CACHE: dict[tuple[Any, ...], list[dict[str, Any]]] = {}


def _resolve_source_content_xpath(
    source_id: str,
    csv_path: Path = DEFAULT_CSV_PATH,
) -> str | None:
    cache_key = f"{csv_path.resolve().parent}:{source_id}"
    if cache_key in _CONTENT_XPATH_CACHE:
        return _CONTENT_XPATH_CACHE[cache_key]

    import yaml

    from wlh.processor.parsers import normalize_parser_config

    candidates = (
        csv_path.resolve().parent / f"{source_id}.yaml",
        csv_path.resolve().parents[2] / "sources" / f"{source_id}.yaml",
        Path(__file__).resolve().parents[2] / "sources" / f"{source_id}.yaml",
    )
    content_xpath: str | None = None
    for yaml_path in candidates:
        if yaml_path.is_file():
            try:
                raw_cfg: Any = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                if isinstance(raw_cfg, dict):
                    cfg_dict = normalize_parser_config(cast(dict[str, Any], raw_cfg))
                    docs_cfg = cfg_dict.get("documents")
                    if isinstance(docs_cfg, dict):
                        docs_dict = cast(dict[str, Any], docs_cfg)
                        cx = docs_dict.get("content_xpath")
                        if isinstance(cx, str) and cx.strip():
                            content_xpath = cx.strip()
                            if source_id in {
                                "dd-504",
                                "dd-592",
                                "dd-594",
                                "dd-595",
                                "dd-596",
                                "dd-603",
                                "dd-604",
                                "dd-605",
                                "dd-607",
                                "dd-608",
                            }:
                                field_xps: list[str] = []
                                fields_cfg = docs_dict.get("fields")
                                if isinstance(fields_cfg, dict):
                                    for f_val in cast(dict[str, Any], fields_cfg).values():
                                        if isinstance(f_val, dict):
                                            fx = cast(dict[str, Any], f_val).get("xpath")
                                            if isinstance(fx, str):
                                                for branch in fx.split("|"):
                                                    b_clean = re.sub(
                                                        r"/(?:@[A-Za-z0-9_:-]+|text\(\))\s*$",
                                                        "",
                                                        branch.strip(),
                                                    ).strip()
                                                    if (
                                                        b_clean.startswith(("/", "(//"))
                                                        and not b_clean.startswith(
                                                            ("//script", "//body", "//text(")
                                                        )
                                                        and b_clean not in field_xps
                                                    ):
                                                        field_xps.append(b_clean)
                                if field_xps:
                                    content_xpath = " | ".join([*field_xps, content_xpath])
                            break
            except Exception:
                pass
    _CONTENT_XPATH_CACHE[cache_key] = content_xpath
    return content_xpath


def _build_hydrated_item_from_resource_rows(
    conn: Any,
    objs: Any,
    source_id: str,
    content_xpath: str | None,
    res_rows: list[dict[str, Any]],
    *,
    schema_name: str = "wlh",
    full: bool = False,
    base_meta: Mapping[str, Any] | None = None,
    preloaded_md_text: str | None = None,
) -> dict[str, Any] | None:
    non_att = [r for r in res_rows if r.get("role") != "independent_attachment"]
    primary = non_att[0] if non_att else res_rows[0]
    detail_url_val = str(
        (base_meta or {}).get("detail_url") or primary.get("detail_url") or ""
    ).strip()

    if preloaded_md_text is not None:
        md_text = preloaded_md_text
    else:
        try:
            md_text = objs.get(str(primary["markdown_hash"])).decode("utf-8", errors="replace")
            for zip_row in res_rows:
                if (
                    zip_row is not primary
                    and zip_row.get("role") == "independent_attachment"
                    and str(zip_row.get("original_media_type") or "").lower() == "application/zip"
                    and zip_row.get("markdown_hash")
                ):
                    zip_md = objs.get(str(zip_row["markdown_hash"])).decode(
                        "utf-8", errors="replace"
                    )
                    if zip_md.strip():
                        md_text = f"{md_text.rstrip()}\n\n{zip_md.lstrip()}"
        except Exception:
            return None

    if any(bad in md_text for bad in FORBIDDEN_PLACEHOLDERS):
        return None

    att_rows = [r for r in res_rows if r.get("original_hash") is not None]
    attachments_list: list[dict[str, str | None]] = []
    seen_att_urls: set[str] = set()
    if base_meta and isinstance(base_meta.get("attachments"), list):
        for b_att in cast(list[Any], base_meta["attachments"]):
            if isinstance(b_att, Mapping):
                b_att_map = cast(Mapping[str, Any], b_att)
                b_u = str(b_att_map.get("url") or "").strip()
                if b_u and b_u not in seen_att_urls:
                    seen_att_urls.add(b_u)
                    attachments_list.append(
                        {
                            "url": b_u,
                            "name": str(b_att_map.get("name") or "").strip() or None,
                            "media_type": str(b_att_map.get("media_type") or "").strip() or None,
                        }
                    )
    for ar in att_rows:
        ar_url: str | None = None
        loc = ar.get("locator")
        if isinstance(loc, Mapping):
            loc_map = cast(Mapping[str, Any], loc)
            loc_url = loc_map.get("url")
            if isinstance(loc_url, str) and loc_url.strip():
                ar_url = loc_url.strip()
        if not ar_url:
            detail_candidate = str(ar.get("detail_url") or "").strip()
            if re.search(
                r"(?:\.(?:pdf|docx?|xlsx?|zip)(?:\?|$)|/UCMServer/TAND\d+)",
                detail_candidate,
                flags=re.I,
            ):
                ar_url = detail_candidate
        ar_file = str(ar.get("original_file") or "").strip() or None
        ar_mime = str(ar.get("original_media_type") or "").strip() or None
        if ar_url and ar_url not in seen_att_urls:
            seen_att_urls.add(ar_url)
            attachments_list.append(
                {
                    "url": ar_url,
                    "name": ar_file,
                    "media_type": ar_mime,
                }
            )

    attachment_url: str | None = (
        attachments_list[0]["url"]
        if attachments_list
        else (
            str(base_meta.get("attachment_url")).strip()
            if base_meta and base_meta.get("attachment_url")
            else None
        )
    )
    original_file: str | None = (
        attachments_list[0]["name"]
        if attachments_list
        else (
            str(base_meta.get("original_file")).strip()
            if base_meta and base_meta.get("original_file")
            else None
        )
    )
    original_media_type: str | None = (
        attachments_list[0]["media_type"]
        if attachments_list
        else (
            str(base_meta.get("original_media_type")).strip()
            if base_meta and base_meta.get("original_media_type")
            else None
        )
    )

    raw_payload = ""
    inputs_ref = primary.get("inputs_ref")
    if isinstance(inputs_ref, str) and inputs_ref:
        try:
            inputs_raw: Any = json.loads(objs.get(inputs_ref).decode("utf-8", errors="replace"))
            inputs_list: list[Any] = []
            if isinstance(inputs_raw, dict):
                raw_in = cast(dict[str, Any], inputs_raw).get("inputs")
                if isinstance(raw_in, list):
                    inputs_list = cast(list[Any], raw_in)
            elif isinstance(inputs_raw, list):
                inputs_list = cast(list[Any], inputs_raw)

            for inp_obj in inputs_list:
                if not isinstance(inp_obj, dict):
                    continue
                inp_map = cast(dict[str, Any], inp_obj)
                acq_id = inp_map.get("acquisition_id")
                arch_ref = inp_map.get("archive_ref")
                inp_charset = str(inp_map.get("charset") or "").strip() or None
                acq_row = (
                    conn.execute(
                        f"""
                        SELECT COALESCE(actual_url, request_url) AS url, mime_type, archive_ref
                        FROM {schema_name}.acquisition
                        WHERE id = %s
                        """,
                        (acq_id,),
                    ).fetchone()
                    if acq_id
                    else None
                )
                mime = str(acq_row["mime_type"]) if acq_row and acq_row.get("mime_type") else None
                acq_url = str(acq_row["url"]) if acq_row and acq_row.get("url") else ""
                target_ref = (
                    arch_ref
                    if isinstance(arch_ref, str) and arch_ref
                    else (
                        str(acq_row["archive_ref"])
                        if acq_row and acq_row.get("archive_ref")
                        else None
                    )
                )
                if not original_media_type and mime:
                    original_media_type = mime
                if (
                    acq_url
                    and acq_url not in seen_att_urls
                    and re.search(
                        r"(?:\.(?:pdf|docx?|xlsx?|zip)(?:\?|$)|/UCMServer/TAND\d+)",
                        acq_url,
                        flags=re.I,
                    )
                ):
                    seen_att_urls.add(acq_url)
                    attachments_list.append(
                        {
                            "url": acq_url,
                            "name": Path(acq_url.split("?")[0]).name or None,
                            "media_type": mime,
                        }
                    )
                    if not attachment_url:
                        attachment_url = acq_url

                is_binary_detail = bool(
                    re.search(
                        r"(?:\.(?:pdf|docx?|xlsx?|zip)(?:\?|$)|/UCMServer/TAND\d+)",
                        detail_url_val,
                        flags=re.I,
                    )
                )
                if (
                    target_ref
                    and (not is_binary_detail or acq_url == detail_url_val)
                    and (
                        len(raw_payload) < 20
                        or (
                            raw_payload.lstrip().startswith("{")
                            and not (mime or "").lower().endswith("json")
                        )
                    )
                ):
                    raw_bytes = objs.get(target_ref)
                    candidate = _extract_payload_snippet(
                        raw_bytes,
                        mime,
                        content_xpath,
                        inp_charset,
                        max_chars=250_000 if full else 12_000,
                    )
                    if len(candidate) >= 20:
                        raw_payload = candidate
        except Exception:
            raw_payload = ""

    att_url_val = attachment_url.strip() if attachment_url else None

    combined_check = " ".join(
        [
            detail_url_val,
            att_url_val or "",
            str(primary.get("title") or ""),
            md_text,
        ]
    )
    if (
        any(bad in combined_check for bad in FORBIDDEN_PLACEHOLDERS)
        or "127.0.0.1" in combined_check
        or "[::1]" in combined_check
        or "localhost" in combined_check
    ):
        return None

    raw_item: dict[str, Any] = {
        "detail_url": detail_url_val,
        "attachment_url": att_url_val,
        "original_file": original_file,
        "original_media_type": original_media_type,
        "attachments": attachments_list,
        "raw_payload": (
            raw_payload if full else _truncate_payload_aligned(raw_payload, md_text, 1500)
        ),
        "markdown": md_text if full else md_text[:4000],
        "title": (base_meta or {}).get("title") or primary.get("title"),
        "doc_number": (
            base_meta["doc_number"]
            if base_meta and "doc_number" in base_meta
            else primary.get("doc_number")
        ),
        "issuer": (base_meta or {}).get("issuer") or primary.get("issuer"),
        "published_at": (base_meta or {}).get("published_at") or primary.get("published_at"),
        "effective_at": (
            base_meta["effective_at"]
            if base_meta and "effective_at" in base_meta
            else primary.get("effective_at")
        ),
        "quality_route": (base_meta or {}).get("quality_route"),
        "sampling_strata": (base_meta or {}).get("sampling_strata"),
    }
    return format_sample_for_display(raw_item, full=full, source_id=source_id)


def _extract_source_items_from_store(
    conn: Any,
    objs: Any,
    source_id: str,
    content_xpath: str | None,
    max_items_per_source: int,
    items: list[dict[str, Any]],
    seen_resource_keys: set[tuple[str, str, str]],
    schema_name: str = "wlh",
    *,
    full: bool = False,
) -> None:
    if len(items) >= max_items_per_source:
        return
    if schema_name not in {"wlh", "wlh_samples_backup"}:
        return

    candidate_limit = max(max_items_per_source * 5, 50)
    db_rows: list[dict[str, Any]] = conn.execute(
        f"""
        WITH top_res AS (
            SELECT r.id, v.id AS version_id, v.inputs_ref, v.result_ref
            FROM {schema_name}.resource r
            JOIN {schema_name}.frontier f ON f.id = r.frontier_id
            JOIN {schema_name}.version v ON v.id = r.current_version_id
            WHERE f.source_id = %s
            ORDER BY (r.public_url LIKE '%%mp.weixin.qq.com%%') ASC, r.id
            LIMIT %s
        )
        SELECT tr.id AS resource_id, tr.inputs_ref, tr.result_ref, d.*
        FROM top_res tr
        JOIN {schema_name}.document_row d ON d.version_id = tr.version_id
        ORDER BY tr.id, d.row_index
        """,
        (source_id, candidate_limit),
    ).fetchall()

    by_res: dict[str, list[dict[str, Any]]] = {}
    for r in db_rows:
        by_res.setdefault(str(r["resource_id"]), []).append(r)

    selected_res = {
        rid: rlist
        for rid, rlist in by_res.items()
        if not any(
            "127.0.0.1" in str(r.get("detail_url") or "")
            or "[::1]" in str(r.get("detail_url") or "")
            or "localhost" in str(r.get("detail_url") or "")
            for r in rlist
        )
    }

    candidate_pool: list[dict[str, Any]] = []
    local_seen_keys: set[tuple[str, str, str]] = set(seen_resource_keys)

    for res_rows in selected_res.values():
        non_att = [r for r in res_rows if r.get("role") != "independent_attachment"]
        primary = non_att[0] if non_att else res_rows[0]
        detail_url_val = str(primary.get("detail_url") or "").strip()
        resource_dedup_key = (
            detail_url_val,
            str(primary.get("title") or "").strip(),
            str(primary.get("markdown_hash") or "").strip(),
        )
        if resource_dedup_key in local_seen_keys:
            continue

        try:
            md_text = objs.get(str(primary["markdown_hash"])).decode("utf-8", errors="replace")
            for zip_row in res_rows:
                if (
                    zip_row is not primary
                    and zip_row.get("role") == "independent_attachment"
                    and str(zip_row.get("original_media_type") or "").lower() == "application/zip"
                    and zip_row.get("markdown_hash")
                ):
                    zip_md = objs.get(str(zip_row["markdown_hash"])).decode(
                        "utf-8", errors="replace"
                    )
                    if zip_md.strip():
                        md_text = f"{md_text.rstrip()}\n\n{zip_md.lstrip()}"
        except Exception:
            continue

        if any(bad in md_text for bad in FORBIDDEN_PLACEHOLDERS):
            continue

        quality_route = _resolve_candidate_quality_route(primary, md_text)
        result_ref = primary.get("result_ref")
        if quality_route != "accept_with_warning" and isinstance(result_ref, str) and result_ref:
            try:
                doc_obj: Any = json.loads(objs.get(result_ref).decode("utf-8", errors="replace"))
                if isinstance(doc_obj, Mapping):
                    doc_map = cast(Mapping[str, Any], doc_obj)
                    for d_entry in cast(Sequence[Any], doc_map.get("deliverables") or ()):
                        if isinstance(d_entry, Mapping):
                            d_map = cast(Mapping[str, Any], d_entry)
                            q_rep = d_map.get("quality_report")
                            if isinstance(q_rep, Mapping):
                                q_map = cast(Mapping[str, Any], q_rep)
                                if q_map.get("recommended_route") == "accept_with_warning":
                                    quality_route = "accept_with_warning"
                                    break
            except Exception:
                pass

        local_seen_keys.add(resource_dedup_key)
        candidate_pool.append(
            {
                "_res_rows": res_rows,
                "_primary": primary,
                "_resource_dedup_key": resource_dedup_key,
                "detail_url": detail_url_val,
                "markdown": md_text,
                "title": primary.get("title"),
                "doc_number": primary.get("doc_number"),
                "issuer": primary.get("issuer"),
                "published_at": primary.get("published_at"),
                "effective_at": primary.get("effective_at"),
                "quality_route": quality_route,
            }
        )

    stratified_candidates = stratify_verification_samples(
        candidate_pool, max_items=len(candidate_pool)
    )

    for cand_entry in stratified_candidates:
        if len(items) >= max_items_per_source:
            break
        res_rows = cast(list[dict[str, Any]], cand_entry["_res_rows"])
        resource_dedup_key = cast(tuple[str, str, str], cand_entry["_resource_dedup_key"])
        md_text = str(cand_entry["markdown"])
        hydrated = _build_hydrated_item_from_resource_rows(
            conn,
            objs,
            source_id,
            content_xpath,
            res_rows,
            schema_name=schema_name,
            full=full,
            base_meta=cand_entry,
            preloaded_md_text=md_text,
        )
        if hydrated is None:
            continue
        seen_resource_keys.add(resource_dedup_key)
        items.append(hydrated)


def _hydrate_cached_source_items_from_conn(
    conn: Any,
    objs: Any,
    source_id: str,
    content_xpath: str | None,
    cached_items: Sequence[Mapping[str, Any]],
    *,
    schema_name: str = "wlh",
    full: bool = False,
) -> list[dict[str, Any]] | None:
    """Fast-hydrate known cached items for `source_id` by matching `detail_url` or `title` without scanning 50 candidates."""
    if not cached_items:
        return None
    target_urls = [
        str(it.get("detail_url") or "").strip()
        for it in cached_items
        if str(it.get("detail_url") or "").strip()
    ]
    target_titles = [
        str(it.get("title") or "").strip()
        for it in cached_items
        if str(it.get("title") or "").strip()
    ]
    if not target_urls and not target_titles:
        return None

    db_rows: list[dict[str, Any]] = conn.execute(
        f"""
        WITH matched_res AS (
            SELECT DISTINCT r.id, v.id AS version_id, v.inputs_ref, v.result_ref
            FROM {schema_name}.resource r
            JOIN {schema_name}.frontier f ON f.id = r.frontier_id
            JOIN {schema_name}.version v ON v.id = r.current_version_id
            JOIN {schema_name}.document_row d ON d.version_id = v.id
            WHERE f.source_id = %s
              AND (d.detail_url = ANY(%s) OR d.title = ANY(%s))
        )
        SELECT mr.id AS resource_id, mr.inputs_ref, mr.result_ref, d.*
        FROM matched_res mr
        JOIN {schema_name}.document_row d ON d.version_id = mr.version_id
        ORDER BY mr.id, d.row_index
        """,
        (source_id, target_urls, target_titles),
    ).fetchall()

    if not db_rows:
        return None

    by_res: dict[str, list[dict[str, Any]]] = {}
    for r in db_rows:
        by_res.setdefault(str(r["resource_id"]), []).append(r)

    by_url: dict[str, list[dict[str, Any]]] = {}
    by_title: dict[str, list[dict[str, Any]]] = {}
    for res_rows in by_res.values():
        if any(
            "127.0.0.1" in str(r.get("detail_url") or "")
            or "[::1]" in str(r.get("detail_url") or "")
            or "localhost" in str(r.get("detail_url") or "")
            for r in res_rows
        ):
            continue
        non_att = [r for r in res_rows if r.get("role") != "independent_attachment"]
        primary = non_att[0] if non_att else res_rows[0]
        u = str(primary.get("detail_url") or "").strip()
        t = str(primary.get("title") or "").strip()
        if u and u not in by_url:
            by_url[u] = res_rows
        if t and t not in by_title:
            by_title[t] = res_rows

    out: list[dict[str, Any]] = []
    for c_item in cached_items:
        u = str(c_item.get("detail_url") or "").strip()
        t = str(c_item.get("title") or "").strip()
        matched_rows = by_url.get(u) or by_title.get(t)
        if not matched_rows:
            return None
        hydrated = _build_hydrated_item_from_resource_rows(
            conn,
            objs,
            source_id,
            content_xpath,
            matched_rows,
            schema_name=schema_name,
            full=full,
            base_meta=c_item,
        )
        if hydrated is None:
            return None
        out.append(hydrated)
    return out


def hydrate_samples_from_store(
    sources_map: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    full: bool = False,
    max_items_per_source: int = 10,
    csv_path: Path = DEFAULT_CSV_PATH,
    database_url: str | None = None,
    s3_bucket: str | None = None,
    fallback_database_url: str | None = None,
    fallback_s3_bucket: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Hydrate `raw_payload` and `markdown` live from PostgreSQL + S3 CAS for a map of `source_id -> items`."""
    from contextlib import ExitStack

    import psycopg
    from psycopg.rows import dict_row

    target_db_url = (
        database_url or os.environ.get("WLH_SAMPLES_DATABASE_URL") or DEFAULT_SAMPLES_DATABASE_URL
    )
    target_bucket = (
        s3_bucket or os.environ.get("WLH_SAMPLES_S3_BUCKET") or DEFAULT_SAMPLES_S3_BUCKET
    )
    prod_db_url = (
        fallback_database_url
        if fallback_database_url is not None
        else (os.environ.get("WLH_FALLBACK_SAMPLES_DATABASE_URL") or FALLBACK_SAMPLES_DATABASE_URL)
    )
    prod_bucket = (
        fallback_s3_bucket
        or os.environ.get("WLH_FALLBACK_SAMPLES_S3_BUCKET")
        or FALLBACK_SAMPLES_S3_BUCKET
    )
    if "PYTEST_CURRENT_TEST" in os.environ:
        prod_bucket = target_bucket

    primary_objs = _make_objects_for_bucket(target_bucket)
    fallback_objs: Any = None
    fallback_conn: Any = None
    out: dict[str, list[dict[str, Any]]] = {}

    with ExitStack() as stack:
        conn = stack.enter_context(
            psycopg.Connection[dict[str, Any]].connect(target_db_url, row_factory=dict_row)
        )
        for source_id, raw_items in sources_map.items():
            cached_seq = list(raw_items)[:max_items_per_source]
            urls_sig = tuple(str(it.get("detail_url") or "") for it in cached_seq)
            cache_key = (
                target_db_url,
                target_bucket,
                prod_db_url,
                source_id,
                full,
                max_items_per_source,
                urls_sig,
            )
            if (
                target_db_url == DEFAULT_SAMPLES_DATABASE_URL
                and cache_key in _HYDRATED_SOURCE_CACHE
            ):
                out[source_id] = [dict(x) for x in _HYDRATED_SOURCE_CACHE[cache_key]]
                continue

            content_xpath = _resolve_source_content_xpath(source_id, csv_path)
            hydrated_items = _hydrate_cached_source_items_from_conn(
                conn,
                primary_objs,
                source_id,
                content_xpath,
                cached_seq,
                full=full,
            )
            if (
                hydrated_items is None
                and prod_db_url
                and (prod_db_url != target_db_url or prod_bucket != target_bucket)
            ):
                if fallback_conn is None:
                    try:
                        fallback_conn = stack.enter_context(
                            psycopg.Connection[dict[str, Any]].connect(
                                prod_db_url, row_factory=dict_row
                            )
                        )
                        try:
                            fallback_objs = _make_objects_for_bucket(prod_bucket)
                        except Exception:
                            fallback_objs = primary_objs
                    except Exception:
                        fallback_conn = False
                if fallback_conn and fallback_objs is not None:
                    hydrated_items = _hydrate_cached_source_items_from_conn(
                        fallback_conn,
                        fallback_objs,
                        source_id,
                        content_xpath,
                        cached_seq,
                        full=full,
                    )

            if hydrated_items is None:
                items: list[dict[str, Any]] = []
                seen_keys: set[tuple[str, str, str]] = set()
                _extract_source_items_from_store(
                    conn,
                    primary_objs,
                    source_id,
                    content_xpath,
                    max_items_per_source,
                    items,
                    seen_keys,
                    "wlh",
                    full=full,
                )
                if (
                    len(items) < max_items_per_source
                    and prod_db_url
                    and (prod_db_url != target_db_url or prod_bucket != target_bucket)
                ):
                    if fallback_conn is None:
                        try:
                            fallback_conn = stack.enter_context(
                                psycopg.Connection[dict[str, Any]].connect(
                                    prod_db_url, row_factory=dict_row
                                )
                            )
                            try:
                                fallback_objs = _make_objects_for_bucket(prod_bucket)
                            except Exception:
                                fallback_objs = primary_objs
                        except Exception:
                            fallback_conn = False
                    if fallback_conn and fallback_objs is not None:
                        _extract_source_items_from_store(
                            fallback_conn,
                            fallback_objs,
                            source_id,
                            content_xpath,
                            max_items_per_source,
                            items,
                            seen_keys,
                            "wlh",
                            full=full,
                        )
                hydrated_items = items

            if target_db_url == DEFAULT_SAMPLES_DATABASE_URL and hydrated_items:
                _HYDRATED_SOURCE_CACHE[cache_key] = [dict(x) for x in hydrated_items]
            out[source_id] = hydrated_items

    return out


def fetch_live_source_samples(
    source_id: str,
    *,
    html_path: Path = DEFAULT_OUTPUT_PATH,
    csv_path: Path = DEFAULT_CSV_PATH,
    max_items: int = 10,
    full: bool = False,
    database_url: str | None = None,
    s3_bucket: str | None = None,
    fallback_database_url: str | None = None,
    fallback_s3_bucket: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch live `raw_payload` and `markdown` for a single `source_id` from PostgreSQL + S3 CAS on demand."""
    existing_map = _parse_existing_html_samples(html_path)
    seed_items = existing_map.get(source_id, [])[:max_items]
    hydrated = hydrate_samples_from_store(
        {source_id: seed_items},
        full=full,
        max_items_per_source=max_items,
        csv_path=csv_path,
        database_url=database_url,
        s3_bucket=s3_bucket,
        fallback_database_url=fallback_database_url,
        fallback_s3_bucket=fallback_s3_bucket,
    )
    return hydrated.get(source_id, [])


def generate_verification_samples_from_db(
    csv_path: Path = DEFAULT_CSV_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    *,
    max_items_per_source: int = 10,
    database_url: str | None = None,
    s3_bucket: str | None = None,
    fallback_database_url: str | None = None,
    fallback_s3_bucket: str | None = None,
) -> Path:
    """Query PostgreSQL (`wlh_samples`, with automatic fallback to production `wlh`) + S3 CAS (`wlh-archive-test` -> `wlh-archive`) for all `[x]`/`[~]` sources and write `source-verification-samples.html`."""
    from contextlib import ExitStack

    import psycopg
    from psycopg.rows import dict_row

    target_db_url = (
        database_url or os.environ.get("WLH_SAMPLES_DATABASE_URL") or DEFAULT_SAMPLES_DATABASE_URL
    )
    target_bucket = (
        s3_bucket or os.environ.get("WLH_SAMPLES_S3_BUCKET") or DEFAULT_SAMPLES_S3_BUCKET
    )
    prod_db_url = (
        fallback_database_url
        if fallback_database_url is not None
        else (os.environ.get("WLH_FALLBACK_SAMPLES_DATABASE_URL") or FALLBACK_SAMPLES_DATABASE_URL)
    )
    prod_bucket = (
        fallback_s3_bucket
        or os.environ.get("WLH_FALLBACK_SAMPLES_S3_BUCKET")
        or FALLBACK_SAMPLES_S3_BUCKET
    )
    if "PYTEST_CURRENT_TEST" in os.environ:
        prod_bucket = target_bucket

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        active_rows = [
            row for row in csv.DictReader(handle) if row["状态"].strip().startswith(("[x]", "[~]"))
        ]

    existing_items_map = _parse_existing_html_samples(output_path)
    primary_objs = _make_objects_for_bucket(target_bucket)
    fallback_objs: Any = None
    fallback_conn: Any = None
    sources_payload: list[dict[str, Any]] = []

    with ExitStack() as stack:
        conn = stack.enter_context(
            psycopg.Connection[dict[str, Any]].connect(target_db_url, row_factory=dict_row)
        )
        for csv_row in active_rows:
            source_id = csv_row["Source ID"].strip()
            source_name = csv_row["来源名称"].strip()
            status = csv_row["状态"].strip()
            content_xpath = _resolve_source_content_xpath(source_id, csv_path)

            items: list[dict[str, Any]] = []
            seen_resource_keys: set[tuple[str, str, str]] = set()

            _extract_source_items_from_store(
                conn,
                primary_objs,
                source_id,
                content_xpath,
                max_items_per_source,
                items,
                seen_resource_keys,
                "wlh",
            )

            if (
                len(items) < max_items_per_source
                and prod_db_url
                and (prod_db_url != target_db_url or prod_bucket != target_bucket)
            ):
                if fallback_conn is None:
                    try:
                        fallback_conn = stack.enter_context(
                            psycopg.Connection[dict[str, Any]].connect(
                                prod_db_url, row_factory=dict_row
                            )
                        )
                        try:
                            fallback_objs = _make_objects_for_bucket(prod_bucket)
                        except Exception:
                            fallback_objs = primary_objs
                    except Exception:
                        fallback_conn = False
                if fallback_conn and fallback_objs is not None:
                    _extract_source_items_from_store(
                        fallback_conn,
                        fallback_objs,
                        source_id,
                        content_xpath,
                        max_items_per_source,
                        items,
                        seen_resource_keys,
                    )

            existing_items = existing_items_map.get(source_id, [])
            if len(items) < len(existing_items):
                items = existing_items

            sources_payload.append(
                {
                    "source_id": source_id,
                    "continent": csv_row.get("大洲/区域", "").strip(),
                    "jurisdiction": csv_row.get("法域/国家", "").strip(),
                    "subcategory": csv_row.get("业务子分类", "").strip(),
                    "document_type": csv_row.get("资料类型", "").strip(),
                    "source_name": source_name,
                    "entry_url": csv_row.get("官方入口 URL", "").strip(),
                    "priority": csv_row.get("优先级", "").strip(),
                    "adapter": csv_row.get("推荐适配器", "").strip(),
                    "status": status,
                    "note": csv_row.get("备注与实测结论", "").strip(),
                    "items": items,
                }
            )

    rendered = render_source_verification_html(
        sources_payload, max_items_per_source=max_items_per_source
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    return output_path


@contextlib.contextmanager
def start_samples_http_server(
    host: str = "127.0.0.1",
    port: int = 18088,
    *,
    html_path: Path = DEFAULT_OUTPUT_PATH,
    csv_path: Path = DEFAULT_CSV_PATH,
    database_url: str | None = None,
    s3_bucket: str | None = None,
    fallback_database_url: str | None = None,
    fallback_s3_bucket: str | None = None,
) -> Generator[ThreadingHTTPServer, None, None]:
    """Start a threaded HTTP server serving `source-verification-samples.html` and live DB/CAS `/api/samples` endpoints."""

    class _SamplesHTTPHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_json(self, status_code: int, payload: Mapping[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            route = unquote(parsed.path)
            qs = parse_qs(parsed.query)

            if route in {
                "/",
                "/source-verification-samples.html",
                "/docs/runbooks/source-verification-samples.html",
            }:
                if not html_path.is_file():
                    self._send_json(404, {"error": "html_not_found"})
                    return
                body = html_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if route == "/api/healthz":
                self._send_json(
                    200,
                    {
                        "status": "ok",
                        "html_path": str(html_path),
                    },
                )
                return

            if route == "/api/samples" or route.startswith("/api/samples/"):
                source_id = (
                    route.removeprefix("/api/samples/").strip("/")
                    if route.startswith("/api/samples/")
                    else (qs.get("source_id", [""])[0]).strip()
                )
                if not source_id:
                    self._send_json(400, {"error": "missing_source_id"})
                    return
                full_flag = qs.get("full", ["0"])[0].lower() in {
                    "1",
                    "true",
                    "yes",
                }
                try:
                    max_items = int(qs.get("max_items", ["10"])[0])
                except ValueError:
                    max_items = 10
                items = fetch_live_source_samples(
                    source_id,
                    html_path=html_path,
                    csv_path=csv_path,
                    max_items=max_items,
                    full=full_flag,
                    database_url=database_url,
                    s3_bucket=s3_bucket,
                    fallback_database_url=fallback_database_url,
                    fallback_s3_bucket=fallback_s3_bucket,
                )
                self._send_json(
                    200,
                    {
                        "source_id": source_id,
                        "full": full_flag,
                        "count": len(items),
                        "items": items,
                    },
                )
                return

            self._send_json(404, {"error": "not_found", "path": route})

    srv = ThreadingHTTPServer((host, port), _SamplesHTTPHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        thread.join(timeout=2.0)
        srv.server_close()


def _file_digest(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def watch_csv_to_html(
    csv_path: Path = DEFAULT_CSV_PATH,
    html_path: Path = DEFAULT_OUTPUT_PATH,
) -> None:
    """Wait on OS kernel VFS events (kqueue EVFILT_VNODE) for CSV modifications and sync HTML TOC."""
    csv_path = csv_path.resolve()
    html_path = html_path.resolve()
    parent_dir = csv_path.parent
    last_digest = _file_digest(csv_path)
    if last_digest:
        changed = sync_csv_to_html(csv_path, html_path)
        ts = datetime.now().strftime("%H:%M:%S")
        print(
            f"[{ts}] [samples-hook] Initial sync check complete (updated={changed}). Waiting for kernel VFS events on {csv_path.name}",
            flush=True,
        )

    kq = select.kqueue()
    dir_fd = os.open(str(parent_dir), os.O_RDONLY)
    file_fd: int | None = None

    def _register_fds() -> list[select.kevent]:
        nonlocal file_fd
        events = [
            select.kevent(
                dir_fd,
                filter=select.KQ_FILTER_VNODE,
                flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR,
                fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_RENAME | select.KQ_NOTE_EXTEND,
            )
        ]
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
            file_fd = None
        if csv_path.exists():
            file_fd = os.open(str(csv_path), os.O_RDONLY)
            events.append(
                select.kevent(
                    file_fd,
                    filter=select.KQ_FILTER_VNODE,
                    flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR,
                    fflags=(
                        select.KQ_NOTE_WRITE
                        | select.KQ_NOTE_EXTEND
                        | select.KQ_NOTE_ATTRIB
                        | select.KQ_NOTE_RENAME
                        | select.KQ_NOTE_DELETE
                    ),
                )
            )
        return events

    try:
        reg_events = _register_fds()
        while True:
            fired = kq.control(reg_events, 1, None)
            reg_events = []
            for ev in fired:
                if (
                    ev.fflags & (select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME)
                    or ev.ident == dir_fd
                ):
                    reg_events = _register_fds()
            digest = _file_digest(csv_path)
            if digest and digest != last_digest:
                last_digest = digest
                changed = sync_csv_to_html(csv_path, html_path)
                ts = datetime.now().strftime("%H:%M:%S")
                print(
                    f"[{ts}] [samples-hook] CSV modification event -> synced {html_path.name} (updated={changed})",
                    flush=True,
                )
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
        os.close(dir_fd)
        kq.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate, sync, or serve docs/runbooks/source-verification-samples.html"
    )
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--max-items", type=int, default=10)
    parser.add_argument(
        "--sync-from-csv",
        action="store_true",
        help="Sync TOC & source metadata from CSV into existing HTML without querying DB",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch CSV via OS kernel VFS events and sync HTML TOC on modification",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start HTTP server serving source-verification-samples.html and live DB/S3 /api/samples endpoints",
    )
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18088)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.serve:
        try:
            with start_samples_http_server(
                host=args.host,
                port=args.port,
                html_path=args.output,
                csv_path=args.csv,
            ) as srv:
                print(
                    f"Serving live verification samples at http://{args.host}:{srv.server_port}/",
                    flush=True,
                )
                threading.Event().wait()
        except KeyboardInterrupt:
            return 0
        return 0
    if args.watch:
        try:
            watch_csv_to_html(args.csv, args.output)
        except KeyboardInterrupt:
            return 0
        return 0
    if args.sync_from_csv:
        updated = sync_csv_to_html(args.csv, args.output)
        print(f"synced {args.csv} -> {args.output} (updated={updated})")
        return 0
    out = generate_verification_samples_from_db(
        args.csv, args.output, max_items_per_source=args.max_items
    )
    print(f"generated {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
