"""CAS fixture bundle exporter and manifest-driven local replay router (wlh.fixture.v1).

Replaces manual `_EXACT_ROUTES` / `_PREFIX_ROUTES` tables and hand-crafted HTML
stubs with self-describing per-source `manifest.json` bundles exported from
verified PostgreSQL `wlh.acquisition` records and S3 CAS raw payloads.
"""

from __future__ import annotations

import html
import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import lxml.etree as etree

from wlh.core import WlhError, atomic_write
from wlh.processor.parsers import normalize_parser_config
from wlh.storage.cas import Objects
from wlh.storage.db import connect

etree: Any

FIXTURE_SCHEMA = "wlh.fixture.v1"

D1N_COOKIE_CHALLENGE = (
    b'<html><body><script>document.cookie="D1N=ok123"'
    b'+"; expires=Thu, 31-Dec-37 23:55:55 GMT; path=/";'
    b"window.location.reload(true);</script></body></html>"
)


def _is_textual_mime(mime_type: str, raw_bytes: bytes = b"") -> bool:
    if raw_bytes.startswith((b"PK\x03\x04", b"%PDF", b"\xd0\xcf\x11\xe0", b"\x1f\x8b")):
        return False
    low = mime_type.lower()
    if any(
        bin_tok in low
        for bin_tok in (
            "officedocument",
            "openxmlformats",
            "msword",
            "ms-excel",
            "ms-powerpoint",
            "octet-stream",
            "pdf",
            "zip",
            "rtf",
        )
    ):
        return False
    return (
        low.startswith("text/")
        or "html" in low
        or "xml" in low
        or "json" in low
        or "javascript" in low
    )


def _ext_for_mime(mime_type: str, path: str) -> str:
    low = mime_type.lower()
    path_low = path.lower()
    for bin_ext in (".docx", ".doc", ".xlsx", ".xls", ".rtf", ".pdf", ".zip"):
        if path_low.endswith(bin_ext):
            return bin_ext
    if "wordprocessingml" in low:
        return ".docx"
    if "msword" in low:
        return ".doc"
    if "spreadsheetml" in low:
        return ".xlsx"
    if "ms-excel" in low:
        return ".xls"
    if "html" in low:
        return ".html"
    if "xml" in low:
        return ".xml"
    if "json" in low:
        return ".json"
    if "pdf" in low:
        return ".pdf"
    if "zip" in low:
        return ".zip"
    if "plain" in low:
        return ".txt"
    for ext in (".html", ".xml", ".json", ".pdf", ".zip", ".txt", ".htm", ".shtml", ".aspx"):
        if path_low.endswith(ext):
            return ".html" if ext in {".htm", ".shtml", ".aspx"} else ext
    return ".bin"


def _slug_filename(index: int, route_path: str, mime_type: str) -> str:
    parts = urlsplit(route_path)
    raw_name = Path(parts.path.rstrip("/") or "index").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_name).strip("-.") or "page"
    ext = _ext_for_mime(mime_type, parts.path)
    if stem.lower().endswith(ext):
        stem = stem[: -len(ext)]
    if parts.query:
        q_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", parts.query).strip("-")[:24]
        if q_slug:
            stem = f"{stem}-{q_slug}"
    return f"{index:02d}-{stem[:48]}{ext}"


def _route_path_from_url(raw_url: str) -> str:
    parts = urlsplit(raw_url)
    path = parts.path or "/"
    if parts.query:
        return f"{path}?{parts.query}"
    return path


def _should_prune_candidate_url(
    raw_url: str,
    base_url: str,
    exported_paths: set[str],
    unselected_paths: set[str],
    detail_patterns: list[re.Pattern[str]],
    allowed_prefixes: list[str] | None = None,
    entry_paths: set[str] | None = None,
) -> bool:
    cleaned = html.unescape(raw_url.strip())
    if not cleaned or cleaned.startswith("#"):
        return False
    if cleaned.lower().startswith(("javascript:__dopostback", "javascript:pagesubmit")):
        return True
    if cleaned.lower().startswith(("javascript:", "javscript:")) or "(" in cleaned:
        m_js = re.search(r"""['"]((?:https?://|/|\.\./)[^'"]+)['"]""", cleaned)
        if not m_js:
            return False
        cleaned = m_js.group(1)
    elif cleaned.lower().startswith(("mailto:", "tel:", "data:")):
        return False
    resolved = urljoin(base_url, cleaned)
    parts = urlsplit(resolved)
    if parts.scheme not in {"http", "https"}:
        return False
    cand_path = parts.path or "/"
    cand_route = f"{cand_path}?{parts.query}" if parts.query else cand_path
    if cand_path.lower().endswith("/hot/addhotlaw.ashx") and parts.query:
        m_pcode = re.search(
            r"(?:^|&)pcode=([A-Za-z0-9]+)", parts.query, flags=re.IGNORECASE
        )
        if m_pcode:
            cand_path = "/LawClass/LawAll.aspx"
            cand_route = f"{cand_path}?pcode={m_pcode.group(1)}"
            resolved = f"{parts.scheme}://{parts.netloc}{cand_route}"
    plaw_mapped = (
        re.sub(
            r"^/app/details/(PLAW-[A-Za-z0-9_-]+).*",
            r"/content/pkg/\1/uslm/\1.xml",
            cand_path,
        )
        if cand_path.startswith("/app/details/PLAW-")
        else cand_route
    )
    unquoted_exported = {unquote(p) for p in exported_paths}
    unquoted_unselected = {unquote(p) for p in unselected_paths}
    if (
        cand_route in exported_paths
        or f"{cand_route}?iframe=true" in exported_paths
        or plaw_mapped in exported_paths
        or unquote(cand_route) in unquoted_exported
    ):
        return False
    if entry_paths and (
        cand_route in entry_paths
        or f"{cand_route}?iframe=true" in entry_paths
        or unquote(cand_route) in {unquote(p) for p in entry_paths}
    ):
        return False
    if (
        cand_route in unselected_paths
        or f"{cand_route}?iframe=true" in unselected_paths
        or plaw_mapped in unselected_paths
        or unquote(cand_route) in unquoted_unselected
    ):
        return True
    plaw_full = f"{parts.scheme}://{parts.netloc}{plaw_mapped}"
    if detail_patterns and any(
        p.search(resolved)
        or p.search(cand_route)
        or p.search(cand_path)
        or p.search(plaw_mapped)
        or p.search(plaw_full)
        for p in detail_patterns
    ):
        return True
    if allowed_prefixes and any(
        cand_path.startswith(pref) or cand_path == pref.rstrip("/")
        for pref in allowed_prefixes
    ):
        return True
    return False


