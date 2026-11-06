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
    """Resolve a dot-separated key path on nested mappings."""
    cur: Any = obj
    for part in dotted_path.split("."):
        if not part:
            continue
        if isinstance(cur, Mapping) and part in cur:
            cur = cast(Mapping[str, Any], cur)[part]
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
    default_doc_type: str
    fingerprint_keys: tuple[str, ...]
    container_keys: tuple[str, ...]
    fields: Mapping[str, tuple[str, ...]] = field(default_factory=_empty_fields)
    body_format: Literal["text", "html"] = "text"
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
            "text": ("text", "abstract"),
        },
        body_format="text",
        enforce_budget_limit=False,
    ),
    JsonSchemaSpec(
        name="opencaselaw",
        namespace="opencaselaw/decision",
        default_doc_type="案例",
        fingerprint_keys=("decision_id", "citation_string_de", "docket_number"),
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
        fields={
            "external_key": ("cluster_id", "docket_number", "docket_numbers", "docketNumber"),
            "title": ("case_name", "case_names", "caseName", "name"),
            "doc_number": ("docket_number", "docket_numbers", "docketNumber"),
            "issuer": ("court",),
            "published_at": ("date_filed", "case_dates", "dateFiled"),
            "status": ("precedential_status", "precedential_statuses", "status"),
            "detail_url": ("detail_url", "absolute_url"),
        },
        body_format="text",
        enforce_budget_limit=True,
    ),
    JsonSchemaSpec(
        name="oldp",
        namespace="oldp/case",
        default_doc_type="案例",
        fingerprint_keys=("ecli", "file_number", "fields", "model"),
        container_keys=("cases", "opinions", "results"),
        fields={
            "external_key": ("ecli", "id", "file_number"),
            "title": ("title", "file_number"),
            "doc_number": ("file_number",),
            "issuer": ("court_name", "court"),
            "published_at": ("date",),
            "detail_url": ("source_url", "url"),
            "html": ("content", "text"),
        },
        body_format="html",
        enforce_budget_limit=True,
    ),
    JsonSchemaSpec(
        name="worldwidelaw",
        namespace="worldwidelaw/document",
        default_doc_type="法律法规",
        fingerprint_keys=("_id", "id", "title", "document_number", "number"),
        container_keys=("records", "results", "data", "items"),
        fields={
            "external_key": ("_id", "id", "document_number", "url"),
            "title": ("title",),
            "doc_number": ("document_number", "number"),
            "issuer": ("issuing_authority", "authority", "issuer"),
            "published_at": ("date", "publication_date"),
            "effective_at": ("effective_date",),
            "detail_url": ("url",),
            "text": ("text", "content"),
        },
        body_format="text",
        enforce_budget_limit=False,
    ),
)

_SCHEMA_BY_NAME: dict[str, JsonSchemaSpec] = {spec.name: spec for spec in JSON_SCHEMA_SPECS}


def match_json_schema(sample: Mapping[str, Any]) -> JsonSchemaSpec:
    """Select the matching ``JsonSchemaSpec`` from a sample record dictionary."""
    if (
        "identifier" in sample
        or "from_organization" in sample
        or "openstates_url" in sample
        or "versions" in sample
    ):
        return _SCHEMA_BY_NAME["openstates"]
    if (
        "decision_id" in sample
        or "citation_string_de" in sample
        or ("docket_number" in sample and ("full_text" in sample or "decision_date" in sample))
    ):
        return _SCHEMA_BY_NAME["opencaselaw"]
    if any(
        k in sample
        for k in (
            "case_name",
            "case_names",
            "caseName",
            "cluster_id",
            "download_url",
            "download_urls",
        )
    ):
        return _SCHEMA_BY_NAME["juriscraper"]
    if (
        sample.get("model") == "cases.case"
        or "ecli" in sample
        or "file_number" in sample
        or isinstance(sample.get("fields"), dict)
    ):
        return _SCHEMA_BY_NAME["oldp"]
    return _SCHEMA_BY_NAME["worldwidelaw"]


