"""JSON API and structured record parser (Federal Register, OpenStates, OpenCaseLaw, Juriscraper, OLDP, e-Gov JSON)."""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import unquote, urlencode, urljoin, urlsplit, urlunsplit

import lxml.etree as _etree  # type: ignore[import-untyped]

from wlh.core import WlhError, canonical, digest, guard, public_url
from wlh.processor.extractors.html.dom_tree import parse_html
from wlh.processor.extractors.html.html_to_md import text_to_markdown
from wlh.processor.extractors.word.office import detect_real_format
from wlh.processor.extractors.xml import dokument_xml_to_markdown, xml_to_markdown
from wlh.processor.parsers.html.document_builder import WebDocumentBuilder
from wlh.processor.parsers.html.document_builder import doc_rules as _doc_rules
from wlh.processor.parsers.html.fields import clean_url, decode, file_name
from wlh.processor.parsers.record_base import (
    RecordAdapter,
    _budget_documents,
    _budget_response_bytes,
    _metadata_cfg,
    _quality_routes,
    day,
    first,
    html_records,
    items,
    payload,
    scalar,
)
from wlh.processor.parsers.xml.parser import japan_records, japan_requests

etree: Any = _etree

FIELDS: tuple[str, ...] = (
    "document_number",
    "title",
    "type",
    "action",
    "abstract",
    "agencies",
    "publication_date",
    "effective_on",
    "citation",
    "html_url",
    "pdf_url",
    "full_text_xml_url",
)
TYPES: dict[str, str] = {
    "Rule": "法律法规",
    "Proposed Rule": "法律法规",
    "Notice": "通知公告",
    "Presidential Document": "政策文件",
}