def _prune_ecfr_children(
    nodes: list[Any], exported_part_queries: set[str]
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue
        node = dict(cast(Mapping[str, Any], raw_node))
        ntype = str(node.get("type") or "").lower()
        ident = str(node.get("identifier") or "")
        if ntype == "part":
            if f"?part={ident}" in exported_part_queries:
                kept.append(node)
            continue
        children_raw = node.get("children")
        if isinstance(children_raw, list):
            pruned_children = _prune_ecfr_children(
                cast(list[Any], children_raw), exported_part_queries
            )
            if pruned_children:
                node["children"] = pruned_children
                kept.append(node)
    return kept


def _prune_unselected_links(
    text: str,
    base_url: str,
    exported_paths: set[str],
    unselected_paths: set[str],
    detail_patterns: list[re.Pattern[str]],
    allowed_prefixes: list[str] | None = None,
    entry_paths: set[str] | None = None,
    selected_sample_urls: set[str] | None = None,
) -> str:
    def _replace_attr(m: re.Match[str]) -> str:
        attr, quote, val = m.group(1), m.group(2), m.group(3)
        if val.strip() == "#":
            return f"{attr}={quote}#unselected{quote}"
        if _should_prune_candidate_url(
            val,
            base_url,
            exported_paths,
            unselected_paths,
            detail_patterns,
            allowed_prefixes=allowed_prefixes,
            entry_paths=entry_paths,
        ):
            if attr.lower() == "onclick":
                return f"{attr}={quote}{quote}"
            return f"{attr}={quote}#unselected{quote}"
        return m.group(0)

    def _replace_unquoted_attr(m: re.Match[str]) -> str:
        attr, val = m.group(1), m.group(2)
        if _should_prune_candidate_url(
            val,
            base_url,
            exported_paths,
            unselected_paths,
            detail_patterns,
            allowed_prefixes=allowed_prefixes,
            entry_paths=entry_paths,
        ):
            return f'{attr}="#unselected"'
        return m.group(0)

    if text.lstrip().startswith("{"):
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                payload_dict = cast(dict[str, Any], payload)
                if isinstance(payload_dict.get("pagedList"), dict):
                    paged_list = cast(dict[str, Any], payload_dict["pagedList"])
                    items_raw = paged_list.get("items")
                    if isinstance(items_raw, list):
                        filtered_items: list[dict[str, Any]] = [
                            cast(dict[str, Any], raw_item)
                            for raw_item in cast(list[Any], items_raw)
                            if isinstance(raw_item, dict)
                            and f"/api/Datasets/{cast(dict[str, Any], raw_item).get('datasetId')}"
                            in exported_paths
                        ]
                        paged_list["items"] = filtered_items
                        paged_list["hasNextPage"] = False
                        paged_list["lastPage"] = int(paged_list.get("currentPage") or 1)
                        return json.dumps(payload_dict, ensure_ascii=False, indent=2)

                if isinstance(payload_dict.get("results"), list):
                    results_list = cast(list[Any], payload_dict["results"])
                    sample_paths = {
                        _route_path_from_url(u) for u in (selected_sample_urls or set()) if u
                    }
                    match_tokens = (
                        {p for p in exported_paths if p not in (entry_paths or set()) and p != "/"}
                        | sample_paths
                        | (selected_sample_urls or set())
                    )
                    filtered_results: list[Any] = []
                    for item in results_list:
                        if not isinstance(item, dict):
                            continue
                        item_dict = cast(dict[str, Any], item)
                        item_json = json.dumps(item_dict, ensure_ascii=False)
                        dec_id = item_dict.get("decision_id")
                        if (
                            (isinstance(dec_id, str) and f"/api/decisions/{dec_id}" in exported_paths)
                            or any(tok and tok in item_json for tok in match_tokens)
                        ):
                            filtered_results.append(item_dict)
                    if filtered_results:
                        payload_dict["results"] = filtered_results
                        if "next_page_url" in payload_dict:
                            payload_dict["next_page_url"] = None
                        if "total_pages" in payload_dict:
                            payload_dict["total_pages"] = 1
                        if "count" in payload_dict:
                            payload_dict["count"] = len(filtered_results)
                        if "has_more" in payload_dict:
                            payload_dict["has_more"] = False
                        if "next_offset" in payload_dict:
                            payload_dict["next_offset"] = None
                        if "returned" in payload_dict:
                            payload_dict["returned"] = len(filtered_results)
                        if "next" in payload_dict:
                            payload_dict["next"] = None
                        if isinstance(payload_dict.get("pagination"), dict):
                            pag = cast(dict[str, Any], payload_dict["pagination"])
                            pag["max_page"] = 1
                            pag["total_items"] = len(filtered_results)
                        return json.dumps(payload_dict, ensure_ascii=False, indent=2)

                if isinstance(payload_dict.get("titles"), list):
                    exported_title_nums = {
                        int(m.group(1))
                        for p in exported_paths
                        for m in [re.search(r"/title-(\d+)\.(?:xml|json)", p)]
                        if m is not None
                    }
                    if exported_title_nums:
                        payload_dict["titles"] = [
                            t
                            for t in cast(list[Any], payload_dict["titles"])
                            if isinstance(t, dict)
                            and int(cast(dict[str, Any], t).get("number") or -1)
                            in exported_title_nums
                        ]
                        return json.dumps(payload_dict, ensure_ascii=False, indent=2)

                if isinstance(payload_dict.get("children"), list) and payload_dict.get("type") == "title":
                    exported_part_queries = {
                        f"?{urlsplit(p).query}"
                        for p in exported_paths
                        if urlsplit(p).query.startswith("part=")
                    }
                    if exported_part_queries:
                        payload_dict["children"] = _prune_ecfr_children(
                            cast(list[Any], payload_dict["children"]), exported_part_queries
                        )
                        return json.dumps(payload_dict, ensure_ascii=False, indent=2)
        except ValueError:
            pass

    out = re.sub(
        r'\b(href|src|docurl|data-url|data-href|onclick)\s*=\s*(["\'])((?:(?!\2)[^\r\n])+)\2',
        _replace_attr,
        text,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"\b(href|src|docurl|data-url|data-href|onclick)\s*=\s*(&quot;|&#34;|&#x22;|&#39;|&#x27;)(.*?)\2",
        _replace_attr,
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r'\b(href|src|docurl|data-url|data-href)\s*=\s*([^\s"\'<>]+)',
        _replace_unquoted_attr,
        out,
        flags=re.IGNORECASE,
    )

    def _replace_xml_tag(m: re.Match[str]) -> str:
        tag, val = m.group(1), m.group(2)
        if _should_prune_candidate_url(
            val,
            base_url,
            exported_paths,
            unselected_paths,
            detail_patterns,
            allowed_prefixes=allowed_prefixes,
            entry_paths=entry_paths,
        ):
            return f"<{tag}>#unselected</{tag}>"
        return m.group(0)

    out = re.sub(
        r"<(link|loc|LinkToXML)>([^<]+)</\1>",
        _replace_xml_tag,
        out,
        flags=re.IGNORECASE,
    )
    if "<akomaNtoso" in out and "<header" in out:
        out = re.sub(
            r"(<header\b[^>]*>.*?)<img\b[^>]*/>(.*?</header>)",
            r"\1\2",
            out,
            flags=re.IGNORECASE | re.DOTALL,
        )
    return out


def _templatize_text(
    text: str,
    primary_netlocs: list[str],
    secondary_netlocs: list[str],
) -> tuple[str, bool]:
    out = text
    changed = False
    for sec in sorted(set(secondary_netlocs), key=len, reverse=True):
        if not sec:
            continue
        for prefix in (f"https://{sec}", f"http://{sec}"):
            if prefix in out:
                out = out.replace(prefix, "{secondary_base}")
                changed = True
        if sec in out:
            out = out.replace(sec, "{sec_host}")
            changed = True

    for pri in sorted(set(primary_netlocs), key=len, reverse=True):
        if not pri:
            continue
        for prefix in (f"https://{pri}", f"http://{pri}"):
            if prefix in out:
                out = out.replace(prefix, "http://{host}")
                changed = True
        if pri in out:
            out = out.replace(pri, "{host}")
            changed = True

    return out, changed


def export_source_fixture(
    source_id: str,
    output_dir: str | os.PathLike[str],
    *,
    run_id: str | None = None,
    max_resources: int = 2,
    source_yaml: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Export verified `wlh.acquisition` records and S3 CAS bytes into a `wlh.fixture.v1` bundle."""
    from datetime import date, datetime

    from wlh.runbook_samples import (
        resolve_candidate_quality_route,
        stratify_verification_samples,
    )

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    objects = Objects()

    existing_expectations: dict[str, Any] | None = None
    existing_manifest_path = out_path / "manifest.json"
    if existing_manifest_path.is_file():
        try:
            existing_raw = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
            if isinstance(existing_raw, dict):
                existing_dict = cast(dict[str, Any], existing_raw)
                if isinstance(existing_dict.get("expectations"), dict):
                    existing_expectations = dict(
                        cast(Mapping[str, Any], existing_dict["expectations"])
                    )
        except (OSError, ValueError):
            pass

    with connect() as c:
        if run_id is not None:
            run_row = c.execute(
                "SELECT id, source_id, config FROM wlh.run WHERE id = %s", (run_id,)
            ).fetchone()
        else:
            run_row = c.execute(
                """
                SELECT r.id, r.source_id, r.config
                FROM wlh.run r
                WHERE r.source_id = %s
                ORDER BY
                    (EXISTS (
                        SELECT 1 FROM wlh.acquisition a
                        WHERE a.run_id = r.id AND a.state = 'archived'
                          AND COALESCE(a.actual_url, a.request_url) NOT LIKE 'http://127.0.0.1%%'
                          AND COALESCE(a.actual_url, a.request_url) NOT LIKE 'http://localhost%%'
                    )) DESC,
                    (EXISTS (
                        SELECT 1 FROM wlh.acquisition a
                        WHERE a.run_id = r.id AND a.state = 'archived'
                    )) DESC,
                    (SELECT count(*) FROM wlh.observation o
                     JOIN wlh.resource res ON res.id = o.resource_id
                     WHERE o.run_id = r.id AND o.outcome <> 'failed'
                       AND res.current_version_id IS NOT NULL) DESC,
                    (r.mode = 'collect' AND r.status = 'complete') DESC,
                    r.completed_at DESC NULLS LAST,
                    r.created_at DESC
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()
        if run_row is None:
            raise WlhError("fixture_export_run_not_found")

        resolved_run_id: str = run_row["id"]
        cfg: dict[str, Any] = normalize_parser_config(
            dict(cast(Mapping[str, Any], run_row["config"] or {}))
        )
        if source_yaml is not None and Path(source_yaml).is_file():
            import yaml

            loaded = yaml.safe_load(Path(source_yaml).read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cfg = normalize_parser_config(dict(cast(Mapping[str, Any], loaded)))

        raw_obs_rows = c.execute(
            """
            SELECT o.id, o.resource_id, r.public_url, r.current_version_id, v.result_ref
            FROM wlh.observation o
            JOIN wlh.resource r ON r.id = o.resource_id
            LEFT JOIN wlh.version v ON v.id = r.current_version_id
            WHERE o.run_id = %s AND o.outcome <> 'failed' AND r.current_version_id IS NOT NULL
            ORDER BY o.observed_at, o.id
            """,
            (resolved_run_id,),
        ).fetchall()
        docs_cfg = cast(Mapping[str, Any], cfg.get("documents") or {})
        raw_pats = [str(p) for p in cast(list[Any], docs_cfg.get("detail_url_patterns") or [])]
        if raw_pats:
            filtered_obs = [
                r
                for r in raw_obs_rows
                if any(re.search(pat, str(r.get("public_url") or "")) for pat in raw_pats)
            ]
            candidate_obs_rows = filtered_obs if filtered_obs else raw_obs_rows
        else:
            candidate_obs_rows = raw_obs_rows

        version_ids = [
            str(r["current_version_id"])
            for r in candidate_obs_rows
            if r.get("current_version_id") is not None
        ]
        doc_rows_by_version: dict[str, list[dict[str, Any]]] = {}
        if version_ids:
            fetched_doc_rows = c.execute(
                """
                SELECT version_id, role, row_index, title, doc_number, issuer,
                       published_at, effective_at, markdown_hash, original_hash,
                       original_media_type
                FROM wlh.document_row
                WHERE version_id = ANY(%s)
                ORDER BY version_id, row_index
                """,
                (version_ids,),
            ).fetchall()
            for dr in fetched_doc_rows:
                doc_rows_by_version.setdefault(str(dr["version_id"]), []).append(dict(dr))

        from wlh.runbook_samples import FORBIDDEN_PLACEHOLDERS

        candidate_pool: list[dict[str, Any]] = []
        for obs_r in candidate_obs_rows:
            ver_id = str(obs_r.get("current_version_id") or "")
            ver_rows = doc_rows_by_version.get(ver_id, [])
            non_att = [dr for dr in ver_rows if dr.get("role") != "independent_attachment"]
            primary = non_att[0] if non_att else (ver_rows[0] if ver_rows else {})
            md_text = ""
            md_hash = primary.get("markdown_hash")
            if isinstance(md_hash, str) and md_hash:
                try:
                    md_text = objects.get(md_hash).decode("utf-8", errors="replace")
                except Exception:
                    md_text = ""

            is_native_fast = True
            for dr in ver_rows:
                orig_hash = dr.get("original_hash")
                if isinstance(orig_hash, str) and orig_hash:
                    try:
                        orig_bytes = objects.get(orig_hash)
                        if orig_bytes.startswith(b"%PDF"):
                            import pymupdf as _fitz  # type: ignore[import-untyped]

                            fitz_mod: Any = _fitz
                            with fitz_mod.open(stream=orig_bytes, filetype="pdf") as pdf_doc:
                                p1_chars = (
                                    len(str(pdf_doc[0].get_text("text") or "").strip())
                                    if len(pdf_doc) > 0
                                    else 0
                                )
                                if p1_chars < 200:
                                    is_native_fast = False
                                    break
                    except Exception:
                        pass

            quality_route = resolve_candidate_quality_route(primary, md_text)
            result_ref = obs_r.get("result_ref")
            if (
                quality_route != "accept_with_warning"
                and isinstance(result_ref, str)
                and result_ref
            ):
                try:
                    doc_obj: Any = json.loads(
                        objects.get(result_ref).decode("utf-8", errors="replace")
                    )
                    if isinstance(doc_obj, Mapping):
                        for d_entry in cast(
                            Sequence[Any], cast(Mapping[str, Any], doc_obj).get("deliverables") or ()
                        ):
                            if isinstance(d_entry, Mapping):
                                q_rep = cast(Mapping[str, Any], d_entry).get("quality_report")
                                if isinstance(q_rep, Mapping) and (
                                    cast(Mapping[str, Any], q_rep).get("recommended_route")
                                    == "accept_with_warning"
                                ):
                                    quality_route = "accept_with_warning"
                                    break
                except Exception:
                    pass

            pub_raw = primary.get("published_at")
            pub_str = (
                pub_raw.isoformat()[:10]
                if isinstance(pub_raw, (date, datetime))
                else (str(pub_raw).strip() if pub_raw is not None else None)
            )
            eff_raw = primary.get("effective_at")
            eff_str = (
                eff_raw.isoformat()[:10]
                if isinstance(eff_raw, (date, datetime))
                else (str(eff_raw).strip() if eff_raw is not None else None)
            )
            combined_text = " ".join(
                [
                    str(obs_r.get("public_url") or ""),
                    str(primary.get("title") or ""),
                    md_text,
                ]
            )
            has_forbidden = any(bad in combined_text for bad in FORBIDDEN_PLACEHOLDERS)
            candidate_pool.append(
                {
                    "obs_id": obs_r["id"],
                    "resource_id": obs_r["resource_id"],
                    "public_url": obs_r["public_url"],
                    "title": primary.get("title"),
                    "doc_number": primary.get("doc_number"),
                    "issuer": primary.get("issuer"),
                    "published_at": pub_str or None,
                    "effective_at": eff_str or None,
                    "markdown": md_text,
                    "quality_route": quality_route,
                    "_has_forbidden": has_forbidden,
                    "_is_native_fast": is_native_fast,
                }
            )

        clean_pool = [c for c in candidate_pool if not c.get("_has_forbidden")]
        if clean_pool:
            candidate_pool = clean_pool
        native_pool = [c for c in candidate_pool if c.get("_is_native_fast")]
        if len(native_pool) >= max_resources:
            candidate_pool = native_pool

        stratified_obs = stratify_verification_samples(
            candidate_pool, max_items=max_resources
        )
        selected_obs_ids = {str(item["obs_id"]) for item in stratified_obs}
        samples_meta: list[dict[str, Any]] = []
        sampling_strata: list[str] = []
        for item in stratified_obs:
            item_strata = [
                str(s)
                for s in cast(Sequence[Any], item.get("sampling_strata") or ["baseline"])
                if str(s).strip()
            ] or ["baseline"]
            for s_tag in item_strata:
                if s_tag not in sampling_strata:
                    sampling_strata.append(s_tag)
            samples_meta.append(
                {
                    "resource_id": str(item["resource_id"]),
                    "public_url": str(item["public_url"]),
                    "title": item.get("title"),
                    "doc_number": item.get("doc_number"),
                    "effective_at": item.get("effective_at"),
                    "quality_route": str(item.get("quality_route") or "accept"),
                    "sampling_strata": item_strata,
                }
            )
        if not sampling_strata:
            sampling_strata = ["baseline"]

        all_ev_rows = c.execute(
            """
            SELECT oe.observation_id, oe.acquisition_id
            FROM wlh.observation_evidence oe
            JOIN wlh.observation o ON o.id = oe.observation_id
            WHERE o.run_id = %s
            """,
            (resolved_run_id,),
        ).fetchall()

        acqs = c.execute(
            """
            SELECT id, role, acquisition_kind, http_method, request_url, actual_url,
                   parent_acquisition_id, mime_type, charset, archive_ref, http_status
            FROM wlh.acquisition
            WHERE run_id = %s AND state = 'archived'
            ORDER BY started_at
            """,
            (resolved_run_id,),
        ).fetchall()

    if not acqs:
        raise WlhError("fixture_export_empty_acquisitions")

    for old_file in out_path.iterdir():
        if old_file.is_file() and old_file.name != "manifest.json":
            old_file.unlink()

    selected_acq_ids: set[str] = set()
    unselected_acq_ids: set[str] = set()
    for ev in all_ev_rows:
        if ev["observation_id"] in selected_obs_ids:
            selected_acq_ids.add(ev["acquisition_id"])
        else:
            unselected_acq_ids.add(ev["acquisition_id"])
    direct_selected_acq_ids = set(selected_acq_ids)

    by_id = {a["id"]: a for a in acqs}
    # Walk parent chain for selected acquisitions
    frontier_ids = list(selected_acq_ids)
    while frontier_ids:
        cid = frontier_ids.pop()
        acq_obj = by_id.get(cid)
        if acq_obj and acq_obj.get("parent_acquisition_id"):
            pid = acq_obj["parent_acquisition_id"]
            if pid not in selected_acq_ids:
                selected_acq_ids.add(pid)
                frontier_ids.append(pid)

    allowed_hosts: list[str] = [str(h) for h in cast(list[Any], cfg.get("allowed_hosts") or [])]
    primary_host = allowed_hosts[0] if allowed_hosts else "127.0.0.1"
    secondary_hosts = allowed_hosts[1:] if len(allowed_hosts) > 1 else []

    primary_netlocs: list[str] = [primary_host]
    secondary_netlocs: list[str] = list(secondary_hosts)

    entry_paths: set[str] = set()
    for entry_url in cast(list[Any], cfg.get("entry_urls") or []):
        ep = urlsplit(str(entry_url))
        if ep.netloc and ep.netloc not in primary_netlocs:
            primary_netlocs.append(ep.netloc)
        entry_paths.add(_route_path_from_url(str(entry_url)))

    allowed_prefixes: list[str] = [
        str(p) for p in cast(list[Any], cfg.get("allowed_path_prefixes") or [])
    ]
    docs_cfg = cast(Mapping[str, Any], cfg.get("documents") or {})
    detail_patterns = [
        re.compile(str(p)) for p in cast(list[Any], docs_cfg.get("detail_url_patterns") or [])
    ]

    for a in acqs:
        for u in (a.get("request_url"), a.get("actual_url")):
            if not u:
                continue
            up = urlsplit(str(u))
            if not up.netloc:
                continue
            if up.hostname in secondary_hosts or up.netloc in secondary_netlocs:
                if up.netloc not in secondary_netlocs:
                    secondary_netlocs.append(up.netloc)
            elif up.netloc not in primary_netlocs:
                primary_netlocs.append(up.netloc)

    from collections import deque

    has_rendered_dom = any(x.get("acquisition_kind") == "rendered_dom" for x in acqs)

    def _effective_route_path(acq_row: Mapping[str, Any]) -> str:
        eff_u = str(acq_row.get("actual_url") or acq_row.get("request_url") or "/")
        rp = _route_path_from_url(eff_u)
        if acq_row.get("acquisition_kind") == "rendered_dom":
            req_u = str(acq_row.get("request_url") or "")
            if req_u:
                req_rp = _route_path_from_url(req_u)
                if req_rp in entry_paths:
                    return req_rp
        return rp

    selected_paths: set[str] = set()
    unselected_paths: set[str] = set()
    for a in acqs:
        rpath = _effective_route_path(a)
        if a["id"] in selected_acq_ids:
            selected_paths.add(rpath)
        elif a["id"] in unselected_acq_ids:
            unselected_paths.add(rpath)

    # Build link graph across candidate acquisitions to find shortest navigation paths from entry_paths to selected_paths
    out_links: dict[str, set[str]] = {}
    for a in acqs:
        if a["id"] in unselected_acq_ids:
            continue
        rpath = _effective_route_path(a)
        eff_u = str(a.get("actual_url") or a.get("request_url") or "/")
        if rpath.startswith("/api/versioner/v1/structure/"):
            m_t = re.search(r"/title-(\d+)\.json", rpath)
            if m_t:
                t_num = m_t.group(1)
                for ep in entry_paths:
                    out_links.setdefault(ep, set()).add(rpath)
                for sp in selected_paths:
                    if f"/title-{t_num}.xml" in sp:
                        out_links.setdefault(rpath, set()).add(sp)
            continue
        m_type = str(a.get("mime_type") or "text/html").split(";")[0].strip().lower()
        if "html" not in m_type and "xml" not in m_type:
            continue
        if any(
             token in m_type
            for token in ("officedocument", "openxmlformats", "msword", "ms-excel", "pdf", "zip")
        ):
            continue
        try:
            raw_b = objects.get(str(a["archive_ref"]))
        except Exception:
            continue
        if not _is_textual_mime(m_type, raw_b):
            continue
        text_b = raw_b.decode("utf-8", errors="ignore")
        targets = out_links.setdefault(rpath, set())
        for m_attr in re.finditer(
            r'\b(?:href|src|docurl|data-url|data-href|onclick)\s*=\s*(["\'])((?:(?!\1)[^\r\n])+)\1',
            text_b,
            flags=re.IGNORECASE,
        ):
            val = html.unescape(m_attr.group(2).strip())
            if val.lower().startswith("javascript:") or "(" in val:
                m_js = re.search(r"['\"]((?:https?://|/|\.\./)[^'\"]+)['\"]", val)
                if m_js:
                    val = m_js.group(1)
            if not val or val.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
                continue
            cand_rp = _route_path_from_url(urljoin(eff_u, val))
            targets.add(cand_rp)
            if cand_rp.endswith("/index.do"):
                targets.add(f"{cand_rp}?iframe=true")
        for m_xml in re.finditer(
            r"<(?:link|loc|LinkToXML)>([^<]+)</(?:link|loc|LinkToXML)>",
            text_b,
            flags=re.IGNORECASE,
        ):
            val = html.unescape(m_xml.group(1).strip())
            if val:
                targets.add(_route_path_from_url(urljoin(eff_u, val)))

    bfs_selected_paths: set[str] = set()
    reachable_targets: set[str] = set()
    for target_p in selected_paths:
        if target_p in entry_paths:
            bfs_selected_paths.add(target_p)
            reachable_targets.add(target_p)
            continue
        visited = set(entry_paths)
        queue: deque[list[str]] = deque([[ep] for ep in sorted(entry_paths)])
        while queue:
            cur_path = queue.popleft()
            last_node = cur_path[-1]
            if last_node == target_p:
                bfs_selected_paths.update(cur_path)
                reachable_targets.add(target_p)
                break
            if len(cur_path) >= 5:
                continue
            for nxt in sorted(out_links.get(last_node, set())):
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append([*cur_path, nxt])

    best_acq_by_path: dict[str, dict[str, Any]] = {}
    ordered_paths: list[str] = []

    has_selected_parent_links = any(
        by_id.get(cid, {}).get("parent_acquisition_id") for cid in direct_selected_acq_ids
    )
    for a in acqs:
        eff_url = str(a.get("actual_url") or a.get("request_url") or "/")
        parts = urlsplit(eff_url)
        route_path = _effective_route_path(a)
        if route_path == "/robots.txt":
            continue
        if (
            allowed_hosts
            and parts.hostname
            and parts.hostname not in allowed_hosts
            and parts.hostname not in {"127.0.0.1", "localhost"}
        ):
            continue

        att_pat_str = str(docs_cfg.get("attachment_url_pattern") or "")
        if (
            att_pat_str
            and not re.search(att_pat_str, eff_url, re.I)
            and route_path.lower().endswith(".pdf")
            and route_path not in entry_paths
            and not any(
                p.search(eff_url) or p.search(route_path) for p in detail_patterns
            )
        ):
            continue

        aid = a["id"]
        mime_low = str(a.get("mime_type") or "text/html").split(";")[0].strip().lower()
        is_selected = (
            aid in selected_acq_ids
            or route_path in selected_paths
            or route_path in bfs_selected_paths
        )
        is_unselected_ev = (
            aid in unselected_acq_ids
            and not is_selected
            and route_path not in entry_paths
        )
        is_unselected_branch = (
            has_selected_parent_links and not is_selected and route_path not in entry_paths
        )
        is_unselected_nav = (
            not is_selected
            and route_path not in entry_paths
            and (
                route_path.startswith("/api/versioner/v1/structure/")
                or ("html" in mime_low and bool(reachable_targets))
                or (
                    has_rendered_dom
                    and (
                        "javascript" in mime_low
                        or "handler=" in route_path
                        or "/athena/forward/" in route_path
                    )
                )
            )
        )
        is_unselected_detail = (
            not is_selected
            and route_path not in entry_paths
            and bool(detail_patterns)
            and any(
                p.search(eff_url) or p.search(route_path) or p.search(parts.path or "/")
                for p in detail_patterns
            )
        )
        if selected_obs_ids and (
            is_unselected_ev
            or is_unselected_detail
            or is_unselected_branch
            or is_unselected_nav
        ):
            unselected_paths.add(route_path)
            continue

        if route_path not in best_acq_by_path:
            best_acq_by_path[route_path] = a
            ordered_paths.append(route_path)
        else:
            prev = best_acq_by_path[route_path]
            prev_status = int(prev.get("http_status") or 200)
            cur_status = int(a.get("http_status") or 200)
            prev_key = (
                prev["id"] in direct_selected_acq_ids,
                200 <= prev_status < 300,
                str(prev.get("http_method") or "GET").upper() == "GET",
                prev.get("acquisition_kind") == "rendered_dom",
            )
            cur_key = (
                aid in direct_selected_acq_ids,
                200 <= cur_status < 300,
                str(a.get("http_method") or "GET").upper() == "GET",
                a.get("acquisition_kind") == "rendered_dom",
            )
            if cur_key > prev_key:
                best_acq_by_path[route_path] = a

    exported_paths = set(ordered_paths)
    unselected_paths -= exported_paths
    selected_sample_urls = {str(s.get("public_url") or "") for s in samples_meta if s.get("public_url")}

    routes: list[dict[str, Any]] = []
    file_index = 1

    for route_path in ordered_paths:
        a = best_acq_by_path[route_path]
        eff_url = str(a.get("actual_url") or a.get("request_url") or "/")

        raw_bytes = objects.get(str(a["archive_ref"]))
        mime_type = str(a.get("mime_type") or "text/html").split(";")[0].strip()
        charset = str(a.get("charset") or "utf-8").strip() or "utf-8"
        status_code = int(a.get("http_status") or 200)
        if a.get("acquisition_kind") == "rendered_dom" and status_code == 202:
            status_code = 200

        filename = _slug_filename(file_index, route_path, mime_type)
        file_index += 1

        if _is_textual_mime(mime_type, raw_bytes):
            try:
                decoded = raw_bytes.decode(charset)
            except (UnicodeDecodeError, LookupError):
                decoded = raw_bytes.decode("utf-8", errors="replace")
                charset = "utf-8"

            if a.get("acquisition_kind") == "rendered_dom" and "html" in mime_type.lower():
                decoded = re.sub(
                    r"<script\b[^>]*>(.*?)</script>",
                    lambda m: m.group(0) if "document.write" in m.group(1) else "",
                    decoded,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                decoded = re.sub(
                    r'\s*data-wlh-truncated=(["\'])[^"\']*\1',
                    "",
                    decoded,
                    flags=re.IGNORECASE,
                )
                decoded = decoded.replace("next-page", "next-page-disabled")

            decoded = _prune_unselected_links(
                decoded,
                base_url=eff_url,
                exported_paths=exported_paths,
                unselected_paths=unselected_paths,
                detail_patterns=detail_patterns,
                allowed_prefixes=allowed_prefixes,
                entry_paths=entry_paths,
                selected_sample_urls=selected_sample_urls,
            )

            for bad_str in FORBIDDEN_PLACEHOLDERS:
                if bad_str in decoded:
                    decoded = decoded.replace(bad_str, "")

            templated, is_template = _templatize_text(
                decoded,
                primary_netlocs=primary_netlocs,
                secondary_netlocs=secondary_netlocs,
            )
            atomic_write(out_path / filename, templated.encode("utf-8"))
            content_type = (
                f"{mime_type}; charset={charset}"
                if "charset" not in mime_type.lower()
                else mime_type
            )
            routes.append(
                {
                    "match": "exact",
                    "path": route_path,
                    "fixture": filename,
                    "content_type": content_type,
                    "encoding": charset,
                    "status": status_code,
                    "template": is_template,
                    "requires_d1n_cookie": False,
                }
            )
        else:
            if raw_bytes.startswith(b"PK\x03\x04"):
                try:
                    import io
                    import zipfile

                    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                        xml_members = [
                            m
                            for m in zf.infolist()
                            if not m.is_dir() and m.filename.lower().endswith(".xml")
                        ]
                        if len(xml_members) == 1:
                            xml_data = zf.read(xml_members[0])
                            xml_root = etree.fromstring(
                                xml_data,
                                etree.XMLParser(
                                    resolve_entities=False, no_network=True, load_dtd=False
                                ),
                            )
                            law_nodes = xml_root.xpath(
                                './/*[local-name()="法規" or local-name()="Law"]'
                            )
                            if len(law_nodes) > max_resources:
                                for extra_node in law_nodes[max_resources:]:
                                    parent = extra_node.getparent()
                                    if parent is not None:
                                        parent.remove(extra_node)
                                trimmed_xml = etree.tostring(
                                    xml_root, encoding="utf-8", xml_declaration=True
                                )
                                out_buf = io.BytesIO()
                                with zipfile.ZipFile(
                                    out_buf, "w", compression=zipfile.ZIP_DEFLATED
                                ) as out_zf:
                                    out_zf.writestr(xml_members[0].filename, trimmed_xml)
                                raw_bytes = out_buf.getvalue()
                except Exception:
                    pass
            elif raw_bytes.startswith(b"%PDF"):
                try:
                    import pymupdf as _fitz  # type: ignore[import-untyped]

                    fitz_mod = cast(Any, _fitz)
                    with fitz_mod.open(stream=raw_bytes, filetype="pdf") as pdf_doc:
                        p1_chars = (
                            len(str(pdf_doc[0].get_text("text") or "").strip())
                            if len(pdf_doc) > 0
                            else 0
                        )
                        max_pages = 2 if p1_chars >= 200 else 1
                        if len(pdf_doc) > max_pages:
                            pdf_doc.select(list(range(max_pages)))
                        if p1_chars >= 200:
                            for page in pdf_doc:
                                if page.get_images(full=True):
                                    page.add_redact_annot(page.rect)
                                    page.apply_redactions(
                                        images=fitz_mod.PDF_REDACT_IMAGE_REMOVE,
                                        graphics=fitz_mod.PDF_REDACT_LINE_ART_NONE,
                                        text=fitz_mod.PDF_REDACT_TEXT_NONE,
                                    )
                        raw_bytes = bytes(pdf_doc.tobytes(garbage=4, deflate=True))
                except Exception:
                    pass
            atomic_write(out_path / filename, raw_bytes)
            routes.append(
                {
                    "match": "exact",
                    "path": route_path,
                    "fixture": filename,
                    "binary": True,
                    "content_type": mime_type,
                    "status": status_code,
                }
            )

    manifest_doc: dict[str, Any] = {
        "schema": FIXTURE_SCHEMA,
        "source_id": source_id,
        "run_id": resolved_run_id,
        "sampling_strata": sampling_strata,
        "samples": samples_meta,
        "routes": routes,
    }
    if existing_expectations:
        manifest_doc["expectations"] = existing_expectations
    atomic_write(
        out_path / "manifest.json",
        (json.dumps(manifest_doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )
    return {
        "schema": FIXTURE_SCHEMA,
        "source_id": source_id,
        "run_id": resolved_run_id,
        "output_dir": str(out_path),
        "route_count": len(routes),
        "sample_count": len(samples_meta),
        "sampling_strata": sampling_strata,
    }


def load_fixture_manifest(fixtures_root: str | os.PathLike[str], source_id: str) -> dict[str, Any]:
    """Load and validate `<fixtures_root>/<source_id>/manifest.json`."""
    manifest_path = Path(fixtures_root) / source_id / "manifest.json"
    if not manifest_path.is_file():
        raise WlhError("fixture_manifest_missing")
    try:
        raw_data: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise WlhError("invalid_fixture_manifest") from None
    if not isinstance(raw_data, dict):
        raise WlhError("invalid_fixture_manifest")
    data = cast(dict[str, Any], raw_data)
    if (
        data.get("schema") != FIXTURE_SCHEMA
        or data.get("source_id") != source_id
        or not isinstance(data.get("routes"), list)
    ):
        raise WlhError("invalid_fixture_manifest")
    return data


class FixtureRouter:
    """Manifest-driven HTTP route resolver for local black-box replay servers."""

    def __init__(
        self,
        fixtures_root: str | os.PathLike[str],
        *,
        binary_providers: Mapping[str, bytes] | None = None,
    ) -> None:
        self.fixtures_root = Path(fixtures_root)
        self.binary_providers: dict[str, bytes] = dict(binary_providers or {})
        self._manifests: dict[str, dict[str, Any]] = {}
        self._known_source_ids: list[str] | None = None

    def _get_manifest(self, source_id: str) -> dict[str, Any] | None:
        if not source_id:
            return None
        if source_id in self._manifests:
            return self._manifests[source_id]
        manifest_path = self.fixtures_root / source_id / "manifest.json"
        if not manifest_path.is_file():
            return None
        manifest = load_fixture_manifest(self.fixtures_root, source_id)
        self._manifests[source_id] = manifest
        return manifest

    def _list_source_ids(self) -> list[str]:
        if self._known_source_ids is not None:
            return self._known_source_ids
        if not self.fixtures_root.is_dir():
            return []
        self._known_source_ids = [
            child.name
            for child in sorted(self.fixtures_root.iterdir())
            if child.is_dir() and (child / "manifest.json").is_file()
        ]
        return self._known_source_ids

    def _match_in_manifest(
        self, manifest: Mapping[str, Any], path: str
    ) -> tuple[str, Mapping[str, Any]] | None:
        source_id = str(manifest["source_id"])
        routes: list[Mapping[str, Any]] = manifest.get("routes") or []
        for route in routes:
            if route.get("match", "exact") == "exact":
                rpath = str(route.get("path") or "")
                if rpath == path or unquote(rpath) == unquote(path):
                    return source_id, route
        for route in routes:
            if route.get("match") == "prefix" and path.startswith(str(route.get("path") or "")):
                return source_id, route
        return None

    def _resolve_route(self, source_id: str, path: str) -> tuple[str, Mapping[str, Any]] | None:
        manifest = self._get_manifest(source_id)
        if manifest is not None:
            matched = self._match_in_manifest(manifest, path)
            if matched is not None:
                return matched
            return None

        # Fallback for stateless routing or ephemeral synthetic source IDs without a dedicated directory (e.g. dd-998)
        for child_name in self._list_source_ids():
            child_manifest = self._get_manifest(child_name)
            if child_manifest is not None:
                matched = self._match_in_manifest(child_manifest, path)
                if matched is not None:
                    return matched
        return None

    def resolve_and_render(
        self,
        source_id: str,
        path: str,
        headers: Mapping[str, str] | None = None,
        secondary_netloc: str = "",
    ) -> tuple[int, str, bytes] | None:
        """Resolve `(source_id, path)` from `manifest.json` and render `(status, content_type, body)`."""
        req_headers = {k.lower(): v for k, v in (headers or {}).items()}
        effective_source_id = source_id.strip()
        effective_path = path
        if effective_path.startswith("/__source__/"):
            rest = effective_path[len("/__source__/") :]
            slash_idx = rest.find("/")
            if slash_idx > 0:
                effective_source_id = rest[:slash_idx]
                effective_path = rest[slash_idx:]
            elif rest:
                effective_source_id = rest
                effective_path = "/"
        header_source_id = req_headers.get("x-wlh-source-id", "").strip()
        if header_source_id:
            effective_source_id = header_source_id

        if effective_path == "/robots.txt":
            resolved = self._resolve_route(effective_source_id, effective_path)
            if resolved is None:
                return 200, "text/plain; charset=utf-8", b"User-agent: *\nAllow: /\n"

        resolved = self._resolve_route(effective_source_id, effective_path)
        if resolved is None:
            return None

        owner_source_id, spec = resolved
        if spec.get("requires_d1n_cookie") and "D1N=" not in req_headers.get("cookie", ""):
            return 200, "text/html; charset=utf-8", D1N_COOKIE_CHALLENGE

        status = int(spec.get("status", 200))
        content_type = str(spec.get("content_type") or "text/html; charset=utf-8")

        if "raw_utf8" in spec and spec["raw_utf8"] is not None:
            return status, content_type, str(spec["raw_utf8"]).encode("utf-8")

        binary_attr = spec.get("binary_attr")
        if binary_attr is not None:
            payload = self.binary_providers.get(str(binary_attr)) or self.binary_providers.get(
                "sample_pdf", b""
            )
            return status, content_type, payload

        fixture_rel = str(spec.get("fixture") or "")
        fixture_path = self.fixtures_root / owner_source_id / fixture_rel
        if not fixture_path.is_file():
            fixture_path = self.fixtures_root / fixture_rel
        if not fixture_path.is_file():
            return None

        if spec.get("binary") is True:
            return status, content_type, fixture_path.read_bytes()

        text = fixture_path.read_text(encoding="utf-8")
        if spec.get("template"):
            host = req_headers.get("host", "127.0.0.1")
            sec_host = secondary_netloc or host
            secondary_base = f"http://{secondary_netloc}" if secondary_netloc else ""
            text = (
                text.replace("{host}", host)
                .replace("{sec_host}", sec_host)
                .replace("{secondary_base}", secondary_base)
            )
        encoding = str(spec.get("encoding") or "utf-8")
        return status, content_type, text.encode(encoding)

    def load_route_map(self, source_id: str) -> dict[str, tuple[bytes, str]]:
        """Load `{path: (raw_bytes, content_type)}` from `<fixtures_root>/<source_id>/manifest.json`."""
        manifest = load_fixture_manifest(self.fixtures_root, source_id)
        directory = self.fixtures_root / source_id
        routes: dict[str, tuple[bytes, str]] = {}
        for route in manifest["routes"]:
            if "fixture" in route:
                routes[str(route["path"])] = (
                    (directory / str(route["fixture"])).read_bytes(),
                    str(route["content_type"]),
                )
        return routes

    @staticmethod
    def resolve_from_route_map(
        routes: Mapping[str, tuple[bytes, str]],
        path: str,
        origin: str,
        hosts: Sequence[str] = (),
    ) -> tuple[int, str, bytes] | None:
        """Resolve a request path against a route map with query-param fallback and host rewriting."""
        found = routes.get(path)
        if found is None:
            parts = urlsplit(path)
            parameters = parse_qs(parts.query)
            offset = parameters.get("offset", ["0"])[0]
            page = parameters.get("page", ["0"])[0]
            found = (
                routes.get(f"{parts.path}?offset={offset}")
                or routes.get(f"{parts.path}?page={page}")
                or routes.get(parts.path)
            )
        if path == "/robots.txt" and found is None:
            found = (b"User-agent: *\nAllow: /\n", "text/plain")
        if found is None:
            return None
        payload, media = found
        if any(t in media for t in ("text", "xml", "json")):
            origin_bytes = origin.encode("utf-8")
            payload = payload.replace(b"{host}", origin_bytes.removeprefix(b"http://"))
            for host in hosts:
                payload = payload.replace(f"https://{host}".encode("utf-8"), origin_bytes)
                payload = payload.replace(f"http://{host}".encode("utf-8"), origin_bytes)
        return 200, media, payload

