"""Unified delivery data semantic quality contract (wlh.delivery_quality.v1).

Enforces semantic invariants across replay tests, live collection acceptance, and
snapshot verification to prevent silent data corruption (boilerplate titles,
duplicate titles across distinct resources, polluted document numbers,
signature-only OCR stubs, duplicated DOM body paragraphs, and projection drift).
Language- and jurisdiction-specific rules are loaded declaratively from
`delivery_quality_rules.yaml`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import yaml

from wlh.config import DOCUMENT_TYPES
from wlh.core import WlhError
from wlh.storage.cas import Objects
from wlh.storage.document_store import load_source_delivery_rows
from wlh.storage.snapshots import COLUMNS, project_canonical

DELIVERY_QUALITY_CONTRACT = "wlh.delivery_quality.v1"
RULES_PATH = Path(__file__).with_name("delivery_quality_rules.yaml")
_REPO_SOURCES_DIR = Path(__file__).resolve().parents[3] / "sources"

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UUID_FILENAME_SUFFIX_RE = re.compile(
    r"(?:^|[-_\s]+)[0-9a-f]{32}(?:\.[a-z0-9]{2,5})?$", re.IGNORECASE
)


@dataclass(frozen=True)
class DeliveryQualityRulePack:
    """Declarative semantic quality rule pack scoped by language and jurisdiction."""

    language: str | None
    jurisdiction: str | None
    boilerplate_titles: frozenset[str]
    bare_doc_number_labels: tuple[str, ...]
    placeholder_patterns: tuple[str, ...]
    running_header_regexes: tuple[str, ...]
    salutation_prefix_regexes: tuple[str, ...]
    signature_stub_rules: tuple[dict[str, Any], ...]
    supplementary_heading_prefixes: tuple[str, ...]


def _normalize_title_token(value: str) -> str:
    norm = unicodedata.normalize("NFKC", value).casefold()
    norm = re.sub(
        r"^[\s.,;:!?\"'()\[\]{}<>\-_/|\\]+|[\s.,;:!?\"'()\[\]{}<>\-_/|\\]+$", "", norm
    )
    return re.sub(r"\s+", " ", norm).strip()


@lru_cache(maxsize=1)
def _raw_rule_registry() -> dict[str, Any]:
    raw: Any = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}


@lru_cache(maxsize=256)
def _source_metadata_from_id(source_id: str) -> tuple[str | None, str | None, str]:
    yaml_path = _REPO_SOURCES_DIR / f"{source_id}.yaml"
    if not yaml_path.is_file():
        return None, None, ""
    try:
        raw: Any = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            return None, None, ""
        raw_map = cast(Mapping[str, Any], raw)
        storage = raw_map.get("storage")
        meta = (
            cast(Mapping[str, Any], storage).get("metadata")
            if isinstance(storage, Mapping)
            else None
        )
        if not isinstance(meta, Mapping):
            return None, None, ""
        meta_map = cast(Mapping[str, Any], meta)
        lang = str(meta_map.get("language") or "").strip() or None
        jur = str(meta_map.get("jurisdiction") or "").strip() or None
        src_name = str(meta_map.get("source_name") or "").strip()
        return lang, jur, src_name
    except Exception:
        return None, None, ""


def _extract_config_context(
    source_config: Any,
) -> tuple[str | None, str | None, str, tuple[str, ...]]:
    if source_config is None:
        return None, None, "", ()
    if not isinstance(source_config, Mapping):
        storage_obj = getattr(source_config, "storage", None)
        meta_obj = (
            getattr(storage_obj, "metadata", None)
            if storage_obj is not None
            else None
        )
        cleaners_obj = getattr(source_config, "cleaners", None)
        if meta_obj is not None or cleaners_obj is not None:
            lang = str(getattr(meta_obj, "language", None) or "").strip() or None
            jur = str(getattr(meta_obj, "jurisdiction", None) or "").strip() or None
            src_name = str(getattr(meta_obj, "source_name", None) or "").strip()
            raw_inv = getattr(cleaners_obj, "invalid_body_patterns", None)
            extra_inv: list[str] = []
            if isinstance(raw_inv, Sequence) and not isinstance(raw_inv, (str, bytes)):
                extra_inv.extend(
                    str(p).strip().casefold()
                    for p in cast(Sequence[Any], raw_inv)
                    if str(p).strip()
                )
            return lang, jur, src_name, tuple(extra_inv)
        return None, None, "", ()

    cfg_map = cast(Mapping[str, Any], source_config)
    cleaners_raw = cfg_map.get("cleaners")
    cleaners_map: Mapping[str, Any] = (
        cast(Mapping[str, Any], cleaners_raw) if isinstance(cleaners_raw, Mapping) else {}
    )
    storage_raw = cfg_map.get("storage")
    storage_map: Mapping[str, Any] = (
        cast(Mapping[str, Any], storage_raw) if isinstance(storage_raw, Mapping) else {}
    )

    lang: str | None = None
    jur: str | None = None
    src_name = ""
    for candidate_meta in (
        cfg_map.get("metadata"),
        storage_map.get("metadata"),
        cleaners_map.get("metadata"),
        cleaners_map.get("defaults"),
        cfg_map,
    ):
        if isinstance(candidate_meta, Mapping):
            m = cast(Mapping[str, Any], candidate_meta)
            if not lang and m.get("language"):
                lang = str(m["language"]).strip() or None
            if not jur and m.get("jurisdiction"):
                jur = str(m["jurisdiction"]).strip() or None
            if not src_name and m.get("source_name"):
                src_name = str(m["source_name"]).strip()

    extra_invalid: list[str] = []
    raw_invalid = cleaners_map.get("invalid_body_patterns")
    if isinstance(raw_invalid, Sequence) and not isinstance(raw_invalid, (str, bytes)):
        extra_invalid.extend(
            str(p).strip().casefold()
            for p in cast(Sequence[Any], raw_invalid)
            if str(p).strip()
        )
    return lang, jur, src_name, tuple(extra_invalid)


@lru_cache(maxsize=256)
def _build_rule_pack_cached(
    resolved_lang: str | None,
    resolved_jur: str | None,
    extra_invalid: tuple[str, ...],
) -> DeliveryQualityRulePack:
    registry = _raw_rule_registry()
    common_sec = cast(Mapping[str, Any], registry.get("common") or {})
    languages_sec = cast(Mapping[str, Any], registry.get("languages") or {})
    jurisdictions_sec = cast(Mapping[str, Any], registry.get("jurisdictions") or {})

    sections_to_merge: list[Mapping[str, Any]] = [common_sec]

    if resolved_lang is not None or resolved_jur is not None:
        lang_key = (
            resolved_lang.split("-")[0].split("_")[0].lower()
            if resolved_lang
            else None
        )
        jur_key = resolved_jur.upper() if resolved_jur else None

        if jur_key and isinstance(jurisdictions_sec.get(jur_key), Mapping):
            jur_pack = cast(Mapping[str, Any], jurisdictions_sec[jur_key])
            if lang_key is None:
                linked_langs = jur_pack.get("languages")
                if isinstance(linked_langs, Sequence) and not isinstance(
                    linked_langs, (str, bytes)
                ):
                    for lk in cast(Sequence[Any], linked_langs):
                        lk_str = str(lk).strip().lower()
                        if isinstance(languages_sec.get(lk_str), Mapping):
                            sections_to_merge.append(
                                cast(Mapping[str, Any], languages_sec[lk_str])
                            )
            sections_to_merge.append(jur_pack)

        if lang_key and isinstance(languages_sec.get(lang_key), Mapping):
            sections_to_merge.append(cast(Mapping[str, Any], languages_sec[lang_key]))
    else:
        for lang_pack in languages_sec.values():
            if isinstance(lang_pack, Mapping):
                sections_to_merge.append(cast(Mapping[str, Any], lang_pack))
        for jur_pack in jurisdictions_sec.values():
            if isinstance(jur_pack, Mapping):
                sections_to_merge.append(cast(Mapping[str, Any], jur_pack))

    boilerplate: set[str] = set()
    bare_labels: list[str] = []
    placeholders: list[str] = []
    running_headers: list[str] = []
    salutations: list[str] = []
    sig_rules: list[dict[str, Any]] = []
    supp_headings: list[str] = []

    for sec in sections_to_merge:
        for t in cast(Sequence[Any], sec.get("boilerplate_titles") or ()):
            norm_t = _normalize_title_token(str(t))
            if norm_t:
                boilerplate.add(norm_t)
        for lbl in cast(Sequence[Any], sec.get("bare_doc_number_labels") or ()):
            s_lbl = str(lbl).strip()
            if s_lbl and s_lbl not in bare_labels:
                bare_labels.append(s_lbl)
        for pat in cast(Sequence[Any], sec.get("placeholder_patterns") or ()):
            s_pat = unicodedata.normalize("NFC", str(pat)).casefold().strip()
            if s_pat and s_pat not in placeholders:
                placeholders.append(s_pat)
        for hdr in cast(Sequence[Any], sec.get("running_header_regexes") or ()):
            s_hdr = str(hdr).strip()
            if s_hdr and s_hdr not in running_headers:
                running_headers.append(s_hdr)
        for sal in cast(Sequence[Any], sec.get("salutation_prefix_regexes") or ()):
            s_sal = str(sal).strip()
            if s_sal and s_sal not in salutations:
                salutations.append(s_sal)
        for sig in cast(Sequence[Any], sec.get("signature_stub_rules") or ()):
            if isinstance(sig, Mapping):
                sig_dict = dict(cast(Mapping[str, Any], sig))
                if sig_dict not in sig_rules:
                    sig_rules.append(sig_dict)
        for h_pref in cast(
            Sequence[Any], sec.get("supplementary_heading_prefixes") or ()
        ):
            s_pref = str(h_pref).strip()
            if s_pref and s_pref not in supp_headings:
                supp_headings.append(s_pref)

    for ext_pat in extra_invalid:
        if ext_pat not in placeholders:
            placeholders.append(ext_pat)

    return DeliveryQualityRulePack(
        language=resolved_lang,
        jurisdiction=resolved_jur,
        boilerplate_titles=frozenset(boilerplate),
        bare_doc_number_labels=tuple(bare_labels),
        placeholder_patterns=tuple(placeholders),
        running_header_regexes=tuple(running_headers),
        salutation_prefix_regexes=tuple(salutations),
        signature_stub_rules=tuple(sig_rules),
        supplementary_heading_prefixes=tuple(supp_headings),
    )


def load_delivery_quality_rules(
    *,
    language: str | None = None,
    jurisdiction: str | None = None,
    source_config: Mapping[str, Any] | Any | None = None,
    source_id: str | None = None,
) -> DeliveryQualityRulePack:
    """Load and merge declarative delivery quality rules for `language` and `jurisdiction`."""
    cfg_lang, cfg_jur, _cfg_name, extra_invalid = _extract_config_context(source_config)
    resolved_lang = (language or cfg_lang or "").strip() or None
    resolved_jur = (jurisdiction or cfg_jur or "").strip() or None

    if resolved_lang is None and resolved_jur is None and source_id:
        sid_lang, sid_jur, _sid_name = _source_metadata_from_id(source_id)
        resolved_lang = sid_lang
        resolved_jur = sid_jur

    return _build_rule_pack_cached(resolved_lang, resolved_jur, extra_invalid)


def __getattr__(name: str) -> Any:
    if name == "BOILERPLATE_TITLES":
        return load_delivery_quality_rules().boilerplate_titles
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _is_signature_only_stub(
    md_text: str,
    non_ws_len: int,
    signature_rules: Sequence[Mapping[str, Any]],
) -> bool:
    if non_ws_len >= 300:
        return False
    lower = unicodedata.normalize("NFC", md_text).casefold()
    for rule in signature_rules:
        primary = str(rule.get("primary") or "").casefold().strip()
        secondary_raw = rule.get("secondary") or ()
        secondary = [
            str(s).casefold().strip()
            for s in cast(Sequence[Any], secondary_raw)
            if str(s).strip()
        ]
        if primary and primary in lower and any(sec in lower for sec in secondary):
            return True
    return False


def _has_duplicated_substantive_paragraph(
    md_text: str,
    supplementary_heading_prefixes: Sequence[str] = (),
) -> bool:
    blocks = re.split(r"\n\s*\n+", md_text.strip())
    substantive_blocks: list[str] = []
    contextual_blocks: list[str] = []
    headings_by_level: dict[int, str] = {}
    sub_headers: list[str] = []
    prev_compact: str | None = None
    has_adjacent_dup = False
    supp_tuple = tuple(supplementary_heading_prefixes)
    for block in blocks:
        stripped = block.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            m_head = re.match(r"^(#+)\s*(.*)$", stripped)
            if m_head:
                head_text = re.sub(r"\s+", " ", m_head.group(2)).strip()
                lvl = (
                    1
                    if supp_tuple
                    and head_text.startswith(supp_tuple)
                    and len(m_head.group(1)) >= 2
                    else len(m_head.group(1))
                )
                for k in [k for k in headings_by_level if k >= lvl]:
                    headings_by_level.pop(k, None)
                headings_by_level[lvl] = head_text
            sub_headers.clear()
            prev_compact = None
            continue
        if (
            stripped.startswith(("|", "```", "- ", "* ", "<table"))
            or "<table" in stripped.lower()
        ):
            prev_compact = None
            continue
        compact = re.sub(r"\s+", " ", stripped)
        if len(compact) < 60:
            sub_headers = (sub_headers + [compact])[-6:]
            prev_compact = None
            continue
        if prev_compact is not None and compact == prev_compact:
            has_adjacent_dup = True
        substantive_blocks.append(compact)
        context_parts = [v for _, v in sorted(headings_by_level.items())]
        context_parts.extend(sub_headers)
        context_trail = " > ".join(context_parts)
        contextual_blocks.append(
            f"{context_trail} || {compact}" if context_trail else compact
        )
        prev_compact = compact
    if has_adjacent_dup and len(substantive_blocks) <= 40:
        return True
    if len(contextual_blocks) >= 2:
        unique_count = len(set(contextual_blocks))
        dup_instances = len(contextual_blocks) - unique_count
        if (
            dup_instances * 2 >= len(contextual_blocks)
            or unique_count <= len(contextual_blocks) * 0.6
        ):
            return True
    return False


def audit_delivery_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    objects: Any | None = None,
    source_config: Mapping[str, Any] | Any | None = None,
    language: str | None = None,
    jurisdiction: str | None = None,
    require_multi_resource: bool = False,
    min_markdown_chars: int = 15,
) -> dict[str, Any]:
    """Audit a sequence of `wlh.document_row` records against semantic quality invariants."""
    violations: list[dict[str, Any]] = []

    def _add(code: str, detail: str, row: Mapping[str, Any] | None = None) -> None:
        violations.append(
            {
                "code": code,
                "resource_id": str(row["resource_id"])
                if row and row.get("resource_id")
                else None,
                "row_index": int(row["row_index"])
                if row and isinstance(row.get("row_index"), int)
                else None,
                "detail": detail,
            }
        )

    first_source_id = (
        str(rows[0].get("source_id") or "").strip() or None if rows else None
    )
    rules = load_delivery_quality_rules(
        language=language,
        jurisdiction=jurisdiction,
        source_config=source_config,
        source_id=first_source_id,
    )
    rule_pack_meta = {
        "language": rules.language,
        "jurisdiction": rules.jurisdiction,
    }

    if not rows:
        _add("empty_delivery", "no delivery rows produced")
        return {
            "contract": DELIVERY_QUALITY_CONTRACT,
            "passed": False,
            "resource_count": 0,
            "row_count": 0,
            "rule_pack": rule_pack_meta,
            "violations": violations,
            "violation_codes": ["empty_delivery"],
        }

    _cfg_lang, _cfg_jur, cfg_source_name, _extra_inv = _extract_config_context(
        source_config
    )
    if not cfg_source_name and first_source_id:
        _sid_l, _sid_j, cfg_source_name = _source_metadata_from_id(first_source_id)

    bare_doc_number_re = (
        re.compile(
            r"^(?:" + "|".join(rules.bare_doc_number_labels) + r")[:：\s]*$",
            re.IGNORECASE,
        )
        if rules.bare_doc_number_labels
        else None
    )

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

    if resource_count >= 2:
        primary_rows: list[Mapping[str, Any]] = []
        for res_rows in by_resource.values():
            non_att = [
                r for r in res_rows if r.get("role") != "independent_attachment"
            ]
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
        elif norm_title in rules.boilerplate_titles:
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
        elif any(
            re.search(pat, title_raw, re.IGNORECASE)
            for pat in rules.salutation_prefix_regexes
        ):
            _add(
                "boilerplate_or_garbage_title",
                f"title contains unstripped salutation/agency prefix: {title_raw!r}",
                row,
            )

        doc_num = row.get("doc_number")
        if doc_num is not None:
            if not isinstance(doc_num, str):
                _add(
                    "polluted_or_malformed_doc_number",
                    "doc_number is not a string",
                    row,
                )
            else:
                dn_stripped = doc_num.strip()
                dn_segments = [
                    seg.strip()
                    for seg in re.split(r"[;；]", dn_stripped)
                    if seg.strip()
                ]
                max_seg_len = max((len(seg) for seg in dn_segments), default=0)
                if (
                    not dn_stripped
                    or "\n" in doc_num
                    or "\r" in doc_num
                    or max_seg_len > 120
                    or len(dn_stripped) > 300
                    or (len(title_raw) > 30 and dn_stripped == title_raw)
                    or (
                        bare_doc_number_re is not None
                        and bool(bare_doc_number_re.match(dn_stripped))
                    )
                ):
                    _add(
                        "polluted_or_malformed_doc_number",
                        f"malformed or polluted doc_number: {doc_num[:60]!r}",
                        row,
                    )

        doc_type = row.get("doc_type")
        if doc_type not in DOCUMENT_TYPES:
            _add(
                "canonical_projection_violation",
                f"invalid doc_type: {doc_type!r}",
                row,
            )

        detail_url = str(row.get("detail_url") or "")
        if not detail_url.startswith(("http://", "https://")):
            _add(
                "canonical_projection_violation",
                f"invalid detail_url: {detail_url!r}",
                row,
            )

        for date_field in ("published_at", "effective_at", "expired_at"):
            val = projected.get(date_field)
            if val is not None and (
                not isinstance(val, str) or not _ISO_DATE_RE.match(val)
            ):
                _add(
                    "canonical_projection_violation",
                    f"invalid ISO date in {date_field}: {val!r}",
                    row,
                )

        if (
            row.get("role") == "independent_attachment"
            and row.get("volume_order") is not None
        ):
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
                _add(
                    "garbled_markdown",
                    "markdown contains >= 3 replacement characters",
                    row,
                )
            lowered_md = unicodedata.normalize("NFC", md_text).casefold()
            if any(pat in lowered_md for pat in rules.placeholder_patterns):
                _add(
                    "blocked_or_placeholder_markdown",
                    "markdown contains login-wall or placeholder stub text",
                    row,
                )
            stripped_gazette = md_text
            for hdr_pat in rules.running_header_regexes:
                stripped_gazette = re.sub(hdr_pat, "", stripped_gazette)
            non_ws_len = len(re.sub(r"\s+", "", md_text))
            substantive_non_ws_len = len(re.sub(r"\s+", "", stripped_gazette))
            if _is_signature_only_stub(
                md_text, non_ws_len, rules.signature_stub_rules
            ):
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
            orig_file = str(row.get("original_file") or "").lower()
            is_binary_doc = orig_file.endswith(
                (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".rtf")
            )
            if not is_binary_doc and _has_duplicated_substantive_paragraph(
                md_text, rules.supplementary_heading_prefixes
            ):
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
        "rule_pack": rule_pack_meta,
        "violations": violations,
        "violation_codes": violation_codes,
    }


def assert_delivery_quality(
    rows: Sequence[Mapping[str, Any]],
    *,
    objects: Any | None = None,
    source_config: Mapping[str, Any] | Any | None = None,
    language: str | None = None,
    jurisdiction: str | None = None,
    require_multi_resource: bool = False,
    min_markdown_chars: int = 15,
) -> dict[str, Any]:
    """Audit delivery rows and raise `WlhError('delivery_quality_failed')` on any violation."""
    report = audit_delivery_rows(
        rows,
        objects=objects,
        source_config=source_config,
        language=language,
        jurisdiction=jurisdiction,
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
    """Load current delivered rows for `source_id` via storage boundary and audit quality."""
    objs = objects if objects is not None else Objects()
    source_cfg, rows = load_source_delivery_rows(source_id)

    multi_req = (
        require_multi_resource
        if require_multi_resource is not None
        else not bool(
            (source_cfg or {}).get("cleaners", {}).get("allow_single_resource", False)
        )
    )
    return audit_delivery_rows(
        rows,
        objects=objs,
        source_config=source_cfg,
        require_multi_resource=multi_req,
        min_markdown_chars=min_markdown_chars,
    )


require_source_delivery_quality = verify_source_delivery
