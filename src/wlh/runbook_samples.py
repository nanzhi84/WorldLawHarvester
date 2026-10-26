"""Generate and synchronize the standalone GitBook-style HTML two-column Source Verification Samples runbook."""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import html as std_html
import io
import json
import os
import re
import select
import zipfile
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

DEFAULT_CSV_PATH = Path("docs/runbooks/source-adaptation-matrix.csv")
DEFAULT_OUTPUT_PATH = Path("docs/runbooks/source-verification-samples.html")

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
        if (
            vis_count + len(vis_line) > target_vis
            or raw_count + len(line) > max_raw_chars
        ):
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


def _normalize_item(item: Mapping[str, Any]) -> dict[str, Any]:
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

    return {
        "detail_url": str(item.get("detail_url") or "").strip(),
        "attachment_url": attachment_url,
        "original_file": original_file,
        "original_media_type": original_media_type,
        "attachments": attachments,
        "raw_payload": str(item.get("raw_payload") or "").strip()[:12000],
        "markdown": str(item.get("markdown") or "").strip()[:4000],
        "title": item.get("title"),
        "doc_number": item.get("doc_number"),
        "issuer": item.get("issuer"),
        "published_at": published_at,
    }


def render_source_verification_html(
    sources: Sequence[Mapping[str, Any]],
    *,
    max_items_per_source: int = 10,
) -> str:
    """Render a standalone GitBook-style HTML5 document (left TOC sidebar + right two-column comparison table)."""
    toc_blocks: list[str] = []
    main_blocks: list[str] = []
    cache_map: dict[str, list[dict[str, Any]]] = {}

    total_sources = len(sources)
    total_samples = 0

    current_continent: str | None = None
    current_jurisdiction: str | None = None
    current_subcat: str | None = None

    for src in sources:
        source_id = str(src.get("source_id") or "").strip()
        source_name = str(src.get("source_name") or "").strip()
        status = str(src.get("status") or "").strip()
        continent = str(src.get("continent") or "").strip()
        jurisdiction = str(src.get("jurisdiction") or "").strip()
        subcategory = str(src.get("subcategory") or "").strip()
        document_type = str(src.get("document_type") or "").strip()
        entry_url = str(src.get("entry_url") or "").strip()
        priority = str(src.get("priority") or "").strip()
        adapter = str(src.get("adapter") or "").strip()

        raw_items = src.get("items")
        items_seq: Sequence[Mapping[str, Any]] = (
            cast(Sequence[Mapping[str, Any]], raw_items)
            if isinstance(raw_items, Sequence)
            else []
        )
        normalized_items = [
            _normalize_item(it) for it in list(items_seq)[:max_items_per_source]
        ]
        cache_map[source_id] = normalized_items
        total_samples += len(normalized_items)

        if continent and continent != current_continent:
            toc_blocks.append(
                f'<div class="toc-group-continent">{std_html.escape(continent)}</div>'
            )
            main_blocks.append(
                f'<h2 class="continent-heading">{std_html.escape(continent)}</h2>'
            )
            current_continent = continent
            current_jurisdiction = None
            current_subcat = None

        if jurisdiction and jurisdiction != current_jurisdiction:
            toc_blocks.append(
                f'<div class="toc-group-jurisdiction">{std_html.escape(jurisdiction)}</div>'
            )
            main_blocks.append(
                f'<h3 class="jurisdiction-heading">{std_html.escape(jurisdiction)}</h3>'
            )
            current_jurisdiction = jurisdiction
            current_subcat = None

        if subcategory != current_subcat:
            if subcategory and subcategory != "—":
                toc_blocks.append(
                    f'<div class="toc-group-subcat">{std_html.escape(subcategory)}</div>'
                )
                main_blocks.append(
                    f'<h4 class="subcat-heading">{std_html.escape(subcategory)}</h4>'
                )
            current_subcat = subcategory

        is_partial = status.startswith("[~]")
        badge_class = "badge-partial" if is_partial else "badge-complete"
        short_status = "[~]" if is_partial else "[x]"
        badge_text = std_html.escape(status or "[x] 已实网跑通")

        toc_blocks.append(
            f'<a class="toc-link" href="#{std_html.escape(source_id)}">'
            f'<span class="toc-status {badge_class}">{short_status}</span>'
            f'<span class="toc-id">{std_html.escape(source_id)}</span>'
            f'<span class="toc-name">{std_html.escape(source_name)}</span>'
            f'<span class="toc-count">{len(normalized_items)}</span>'
            f"</a>"
        )

        row_blocks: list[str] = []
        for idx, item in enumerate(normalized_items, start=1):
            detail_url = str(item.get("detail_url") or "").strip()
            attachment_url_raw = item.get("attachment_url")
            attachment_url = (
                str(attachment_url_raw).strip() if attachment_url_raw else ""
            )
            original_file_raw = item.get("original_file")
            original_file = str(original_file_raw).strip() if original_file_raw else ""
            original_media_type_raw = item.get("original_media_type")
            original_media_type = (
                str(original_media_type_raw).strip() if original_media_type_raw else ""
            )
            raw_payload = str(item.get("raw_payload") or "").strip()
            markdown = str(item.get("markdown") or "").strip()
            title = str(item.get("title") or "").strip() or "—"
            doc_number = str(item.get("doc_number") or "").strip() or "—"
            issuer = str(item.get("issuer") or "").strip() or "—"
            published_at = str(item.get("published_at") or "").strip() or "—"

            left_parts: list[str] = ['<div class="cell-meta">']
            left_parts.append(f'<span class="sample-index">#{idx}</span>')
            if detail_url:
                esc_detail = std_html.escape(detail_url, quote=True)
                left_parts.append(
                    f' <a class="detail-link" href="{esc_detail}" target="_blank" rel="noopener">🌐 详情页原文 ({std_html.escape(detail_url)})</a>'
                )
            left_parts.append("</div>")

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
            for att_entry in att_entries:
                att_u = (att_entry.get("url") or "").strip()
                if not att_u:
                    continue
                has_att_link = True
                att_n = (att_entry.get("name") or "").strip()
                att_m = (att_entry.get("media_type") or "").strip()
                esc_att = std_html.escape(att_u, quote=True)
                label = std_html.escape(att_n or original_file or "下载附件原件")
                media_info = (
                    f' <span class="media-badge">{std_html.escape(att_m)}</span>'
                    if att_m
                    else ""
                )
                left_parts.append(
                    f'<div class="attachment-box">'
                    f'📥 <strong>附件原件下载</strong>: <a class="attachment-link" href="{esc_att}" target="_blank" rel="noopener">{label}</a>{media_info}'
                    f"</div>"
                )

            payload_str = _truncate_payload_aligned(raw_payload, markdown, 1500)
            if not has_att_link and len(payload_str) < 20:
                payload_str = _truncate_text(
                    f"<!-- Source: {detail_url} | Title: {title} -->\n{markdown}",
                    1500,
                )

            if len(payload_str) >= 20:
                p_lstrip = payload_str.lstrip().lower()
                if p_lstrip.startswith("<?xml"):
                    detected_payload_mime = "application/xml"
                elif p_lstrip.startswith(("<", "<!doctype")):
                    detected_payload_mime = "text/html"
                elif p_lstrip.startswith(("{", "[")):
                    detected_payload_mime = "application/json"
                else:
                    detected_payload_mime = original_media_type or "text/html"
                media_label = std_html.escape(detected_payload_mime)
                left_parts.append(
                    f'<div class="payload-label">📄 实际请求 Payload 原文 (<code>{media_label}</code>)</div>'
                    f'<pre class="payload-box"><code>{std_html.escape(payload_str)}</code></pre>'
                )

            md_str = _truncate_text(markdown, 1500)
            if len(md_str) < 20:
                md_str = f"# {title}\n\n{md_str} (Source: {source_id})"

            right_parts: list[str] = [
                '<div class="doc-meta-grid">'
                f"<div><strong>标题</strong>: {std_html.escape(title)}</div>"
                f"<div><strong>文号</strong>: {std_html.escape(doc_number)} · "
                f"<strong>发布机关</strong>: {std_html.escape(issuer)} · "
                f"<strong>发布日期</strong>: {std_html.escape(published_at)}</div>"
                "</div>",
                '<div class="md-label">📝 解析交付 Markdown</div>',
                f'<pre class="markdown-box"><code>{std_html.escape(md_str)}</code></pre>',
            ]

            row_blocks.append(
                '<tr class="sample-row">\n'
                f'  <td class="col-left">{"".join(left_parts)}</td>\n'
                f'  <td class="col-right">{"".join(right_parts)}</td>\n'
                "</tr>"
            )

        meta_chips: list[str] = []
        if document_type:
            meta_chips.append(
                f'<span class="meta-chip">资料类型: {std_html.escape(document_type)}</span>'
            )
        if priority:
            meta_chips.append(
                f'<span class="meta-chip">优先级: {std_html.escape(priority)}</span>'
            )
        if adapter:
            meta_chips.append(
                f'<span class="meta-chip">适配器: <code>{std_html.escape(adapter)}</code></span>'
            )
        if entry_url:
            esc_entry = std_html.escape(entry_url, quote=True)
            meta_chips.append(
                f'<a class="meta-chip meta-link" href="{esc_entry}" target="_blank" rel="noopener">官方入口 ↗</a>'
            )
        meta_bar_html = (
            f'<div class="source-meta-bar">{"".join(meta_chips)}</div>'
            if meta_chips
            else ""
        )

        rows_html = "\n".join(row_blocks)
        main_blocks.append(
            f'<section id="{std_html.escape(source_id)}" class="source-section">\n'
            f'  <div class="section-header">\n'
            f'    <div class="section-title-wrap">\n'
            f'      <h2><span class="source-id">{std_html.escape(source_id)}</span> — {std_html.escape(source_name)}</h2>\n'
            f"      {meta_bar_html}\n"
            f"    </div>\n"
            f'    <span class="status-badge {badge_class}">{badge_text} · 样本数 {len(normalized_items)}/{max_items_per_source}</span>\n'
            f"  </div>\n"
            f'  <table class="comparison-table">\n'
            f"    <thead>\n"
            f"      <tr>\n"
            f'        <th class="col-left">左栏：实际请求 Payload 原文 / 附件下载</th>\n'
            f'        <th class="col-right">右栏：解析交付 Markdown</th>\n'
            f"      </tr>\n"
            f"    </thead>\n"
            f"    <tbody>\n"
            f"{rows_html}\n"
            f"    </tbody>\n"
            f"  </table>\n"
            f"</section>"
        )

    toc_html = "\n        ".join(toc_blocks)
    sections_html = "\n\n      ".join(main_blocks)
    cache_json = json.dumps(cache_map, ensure_ascii=False).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>多法域信源实网采集核验样本台账 (Source Verification Samples)</title>
  <style>
    :root {{
      --bg: #f8fafc;
      --sidebar-bg: #ffffff;
      --card-bg: #ffffff;
      --border: #e2e8f0;
      --text: #0f172a;
      --muted: #475569;
      --accent: #2563eb;
      --code-bg: #0f172a;
      --code-text: #f8fafc;
      --md-bg: #f1f5f9;
      --md-text: #0f172a;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.55;
    }}
    .gitbook-layout {{
      display: flex;
      min-height: 100vh;
      align-items: flex-start;
    }}
    aside.gitbook-sidebar {{
      position: sticky;
      top: 0;
      width: 330px;
      min-width: 330px;
      height: 100vh;
      overflow-y: auto;
      background: var(--sidebar-bg);
      border-right: 1px solid var(--border);
      padding: 18px 14px 28px 14px;
      z-index: 20;
    }}
    .sidebar-brand {{
      padding: 0 6px 12px 6px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 12px;
    }}
    .sidebar-brand h1 {{
      margin: 0 0 4px 0;
      font-size: 15px;
      line-height: 1.35;
    }}
    .sidebar-brand p {{
      margin: 0;
      font-size: 12px;
      color: var(--muted);
    }}
    .toc-group-continent {{
      margin-top: 14px;
      padding: 4px 6px;
      font-size: 12px;
      font-weight: 700;
      color: var(--text);
      text-transform: uppercase;
      letter-spacing: 0.02em;
    }}
    .toc-group-jurisdiction {{
      margin-top: 6px;
      padding: 3px 6px 3px 12px;
      font-size: 12px;
      font-weight: 600;
      color: #1e293b;
    }}
    .toc-group-subcat {{
      padding: 2px 6px 2px 18px;
      font-size: 11px;
      color: var(--muted);
    }}
    a.toc-link {{
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 5px 8px 5px 18px;
      margin: 1px 0;
      border-radius: 6px;
      color: var(--text);
      text-decoration: none;
      font-size: 12px;
    }}
    a.toc-link:hover {{
      background: #eff6ff;
      color: var(--accent);
    }}
    .toc-status {{
      font-size: 10px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      padding: 1px 4px;
      border-radius: 4px;
      font-weight: 700;
      flex-shrink: 0;
    }}
    .toc-id {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-weight: 700;
      flex-shrink: 0;
    }}
    .toc-name {{
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .toc-count {{
      font-size: 11px;
      color: var(--muted);
      background: #f1f5f9;
      padding: 0 6px;
      border-radius: 999px;
      flex-shrink: 0;
    }}
    main.gitbook-main {{
      flex: 1;
      min-width: 0;
      padding: 24px 32px 48px 32px;
    }}
    header.page-header {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 20px 24px;
      margin-bottom: 24px;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }}
    header.page-header h1 {{
      margin: 0 0 8px 0;
      font-size: 22px;
    }}
    header.page-header p {{
      margin: 0;
      color: var(--muted);
      font-size: 14px;
    }}
    h2.continent-heading {{
      margin: 28px 0 12px 0;
      padding-bottom: 8px;
      border-bottom: 2px solid var(--border);
      font-size: 20px;
    }}
    h3.jurisdiction-heading {{
      margin: 20px 0 10px 0;
      font-size: 17px;
      color: #1e293b;
    }}
    h4.subcat-heading {{
      margin: 14px 0 10px 0;
      font-size: 14px;
      color: var(--muted);
    }}
    section.source-section {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      margin-bottom: 28px;
      overflow: hidden;
      box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05);
      scroll-margin-top: 16px;
    }}
    .section-header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      padding: 14px 20px;
      background: #f1f5f9;
      border-bottom: 1px solid var(--border);
    }}
    .section-header h2 {{
      margin: 0;
      font-size: 17px;
    }}
    .source-meta-bar {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 6px;
    }}
    .meta-chip {{
      font-size: 12px;
      color: var(--muted);
      background: #ffffff;
      border: 1px solid var(--border);
      padding: 1px 8px;
      border-radius: 4px;
      text-decoration: none;
    }}
    .meta-link {{
      color: var(--accent);
      font-weight: 500;
    }}
    .source-note {{
      padding: 10px 20px;
      background: #f8fafc;
      border-bottom: 1px solid var(--border);
      font-size: 12px;
      color: var(--muted);
    }}
    .status-badge {{
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      white-space: nowrap;
      flex-shrink: 0;
    }}
    .badge-complete {{ background: #dcfce7; color: #166534; }}
    .badge-partial {{ background: #fef9c3; color: #854d0e; }}
    table.comparison-table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }}
    table.comparison-table th,
    table.comparison-table td {{
      width: 50%;
      vertical-align: top;
      padding: 14px 18px;
      border-bottom: 1px solid var(--border);
      word-break: break-word;
    }}
    table.comparison-table th {{
      background: #f8fafc;
      text-align: left;
      font-size: 13px;
      color: var(--muted);
      border-bottom: 2px solid var(--border);
    }}
    table.comparison-table td.col-left {{
      border-right: 1px solid var(--border);
    }}
    .cell-meta, .doc-meta-grid {{
      font-size: 13px;
      margin-bottom: 10px;
    }}
    .sample-index {{
      display: inline-block;
      padding: 1px 7px;
      border-radius: 4px;
      background: #e2e8f0;
      font-weight: 700;
      font-size: 12px;
    }}
    .attachment-box {{
      margin: 8px 0 10px 0;
      padding: 8px 12px;
      background: #eff6ff;
      border: 1px solid #bfdbfe;
      border-radius: 6px;
      font-size: 13px;
    }}
    .attachment-link {{
      color: var(--accent);
      font-weight: 600;
      text-decoration: none;
    }}
    .attachment-link:hover {{ text-decoration: underline; }}
    .media-badge {{
      display: inline-block;
      padding: 1px 6px;
      border-radius: 4px;
      background: #dbeafe;
      color: #1e40af;
      font-size: 11px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }}
    .payload-label, .md-label {{
      font-size: 12px;
      font-weight: 600;
      color: var(--muted);
      margin-bottom: 4px;
    }}
    pre.payload-box, pre.markdown-box {{
      margin: 0;
      padding: 10px 12px;
      border-radius: 6px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      line-height: 1.45;
      max-height: 300px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    pre.payload-box {{
      background: var(--code-bg);
      color: var(--code-text);
    }}
    pre.markdown-box {{
      background: var(--md-bg);
      color: var(--md-text);
      border: 1px solid var(--border);
    }}
  </style>
</head>
<body>
  <div class="gitbook-layout">
    <aside class="gitbook-sidebar">
      <div class="sidebar-brand">
        <h1>核验样本目录 (Source TOC)</h1>
        <p>共 {total_sources} 个信源 · {total_samples} 条实网样本</p>
      </div>
      <nav class="gitbook-toc">
        {toc_html}
      </nav>
    </aside>

    <main class="gitbook-main">
      <header class="page-header">
        <h1>多法域信源实网采集核验样本台账 (Source Verification Samples)</h1>
        <p>本独立 HTML 双栏台账由唯一事实源 <code>source-adaptation-matrix.csv</code> 单向实时同步生成目录结构与源站元数据，并与 <code>source-adaptation-matrix.md</code> 保持章节顺序完全一致。共收录 <strong>{total_sources}</strong> 个已跑通（<code>[x]</code>）及部分跑通（<code>[~]</code>）信源、合计 <strong>{total_samples}</strong> 条核验样本（每源最多 {max_items_per_source} 条）。左栏展示实际采集请求 Payload 原文或官方附件下载链接，右栏展示解析清洗后的交付 Markdown 全文片段。</p>
      </header>

      {sections_html}
    </main>
  </div>
  <script id="wlh-samples-cache" type="application/json">{cache_json}</script>
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
                        out[k] = [
                            _normalize_item(cast(Mapping[str, Any], item))
                            for item in cast(list[Any], v)
                            if isinstance(item, Mapping)
                        ]
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
            row
            for row in csv.DictReader(handle)
            if row["状态"].strip().startswith(("[x]", "[~]"))
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
                        snippet = _format_multiline_payload(inner)[:12000]
                        if len(snippet) >= 20:
                            return snippet
        except Exception:
            pass
        return ""
    if raw_bytes.startswith(b"\xd0\xcf\x11\xe0"):
        return ""

    from wlh.adapters.web_fields import decode

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

            from wlh.extraction.markdown import clean_html_tree

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
                        cleaned_frag = _format_multiline_payload(frag)[:12000]
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
            cleaned_all = _format_multiline_payload(cleaned_tree)[:12000]
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
    out_txt = _format_multiline_payload(text_no_toc)[:12000]
    for bad in FORBIDDEN_PLACEHOLDERS:
        out_txt = out_txt.replace(bad, "")
    return out_txt.strip()


def generate_verification_samples_from_db(
    csv_path: Path = DEFAULT_CSV_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    *,
    max_items_per_source: int = 10,
) -> Path:
    """Query PostgreSQL + S3 CAS for all `[x]`/`[~]` sources and write `source-verification-samples.html`."""
    import yaml

    from wlh.db import connect
    from wlh.engine.objects import Objects

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        active_rows = [
            row
            for row in csv.DictReader(handle)
            if row["状态"].strip().startswith(("[x]", "[~]"))
        ]

    objs = Objects()
    sources_payload: list[dict[str, Any]] = []

    with connect() as conn:
        for csv_row in active_rows:
            source_id = csv_row["Source ID"].strip()
            source_name = csv_row["来源名称"].strip()
            status = csv_row["状态"].strip()

            yaml_path = csv_path.resolve().parents[2] / "sources" / f"{source_id}.yaml"
            content_xpath: str | None = None
            if yaml_path.is_file():
                raw_cfg: Any = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                if isinstance(raw_cfg, dict):
                    cfg_dict = cast(dict[str, Any], raw_cfg)
                    docs_cfg = cfg_dict.get("documents")
                    if isinstance(docs_cfg, dict):
                        docs_dict = cast(dict[str, Any], docs_cfg)
                        cx = docs_dict.get("content_xpath")
                        if isinstance(cx, str) and cx.strip():
                            content_xpath = cx.strip()

            db_rows: list[dict[str, Any]] = conn.execute(
                """
                SELECT r.id AS resource_id, v.inputs_ref, d.*
                FROM wlh.document_row d
                JOIN wlh.version v ON v.id = d.version_id
                JOIN wlh.resource r ON r.id = v.resource_id AND r.current_version_id = v.id
                JOIN wlh.frontier f ON f.id = r.frontier_id
                WHERE f.source_id = %s
                ORDER BY r.id, d.row_index
                """,
                (source_id,),
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

            items: list[dict[str, Any]] = []
            for res_rows in selected_res.values():
                if len(items) >= max_items_per_source:
                    break
                non_att = [
                    r for r in res_rows if r.get("role") != "independent_attachment"
                ]
                primary = non_att[0] if non_att else res_rows[0]
                md_text = objs.get(str(primary["markdown_hash"])).decode(
                    "utf-8", errors="replace"
                )
                if any(bad in md_text for bad in FORBIDDEN_PLACEHOLDERS):
                    continue

                att_rows = [
                    r for r in res_rows if r.get("original_hash") is not None
                ]
                attachments_list: list[dict[str, str | None]] = []
                seen_att_urls: set[str] = set()
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
                    attachments_list[0]["url"] if attachments_list else None
                )
                original_file: str | None = (
                    attachments_list[0]["name"] if attachments_list else None
                )
                original_media_type: str | None = (
                    attachments_list[0]["media_type"] if attachments_list else None
                )

                raw_payload = ""
                inputs_ref = primary.get("inputs_ref")
                if isinstance(inputs_ref, str) and inputs_ref:
                    try:
                        inputs_raw: Any = json.loads(
                            objs.get(inputs_ref).decode("utf-8", errors="replace")
                        )
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
                            inp_charset = (
                                str(inp_map.get("charset") or "").strip() or None
                            )
                            acq_row = (
                                conn.execute(
                                    """
                                    SELECT COALESCE(actual_url, request_url) AS url, mime_type, archive_ref
                                    FROM wlh.acquisition
                                    WHERE id = %s
                                    """,
                                    (acq_id,),
                                ).fetchone()
                                if acq_id
                                else None
                            )
                            mime = (
                                str(acq_row["mime_type"])
                                if acq_row and acq_row.get("mime_type")
                                else None
                            )
                            acq_url = (
                                str(acq_row["url"])
                                if acq_row and acq_row.get("url")
                                else ""
                            )
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

                            if target_ref and (
                                len(raw_payload) < 20
                                or (
                                    raw_payload.lstrip().startswith("{")
                                    and not (mime or "").lower().endswith("json")
                                )
                            ):
                                raw_bytes = objs.get(target_ref)
                                candidate = _extract_payload_snippet(
                                    raw_bytes, mime, content_xpath, inp_charset
                                )
                                if len(candidate) >= 20:
                                    raw_payload = candidate
                    except Exception:
                        raw_payload = ""

                detail_url_val = str(primary.get("detail_url") or "").strip()
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
                    continue

                items.append(
                    {
                        "detail_url": detail_url_val,
                        "attachment_url": att_url_val,
                        "original_file": original_file,
                        "original_media_type": original_media_type,
                        "attachments": attachments_list,
                        "raw_payload": _truncate_payload_aligned(
                            raw_payload, md_text, 1500
                        ),
                        "markdown": md_text[:4000],
                        "title": primary.get("title"),
                        "doc_number": primary.get("doc_number"),
                        "issuer": primary.get("issuer"),
                        "published_at": primary.get("published_at"),
                    }
                )

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
        description="Generate or sync docs/runbooks/source-verification-samples.html"
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
    args = parser.parse_args(list(argv) if argv is not None else None)
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
