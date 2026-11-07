"""Declarative JSON dataset schema specifications and table-driven record converter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast
from urllib.parse import urljoin

from wlh.core import WlhError
from wlh.processor.extractors.registry import inline_markdown
from wlh.processor.parsers.record_base import items, scalar


def dot_lookup(obj: Any, dotted_path: str) -> Any:
    """Resolve a dot-separated key path on nested mappings and sequences."""
    parts = [p for p in dotted_path.split(".") if p]
    cur: Any = obj
    for idx, part in enumerate(parts):
        if part == "*" and isinstance(cur, Sequence) and not isinstance(cur, (str, bytes)):
            seq = cast(Sequence[Any], cur)
            rest = ".".join(parts[idx + 1 :])
            joined = "\n\n".join(
                str(v)
                for item in seq
                if (v := (dot_lookup(item, rest) if rest else item)) is not None
            )
            return joined or None
        if isinstance(cur, Mapping) and part in cur:
            cur = cast(Mapping[str, Any], cur)[part]
        elif (
            isinstance(cur, Sequence)
            and not isinstance(cur, (str, bytes))
            and part.isdigit()
            and int(part) < len(cast(Sequence[Any], cur))
        ):
            cur = cast(Sequence[Any], cur)[int(part)]
        else:
            return None
    return cur


def _empty_fields() -> dict[str, tuple[str, ...]]:
    return {}


@dataclass(frozen=True, slots=True)
class JsonSchemaSpec:
    """Declarative specification for one structured JSON legal dataset."""

    name: str
    namespace: str
    default_doc_type: str | None = "法律法规"
    include_doc_type: bool = True
    fingerprint_keys: tuple[str | tuple[str, ...], ...] = ()
    container_keys: tuple[str, ...] = ("results", "data", "items", "records")
    parallel_list_key: str | None = None
    model_filter: tuple[str, str] | None = None
    unwrap_key: str | None = None
    fields: Mapping[str, tuple[str, ...]] = field(default_factory=_empty_fields)
    scalar_fields: tuple[str, ...] = ()
    strip_title_comma: bool = False
    root_issuer_key: str | None = None
    fallback_detail_to_actual_url: bool = False
    allow_options_overrides: bool = False
    download_url_fields: tuple[str, ...] = ()
    version_document_groups: tuple[str, ...] = ()
    storage_path_field: str | None = None
    require_body_or_download: bool = False
    invalid_record_error: str | None = None
    required_fields: tuple[str, ...] = ()
    body_format: Literal["text", "html"] = "text"
    inline_text_markdown: bool = False
    enforce_budget_limit: bool = False


JSON_SCHEMA_SPECS: tuple[JsonSchemaSpec, ...] = (
    JsonSchemaSpec(
        name="openstates",
        namespace="openstates/bill",
        default_doc_type="法律法规",
        fingerprint_keys=("identifier", "from_organization", "openstates_url", "versions"),
        container_keys=("bills", "results"),
        fields={
            "external_key": ("id", "identifier"),
            "title": ("title",),
            "doc_number": ("identifier",),
            "issuer": ("from_organization",),
            "published_at": ("first_action_date", "latest_action_date"),
            "status": ("status", "latest_action_description"),
            "detail_url": ("url", "sources.0.url", "openstates_url"),
            "text": ("text", "abstract", "abstracts.*.abstract"),
        },
        scalar_fields=("issuer",),
        version_document_groups=("versions", "documents"),
        invalid_record_error="openstates_bill_invalid",
        required_fields=("title", "doc_number"),
        body_format="text",
        inline_text_markdown=True,
        enforce_budget_limit=False,
    ),
    JsonSchemaSpec(
        name="opencaselaw",
        namespace="opencaselaw/decision",
        default_doc_type="案例",
        fingerprint_keys=(
            "decision_id",
            "citation_string_de",
            ("docket_number", "full_text"),
            ("docket_number", "decision_date"),
        ),
        container_keys=("decisions", "results"),
        fields={
            "external_key": ("decision_id", "id", "docket_number"),
            "title": ("title", "citation_string_de", "docket_number"),
            "doc_number": ("docket_number", "case_number"),
            "issuer": ("court_name", "court"),
            "published_at": ("decision_date", "date"),
            "detail_url": ("canonical_url", "source_url", "url"),
            "text": ("full_text", "text"),
        },
        strip_title_comma=True,
        download_url_fields=("pdf_url",),
        require_body_or_download=True,
        body_format="text",
        enforce_budget_limit=True,
    ),
    JsonSchemaSpec(
        name="juriscraper",
        namespace="juriscraper/opinion",
        default_doc_type="案例",
        fingerprint_keys=(
            "case_name",
            "case_names",
            "caseName",
            "cluster_id",
            "download_url",
            "download_urls",
        ),
        container_keys=("cases", "opinions", "results"),
        parallel_list_key="case_names",
        fields={
            "external_key": (
                "cluster_id",
                "docket_number",
                "docket_numbers",
                "docketNumber",
                "$download_url",
            ),
            "title": ("case_name", "case_names", "caseName", "name"),
            "doc_number": ("docket_number", "docket_numbers", "docketNumber"),
            "issuer": ("court",),
            "published_at": ("date_filed", "case_dates", "dateFiled"),
            "status": ("precedential_status", "precedential_statuses", "status"),
            "detail_url": ("detail_url", "absolute_url"),
        },
        scalar_fields=("doc_number",),
        root_issuer_key="court_name",
        fallback_detail_to_actual_url=True,
        download_url_fields=("download_url", "download_urls"),
        storage_path_field="opinions.0.local_path",
        invalid_record_error="juriscraper_opinion_invalid",
        required_fields=("title", "downloads"),
        body_format="text",
        enforce_budget_limit=True,
    ),
    JsonSchemaSpec(
        name="oldp",
        namespace="oldp/case",
        default_doc_type="案例",
        fingerprint_keys=("ecli", "file_number", "fields:dict", "model=cases.case"),
        container_keys=("cases", "opinions", "results"),
        model_filter=("model", "cases.case"),
        unwrap_key="fields",
        fields={
            "external_key": ("ecli", "id", "file_number"),
            "title": ("title", "file_number"),
            "doc_number": ("file_number",),
            "issuer": ("court_name", "court"),
            "published_at": ("date",),
            "detail_url": ("source_url", "url"),
            "html": ("content", "text"),
        },
        download_url_fields=("pdf_url",),
        body_format="html",
        enforce_budget_limit=True,
    ),
    JsonSchemaSpec(
        name="worldwidelaw",
        namespace="worldwidelaw/document",
        default_doc_type="法律法规",
        include_doc_type=False,
        fingerprint_keys=("_id", "id", "title", "document_number", "number"),
        container_keys=("records", "results", "data", "items"),
        fields={
            "external_key": ("_id", "id", "document_number", "$detail_url", "url"),
            "title": ("title",),
            "doc_number": ("document_number", "number"),
            "issuer": ("issuing_authority", "authority", "issuer"),
            "published_at": ("date", "publication_date"),
            "effective_at": ("effective_date",),
            "detail_url": ("url",),
            "text": ("text", "content"),
        },
        allow_options_overrides=True,
        download_url_fields=("pdf_url", "docx_url"),
        body_format="text",
        enforce_budget_limit=False,
    ),
)

GENERIC_CONTAINER_KEYS: tuple[str, ...] = ("records", "results", "data", "items")


def _matches_fingerprint_rule(sample: Mapping[str, Any], rule: str | tuple[str, ...]) -> bool:
    if isinstance(rule, tuple):
        return all(_matches_fingerprint_rule(sample, part) for part in rule)
    if "=" in rule:
        key, expected = rule.split("=", 1)
        return sample.get(key) == expected
    if rule.endswith(":dict"):
        key = rule.removesuffix(":dict")
        return isinstance(sample.get(key), Mapping)
    return rule in sample


def _matches_fingerprint(sample: Mapping[str, Any], spec: JsonSchemaSpec) -> bool:
    return any(_matches_fingerprint_rule(sample, key) for key in spec.fingerprint_keys)


def match_json_schema(
    sample: Mapping[str, Any],
    *,
    container: str | None = None,
    specs: Sequence[JsonSchemaSpec] = JSON_SCHEMA_SPECS,
) -> JsonSchemaSpec:
    """Select the matching ``JsonSchemaSpec`` from a sample record dictionary."""
    if container and container not in GENERIC_CONTAINER_KEYS:
        container_specs = [s for s in specs if container in s.container_keys]
        if len(container_specs) == 1:
            return container_specs[0]
        if len(container_specs) > 1:
            for spec in container_specs:
                if _matches_fingerprint(sample, spec):
                    return spec
            for spec in container_specs[:-1]:
                id_keys = (
                    *spec.fields.get("external_key", ()),
                    *spec.fields.get("doc_number", ()),
                    *spec.download_url_fields,
                )
                if any(not k.startswith("$") and k in sample for k in id_keys):
                    return spec
            return container_specs[-1]
    for spec in specs[:-1]:
        if _matches_fingerprint(sample, spec):
            return spec
    for spec in specs:
        if _matches_fingerprint(sample, spec):
            return spec
    return specs[-1]


def _first_candidate(
    row: Mapping[str, Any],
    candidates: Sequence[str],
    context: Mapping[str, Any] | None = None,
) -> Any:
    for key in candidates:
        if key.startswith("$"):
            val = context.get(key[1:]) if context is not None else None
        else:
            val = dot_lookup(row, key) if "." in key else row.get(key)
        if val:
            return val
    return None


def _resolve_storage_url(
    row: Mapping[str, Any],
    storage_path_field: str,
    acquisition: Mapping[str, Any],
    *,
    options: Mapping[str, Any],
    config: Mapping[str, Any],
) -> str | None:
    local_path = dot_lookup(row, storage_path_field)
    if not local_path:
        return None
    storage_base = str(options.get("storage_base_url") or "").rstrip("/")
    if not storage_base:
        allowed = cast(
            Sequence[Any],
            cast(dict[str, Any], config.get("scheduler") or {}).get("allowed_hosts")
            or config.get("allowed_hosts")
            or (),
        )
        storage_host = next((str(h) for h in allowed if str(h).startswith("storage.")), "")
        storage_base = (
            f"https://{storage_host}"
            if storage_host
            else urljoin(str(acquisition.get("actual_url") or ""), "/").rstrip("/")
        )
    return f"{storage_base}/{str(local_path).lstrip('/')}" if storage_base else None


def _resolve_downloads(
    row: Mapping[str, Any],
    spec: JsonSchemaSpec,
    *,
    title: Any,
    acquisition: Mapping[str, Any],
    options: Mapping[str, Any],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    downloads: list[dict[str, Any]] = []

    for group in spec.version_document_groups:
        documents: list[dict[str, Any]] = row.get(group) or []
        for document in documents:
            links: list[dict[str, Any]] = document.get("links") or []
            preferred = next(
                (v for v in links if v.get("media_type") == "application/pdf"),
                links[0] if links else None,
            )
            if preferred:
                note = str(document.get("note") or "").strip()
                bill_title = str(title or "").strip()
                dl_title = (
                    f"{bill_title} ({note})"
                    if note and note.lower() != bill_title.lower()
                    else bill_title
                )
                downloads.append(
                    {
                        "url": preferred["url"],
                        "title": dl_title,
                        "role": "primary" if not downloads else "independent_attachment",
                    }
                )

    att_rules = options.get("attachment_rules") if spec.allow_options_overrides else None
    if isinstance(att_rules, list):
        for rule_raw in cast(list[Any], att_rules):
            if not isinstance(rule_raw, Mapping):
                continue
            rule = cast(Mapping[str, Any], rule_raw)
            u = dot_lookup(row, str(rule.get("url_path") or ""))
            if u:
                t_path = rule.get("title_path")
                t_val = dot_lookup(row, t_path) if isinstance(t_path, str) else None
                dl: dict[str, Any] = {"url": str(u)}
                if t_val:
                    dl["title"] = str(t_val)
                downloads.append(dl)

    if not downloads and (spec.download_url_fields or spec.storage_path_field):
        direct_url = _first_candidate(row, spec.download_url_fields)
        if not direct_url and spec.storage_path_field:
            direct_url = _resolve_storage_url(
                row, spec.storage_path_field, acquisition, options=options, config=config
            ) or _first_candidate(row, ("opinions.0.download_url",))
        if direct_url:
            downloads.append({"url": direct_url})

    return downloads


def extract_json_records(
    value: Any,
    acquisition: Mapping[str, Any],
    *,
    options: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
    limit: int = 25,
    specs: Sequence[JsonSchemaSpec] = JSON_SCHEMA_SPECS,
) -> tuple[list[dict[str, Any]], str | None]:
    """Evaluate a parsed JSON payload against ``JSON_SCHEMA_SPECS`` and return ``(records, truncated)``."""
    opts = options or {}
    cfg = config or {}

    items_path = opts.get("items_path")
    entries: list[dict[str, Any]] | None = None
    spec: JsonSchemaSpec | None = None

    if isinstance(items_path, str) and items_path.strip() and isinstance(value, Mapping):
        resolved_items = dot_lookup(value, items_path.strip())
        if isinstance(resolved_items, list):
            entries = [
                cast(dict[str, Any], x)
                for x in cast(list[Any], resolved_items)
                if isinstance(x, dict)
            ]
            if not entries:
                return [], None
            spec = match_json_schema(entries[0], specs=specs)

    if spec is None and isinstance(value, Mapping):
        vmap = cast(Mapping[str, Any], value)
        for candidate_spec in specs:
            pk = candidate_spec.parallel_list_key
            if pk and isinstance(vmap.get(pk), list):
                spec = candidate_spec
                col_items = cast(list[Any], vmap[pk])
                entries = [
                    {
                        key: cast(list[Any], vals)[i]
                        for key, vals in vmap.items()
                        if isinstance(vals, list) and i < len(cast(list[Any], vals))
                    }
                    for i in range(len(col_items))
                ]
                break

    if spec is None:
        specific_containers = (
            k
            for candidate in specs
            for k in candidate.container_keys
            if k not in GENERIC_CONTAINER_KEYS
        )
        all_containers = tuple(
            dict.fromkeys(
                (
                    *specific_containers,
                    *(k for candidate in specs for k in candidate.container_keys),
                )
            )
        )
        matched_container: str | None = None
        if isinstance(value, Mapping):
            vmap = cast(Mapping[str, Any], value)
            matched_container = next(
                (k for k in all_containers if isinstance(vmap.get(k), list)),
                None,
            )
        entries = items(value, all_containers)
        if not entries:
            return [], None
        spec = match_json_schema(entries[0], container=matched_container, specs=specs)

    assert entries is not None
    if spec.model_filter is not None:
        m_key, m_val = spec.model_filter
        entries = [raw for raw in entries if not raw.get(m_key) or raw.get(m_key) == m_val]

    truncated = (
        "document_budget_exceeded" if spec.enforce_budget_limit and len(entries) > limit else None
    )
    iter_entries = entries[:limit] if spec.enforce_budget_limit else entries
    root_issuer = (
        cast(Mapping[str, Any], value).get(spec.root_issuer_key)
        if spec.root_issuer_key and isinstance(value, Mapping)
        else None
    )
    detail_tmpl = opts.get("detail_url_template") if spec.allow_options_overrides else None
    namespace = (
        str(opts.get("namespace") or spec.namespace)
        if spec.allow_options_overrides
        else spec.namespace
    )

    records_out: list[dict[str, Any]] = []
    for raw in iter_entries:
        row = (
            cast(Mapping[str, Any], raw[spec.unwrap_key])
            if spec.unwrap_key and isinstance(raw.get(spec.unwrap_key), Mapping)
            else raw
        )
        resolved: dict[str, Any] = {
            field_name: _first_candidate(row, candidates)
            for field_name, candidates in spec.fields.items()
        }
        for s_field in spec.scalar_fields:
            if s_field in resolved:
                resolved[s_field] = scalar(resolved[s_field])
        if spec.strip_title_comma and resolved.get("title") is not None:
            resolved["title"] = str(resolved["title"]).strip().rstrip(",") or None
        if not resolved.get("issuer") and root_issuer:
            resolved["issuer"] = root_issuer

        downloads = _resolve_downloads(
            row,
            spec,
            title=resolved.get("title"),
            acquisition=acquisition,
            options=opts,
            config=cfg,
        )

        if not resolved.get("detail_url") and isinstance(detail_tmpl, str) and detail_tmpl:
            try:
                resolved["detail_url"] = detail_tmpl.format_map({k: row.get(k, "") for k in row})
            except KeyError:
                resolved["detail_url"] = None
        if spec.fallback_detail_to_actual_url and not resolved.get("detail_url"):
            resolved["detail_url"] = acquisition.get("actual_url")

        body_key = "html" if spec.body_format == "html" else "text"
        body_val = resolved.get(body_key)
        if spec.require_body_or_download and not (body_val or downloads):
            continue
        if (
            spec.allow_options_overrides
            and not (body_val or downloads)
            and isinstance(detail_tmpl, str)
            and detail_tmpl
        ):
            continue

        if spec.invalid_record_error:
            for req in spec.required_fields:
                present = downloads if req == "downloads" else resolved.get(req)
                if not present:
                    raise WlhError(spec.invalid_record_error)

        if spec.inline_text_markdown and "text" in resolved:
            resolved["text"] = (
                inline_markdown(str(body_val), "txt", title=str(resolved.get("title") or ""))
                if body_val
                else ""
            )

        ext_context: dict[str, Any] = {
            **resolved,
            "download_url": downloads[0]["url"] if downloads else None,
        }
        ext_key = _first_candidate(row, spec.fields.get("external_key", ()), ext_context)
        resolved["external_key"] = str(ext_key)

        record_out: dict[str, Any] = {
            "namespace": namespace,
            **resolved,
            **({"doc_type": spec.default_doc_type} if spec.include_doc_type else {}),
            "downloads": downloads,
        }
        records_out.append(record_out)

    return records_out, truncated


__all__ = [
    "JSON_SCHEMA_SPECS",
    "JsonSchemaSpec",
    "dot_lookup",
    "extract_json_records",
    "match_json_schema",
]
