"""Resolve binary document titles from independently archived directory evidence."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from wlh.cleaners.text_cleaner import UI_ACTION_LABELS, blocked, compact
from wlh.core import WlhError, digest, public_url
from wlh.processor.parsers.html.fields import clean_url, file_name, parse_html

GENERIC_BINARY_LINK_TITLES: set[str] = {
    "pdf",
    "doc",
    "docx",
    "rtf",
    "xls",
    "xlsx",
    "zip",
    "download",
    "file",
    "attachment",
    "view",
    "open",
    "full text",
    "fulltext",
    "全文",
    "附件",
    "下载",
    "下載",
    "파일",
    "다운로드",
    "첨부파일",
    "別紙",
    "本文",
    *UI_ACTION_LABELS,
}

_VN_DOC_HEAD_RE = re.compile(
    r"((?:NGHỊ\s+ĐỊNH|NGHỊ\s+QUYẾT|QUYẾT\s+ĐỊNH|THÔNG\s+TƯ(?:\s+LIÊN\s+TỊCH)?|"
    r"LUẬT|PHÁP\s+LỆNH|CHỈ\s+THỊ|CÔNG\s+ĐIỆN|THÔNG\s+BÁO)\s+"
    r"(?:về\s+việc|ban\s+hành|quy\s+định|sửa\s+đổi|phê\s+duyệt|hướng\s+dẫn|công\s+bố)[^\n.]{10,220})",
    re.I,
)
_VN_SO_HIEU_RE = re.compile(
    r"Số\s*[:：]?\s*([0-9]{1,5}(?:/[0-9]{4})?/[A-ZĐƯƠĂÂÊÔ0-9-]+)",
    re.I,
)


def directory_title(
    ctx: Any,
    inputs: Sequence[Mapping[str, Any]],
    document_url: str,
    generic_labels: set[str],
) -> tuple[str, dict[str, Any]]:
    contexts = [item for item in inputs if item.get("role") == "title_context"]
    if len(contexts) != 1:
        raise WlhError("binary_title_evidence_missing")
    item = contexts[0]
    acquisition = item["acquisition"]
    target = public_url(document_url)
    if item.get("document_url") != target:
        raise WlhError("binary_title_evidence_mismatch")
    base = acquisition["actual_url"] or acquisition["request_url"]
    tree, _, encoding = parse_html(ctx.read(acquisition), acquisition["charset"])
    titles: set[str] = set()
    for anchor in tree.xpath("//a[@href]"):
        if clean_url(base, anchor.get("href")) != target:
            continue
        label = re.sub(r"\s+", " ", anchor.text_content()).strip()
        label = re.sub(r"[（(]\s*PDF[^()（）]*[)）]\s*$", "", label, flags=re.I).strip()
        if (
            label.casefold() in generic_labels
            or label.casefold() == file_name(target).casefold()
            or not any(char.isalpha() for char in label)
        ):
            continue
        if label:
            titles.add(label)
    if not titles:
        raise WlhError("binary_title_evidence_missing")
    if len(titles) != 1:
        raise WlhError("binary_title_evidence_ambiguous")
    title = titles.pop()
    return title, {
        "acquisition_id": acquisition["id"],
        "archive_ref": acquisition["archive_ref"],
        "role": "context",
        "locator": {"url": public_url(base), "kind": "directory_link", "target": target},
        "charset": encoding,
        "base_url": public_url(base),
        "slice_hash": digest({"target": target, "title": title}),
    }


def norm_title(value: str | None) -> str:
    cleaned = unicodedata.normalize("NFKC", value or "")
    cleaned = re.sub(
        r"[\s\u3000・･/／\\|_.,，。:：;；\-‐‑–—―~〜()（）\[\]［］「」『』【】]+", "", cleaned
    )
    return cleaned.casefold()


def meaningful_binary_link_title(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    cleaned = re.sub(
        r"\s*[\[(（]?\s*(?:PDF|DOCX?|XLSX?|ZIP)\s*(?:ファイル|形式)?(?:\s*[/:：]?\s*\d[\d.,]*\s*(?:KB|MB|GB|バイト|bytes?))?\s*[\])）]?\s*$",
        "",
        cleaned,
        flags=re.I,
    ).strip()
    if len(cleaned) < 2 or cleaned.casefold() in GENERIC_BINARY_LINK_TITLES:
        return None
    if re.fullmatch(r"[\d\W_]+", cleaned):
        return None
    return cleaned


def text_confirms_link_title(link_title: str, markdown: str) -> bool:
    norm_link = norm_title(link_title)
    norm_body = norm_title(markdown[:6000])
    if not norm_link or not norm_body:
        return False
    if norm_link in norm_body:
        return True
    tokens = [
        tok
        for tok in re.findall(r"[一-龥々〆ヵヶ]{2,}|[ァ-ヶー]{3,}|[A-Za-z0-9]{3,}", link_title)
        if tok not in {"別紙", "ファイル", "ダウンロード", "一覧", "資料", "PDF", "pdf"}
    ]
    if not tokens:
        return False
    matched = sum(1 for tok in tokens if norm_title(tok) in norm_body)
    return matched >= max(1, (len(tokens) + 1) // 2)


def assert_binary_link_title_matches(link_title: str, markdown: str) -> None:
    if not text_confirms_link_title(link_title, markdown):
        raise WlhError("binary_title_content_mismatch")


def extract_vn_fallback_title(
    raw: bytes,
    charset: str | None,
    url: str,
    current_title: str | None = None,
    doc_number: str | None = None,
) -> str | None:
    """4-tier Vietnamese gazette/legal portal title recovery when primary title is generic or blocked."""
    if (
        current_title
        and not blocked(current_title)
        and current_title.strip().casefold() not in GENERIC_BINARY_LINK_TITLES
        and not re.fullmatch(r"[\d\W_]+", current_title.strip())
    ):
        return current_title
    try:
        tree, _, _ = parse_html(raw, charset)
    except WlhError:
        tree = None

    if tree is not None:
        for xp in (
            "//div[contains(@class, 'ten-van-ban')]",
            "//td[contains(@class, 'title')]",
            "//div[contains(@class, 'vbInfo')]//strong",
            "//*[contains(@class, 'content-title') or contains(@class, 'box-vb-title')]",
            "//div[contains(@class, 'fulltext')]//p/strong",
        ):
            for node in tree.xpath(xp):
                txt = compact(node.text_content())
                if (
                    len(txt) >= 10
                    and not blocked(txt)
                    and txt.casefold() not in GENERIC_BINARY_LINK_TITLES
                ):
                    return txt[:300]

        body_text = compact(tree.text_content())
        m = _VN_DOC_HEAD_RE.search(body_text)
        if m:
            cand = compact(m.group(1))
            if len(cand) >= 10 and not blocked(cand):
                return cand[:300]

        if not doc_number:
            m_so = _VN_SO_HIEU_RE.search(body_text)
            if m_so:
                doc_number = compact(m_so.group(1))

    if doc_number:
        return f"Văn bản {doc_number}"

    parts: Any = urlsplit(url)
    qs = parse_qs(parts.query)
    if qs.get("ItemID"):
        return f"VBPL #{qs['ItemID'][0]}"
    if qs.get("id"):
        return f"CongBao #{qs['id'][0]}"
    stem = unquote(parts.path).rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if stem:
        return f"VanBan {stem}"
    return current_title


__all__ = [
    "GENERIC_BINARY_LINK_TITLES",
    "assert_binary_link_title_matches",
    "directory_title",
    "extract_vn_fallback_title",
    "meaningful_binary_link_title",
    "norm_title",
    "text_confirms_link_title",
]
