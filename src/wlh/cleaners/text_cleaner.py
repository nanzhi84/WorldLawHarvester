"""Pure text normalization, title enrichment, and UI boilerplate stripping (Stage 4 Cleaners)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, cast

UI_ACTION_LABELS = (
    "in bài",
    "bản in",
    "trang in",
    "in văn bản",
    "tải về",
    "tải xuống",
    "tải file",
    "lưu trữ",
    "chia sẻ",
    "quay lại",
    "trở về",
    "đóng",
    "xem thêm",
    "chi tiết",
    "download",
    "print",
    "print this page",
    "skip to document content",
    "report a problem",
    "▲ to the top",
    "to the top",
    "back",
    "トップに戻る",
    "【打印】",
    "【返回】",
    "【关闭】",
    "[打印]",
    "[返回]",
    "[关闭]",
    "打印本页",
    "关闭窗口",
    "打印",
    "返回",
    "关闭",
)

BLOCKERS = (
    r'<input\b[^>]*type=["\']?password',
    r"cf-chl-|g-recaptcha|\bhcaptcha\b",
    r"<title[^>]*>[^<]*(?:sign in|log in|login|captcha|access denied|just a moment)",
    r"verify you are human|checking your browser",
)


def compact(value: str) -> str:
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[ \t\u00a0]+", " ", value).strip()


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("****", "")
    value = re.sub(r"【字体[：:]\s*大\s*中\s*小\s*】", "", value)
    value = re.sub(r"\d{4}-\d{2}-\d{2}[ \t]+字体\s*\[\s*大\s+中\s+小\s*\]", "", value)
    value = re.sub(r"字体\s*\[\s*大\s+中\s+小\s*\]", "", value)
    value = re.sub(r"\[责任编辑[：:][^\]]*\]", "", value)
    lines = [line.rstrip() for line in value.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def apply_text_cleaners(text: str, strip_regexes: Sequence[str] = ()) -> str:
    cleaned = text
    for pattern in strip_regexes:
        cleaned = re.sub(pattern, "", cleaned)
    return normalized(cleaned)


def clean_title(title: str, strip_regexes: Sequence[str] = ()) -> str:
    cleaned = compact(title)
    for pattern in strip_regexes:
        cleaned = re.sub(pattern, "", cleaned)
    return compact(cleaned)


def enrich_bare_title(
    title: str | None,
    second: str | None = None,
    third: Any | None = None,
    fourth: str | None = None,
    fifth: str | None = None,
    *,
    tree: Any | None = None,
    body_text: str | None = None,
) -> str | None:
    """Enrich bare document titles (e.g. bare act/circular numbers) from body or summary text."""
    if not title:
        return title
    cleaned_title = re.sub(r"[^\S\u3000]+", " ", title).strip()
    doc_num_str = ""
    candidate_texts: list[str] = []

    if tree is not None or body_text is not None:
        doc_num_str = re.sub(r"[^\S\u3000]+", " ", second or "").strip()
        if tree is not None and hasattr(tree, "text_content"):
            candidate_texts.append(tree.text_content())
        if body_text:
            candidate_texts.append(body_text)
    else:
        for arg in (second, third, fourth, fifth):
            if isinstance(arg, str) and arg.strip():
                candidate_texts.append(arg.strip())

    is_bare = len(cleaned_title) < 25 and bool(
        re.match(
            r"^(?:luật|pháp\s+lệnh|nghị\s+quyết|nghị\s+định|thông\s+tư|quyết\s+định|chỉ\s+thị|văn\s+bản|số)?[\s:]*[0-9]{1,5}(?:/[0-9]{4})?/[a-z0-9-]+$",
            cleaned_title,
            re.I,
        )
    )
    if not is_bare and doc_num_str:
        if cleaned_title.casefold() == doc_num_str.casefold():
            is_bare = True
        elif re.match(
            rf"^(?:luật|pháp\s+lệnh|nghị\s+quyết|nghị\s+định|thông\s+tư|quyết\s+định|chỉ\s+thị)(?:\s+số)?\s+{re.escape(doc_num_str)}$",
            cleaned_title,
            re.IGNORECASE,
        ):
            is_bare = True
    if not is_bare:
        return cleaned_title

    if tree is not None and hasattr(tree, "xpath"):
        for node in tree.xpath('//div[contains(@class, "toanvan")]'):
            txt = re.sub(r"\s+", " ", node.text_content()).strip()
            if 15 <= len(txt) <= 260 and txt.casefold() != cleaned_title.casefold():
                return txt
            m_tv = re.search(
                r"(?:LUẬT|PHÁP LỆNH|NGHỊ QUYẾT|NGHỊ ĐỊNH|THÔNG TƯ|QUYẾT ĐỊNH)\s+((?:Về|Sửa đổi|Ban hành|Quy định|Phê chuẩn|Bãi bỏ)[^.;\n]{10,200})",
                txt,
                re.IGNORECASE,
            )
            if m_tv:
                subj = re.sub(r"\s+", " ", m_tv.group(1)).strip()
                return f"{cleaned_title} - {subj}"

    for raw in candidate_texts:
        m = re.search(
            r"(?:LU[ẬA]T|PH[ÁA]P\s+L[ỆE]NH|NGH[ỊI]\s+QUY[ẾÉEe]T|NGH[ỊI]\s+[ĐD][ỊI]NH|TH[ÔO]NG\s+T[ƯU]|QUY[ẾÉEe]T\s+[ĐD][ỊI]NH|CH[ỈI]\s+TH[ỊI])(?!\s+s[ốo]\b)[^\n]*\n+\s*((?:Về|Sửa đổi|Ban hành|Quy định|Phê chuẩn|Phê duyệt|Bãi bỏ|Thông qua|Chương trình|Kế hoạch|Hướng dẫn)[\s\S]{10,280}?)(?=(?:\n|\s+)(?:QU[ỐÓO]C\s+H[ỘỘO]I|U[ỶY]\s+BAN|CH[ÍI]NH\s+PH[ỦU]|TH[ỦU]\s+T[ƯU][ỚƠO]NG|C[ăa]n\s+c[ứư]|ĐI[ỀE]U\s+1)\b|\n\s*\n|$)",
            raw,
            re.IGNORECASE,
        )
        if m:
            subj = re.sub(r"\s+", " ", m.group(1)).strip(" .;-_")
            if subj and subj.casefold() not in cleaned_title.casefold():
                return f"{cleaned_title} - {subj}"
        m2 = re.search(
            r"(?:LUẬT|PHÁP LỆNH|NGHỊ QUYẾT|NGHỊ ĐỊNH|THÔNG TƯ|QUYẾT ĐỊNH|CHỈ THỊ)\s+((?:Về|Sửa đổi|Ban hành|Quy định|Phê chuẩn|Bãi bỏ|Hướng dẫn)[^.;\n]{10,200})",
            raw,
            re.IGNORECASE,
        )
        if m2:
            subj = re.sub(r"\s+", " ", m2.group(1)).strip(" .;-_")
            if subj and subj.casefold() not in cleaned_title.casefold():
                return f"{cleaned_title} - {subj}"
    return cleaned_title


def _blocked_patterns(config: Mapping[str, Any] | None) -> Sequence[str]:
    if not isinstance(config, Mapping):
        return ()
    patterns: list[str] = []
    for section_key in ("fetcher", "fetch", "cleaners"):
        sec = config.get(section_key)
        if isinstance(sec, Mapping):
            sec_map = cast(Mapping[str, Any], sec)
            for pat_key in ("blocked_patterns", "invalid_body_patterns"):
                raw = sec_map.get(pat_key)
                if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
                    patterns.extend(str(x) for x in cast(Sequence[Any], raw) if x)
    for pat_key in ("blocked_patterns", "invalid_body_patterns"):
        raw = config.get(pat_key)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            patterns.extend(str(x) for x in cast(Sequence[Any], raw) if x)
    return list(dict.fromkeys(patterns))


def blocked(body: bytes | str, config: Mapping[str, Any] | None = None) -> bool:
    if isinstance(body, bytes):
        text = body[:262144].decode("utf-8", errors="replace")
    else:
        text = body[:262144]
    return any(re.search(p, text, re.I) for p in (*BLOCKERS, *_blocked_patterns(config)))