def _first_candidate(row: Mapping[str, Any], candidates: Sequence[str]) -> Any:
    for key in candidates:
        if "." in key:
            val = dot_lookup(row, key)
        else:
            val = row.get(key)
        if val:
            return val
    return None


def _convert_openstates(
    entries: Sequence[Mapping[str, Any]], spec: JsonSchemaSpec
) -> tuple[list[dict[str, Any]], str | None]:
    records: list[dict[str, Any]] = []
    for item in entries:
        if not item.get("title") or not item.get("identifier"):
            raise WlhError("openstates_bill_invalid")
        downloads: list[dict[str, Any]] = []
        for group in ("versions", "documents"):
            documents: list[dict[str, Any]] = item.get(group) or []
            for document in documents:
                links: list[dict[str, Any]] = document.get("links") or []
                preferred = next(
                    (v for v in links if v.get("media_type") == "application/pdf"),
                    links[0] if links else None,
                )
                if preferred:
                    note = str(document.get("note") or "").strip()
                    bill_title = str(item["title"]).strip()
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
        sources: list[dict[str, Any]] = item.get("sources") or []
        detail = (
            item.get("url")
            or (sources[0].get("url") if sources else None)
            or item.get("openstates_url")
        )
        abstracts: list[dict[str, Any]] = item.get("abstracts") or []
        body: str = _first_candidate(item, spec.fields["text"]) or "\n\n".join(
            str(a.get("abstract", "")) for a in abstracts
        )
        records.append(
            {
                "namespace": spec.namespace,
                "external_key": str(_first_candidate(item, spec.fields["external_key"])),
                "title": item["title"],
                "doc_type": spec.default_doc_type,
                "doc_number": _first_candidate(item, spec.fields["doc_number"]),
                "issuer": scalar(_first_candidate(item, spec.fields["issuer"])),
                "published_at": _first_candidate(item, spec.fields["published_at"]),
                "status": _first_candidate(item, spec.fields["status"]),
                "detail_url": detail,
                "text": inline_markdown(body, "txt", title=str(item["title"])) if body else "",
                "downloads": downloads,
            }
        )
    return records, None


def _convert_opencaselaw(
    entries: Sequence[Mapping[str, Any]], spec: JsonSchemaSpec, limit: int
) -> tuple[list[dict[str, Any]], str | None]:
    truncated = "document_budget_exceeded" if len(entries) > limit else None
    records = [
        {
            "namespace": spec.namespace,
            "external_key": str(_first_candidate(r, spec.fields["external_key"])),
            "title": str(_first_candidate(r, spec.fields["title"]) or "").strip().rstrip(",")
            or None,
            "doc_type": spec.default_doc_type,
            "doc_number": _first_candidate(r, spec.fields["doc_number"]),
            "issuer": _first_candidate(r, spec.fields["issuer"]),
            "published_at": _first_candidate(r, spec.fields["published_at"]),
            "text": _first_candidate(r, spec.fields["text"]),
            "detail_url": _first_candidate(r, spec.fields["detail_url"]),
            "downloads": [{"url": r["pdf_url"]}] if r.get("pdf_url") else [],
        }
        for r in entries[:limit]
        if _first_candidate(r, spec.fields["text"]) or r.get("pdf_url")
    ]
    return records, truncated


