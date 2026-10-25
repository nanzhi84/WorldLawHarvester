"""Unified delivery data semantic quality contract (wlh.delivery_quality.v1).

Enforces semantic invariants across replay tests, live collection acceptance, and
snapshot verification to prevent silent data corruption (boilerplate titles,
duplicate titles across distinct resources, polluted document numbers,
signature-only OCR stubs, duplicated DOM body paragraphs, and projection drift).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, cast

from wlh.config import DOCUMENT_TYPES
from wlh.core import WlhError
from wlh.db import connect
from wlh.engine.objects import Objects
from wlh.projections.canonical import COLUMNS, project_canonical

DELIVERY_QUALITY_CONTRACT = "wlh.delivery_quality.v1"

BOILERPLATE_TITLES = frozenset(
    {
        "contents",
        "table of contents",
        "index",
        "home",
        "untitled",
        "document",
        "目录",
        "首页",
        "网站首页",
        "附件",
        "附件下载",
        "点击下载",
        "下载附件",
        "查看详情",
        "详情",
        "download",
        "download pdf",
        "download file",
        "attachment",
        "tải file",
        "tải file đính kèm",
        "file đính kèm",
        "tải về",
        "xem chi tiết",
        "chi tiết",
    }
)

_BARE_DOC_NUMBER_LABEL_RE = re.compile(
    r"^(?:文号|发文字号|案号|số|số\s+ký\s+hiệu|no\.?)[:：\s]*$", re.IGNORECASE
)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VI_PLACEHOLDER_PATTERNS = (
    "bạn chưa đăng nhập thành viên",
    "văn bản này đang cập nhật nội dung",
    "đây là tiện ích dành cho tài khoản tiêu chuẩn hoặc nâng cao",
    "vui lòng xem nội dung chi tiết trong file đính kèm",
)
_GAZETTE_RUNNING_HEADER_RE = re.compile(r"(?im)^\s*công\s+báo\s*/\s*số\s+\d+.*$")
_UUID_FILENAME_SUFFIX_RE = re.compile(
    r"(?:^|[-_\s]+)[0-9a-f]{32}(?:\.[a-z0-9]{2,5})?$", re.IGNORECASE
)
_SALUTATION_PREFIX_RE = re.compile(
    r"^bộ\s+tài\s+chính\s*[.:,-]\s*", re.IGNORECASE
)


def _normalize_title_token(value: str) -> str:
    norm = unicodedata.normalize("NFKC", value).casefold()
    norm = re.sub(r"^[\s.,;:!?\"'()\[\]{}<>\-_/|\\]+|[\s.,;:!?\"'()\[\]{}<>\-_/|\\]+$", "", norm)
    return re.sub(r"\s+", " ", norm).strip()


def _is_signature_only_stub(md_text: str, non_ws_len: int) -> bool:
    if non_ws_len >= 300:
        return False
    lower = unicodedata.normalize("NFC", md_text).casefold()
    en_sig = "signer:" in lower and ("certificate id:" in lower or "signed at:" in lower)
    vi_sig = "ký bởi" in lower and ("chứng thư" in lower or "ngày ký" in lower)
    return en_sig or vi_sig


def _has_duplicated_substantive_paragraph(md_text: str) -> bool:
    blocks = re.split(r"\n\s*\n+", md_text.strip())
    substantive_blocks: list[str] = []
    prev_compact: str | None = None
    has_adjacent_dup = False
    for block in blocks:
        stripped = block.strip()
        if not stripped or stripped.startswith(("#", "|", "```", "- ", "* ")):
            continue
        compact = re.sub(r"\s+", " ", stripped)
        if len(compact) < 60:
            continue
        if prev_compact is not None and compact == prev_compact:
            has_adjacent_dup = True
        substantive_blocks.append(compact)
        prev_compact = compact
    if has_adjacent_dup and len(substantive_blocks) <= 40:
        return True
    if len(substantive_blocks) >= 2:
        unique_count = len(set(substantive_blocks))
        dup_instances = len(substantive_blocks) - unique_count
        if dup_instances * 2 >= len(substantive_blocks) or unique_count <= len(substantive_blocks) * 0.6:
            return True
    return False


def audit_delivery_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    objects: Any | None = None,
    source_config: Mapping[str, Any] | Any | None = None,
    require_multi_resource: bool = False,
    min_markdown_chars: int = 15,
) -> dict[str, Any]:
    """Audit a sequence of `wlh.document_row` records against semantic quality invariants."""
    violations: list[dict[str, Any]] = []

    def _add(code: str, detail: str, row: Mapping[str, Any] | None = None) -> None:
        violations.append(
            {
                "code": code,
                "resource_id": str(row["resource_id"]) if row and row.get("resource_id") else None,
                "row_index": int(row["row_index"])
                if row and isinstance(row.get("row_index"), int)
                else None,
                "detail": detail,
            }
        )

    if not rows:
        _add("empty_delivery", "no delivery rows produced")
        return {
            "contract": DELIVERY_QUALITY_CONTRACT,
            "passed": False,
            "resource_count": 0,
            "row_count": 0,
            "violations": violations,
            "violation_codes": ["empty_delivery"],
        }

    cfg_source_name = ""
    if isinstance(source_config, Mapping):
        cfg_map = cast(Mapping[str, Any], source_config)
        meta_raw = cfg_map.get("metadata")
        if isinstance(meta_raw, Mapping):
            meta_map = cast(Mapping[str, Any], meta_raw)
            cfg_source_name = str(meta_map.get("source_name") or "").strip()

    by_resource: dict[str, list[Mapping[str, Any]]] = {}
    for idx, row in enumerate(rows):
        res_id = str(row.get("resource_id") or f"anon-{idx}")
        by_resource.setdefault(res_id, []).append(row)

    resource_count = len(by_resource)
    if require_multi_resource and resource_count < 2:
        _add(
            "single_resource_in_collection",
            f"expected >= 2 distinct resources in collection, got {resource_count}",
            rows[0],
        )

    # Check cross-resource title uniqueness when >= 2 resources are present
    if resource_count >= 2:
        primary_rows: list[Mapping[str, Any]] = []
        for res_rows in by_resource.values():
            non_att = [r for r in res_rows if r.get("role") != "independent_attachment"]
            primary_rows.append(non_att[0] if non_att else res_rows[0])
        norm_titles = {
            _normalize_title_token(str(r.get("title") or ""))
            for r in primary_rows
            if str(r.get("title") or "").strip()
        }
        distinct_doc_numbers = {
            str(r.get("doc_number") or "").strip()
            for r in primary_rows
            if str(r.get("doc_number") or "").strip()
        }
        if len(norm_titles) <= 1 and len(distinct_doc_numbers) < resource_count:
            _add(
                "duplicate_resource_titles",
                "multiple distinct resources share an identical primary title without distinguishing doc_number",
                primary_rows[0],
            )

    for row in rows:
        projected = project_canonical(row)
        if tuple(projected.keys()) != COLUMNS:
            _add(
                "canonical_projection_violation",
                "projected keys do not match canonical 16-column contract",
                row,
            )

        title_raw = str(row.get("title") or "").strip()
        source_name_raw = str(row.get("source_name") or cfg_source_name or "").strip()
        norm_title = _normalize_title_token(title_raw)
        norm_source_name = _normalize_title_token(source_name_raw)

        if not norm_title:
            _add("boilerplate_or_garbage_title", "title is empty", row)
        elif norm_title in BOILERPLATE_TITLES:
            _add(
                "boilerplate_or_garbage_title",
                f"title matches boilerplate placeholder: {title_raw!r}",
                row,
            )
        elif norm_source_name and norm_title == norm_source_name:
            _add(
                "boilerplate_or_garbage_title",
                f"title duplicates source_name: {title_raw!r}",
                row,
            )
        elif _UUID_FILENAME_SUFFIX_RE.search(title_raw):
            _add(
                "boilerplate_or_garbage_title",
                f"title contains unstripped UUID hash filename: {title_raw!r}",
                row,
            )
        elif _SALUTATION_PREFIX_RE.search(title_raw):
            _add(
                "boilerplate_or_garbage_title",
                f"title contains unstripped salutation/agency prefix: {title_raw!r}",
                row,
            )

        doc_num = row.get("doc_number")
        if doc_num is not None:
            if not isinstance(doc_num, str):
                _add("polluted_or_malformed_doc_number", "doc_number is not a string", row)
            else:
                dn_stripped = doc_num.strip()
                if (
                    not dn_stripped
                    or "\n" in doc_num
                    or "\r" in doc_num
                    or len(dn_stripped) > 120
                    or (len(title_raw) > 30 and dn_stripped == title_raw)
                    or bool(_BARE_DOC_NUMBER_LABEL_RE.match(dn_stripped))
                ):
                    _add(
                        "polluted_or_malformed_doc_number",
                        f"malformed or polluted doc_number: {doc_num[:60]!r}",
                        row,
                    )

        doc_type = row.get("doc_type")
        if doc_type not in DOCUMENT_TYPES:
            _add("canonical_projection_violation", f"invalid doc_type: {doc_type!r}", row)

        detail_url = str(row.get("detail_url") or "")
        if not detail_url.startswith(("http://", "https://")):
            _add("canonical_projection_violation", f"invalid detail_url: {detail_url!r}", row)

        for date_field in ("published_at", "effective_at", "expired_at"):
            val = projected.get(date_field)
            if val is not None and (not isinstance(val, str) or not _ISO_DATE_RE.match(val)):
                _add(
                    "canonical_projection_violation",
                    f"invalid ISO date in {date_field}: {val!r}",
                    row,
                )

        if row.get("role") == "independent_attachment" and row.get("volume_order") is not None:
            _add(
                "canonical_projection_violation",
                "independent_attachment must not have volume_order",
                row,
            )

        if row.get("original_hash") is not None and not row.get("original_file"):
            _add(
                "canonical_projection_violation",
                "original_file missing when original_hash is present",
                row,
            )

        md_text: str | None = None
        if isinstance(row.get("markdown_text"), str):
            md_text = str(row["markdown_text"])
        elif objects is not None and row.get("markdown_hash"):
            md_bytes = objects.get(str(row["markdown_hash"]))
            md_text = md_bytes.decode("utf-8", errors="replace")

        if md_text is not None:
            if md_text.count("\ufffd") >= 3:
                _add("garbled_markdown", "markdown contains >= 3 replacement characters", row)
            lowered_md = unicodedata.normalize("NFC", md_text).casefold()
            if any(pat in lowered_md for pat in _VI_PLACEHOLDER_PATTERNS):
                _add(
                    "blocked_or_placeholder_markdown",
                    "markdown contains login-wall or placeholder stub text",
                    row,
                )
            stripped_gazette = _GAZETTE_RUNNING_HEADER_RE.sub("", md_text)
            non_ws_len = len(re.sub(r"\s+", "", md_text))
            substantive_non_ws_len = len(re.sub(r"\s+", "", stripped_gazette))
            if _is_signature_only_stub(md_text, non_ws_len):
                _add(
                    "signature_only_markdown",
                    "markdown contains only a digital signature block without substantive text",
                    row,
                )
            elif substantive_non_ws_len < min_markdown_chars:
                _add(
                    "stub_markdown",
                    f"markdown substantive non-whitespace length {substantive_non_ws_len} < {min_markdown_chars}",
                    row,
                )
            if _has_duplicated_substantive_paragraph(md_text):
                _add(
                    "duplicated_body_markdown",
                    "markdown repeats an identical substantive paragraph multiple times",
                    row,
                )

    violation_codes = sorted({v["code"] for v in violations})
    return {
        "contract": DELIVERY_QUALITY_CONTRACT,
        "passed": len(violations) == 0,
        "resource_count": resource_count,
        "row_count": len(rows),
        "violations": violations,
        "violation_codes": violation_codes,
    }


def assert_delivery_quality(
    rows: Sequence[Mapping[str, Any]],
    *,
    objects: Any | None = None,
    source_config: Mapping[str, Any] | Any | None = None,
    require_multi_resource: bool = False,
    min_markdown_chars: int = 15,
) -> dict[str, Any]:
    """Audit delivery rows and raise `WlhError('delivery_quality_failed')` on any violation."""
    report = audit_delivery_rows(
        rows,
        objects=objects,
        source_config=source_config,
        require_multi_resource=require_multi_resource,
        min_markdown_chars=min_markdown_chars,
    )
    if not report["passed"]:
        err = WlhError("delivery_quality_failed")
        err.report = report  # type: ignore[attr-defined]
        raise err
    return report


def verify_source_delivery(
    source_id: str,
    *,
    objects: Any | None = None,
    require_multi_resource: bool | None = None,
    min_markdown_chars: int = 15,
) -> dict[str, Any]:
    """Load current delivered rows for `source_id` from PostgreSQL + S3 CAS and audit quality."""
    objs = objects if objects is not None else Objects()
    with connect() as c:
        source_row = c.execute(
            "SELECT config FROM wlh.source WHERE id = %s", (source_id,)
        ).fetchone()
        source_cfg: Mapping[str, Any] | None = source_row["config"] if source_row else None
        rows: list[dict[str, Any]] = c.execute(
            """
            SELECT f.source_id, r.id AS resource_id, d.*
            FROM wlh.document_row d
            JOIN wlh.version v ON v.id = d.version_id
            JOIN wlh.resource r ON r.id = v.resource_id AND r.current_version_id = v.id
            JOIN wlh.frontier f ON f.id = r.frontier_id
            WHERE f.source_id = %s
            ORDER BY d.row_index, r.id
            """,
            (source_id,),
        ).fetchall()

    multi_req = (
        require_multi_resource
        if require_multi_resource is not None
        else (source_id != "dd-375")
    )
    return audit_delivery_rows(
        rows,
        objects=objs,
        source_config=source_cfg,
        require_multi_resource=multi_req,
        min_markdown_chars=min_markdown_chars,
    )