def pointer(value: Any, path: str) -> Any:
    """RFC 6901 JSON Pointer resolution (real locator into the archived response)."""
    if path == "":
        return value
    if not path.startswith("/"):
        raise WlhError("json_pointer_invalid")
    for raw in path[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not token.isdigit() or int(token) >= len(cast(list[Any], value)):
                raise WlhError("json_pointer_unresolved")
            value = cast(list[Any], value)[int(token)]
        elif isinstance(value, dict):
            if token not in value:
                raise WlhError("json_pointer_unresolved")
            value = cast(dict[str, Any], value)[token]
        else:
            raise WlhError("json_pointer_unresolved")
    return value


def rii_xml(data: bytes, max_bytes: int) -> bytes:
    """Read the first XML member in memory, bounded like other archived inputs."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            member: zipfile.ZipInfo | None = next(
                (
                    m
                    for m in archive.infolist()
                    if not m.is_dir() and m.filename.lower().endswith(".xml")
                ),
                None,
            )
            if member is None:
                raise WlhError("zip_without_xml")
            if member.file_size > max_bytes:
                raise WlhError("zip_expanded_size_exceeded")
            with archive.open(member) as source:
                xml = source.read(max_bytes + 1)
            if len(xml) > max_bytes:
                raise WlhError("zip_expanded_size_exceeded")
            return xml
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError):
        raise WlhError("zip_invalid") from None


def _parser_options(config: Mapping[str, Any]) -> dict[str, Any]:
    proc = config.get("processor")
    if isinstance(proc, Mapping):
        proc_map = cast(Mapping[str, Any], proc)
        parser_cfg = proc_map.get("parser")
        if isinstance(parser_cfg, Mapping):
            parser_map = cast(Mapping[str, Any], parser_cfg)
            if isinstance(parser_map.get("options"), Mapping):
                return dict(cast(Mapping[str, Any], parser_map["options"]))
    opts = config.get("adapter_options")
    return dict(cast(Mapping[str, Any], opts)) if isinstance(opts, Mapping) else {}


def _entry_urls(config: Mapping[str, Any]) -> list[str]:
    sched = config.get("scheduler")
    if isinstance(sched, Mapping):
        sched_map = cast(Mapping[str, Any], sched)
        if isinstance(sched_map.get("entry_urls"), Sequence):
            return [str(u) for u in cast(Sequence[Any], sched_map["entry_urls"])]
    entries = config.get("entry_urls")
    if isinstance(entries, Sequence):
        return [str(u) for u in cast(Sequence[Any], entries)]
    return []


def _dot_lookup(obj: Any, dotted_path: str) -> Any:
    cur: Any = obj
    for part in dotted_path.split("."):
        if not part:
            continue
        if isinstance(cur, Mapping) and part in cur:
            cur = cast(Mapping[str, Any], cur)[part]
        else:
            return None
    return cur


class JsonParser(RecordAdapter):
    CONTRACT: str = "wlh.parser.json.v2"
    NAMESPACE: str = "json-record"
    PLANNING_OPTIONS: tuple[str, ...] = ("per_page", "max_pages", "conditions", "pagination")

    @staticmethod
    def validate_options(options: Mapping[str, Any]) -> None:
        allowed = {
            "conditions",
            "per_page",
            "max_pages",
            "listing_body_field",
            "document_types",
            "schema",
            "namespace",
            "items_path",
            "pagination",
            "detail_url_template",
            "attachment_rules",
        }
        if set(options) - allowed:
            raise ValueError("unexpected_adapter_options")
        conditions: Any = options.get("conditions", {})
        if not isinstance(conditions, dict):
            raise ValueError("invalid_conditions")
        for key, value in cast(dict[str, Any], conditions).items():
            values = cast(list[Any], value) if isinstance(value, list) else [value]
            if not re.fullmatch(r"[a-z_]+(\[[a-z_]*\])*", key) or not all(
                isinstance(v, str) and len(v) <= 200 for v in values
            ):
                raise ValueError("invalid_conditions")
        per_page: Any = options.get("per_page", 10)
        pages: Any = options.get("max_pages", 1)
        if type(per_page) is not int or not 1 <= per_page <= 100:
            raise ValueError("invalid_per_page")
        if type(pages) is not int or not 1 <= pages <= 1000:
            raise ValueError("invalid_max_pages")
        field: Any = options.get("listing_body_field")
        if field is not None and (
            not isinstance(field, str) or not re.fullmatch(r"[a-z_]+", field)
        ):
            raise ValueError("invalid_listing_body_field")
        types: Any = options.get("document_types", {})
        if not isinstance(types, dict):
            raise ValueError("invalid_document_types")
        items_path: Any = options.get("items_path")
        if items_path is not None and (not isinstance(items_path, str) or not items_path.strip()):
            raise ValueError("invalid_items_path")
        pagination: Any = options.get("pagination")
        if pagination is not None and not isinstance(pagination, Mapping):
            raise ValueError("invalid_pagination")
        detail_tmpl: Any = options.get("detail_url_template")
        if detail_tmpl is not None and (
            not isinstance(detail_tmpl, str) or not detail_tmpl.strip()
        ):
            raise ValueError("invalid_detail_url_template")
        att_rules: Any = options.get("attachment_rules")
        if att_rules is not None:
            if not isinstance(att_rules, list) or any(
                not isinstance(r, Mapping)
                or not isinstance(cast(Mapping[str, Any], r).get("url_path"), str)
                for r in cast(list[Any], att_rules)
            ):
                raise ValueError("invalid_attachment_rules")

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.options: dict[str, Any] = _parser_options(config)
        entries = _entry_urls(config)
        self.base: str = entries[0].rstrip("/") if entries else ""
        self.types: dict[str | None, Any] = {
            **TYPES,
            **cast(dict[str, Any], self.options.get("document_types") or {}),
        }
        self._doc_builder = WebDocumentBuilder(config)
        rules = _doc_rules(config)
        self._details: list[re.Pattern[str]] = [
            re.compile(p) for p in rules.get("detail_url_patterns", ())
        ]
        self._attachment: re.Pattern[str] = re.compile(
            rules.get("attachment_url_pattern")
            or r"(?i)\.(?:pdf|docx?|rtf|xlsx?|zip)(?:$|[?#])"
        )
        self._max_pages: int = int(self.options.get("max_pages", 1))

    def _is_fr_mode(self) -> bool:
        if any(
            k in self.options
            for k in ("conditions", "per_page", "listing_body_field", "document_types")
        ):
            return True
        return self.base.endswith("/api/v1")

    def _fr_namespace(self) -> str:
        host = urlsplit(self.base).netloc.removeprefix("www.")
        if host:
            return f"{host}/document_number"
        return "federal-register/document_number"

    def api_url(self, path: str, params: Sequence[tuple[str, Any]]) -> str:
        return public_url(
            self.base + path + ("?" + urlencode(params, doseq=True) if params else "")
        )

    def fields(self) -> list[tuple[str, str]]:
        return [("fields[]", f) for f in FIELDS] + (
            [("fields[]", self.options["listing_body_field"])]
            if self.options.get("listing_body_field") not in (None, *FIELDS)
            else []
        )

    def plan(self, state: dict[str, Any]) -> dict[str, Any]:
        if not self._is_fr_mode():
            return super().plan(state)
        params = [("per_page", str(self.options.get("per_page", 10))), ("order", "newest")]
        for key, value in sorted(self.options.get("conditions", {}).items()):
            name, _, rest = key.partition("[")
            param = "conditions[" + name + "]" + ("[" + rest if rest else "")
            if isinstance(value, list):
                params.extend((param + "[]", item) for item in cast(list[Any], value))
            else:
                params.append((param, value))
        params.extend(self.fields())
        url = self.api_url("/documents.json", params)
        return {
            "requests": [{"url": url, "role": "listing", "depth": 0, "meta": {"page": 1}}],
            "targets": [],
            "state": state,
        }

    def record_url(self, number: str) -> str:
        return self.api_url("/documents/" + number + ".json", self.fields())

    def target(
        self,
        item: Mapping[str, Any],
        listing_request: Mapping[str, Any],
        index: int | None,
    ) -> dict[str, Any]:
        number: Any = item.get("document_number")
        if not isinstance(number, str) or not re.fullmatch(r"[A-Za-z0-9-]{4,40}", number):
            raise WlhError("document_identity_missing")
        inputs: list[dict[str, Any]] = [
            {
                "request": listing_request,
                "role": "listing",
                "pointer": "" if index is None else "/results/" + str(index),
            }
        ]
        if not self.options.get("listing_body_field"):
            for role, key in (("full_text", "full_text_xml_url"), ("original", "pdf_url")):
                url: str | None = item.get(key)
                if url:
                    inputs.append(
                        {
                            "request": {"url": guard(self.config, url), "role": role, "depth": 1},
                            "role": role,
                        }
                    )
        html_url: Any = item.get("html_url")
        return {
            "identity": {
                "namespace": self._fr_namespace(),
                "external_key": number,
                "public_url": public_url(html_url) if html_url else None,
                "recheck_locator": {
                    "url": self.record_url(number),
                    "role": "record",
                    "document_number": number,
                },
            },
            "inputs": inputs,
        }

    def plan_recheck(
        self, resources: Sequence[Mapping[str, Any]], state: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._is_fr_mode() and not any(
            cast(Mapping[str, Any], r.get("recheck_locator") or {}).get("role") == "record"
            for r in resources
        ):
            return super().plan_recheck(resources, state)
        targets: list[dict[str, Any]] = []
        size = self.options.get("per_page", 20)
        numbers: list[tuple[str, Mapping[str, Any]]] = []
        for resource in resources:
            locator = resource["recheck_locator"]
            if locator.get("role") != "record" or not locator.get("document_number"):
                raise WlhError("recheck_locator_unsupported")
            numbers.append((locator["document_number"], resource))
        for start in range(0, len(numbers), size):
            chunk = numbers[start : start + size]
            batch = ",".join(sorted(number for number, _ in chunk))
            request: dict[str, Any] = {
                "url": self.api_url("/documents/" + batch + ".json", self.fields()),
                "role": "batch",
                "depth": 0,
            }
            for number, resource in chunk:
                targets.append(
                    {
                        "identity": {
                            "namespace": resource["namespace"],
                            "external_key": resource["external_key"],
                            "public_url": resource["public_url"],
                            "recheck_locator": resource["recheck_locator"],
                        },
                        "inputs": [
                            {
                                "request": request,
                                "role": "listing",
                                "select": {"document_number": number},
                            }
                        ],
                    }
                )
        return {"requests": [], "targets": targets, "state": state}

    def load_json(self, ctx: Any, acquisition: Mapping[str, Any]) -> Any:
        try:
            return json.loads(ctx.read(acquisition))
        except ValueError:
            raise WlhError("api_response_not_json") from None

    def _has_detail_rules(self) -> bool:
        return bool(self._details) and not self._is_fr_mode()

    def _in_scope(self, url: str) -> bool:
        try:
            guard(self.config, url)
            return True
        except WlhError:
            return False

    def _resolve_link(self, base: str, raw_href: str | None) -> str | None:
        return clean_url(base, raw_href)

    def _parse_json_detail_catalog(
        self, ctx: Any, task: Mapping[str, Any], acquisition: Mapping[str, Any]
    ) -> dict[str, Any]:
        request = task["payload"]
        if acquisition["outcome"] != "ok" or request.get("role", "page") != "page":
            return {"requests": [], "targets": []}
        data = ctx.read(acquisition)
        url: str = str(acquisition["actual_url"] or acquisition["request_url"] or "")
        detail = any(p.search(url) for p in self._details)
        requests: list[dict[str, Any]] = []
        attachments: list[dict[str, Any]] = []
        truncated: str | None = None
        json_text, _ = decode(data, acquisition["charset"])
        try:
            parsed_json = json.loads(json_text)
        except ValueError:
            parsed_json = None
        if isinstance(parsed_json, dict):
            json_obj = cast(dict[str, Any], parsed_json)
            seen_json_links: set[str] = set()
            if detail:
                ds_raw = json_obj.get("dataset")
                ds_obj: dict[str, Any] = (
                    cast(dict[str, Any], ds_raw) if isinstance(ds_raw, dict) else json_obj
                )
                fs_raw = ds_obj.get("filesetLists")
                if isinstance(fs_raw, list):
                    for fs_item in cast(list[Any], fs_raw):
                        if not isinstance(fs_item, dict):
                            continue
                        fs_map = cast(dict[str, Any], fs_item)
                        fs_id = fs_map.get("fileSetId")
                        if fs_id is None:
                            continue
                        att_link = self._resolve_link(url, f"/api/FilesetLists/{fs_id}/file")
                        if (
                            att_link
                            and att_link not in seen_json_links
                            and self._in_scope(att_link)
                            and (
                                self._attachment.search(unquote(att_link))
                                or self._attachment.search(file_name(att_link))
                            )
                        ):
                            seen_json_links.add(att_link)
                            desc = str(
                                fs_map.get("resourceDescription")
                                or ds_obj.get("title")
                                or ""
                            ).strip()
                            attachments.append({"url": att_link, "text": desc})
            else:
                pl_raw = json_obj.get("pagedList")
                if isinstance(pl_raw, dict):
                    pl_obj = cast(dict[str, Any], pl_raw)
                    items_raw = pl_obj.get("items")
                    has_bearer = bool(os.environ.get("JUDICIAL_OPENDATA_BEARER_TOKEN"))
                    eligible_items = 0
                    if isinstance(items_raw, list):
                        for it_raw in cast(list[Any], items_raw):
                            if not isinstance(it_raw, dict):
                                continue
                            it_map = cast(dict[str, Any], it_raw)
                            if it_map.get("categoryDataset") == "B" and not has_bearer:
                                continue
                            did = it_map.get("datasetId")
                            if did is None:
                                continue
                            eligible_items += 1
                            det_link = self._resolve_link(url, f"/api/Datasets/{did}")
                            if (
                                det_link
                                and det_link not in seen_json_links
                                and self._in_scope(det_link)
                                and ctx.follow_links(task)
                            ):
                                seen_json_links.add(det_link)
                                requests.append(
                                    {"url": det_link, "role": "page", "depth": task["depth"] + 1}
                                )
                    if eligible_items > 0:
                        cur_page_raw = (
                            pl_obj.get("currentPage")
                            or pl_obj.get("page")
                            or cast(Mapping[str, Any], request.get("meta") or {}).get("page", 1)
                        )
                        cur_page = (
                            int(cur_page_raw)
                            if isinstance(cur_page_raw, (int, str))
                            and str(cur_page_raw).isdigit()
                            else 1
                        )
                        last_page_raw = pl_obj.get("lastPage")
                        last_page = (
                            int(last_page_raw)
                            if isinstance(last_page_raw, (int, str))
                            and str(last_page_raw).isdigit()
                            else cur_page
                        )
                        has_next = bool(pl_obj.get("hasNextPage")) or (cur_page < last_page)
                        if has_next:
                            if cur_page >= self._max_pages:
                                truncated = truncated or "page_limit_exceeded"
                            elif ctx.follow_links(task):
                                next_page = cur_page + 1
                                u_parts = urlsplit(url)
                                if re.search(r"(?:^|&)page=\d+", u_parts.query):
                                    next_query = re.sub(
                                        r"(^|&)page=\d+",
                                        rf"\g<1>page={next_page}",
                                        u_parts.query,
                                    )
                                else:
                                    next_query = (
                                        f"{u_parts.query}&page={next_page}"
                                        if u_parts.query
                                        else f"page={next_page}"
                                    )
                                next_url = urlunsplit(
                                    (u_parts.scheme, u_parts.netloc, u_parts.path, next_query, "")
                                )
                                if next_url not in seen_json_links and self._in_scope(next_url):
                                    seen_json_links.add(next_url)
                                    requests.append(
                                        {
                                            "url": next_url,
                                            "role": "page",
                                            "depth": 0,
                                            "meta": {"page": next_page},
                                        }
                                    )
        targets: list[dict[str, Any]] = []
        if detail:
            inputs = [{"request": request, "role": "detail"}]
            for item in attachments:
                inputs.append(
                    {
                        "request": {
                            "url": item["url"],
                            "role": "attachment",
                            "depth": task["depth"],
                        },
                        "role": "attachment",
                        "text": item["text"],
                    }
                )
            targets.append(
                {
                    "identity": {
                        "namespace": "url",
                        "external_key": public_url(url),
                        "public_url": public_url(url),
                        "recheck_locator": {"url": public_url(request["url"]), "role": "page"},
                    },
                    "inputs": inputs,
                }
            )
        return {"requests": requests, "targets": targets, "truncated": truncated}

    def parse(
        self, ctx: Any, task: Mapping[str, Any], acquisition: Mapping[str, Any]
    ) -> dict[str, Any]:
        if self._has_detail_rules():
            return self._parse_json_detail_catalog(ctx, task, acquisition)
        request = task["payload"]
        role: Any = request.get("role")
        if self._is_fr_mode() or role in {"listing", "record", "batch"}:
            if acquisition["outcome"] != "ok" or role not in {"listing", "record", "batch"}:
                return {"requests": [], "targets": []}
            value: Any = self.load_json(ctx, acquisition)
            request = {k: request[k] for k in ("url", "role") if k in request} | {
                "depth": task["depth"]
            }
            if role == "record":
                if not isinstance(value, dict):
                    raise WlhError("api_structure_changed")
                return {
                    "requests": [],
                    "targets": [self.target(cast(dict[str, Any], value), request, None)],
                }
            if role == "batch":
                results: Any = (
                    cast(dict[str, Any], value).get("results")
                    if isinstance(value, dict)
                    else None
                )
                if (
                    results is None
                    and isinstance(value, dict)
                    and cast(dict[str, Any], value).get("document_number")
                ):
                    results = [cast(dict[str, Any], value)]
                if not isinstance(results, list):
                    raise WlhError("api_structure_changed")
                targets: list[dict[str, Any]] = []
                for item in cast(list[dict[str, Any]], results):
                    target = self.target(item, request, None)
                    target["inputs"][0].pop("pointer")
                    target["inputs"][0]["select"] = {
                        "document_number": item["document_number"]
                    }
                    targets.append(target)
                return {"requests": [], "targets": targets}
            results = (
                cast(dict[str, Any], value).get("results") if isinstance(value, dict) else None
            )
            if not isinstance(results, list):
                raise WlhError("api_structure_changed")
            targets = [
                self.target(item, request, i)
                for i, item in enumerate(cast(list[dict[str, Any]], results))
            ]
            page = cast(Mapping[str, Any], task["payload"].get("meta") or {}).get("page", 1)
            follow: list[dict[str, Any]] = []
            following: Any = cast(dict[str, Any], value).get("next_page_url")
            if following and page < self.options.get("max_pages", 1):
                follow.append(
                    {
                        "url": guard(self.config, following),
                        "role": "listing",
                        "depth": 0,
                        "meta": {"page": page + 1},
                    }
                )
            return {
                "requests": follow,
                "targets": targets,
                "truncated": "page_limit_exceeded" if following and not follow else None,
                "state": {
                    "listing_pages": page,
                    "count": cast(dict[str, Any], value).get("count"),
                    "last_page": not following,
                },
            }
        return super().parse(ctx, task, acquisition)

    def build(
        self, ctx: Any, target: Mapping[str, Any], inputs: Sequence[Mapping[str, Any]]
    ) -> list[Any]:
        by_role = {item["role"]: item for item in inputs}
        if "detail" in by_role:
            return self._doc_builder.build(ctx, target, inputs)
        if "listing" not in by_role:
            return super().build(ctx, target, inputs)
        listing: dict[str, Any] = dict(by_role["listing"])
        response: Any = self.load_json(ctx, listing["acquisition"])
        if listing.get("select"):
            wanted = listing["select"]["document_number"]
            if (
                isinstance(response, dict)
                and cast(dict[str, Any], response).get("document_number") == wanted
            ):
                listing = dict(listing, pointer="")
            else:
                results: Any = (
                    cast(dict[str, Any], response).get("results")
                    if isinstance(response, dict)
                    else None
                )
                matches: list[int] = [
                    i
                    for i, item in enumerate(cast(Sequence[Any], results or []))
                    if isinstance(item, dict)
                    and cast(dict[str, Any], item).get("document_number") == wanted
                ]
                if len(matches) != 1:
                    raise WlhError("document_missing_from_batch")
                listing = dict(listing, pointer="/results/" + str(matches[0]))
        document: Any = pointer(response, listing.get("pointer", ""))
        if not isinstance(document, dict):
            raise WlhError("api_structure_changed")
        document = cast(dict[str, Any], document)
        number: Any = target["identity"]["external_key"]
        if document.get("document_number") != number:
            raise WlhError("api_structure_changed")
        listing_url = public_url(listing["acquisition"]["request_url"])
        slice_bytes = canonical(document)
        candidate_inputs: list[dict[str, Any]] = [
            {
                "acquisition_id": listing["acquisition"]["id"],
                "archive_ref": listing["acquisition"]["archive_ref"],
                "role": "source_response",
                "locator": {"url": listing_url, "json_pointer": listing.get("pointer", "")},
                "semantic_locator": {
                    "collection": "federal_register.documents",
                    "document_number": number,
                },
                "slice_hash": digest(slice_bytes),
                "charset": listing["acquisition"]["charset"],
            }
        ]
        parsers = {"markdown"}
        deliverables: list[dict[str, Any]] = []
        accept_routes = _quality_routes(self.config)
        body_field: str | None = self.options.get("listing_body_field")
        if body_field:
            body: Any = document.get(body_field)
            if not isinstance(body, str) or len(body.strip()) < 20:
                raise WlhError("document_body_missing")
            quality = ctx.text_quality(body)
            if quality["recommended_route"] not in accept_routes:
                raise WlhError("quality_check_failed:" + quality["recommended_route"])
            deliverables.append(
                {
                    "role": "primary",
                    "markdown": text_to_markdown(body),
                    "input": 0,
                    "original": None,
                    "quality": quality,
                    "extraction": {"kind": "json_slice", "field": body_field},
                }
            )
        else:
            xml, pdf = by_role.get("full_text"), by_role.get("original")
            if pdf is None:
                raise WlhError("document_original_missing")
            pdf_acq = pdf["acquisition"]
            pdf_data = ctx.read(pdf_acq)
            if detect_real_format(pdf_data) != "pdf":
                raise WlhError("document_original_not_pdf")
            candidate_inputs.append(
                {
                    "acquisition_id": pdf_acq["id"],
                    "archive_ref": pdf_acq["archive_ref"],
                    "role": "primary",
                    "locator": {
                        "url": public_url(pdf_acq["request_url"]),
                        "kind": "whole_response",
                    },
                }
            )
            if xml is not None:
                xml_acq = xml["acquisition"]
                markdown = xml_to_markdown(ctx.read(xml_acq))
                candidate_inputs.append(
                    {
                        "acquisition_id": xml_acq["id"],
                        "archive_ref": xml_acq["archive_ref"],
                        "role": "source_response",
                        "locator": {"url": public_url(xml_acq["request_url"]), "xpath": "/*"},
                        "charset": xml_acq["charset"],
                    }
                )
                extraction = {
                    "kind": "federal_register_xml",
                    "markdown_source": "full_text_xml",
                }
                quality = ctx.text_quality(markdown)
            else:
                result = ctx.extract(pdf_data, "pdf")
                markdown, quality = result["markdown"], result["quality"]
                extraction = result["info"]
                parsers.update(result["parsers"])
            if quality["recommended_route"] not in accept_routes:
                raise WlhError("quality_check_failed:" + quality["recommended_route"])
            deliverables.append(
                {
                    "role": "primary",
                    "markdown": markdown,
                    "input": 1,
                    "original": {
                        "ref": pdf_acq["archive_ref"],
                        "kind": "pdf",
                        "byte_size": pdf_acq["byte_size"],
                        "source_url": public_url(pdf_acq["request_url"]),
                        "original_name": urlsplit(cast(str, pdf_acq["request_url"])).path.rsplit(
                            "/", 1
                        )[-1],
                    },
                    "quality": quality,
                    "extraction": extraction,
                }
            )
        agencies = [
            cast(dict[str, Any], a).get("name")
            for a in cast(Sequence[Any], document.get("agencies") or [])
            if isinstance(a, dict)
        ]
        meta_cfg = _metadata_cfg(self.config)
        dtype: Any = self.types.get(document.get("type"), meta_cfg.get("document_type"))
        metadata = dict(meta_cfg, document_type=dtype)
        metadata.update(
            title=document.get("title"),
            document_number=number,
            issuer="; ".join(a for a in agencies if a) or None,
        )
        dates: dict[str, Any] = {
            k: document.get(src)
            for k, src in (("published_at", "publication_date"), ("effective_at", "effective_on"))
            if document.get(src)
        }
        return [
            {
                **target["identity"],
                "metadata": metadata,
                "dates": dates,
                "deliverables": deliverables,
                "inputs": candidate_inputs,
                "parsers": sorted(parsers | {"quality"}),
                "extensions": {
                    "federal_register": {
                        k: document.get(k)
                        for k in ("citation", "type", "action")
                        if document.get(k)
                    }
                },
            }
        ]

    def records(self, data: bytes, acquisition: Mapping[str, Any]) -> list[dict[str, Any]]:
        official = japan_records(data, acquisition, self.config)
        if official is not None:
            return official
        raw_bytes = data
        zipped = data.startswith(b"PK\x03\x04")
        if zipped:
            data = rii_xml(data, _budget_response_bytes(self.config))
        limit = _budget_documents(self.config)
        if data.lstrip().startswith((b"{", b"[")):
            value: Any = payload(data)
            items_path = self.options.get("items_path")
            if isinstance(items_path, str) and items_path.strip() and isinstance(value, Mapping):
                resolved = _dot_lookup(value, items_path.strip())
                if isinstance(resolved, list):
                    entries = [
                        cast(dict[str, Any], x)
                        for x in cast(list[Any], resolved)
                        if isinstance(x, dict)
                    ]
                    if not entries:
                        return []
                    sample = entries[0]
                    if (
                        "identifier" in sample
                        or "from_organization" in sample
                        or "openstates_url" in sample
                        or "versions" in sample
                    ):
                        return self._openstates_records(entries)
                    if (
                        "decision_id" in sample
                        or "citation_string_de" in sample
                        or (
                            "docket_number" in sample
                            and ("full_text" in sample or "decision_date" in sample)
                        )
                    ):
                        return self._opencaselaw_records(entries, limit)
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
                        return self._juriscraper_json_records(value, acquisition, limit)
                    if (
                        sample.get("model") == "cases.case"
                        or "ecli" in sample
                        or "file_number" in sample
                        or isinstance(sample.get("fields"), dict)
                    ):
                        return self._oldp_json_records(entries, limit)
                    return self._worldwidelaw_records(entries)
            if isinstance(value, dict) and isinstance(
                cast(dict[str, Any], value).get("case_names"), list
            ):
                return self._juriscraper_json_records(value, acquisition, limit)
            if isinstance(value, dict):
                vdict = cast(dict[str, Any], value)
                if "bills" in vdict:
                    return self._openstates_records(items(value, ("bills", "results")))
                if "decisions" in vdict:
                    return self._opencaselaw_records(items(value, ("decisions", "results")), limit)
                if "cases" in vdict or "opinions" in vdict:
                    entries = items(value, ("cases", "opinions", "results"))
                    if entries and any(
                        k in entries[0] for k in ("case_name", "caseName", "docket_number", "download_url")
                    ):
                        return self._juriscraper_json_records(value, acquisition, limit)
                    return self._oldp_json_records(entries, limit)
            entries = items(value, ("bills", "decisions", "cases", "opinions", "records", "results", "data"))
            if not entries:
                return []
            sample = entries[0]
            if "identifier" in sample or "from_organization" in sample or "openstates_url" in sample or "versions" in sample:
                return self._openstates_records(entries)
            if "decision_id" in sample or "citation_string_de" in sample or ("docket_number" in sample and ("full_text" in sample or "decision_date" in sample)):
                return self._opencaselaw_records(entries, limit)
            if any(k in sample for k in ("case_name", "case_names", "caseName", "cluster_id", "download_url", "download_urls")):
                return self._juriscraper_json_records(value, acquisition, limit)
            if sample.get("model") == "cases.case" or "ecli" in sample or "file_number" in sample or isinstance(sample.get("fields"), dict):
                return self._oldp_json_records(entries, limit)
            return self._worldwidelaw_records(entries)
        if zipped or data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(
            (b"<?xml", b"<dokument", b"<items")
        ):
            tree: Any = etree.fromstring(
                data, etree.XMLParser(resolve_entities=False, no_network=True)
            )
            if not zipped and etree.QName(tree).localname == "items":
                return []
            key: str | None = first(tree, ".//ecli/text()") or first(tree, ".//doknr/text()")
            number: str | None = first(tree, ".//aktenzeichen/text()") or first(
                tree, ".//az/text()"
            )
            title: str | None = (
                first(tree, ".//titelzeile") or first(tree, ".//titel") or number or key
            )
            published: str | None = first(tree, ".//entsch-datum/text()")
            if published and re.fullmatch(r"\d{8}", published):
                published = f"{published[:4]}-{published[4:6]}-{published[6:]}"
            return [
                {
                    "namespace": "oldp/case",
                    "external_key": key or number,
                    "title": title,
                    "doc_number": number,
                    "issuer": first(
                        tree, ".//gertyp/text()|.//gerort/text()|.//spruchkoerper/text()"
                    ),
                    "published_at": day(published),
                    "doc_type": "案例",
                    "text": dokument_xml_to_markdown(tree),
                    "original": (
                        {
                            "ref": acquisition["archive_ref"],
                            "kind": "zip",
                            "byte_size": len(raw_bytes),
                        }
                        if zipped
                        else None
                    ),
                }
            ]
        tree_html, _, _ = parse_html(data, acquisition.get("charset"))
        if tree_html.xpath("//article[@data-ecli]"):
            return html_records(
                data,
                acquisition,
                containers="//article[@data-ecli]",
                namespace="oldp/case",
                fields={
                    "title": ".//h1//text()|.//h2//text()",
                    "external_key": "./@data-ecli",
                    "doc_number": './/*[@class="file-number"]//text()',
                    "issuer": './/*[@class="court"]//text()',
                    "published_at": './/*[@class="date"]//text()',
                    "doc_type": 'string("案例")',
                },
            )
        if tree_html.xpath('//*[@class="decision" or @class="content"]'):
            return html_records(
                data,
                acquisition,
                containers='//*[@class="decision" or @class="content"]',
                namespace="opencaselaw/decision",
                fields={
                    "title": ".//h1//text()|.//h2//text()",
                    "external_key": "./@data-id",
                    "doc_number": './/*[@class="docket" or @class="docket-number"]//text()',
                    "issuer": './/*[@class="court"]//text()',
                    "published_at": './/*[@class="date"]//text()',
                    "doc_type": 'string("案例")',
                },
            )
        records_out: list[dict[str, Any]] = []
        for link in tree_html.xpath('//a[contains(@href,".pdf") or contains(@href,"getopn.pl")]'):
            row = link.xpath("ancestor::tr[1]")
            cells = [" ".join(c.itertext()).strip() for c in row[0].xpath("./td")] if row else []
            title = " ".join(link.itertext()).strip()
            records_out.append(
                {
                    "namespace": "juriscraper/opinion",
                    "external_key": link.get("href"),
                    "title": title or " ".join(cells),
                    "doc_type": "案例",
                    "text": "",
                    "downloads": [{"url": link.get("href")}],
                    "detail_url": acquisition["actual_url"],
                }
            )
        if not records_out:
            raise WlhError("juriscraper_opinions_missing")
        return records_out

    def _openstates_records(self, entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
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
            body: str = (
                item.get("text")
                or item.get("abstract")
                or "\n\n".join(str(a.get("abstract", "")) for a in abstracts)
            )
            records.append(
                {
                    "namespace": "openstates/bill",
                    "external_key": str(item.get("id") or item["identifier"]),
                    "title": item["title"],
                    "doc_type": "法律法规",
                    "doc_number": item["identifier"],
                    "issuer": scalar(item.get("from_organization")),
                    "published_at": item.get("first_action_date") or item.get("latest_action_date"),
                    "status": item.get("status") or item.get("latest_action_description"),
                    "detail_url": detail,
                    "text": "# " + item["title"] + "\n\n" + body if body else "",
                    "downloads": downloads,
                }
            )
        return records

    def _opencaselaw_records(
        self, entries: Sequence[Mapping[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        if len(entries) > limit:
            self.truncated = "document_budget_exceeded"
        return [
            {
                "namespace": "opencaselaw/decision",
                "external_key": str(r.get("decision_id") or r.get("id") or r["docket_number"]),
                "title": str(
                    r.get("title") or r.get("citation_string_de") or r.get("docket_number") or ""
                )
                .strip()
                .rstrip(",")
                or None,
                "doc_type": "案例",
                "doc_number": r.get("docket_number") or r.get("case_number"),
                "issuer": r.get("court_name") or r.get("court"),
                "published_at": r.get("decision_date") or r.get("date"),
                "text": r.get("full_text") or r.get("text"),
                "detail_url": r.get("canonical_url") or r.get("source_url") or r.get("url"),
                "downloads": [{"url": r["pdf_url"]}] if r.get("pdf_url") else [],
            }
            for r in entries[:limit]
            if r.get("full_text") or r.get("text") or r.get("pdf_url")
        ]

    def _juriscraper_json_records(
        self, value: Any, acquisition: Mapping[str, Any], limit: int
    ) -> list[dict[str, Any]]:
        court: Any = (
            cast(dict[str, Any], value).get("court_name") if isinstance(value, dict) else None
        )
        entries = items(value, ("cases", "opinions", "results"))
        if isinstance(value, dict) and isinstance(
            cast(dict[str, Any], value).get("case_names"), list
        ):
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
        records: list[dict[str, Any]] = []
        if len(entries) > limit:
            self.truncated = "document_budget_exceeded"
        for item in entries[:limit]:
            name = (
                item.get("case_name")
                or item.get("case_names")
                or item.get("caseName")
                or item.get("name")
            )
            number = (
                item.get("docket_number")
                or item.get("docket_numbers")
                or item.get("docketNumber")
            )
            raw_opinions = item.get("opinions")
            opinions: list[Any] = (
                cast(list[Any], raw_opinions) if isinstance(raw_opinions, list) else []
            )
            first_op: dict[str, Any] = (
                cast(dict[str, Any], opinions[0])
                if opinions and isinstance(opinions[0], dict)
                else {}
            )
            storage_base = str(self.options.get("storage_base_url") or "").rstrip("/")
            if not storage_base:
                allowed = cast(
                    Sequence[Any],
                    cast(dict[str, Any], self.config.get("scheduler") or {}).get("allowed_hosts")
                    or self.config.get("allowed_hosts")
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
                    "namespace": "juriscraper/opinion",
                    "external_key": str(item.get("cluster_id") or number or url),
                    "title": name,
                    "doc_number": scalar(number),
                    "issuer": item.get("court") or court,
                    "published_at": item.get("date_filed")
                    or item.get("case_dates")
                    or item.get("dateFiled"),
                    "status": item.get("precedential_status")
                    or item.get("precedential_statuses")
                    or item.get("status"),
                    "detail_url": item.get("detail_url")
                    or item.get("absolute_url")
                    or acquisition["actual_url"],
                    "doc_type": "案例",
                    "downloads": [{"url": url}],
                }
            )
        return records

    def _oldp_json_records(
        self, entries: Sequence[Mapping[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for raw in entries:
            if raw.get("model") and raw["model"] != "cases.case":
                continue
            r = cast(Mapping[str, Any], raw.get("fields", raw))
            result.append(
                {
                    "namespace": "oldp/case",
                    "external_key": str(r.get("ecli") or r.get("id") or r["file_number"]),
                    "title": r.get("title") or r["file_number"],
                    "doc_number": r.get("file_number"),
                    "issuer": r.get("court_name") or r.get("court"),
                    "published_at": r.get("date"),
                    "doc_type": "案例",
                    "detail_url": r.get("source_url") or r.get("url"),
                    "html": r.get("content") or r.get("text"),
                    "downloads": [{"url": r["pdf_url"]}] if r.get("pdf_url") else [],
                }
            )
        if len(result) > limit:
            self.truncated = "document_budget_exceeded"
        return result[:limit]

    def _worldwidelaw_records(
        self, entries: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        detail_tmpl = self.options.get("detail_url_template")
        att_rules = self.options.get("attachment_rules")
        records_out: list[dict[str, Any]] = []
        for r in entries:
            downloads: list[dict[str, Any]] = []
            if isinstance(att_rules, list):
                for rule_raw in cast(list[Any], att_rules):
                    if not isinstance(rule_raw, Mapping):
                        continue
                    rule = cast(Mapping[str, Any], rule_raw)
                    u = _dot_lookup(r, str(rule.get("url_path") or ""))
                    if u:
                        t_path = rule.get("title_path")
                        t_val = _dot_lookup(r, t_path) if isinstance(t_path, str) else None
                        dl: dict[str, Any] = {"url": str(u)}
                        if t_val:
                            dl["title"] = str(t_val)
                        downloads.append(dl)
            if not downloads and (u := r.get("pdf_url") or r.get("docx_url")):
                downloads = [{"url": u}]
            detail_url = r.get("url")
            if not detail_url and isinstance(detail_tmpl, str) and detail_tmpl:
                try:
                    detail_url = detail_tmpl.format_map({k: r.get(k, "") for k in r})
                except KeyError:
                    detail_url = None
            has_content = bool(r.get("text") or r.get("content") or downloads)
            if not has_content and isinstance(detail_tmpl, str) and detail_tmpl:
                continue
            records_out.append(
                {
                    "namespace": str(self.options.get("namespace") or "worldwidelaw/document"),
                    "external_key": str(
                        r.get("_id")
                        or r.get("id")
                        or r.get("document_number")
                        or detail_url
                        or r["url"]
                    ),
                    "title": r["title"],
                    "doc_number": r.get("document_number") or r.get("number"),
                    "issuer": (
                        r.get("issuing_authority") or r.get("authority") or r.get("issuer")
                    ),
                    "published_at": r.get("date") or r.get("publication_date"),
                    "effective_at": r.get("effective_date"),
                    "detail_url": detail_url,
                    "text": r.get("text") or r.get("content"),
                    "downloads": downloads,
                }
            )
        return records_out

    def next_requests(
        self, data: bytes, acquisition: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        official = japan_requests(data, acquisition, self.config)
        if official is not None:
            return official
        limit = _budget_documents(self.config)
        if data.lstrip().startswith((b"{", b"[")):
            value = payload(data)
            requests: list[dict[str, Any]] = []
            items_path = self.options.get("items_path")
            if isinstance(items_path, str) and items_path.strip() and isinstance(value, Mapping):
                resolved = _dot_lookup(value, items_path.strip())
                entries: list[dict[str, Any]] = (
                    [
                        cast(dict[str, Any], x)
                        for x in cast(list[Any], resolved)
                        if isinstance(x, dict)
                    ]
                    if isinstance(resolved, list)
                    else []
                )
            else:
                entries = items(value, ("decisions", "results"))
            if entries and any("decision_id" in r for r in entries):
                if len(entries) > limit:
                    self.truncated = "document_budget_exceeded"
                for r in entries[:limit]:
                    if r.get("decision_id") and not (
                        r.get("full_text") or r.get("text") or r.get("pdf_url")
                    ):
                        target = urljoin(
                            acquisition["actual_url"], f"/api/decisions/{r['decision_id']}"
                        )
                        requests.append({"url": target, "role": "page", "depth": 1})
            detail_tmpl = self.options.get("detail_url_template")
            if isinstance(detail_tmpl, str) and detail_tmpl and entries:
                for r in entries[:limit]:
                    if not (r.get("text") or r.get("content") or r.get("pdf_url")):
                        try:
                            det_url = detail_tmpl.format_map({k: r.get(k, "") for k in r})
                        except KeyError:
                            continue
                        if det_url:
                            requests.append(
                                {
                                    "url": urljoin(acquisition["actual_url"], det_url),
                                    "role": "page",
                                    "depth": 1,
                                }
                            )
            pag = self.options.get("pagination")
            if isinstance(pag, Mapping) and isinstance(value, Mapping):
                pag_map = cast(Mapping[str, Any], pag)
                pag_type = str(pag_map.get("type") or "")
                actual_url = str(acquisition.get("actual_url") or "")
                if pag_type == "next_url_path":
                    next_u = _dot_lookup(
                        value, str(pag_map.get("path") or pag_map.get("next_url_path") or "next")
                    )
                    if isinstance(next_u, str) and next_u.strip():
                        requests.append(
                            {"url": urljoin(actual_url, next_u.strip()), "role": "page"}
                        )
                elif pag_type == "page_param":
                    param = str(pag_map.get("param") or "page")
                    u_parts = urlsplit(actual_url)
                    m_page = re.search(rf"(?:^|&){re.escape(param)}=(\d+)", u_parts.query)
                    cur_page = int(m_page.group(1)) if m_page else 1
                    max_p_path = pag_map.get("max_pages_path")
                    max_p_val = (
                        _dot_lookup(value, max_p_path)
                        if isinstance(max_p_path, str)
                        else self._max_pages
                    )
                    max_page = (
                        int(max_p_val)
                        if isinstance(max_p_val, (int, str)) and str(max_p_val).isdigit()
                        else self._max_pages
                    )
                    if cur_page < min(max_page, self._max_pages):
                        next_page = cur_page + 1
                        if m_page:
                            next_query = re.sub(
                                rf"(^|&){re.escape(param)}=\d+",
                                rf"\g<1>{param}={next_page}",
                                u_parts.query,
                            )
                        else:
                            next_query = (
                                f"{u_parts.query}&{param}={next_page}"
                                if u_parts.query
                                else f"{param}={next_page}"
                            )
                        next_url = urlunsplit(
                            (u_parts.scheme, u_parts.netloc, u_parts.path, next_query, "")
                        )
                        requests.append({"url": next_url, "role": "page"})
            return requests
        if not data.startswith(b"PK\x03\x04") and data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(
            (b"<?xml", b"<items")
        ):
            try:
                tree: Any = etree.fromstring(
                    data, etree.XMLParser(resolve_entities=False, no_network=True)
                )
            except etree.XMLSyntaxError:
                return []
            if etree.QName(tree).localname == "items":
                base = str(
                    acquisition.get("actual_url") or acquisition.get("request_url") or ""
                )
                base_parts = urlsplit(base)
                urls: list[str] = []
                seen: set[str] = set()
                for raw in tree.xpath(".//*[local-name()='link']/text()"):
                    href = str(raw).strip()
                    if not href:
                        continue
                    parts = urlsplit(urljoin(base, href))
                    scheme = (
                        "https"
                        if parts.scheme == "http"
                        and base_parts.scheme == "https"
                        and parts.netloc == base_parts.netloc
                        else parts.scheme
                    )
                    try:
                        candidate = public_url(
                            urlunsplit(
                                (scheme, parts.netloc, parts.path or "/", parts.query, "")
                            )
                        )
                        guard(self.config, candidate)
                    except (WlhError, ValueError):
                        continue
                    if candidate not in seen:
                        seen.add(candidate)
                        urls.append(candidate)
                if len(urls) > limit:
                    self.truncated = "document_budget_exceeded"
                return [{"url": u, "role": "page"} for u in urls[:limit]]
        return []


Adapter = JsonParser

__all__ = [
    "Adapter",
    "FIELDS",
    "JsonParser",
    "TYPES",
    "pointer",
    "rii_xml",
]