def _convert_juriscraper(
    value: Any,
    acquisition: Mapping[str, Any],
    spec: JsonSchemaSpec,
    *,
    options: Mapping[str, Any],
    config: Mapping[str, Any],
    limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    court: Any = cast(dict[str, Any], value).get("court_name") if isinstance(value, dict) else None
    entries = items(value, spec.container_keys)
    if isinstance(value, dict) and isinstance(cast(dict[str, Any], value).get("case_names"), list):
        val_dict = cast(dict[str, Any], value)
        case_names = cast(list[Any], val_dict["case_names"])
        entries = [
            {
                key: cast(list[Any], values)[i]
                for key, values in val_dict.items()
                if isinstance(values, list) and i < len(cast(list[Any], values))
            }
            for i in range(len(case_names))
        ]
    truncated = "document_budget_exceeded" if len(entries) > limit else None
    records: list[dict[str, Any]] = []
    for item in entries[:limit]:
        name = _first_candidate(item, spec.fields["title"])
        number = _first_candidate(item, spec.fields["doc_number"])
        raw_opinions = item.get("opinions")
        opinions: list[Any] = (
            cast(list[Any], raw_opinions) if isinstance(raw_opinions, list) else []
        )
        first_op: dict[str, Any] = (
            cast(dict[str, Any], opinions[0]) if opinions and isinstance(opinions[0], dict) else {}
        )
        storage_base = str(options.get("storage_base_url") or "").rstrip("/")
        if not storage_base:
            allowed = cast(
                Sequence[Any],
                cast(dict[str, Any], config.get("scheduler") or {}).get("allowed_hosts")
                or config.get("allowed_hosts")
                or (),
            )
            storage_host = next(
                (str(h) for h in allowed if str(h).startswith("storage.")),
                "",
            )
            storage_base = (
                f"https://{storage_host}"
                if storage_host
                else urljoin(str(acquisition.get("actual_url") or ""), "/").rstrip("/")
            )
        local_pdf = (
            f"{storage_base}/{str(first_op['local_path']).lstrip('/')}"
            if first_op.get("local_path") and storage_base
            else None
        )
        url = (
            item.get("download_url")
            or item.get("download_urls")
            or local_pdf
            or first_op.get("download_url")
        )
        if not name or not url:
            raise WlhError("juriscraper_opinion_invalid")
        records.append(
            {
                "namespace": spec.namespace,
                "external_key": str(item.get("cluster_id") or number or url),
                "title": name,
                "doc_number": scalar(number),
                "issuer": _first_candidate(item, spec.fields["issuer"]) or court,
                "published_at": _first_candidate(item, spec.fields["published_at"]),
                "status": _first_candidate(item, spec.fields["status"]),
                "detail_url": _first_candidate(item, spec.fields["detail_url"])
                or acquisition["actual_url"],
                "doc_type": spec.default_doc_type,
                "downloads": [{"url": url}],
            }
        )
    return records, truncated


def _convert_oldp(
    entries: Sequence[Mapping[str, Any]], spec: JsonSchemaSpec, limit: int
) -> tuple[list[dict[str, Any]], str | None]:
    result: list[dict[str, Any]] = []
    for raw in entries:
        if raw.get("model") and raw["model"] != "cases.case":
            continue
        r = cast(Mapping[str, Any], raw.get("fields", raw))
        result.append(
            {
                "namespace": spec.namespace,
                "external_key": str(_first_candidate(r, spec.fields["external_key"])),
                "title": _first_candidate(r, spec.fields["title"]),
                "doc_number": _first_candidate(r, spec.fields["doc_number"]),
                "issuer": _first_candidate(r, spec.fields["issuer"]),
                "published_at": _first_candidate(r, spec.fields["published_at"]),
                "doc_type": spec.default_doc_type,
                "detail_url": _first_candidate(r, spec.fields["detail_url"]),
                "html": _first_candidate(r, spec.fields["html"]),
                "downloads": [{"url": r["pdf_url"]}] if r.get("pdf_url") else [],
            }
        )
    truncated = "document_budget_exceeded" if len(result) > limit else None
    return result[:limit], truncated


def _convert_worldwidelaw(
    entries: Sequence[Mapping[str, Any]],
    spec: JsonSchemaSpec,
    *,
    options: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str | None]:
    detail_tmpl = options.get("detail_url_template")
    att_rules = options.get("attachment_rules")
    records_out: list[dict[str, Any]] = []
    for r in entries:
        downloads: list[dict[str, Any]] = []
        if isinstance(att_rules, list):
            for rule_raw in cast(list[Any], att_rules):
                if not isinstance(rule_raw, Mapping):
                    continue
                rule = cast(Mapping[str, Any], rule_raw)
                u = dot_lookup(r, str(rule.get("url_path") or ""))
                if u:
                    t_path = rule.get("title_path")
                    t_val = dot_lookup(r, t_path) if isinstance(t_path, str) else None
                    dl: dict[str, Any] = {"url": str(u)}
                    if t_val:
                        dl["title"] = str(t_val)
                    downloads.append(dl)
        if not downloads and (u := r.get("pdf_url") or r.get("docx_url")):
            downloads = [{"url": u}]
        detail_url = _first_candidate(r, spec.fields["detail_url"])
        if not detail_url and isinstance(detail_tmpl, str) and detail_tmpl:
            try:
                detail_url = detail_tmpl.format_map({k: r.get(k, "") for k in r})
            except KeyError:
                detail_url = None
        body_text = _first_candidate(r, spec.fields["text"])
        has_content = bool(body_text or downloads)
        if not has_content and isinstance(detail_tmpl, str) and detail_tmpl:
            continue
        ext_key = r.get("_id") or r.get("id") or r.get("document_number") or detail_url or r["url"]
        records_out.append(
            {
                "namespace": str(options.get("namespace") or spec.namespace),
                "external_key": str(ext_key),
                "title": r["title"],
                "doc_number": _first_candidate(r, spec.fields["doc_number"]),
                "issuer": _first_candidate(r, spec.fields["issuer"]),
                "published_at": _first_candidate(r, spec.fields["published_at"]),
                "effective_at": _first_candidate(r, spec.fields["effective_at"]),
                "detail_url": detail_url,
                "text": body_text,
                "downloads": downloads,
            }
        )
    return records_out, None


def extract_json_records(
    value: Any,
    acquisition: Mapping[str, Any],
    *,
    options: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
    limit: int = 25,
) -> tuple[list[dict[str, Any]], str | None]:
    """Evaluate a parsed JSON payload against ``JSON_SCHEMA_SPECS`` and return ``(records, truncated)``."""
    opts = options or {}
    cfg = config or {}

    items_path = opts.get("items_path")
    entries: list[dict[str, Any]] | None = None
    spec: JsonSchemaSpec | None = None

    if isinstance(items_path, str) and items_path.strip() and isinstance(value, Mapping):
        resolved = dot_lookup(value, items_path.strip())
        if isinstance(resolved, list):
            entries = [
                cast(dict[str, Any], x) for x in cast(list[Any], resolved) if isinstance(x, dict)
            ]
            if not entries:
                return [], None
            spec = match_json_schema(entries[0])

    if spec is None:
        if isinstance(value, dict) and isinstance(
            cast(dict[str, Any], value).get("case_names"), list
        ):
            spec = _SCHEMA_BY_NAME["juriscraper"]
            entries = []
        elif isinstance(value, dict):
            vdict = cast(dict[str, Any], value)
            if "bills" in vdict:
                spec = _SCHEMA_BY_NAME["openstates"]
                entries = items(value, spec.container_keys)
            elif "decisions" in vdict:
                spec = _SCHEMA_BY_NAME["opencaselaw"]
                entries = items(value, spec.container_keys)
            elif "cases" in vdict or "opinions" in vdict:
                entries = items(value, ("cases", "opinions", "results"))
                if entries and any(
                    k in entries[0]
                    for k in ("case_name", "caseName", "docket_number", "download_url")
                ):
                    spec = _SCHEMA_BY_NAME["juriscraper"]
                else:
                    spec = _SCHEMA_BY_NAME["oldp"]

    if spec is None:
        entries = items(
            value, ("bills", "decisions", "cases", "opinions", "records", "results", "data")
        )
        if not entries:
            return [], None
        spec = match_json_schema(entries[0])

    assert entries is not None
    if spec.name == "openstates":
        return _convert_openstates(entries, spec)
    if spec.name == "opencaselaw":
        return _convert_opencaselaw(entries, spec, limit)
    if spec.name == "juriscraper":
        return _convert_juriscraper(value, acquisition, spec, options=opts, config=cfg, limit=limit)
    if spec.name == "oldp":
        return _convert_oldp(entries, spec, limit)
    return _convert_worldwidelaw(entries, spec, options=opts)


__all__ = [
    "JSON_SCHEMA_SPECS",
    "JsonSchemaSpec",
    "dot_lookup",
    "extract_json_records",
    "match_json_schema",
]
