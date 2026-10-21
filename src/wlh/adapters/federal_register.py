"""Federal Register API Adapter (JSON listing, per-document full-text XML and PDF original).

One listing response produces many documents: each target keeps the shared response with a
real JSON Pointer locator, plus the document's full-text XML (Markdown source) and the
official GPO PDF (the original file). A stable selector (document_number) — not the list
position — enters the input fingerprint, so re-ordering or unrelated listing changes do not
re-version a document. Only endpoint and field-mapping ideas come from public projects; no
external runner, storage or de-duplication state machine is used.
"""

import json
import re
from typing import Any
from urllib.parse import urlencode, urlsplit

import lxml.etree as etree

from wlh.adapters import Base
from wlh.core import WlhError, canonical, digest, public_url
from wlh.engine.scope import guard
from wlh.extraction.markdown import normalized, text_to_markdown
from wlh.extraction.office import detect_real_format

API = "https://www.federalregister.gov/api/v1"
FIELDS = (
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
TYPES = {
    "Rule": "法律法规",
    "Proposed Rule": "法律法规",
    "Notice": "通知公告",
    "Presidential Document": "政策文件",
}
HEADINGS = {"HD1": "##", "HD2": "###", "HD3": "####", "HD4": "#####", "HD5": "#####"}
SKIP = {"PRTPAGE", "FRDOC", "BILCOD", "GPH", "GID", "STARS"}


def pointer(value, path):
    """RFC 6901 JSON Pointer resolution (real locator into the archived response)."""
    if path == "":
        return value
    if not path.startswith("/"):
        raise WlhError("json_pointer_invalid")
    for raw in path[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not token.isdigit() or int(token) >= len(value):
                raise WlhError("json_pointer_unresolved")
            value = value[int(token)]
        elif isinstance(value, dict):
            if token not in value:
                raise WlhError("json_pointer_unresolved")
            value = value[token]
        else:
            raise WlhError("json_pointer_unresolved")
    return value


def text_of(node):
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def xml_to_markdown(data):
    """Federal Register full-text XML → Markdown: headings, paragraphs, tables, footnotes."""
    try:
        root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError:
        raise WlhError("full_text_xml_invalid") from None
    blocks = []

    def walk(node):
        tag = node.tag if isinstance(node.tag, str) else ""
        if tag in SKIP:
            return
        if tag == "HD":
            level = HEADINGS.get(node.get("SOURCE", ""), "")
            text = text_of(node)
            if text:
                blocks.append((level + " " if level else "**") + text + ("" if level else "**"))
            return
        if tag in {"SUBJECT", "SECTNO"}:
            text = text_of(node)
            if text:
                blocks.append("## " + text if tag == "SUBJECT" else "### " + text)
            return
        if tag in {"P", "FP", "AMDPAR", "NOTE", "SIG", "NAME", "TITLE", "DATED", "FTNT"}:
            if tag in {"NOTE", "SIG", "FTNT"} and len(node):
                for child in node:
                    walk(child)
                return
            text = text_of(node)
            if text:
                blocks.append(text_to_markdown(text).strip())
            return
        if tag == "GPOTABLE":
            rows = []
            heads = [text_of(c) for c in node.iter("CHED")]
            if heads:
                rows.append(heads)
            for row in node.iter("ROW"):
                rows.append([text_of(e) for e in row.iter("ENT")])
            if rows:
                width = max(len(r) for r in rows)
                rows = [[c.replace("|", "\\|") for c in r] + [""] * (width - len(r)) for r in rows]
                lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
                lines.extend("| " + " | ".join(r) + " |" for r in rows[1:])
                caption = node.find("TTITLE")
                if caption is not None and text_of(caption):
                    blocks.append("**" + text_of(caption) + "**")
                blocks.append("\n".join(lines))
            return
        for child in node:
            walk(child)

    walk(root)
    markdown = normalized("\n\n".join(b for b in blocks if b))
    if len(markdown.strip()) < 20:
        raise WlhError("full_text_xml_empty")
    return markdown


class Adapter(Base):
    CONTRACT = "wlh.adapter.federal-register.v1"
    PLANNING_OPTIONS = ("per_page", "max_pages", "conditions")

    @staticmethod
    def validate_options(options):
        allowed = {"conditions", "per_page", "max_pages", "listing_body_field", "document_types"}
        if set(options) - allowed:
            raise ValueError("unexpected_adapter_options")
        conditions = options.get("conditions", {})
        if not isinstance(conditions, dict):
            raise ValueError("invalid_conditions")
        for key, value in conditions.items():
            values = value if isinstance(value, list) else [value]
            if not re.fullmatch(r"[a-z_]+(\[[a-z_]*\])*", key) or not all(
                isinstance(v, str) and len(v) <= 200 for v in values
            ):
                raise ValueError("invalid_conditions")
        per_page = options.get("per_page", 20)
        pages = options.get("max_pages", 1)
        if type(per_page) is not int or not 1 <= per_page <= 100:
            raise ValueError("invalid_per_page")
        if type(pages) is not int or not 1 <= pages <= 1000:
            raise ValueError("invalid_max_pages")
        field = options.get("listing_body_field")
        if field is not None and (
            not isinstance(field, str) or not re.fullmatch(r"[a-z_]+", field)
        ):
            raise ValueError("invalid_listing_body_field")
        types = options.get("document_types", {})
        if not isinstance(types, dict):
            raise ValueError("invalid_document_types")

    def __init__(self, config):
        super().__init__(config)
        options = config["adapter_options"]
        self.options = options
        self.base = config["entry_urls"][0].rstrip("/")
        self.types = {**TYPES, **options.get("document_types", {})}

    def api_url(self, path, params):
        return public_url(
            self.base + path + ("?" + urlencode(params, doseq=True) if params else "")
        )

    def fields(self):
        return [("fields[]", f) for f in FIELDS] + (
            [("fields[]", self.options["listing_body_field"])]
            if self.options.get("listing_body_field") not in (None, *FIELDS)
            else []
        )

    def plan(self, state):
        params = [("per_page", str(self.options.get("per_page", 20))), ("order", "newest")]
        for key, value in sorted(self.options.get("conditions", {}).items()):
            name, _, rest = key.partition("[")
            param = "conditions[" + name + "]" + ("[" + rest if rest else "")
            if isinstance(value, list):
                params.extend((param + "[]", item) for item in value)
            else:
                params.append((param, value))
        params.extend(self.fields())
        url = self.api_url("/documents.json", params)
        return {
            "requests": [{"url": url, "role": "listing", "depth": 0, "meta": {"page": 1}}],
            "targets": [],
            "state": state,
        }

    def record_url(self, number):
        return self.api_url("/documents/" + number + ".json", self.fields())

    def target(self, item, listing_request, index):
        number = item.get("document_number")
        if not isinstance(number, str) or not re.fullmatch(r"[A-Za-z0-9-]{4,40}", number):
            raise WlhError("document_identity_missing")
        inputs = [
            {
                "request": listing_request,
                "role": "listing",
                "pointer": "" if index is None else "/results/" + str(index),
            }
        ]
        if not self.options.get("listing_body_field"):
            for role, key in (("full_text", "full_text_xml_url"), ("original", "pdf_url")):
                url = item.get(key)
                if url:
                    inputs.append(
                        {
                            "request": {"url": guard(self.config, url), "role": role, "depth": 1},
                            "role": role,
                        }
                    )
        html_url = item.get("html_url")
        return {
            "identity": {
                "namespace": "federalregister.gov/document_number",
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

    def plan_recheck(self, resources, state):
        """Due documents are merged into batch requests; each still gets its own Observation.

        The request budget counts real batch requests, so more due Resources than requests
        can be rechecked. Identity comes from the frozen recheck_locator, never public_url.
        """
        targets = []
        size = self.options.get("per_page", 20)
        numbers = []
        for resource in resources:
            locator = resource["recheck_locator"]
            if locator.get("role") != "record" or not locator.get("document_number"):
                raise WlhError("recheck_locator_unsupported")
            numbers.append((locator["document_number"], resource))
        for start in range(0, len(numbers), size):
            chunk = numbers[start : start + size]
            batch = ",".join(sorted(number for number, _ in chunk))
            request = {
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

    def load_json(self, ctx, acquisition):
        try:
            return json.loads(ctx.read(acquisition))
        except ValueError:
            raise WlhError("api_response_not_json") from None

    def parse(self, ctx, task, acquisition):
        request = task["payload"]
        role = request.get("role")
        if acquisition["outcome"] != "ok" or role not in {"listing", "record", "batch"}:
            return {"requests": [], "targets": []}
        value = self.load_json(ctx, acquisition)
        request = {k: request[k] for k in ("url", "role") if k in request} | {
            "depth": task["depth"]
        }
        if role == "record":
            if not isinstance(value, dict):
                raise WlhError("api_structure_changed")
            return {"requests": [], "targets": [self.target(value, request, None)]}
        if role == "batch":
            # Batch recheck response: each document gains its body inputs; selection is by
            # document_number, never by position in the shared response.
            results = value.get("results") if isinstance(value, dict) else None
            if results is None and isinstance(value, dict) and value.get("document_number"):
                results = [value]
            if not isinstance(results, list):
                raise WlhError("api_structure_changed")
            targets = []
            for item in results:
                target = self.target(item, request, None)
                target["inputs"][0].pop("pointer")
                target["inputs"][0]["select"] = {"document_number": item["document_number"]}
                targets.append(target)
            return {"requests": [], "targets": targets}
        results = value.get("results") if isinstance(value, dict) else None
        if not isinstance(results, list):
            raise WlhError("api_structure_changed")
        targets = [self.target(item, request, i) for i, item in enumerate(results)]
        page = (task["payload"].get("meta") or {}).get("page", 1)
        follow = []
        following = value.get("next_page_url")
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
                "count": value.get("count"),
                "last_page": not following,
            },
        }

    def build(self, ctx, target, inputs):
        by_role = {item["role"]: item for item in inputs}
        listing: dict[str, Any] = by_role["listing"]
        response = self.load_json(ctx, listing["acquisition"])
        if listing.get("select"):
            wanted = listing["select"]["document_number"]
            if isinstance(response, dict) and response.get("document_number") == wanted:
                listing = dict(listing, pointer="")
            else:
                results = response.get("results") if isinstance(response, dict) else None
                matches = [
                    i
                    for i, item in enumerate(results or [])
                    if isinstance(item, dict) and item.get("document_number") == wanted
                ]
                if len(matches) != 1:
                    raise WlhError("document_missing_from_batch")
                listing = dict(listing, pointer="/results/" + str(matches[0]))
        document = pointer(response, listing.get("pointer", ""))
        if not isinstance(document, dict):
            raise WlhError("api_structure_changed")
        number = target["identity"]["external_key"]
        if document.get("document_number") != number:
            raise WlhError("api_structure_changed")
        listing_url = public_url(listing["acquisition"]["request_url"])
        slice_bytes = canonical(document)
        candidate_inputs = [
            {
                "acquisition_id": listing["acquisition"]["id"],
                "archive_ref": listing["acquisition"]["archive_ref"],
                "role": "source_response",
                "locator": {"url": listing_url, "json_pointer": listing.get("pointer", "")},
                # The selector, not the list position or page cursor, is document semantics.
                "semantic_locator": {
                    "collection": "federal_register.documents",
                    "document_number": number,
                },
                "slice_hash": digest(slice_bytes),
                "charset": listing["acquisition"]["charset"],
            }
        ]
        parsers = {"markdown"}
        deliverables = []
        body_field = self.options.get("listing_body_field")
        if body_field:
            body = document.get(body_field)
            if not isinstance(body, str) or len(body.strip()) < 20:
                raise WlhError("document_body_missing")
            quality = ctx.text_quality(body)
            if quality["recommended_route"] not in self.config["parser"]["quality_accept_routes"]:
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
                extraction = {"kind": "federal_register_xml", "markdown_source": "full_text_xml"}
                quality = ctx.text_quality(markdown)
            else:
                result = ctx.extract(pdf_data, "pdf")
                markdown, quality = result["markdown"], result["quality"]
                extraction = result["info"]
                parsers.update(result["parsers"])
            if quality["recommended_route"] not in self.config["parser"]["quality_accept_routes"]:
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
                        "original_name": urlsplit(pdf_acq["request_url"]).path.rsplit("/", 1)[-1],
                    },
                    "quality": quality,
                    "extraction": extraction,
                }
            )
        agencies = [a.get("name") for a in document.get("agencies") or [] if isinstance(a, dict)]
        dtype = self.types.get(document.get("type"), self.config["metadata"]["document_type"])
        metadata = dict(self.config["metadata"], document_type=dtype)
        metadata.update(
            title=document.get("title"),
            document_number=number,
            issuer="; ".join(a for a in agencies if a) or None,
        )
        dates = {
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
