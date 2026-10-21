"""Shared archived-record delivery for multiple independent legal ecosystems.

Only source field mapping varies. Request planning, identity selectors, bounded
extraction and the core RawRecord contract stay the same for JSON, XML and HTML.
"""

import json
from datetime import date, datetime
from urllib.parse import urljoin

from wlh.adapters import Base, RawRecord, RawUnitSource
from wlh.adapters.web import parse_html
from wlh.core import WlhError, digest, public_url
from wlh.engine.scope import guard
from wlh.extraction.markdown import html_to_markdown, normalized
from wlh.extraction.office import detect_real_format


def scalar(value):
    if isinstance(value, dict):
        return str(value.get("name") or value.get("title") or value.get("id") or "")
    if isinstance(value, list):
        return "; ".join(scalar(v) or "" for v in value)
    return str(value).strip() if value is not None else None


def day(value):
    text = scalar(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        raise WlhError("document_date_unparseable") from None


def first(node, xpath):
    values = node.xpath(xpath)
    if isinstance(values, str):
        return values.strip() or None
    return (
        " ".join(
            " ".join(v.itertext()) if hasattr(v, "itertext") else str(v) for v in values
        ).strip()
        or None
    )


def payload(data):
    try:
        return json.loads(data)
    except ValueError:
        raise WlhError("bridge_json_invalid") from None


def items(value, names):
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        raise WlhError("bridge_records_invalid")
    for name in names:
        if isinstance(value.get(name), list):
            return value[name]
    return [value]


class RecordAdapter(Base):
    NAMESPACE = "record"

    def records(self, data, acquisition):
        raise NotImplementedError

    def next_requests(self, data, acquisition):
        return []

    def parse(self, ctx, task, acquisition):
        if task["payload"].get("role") == "attachment":
            return {"requests": [], "targets": []}
        data = ctx.read(acquisition)
        records = self.records(data, acquisition)
        targets = []
        for index, record in enumerate(records):
            key = str(record["external_key"])
            url = public_url(
                urljoin(
                    acquisition["actual_url"], record.get("detail_url") or acquisition["actual_url"]
                )
            )
            inputs = [
                {"request": task["payload"], "role": "record", "selector": key, "position": index}
            ]
            for unit in record.get("downloads", []):
                target = guard(self.config, urljoin(url, unit["url"]))
                inputs.append(
                    {
                        "request": {"url": target, "role": "attachment"},
                        "role": "attachment",
                        "unit": {k: v for k, v in unit.items() if k != "url"},
                    }
                )
            targets.append(
                {
                    "identity": {
                        "namespace": self.NAMESPACE,
                        "external_key": key,
                        "public_url": url,
                        "recheck_locator": {"url": task["payload"]["url"], "role": "page"},
                    },
                    "inputs": inputs,
                }
            )
        return {
            "requests": self.next_requests(data, acquisition) if ctx.follow_links(task) else [],
            "targets": targets,
        }

    def plan_recheck(self, resources, state):
        return {
            "requests": [{"url": r["recheck_locator"]["url"], "role": "page"} for r in resources],
            "targets": [],
            "state": state,
        }

    def build(self, ctx, target, inputs):
        acquisition = inputs[0]["acquisition"]
        records = self.records(ctx.read(acquisition), acquisition)
        key = inputs[0].get("selector") or target["identity"]["external_key"]
        matches = [(i, r) for i, r in enumerate(records) if str(r["external_key"]) == key]
        if len(matches) != 1:
            raise WlhError("bridge_record_identity_ambiguous")
        index, record = matches[0]
        url = target["identity"]["public_url"]
        lineage = [
            {
                "acquisition_id": acquisition["id"],
                "archive_ref": acquisition["archive_ref"],
                "role": "source_response",
                "locator": {"record_index": index},
                "semantic_locator": {"namespace": self.NAMESPACE, "external_key": key},
                "slice_hash": digest(record),
            }
        ]
        units = []
        for item in inputs[1:]:
            acq = item["acquisition"]
            data = ctx.read(acq)
            kind = detect_real_format(data)
            if kind == "html":
                markdown = html_to_markdown(data, acq["actual_url"])
                result = {
                    "quality": ctx.text_quality(markdown),
                    "info": {"kind": kind},
                    "parsers": ["markdown", "quality"],
                }
            else:
                result = ctx.extract(data, kind)
                markdown = result["markdown"]
            unit = item.get("unit") or {}
            role = unit.get("role", "primary" if not units else "independent_attachment")
            lineage.append(
                {
                    "acquisition_id": acq["id"],
                    "archive_ref": acq["archive_ref"],
                    "role": role,
                    "locator": {"kind": "whole_response", "url": acq["actual_url"]},
                    "volume_order": unit.get("volume_order"),
                }
            )
            units.append(
                RawUnitSource(
                    role=role,
                    title=unit.get("title"),
                    volume_order=unit.get("volume_order"),
                    markdown=markdown,
                    input=len(lineage) - 1,
                    quality=result["quality"],
                    extraction=result["info"],
                    parsers=result["parsers"],
                    original={"ref": acq["archive_ref"], "kind": kind, "byte_size": len(data)},
                )
            )
        if not units:
            markdown = (
                html_to_markdown(record["html"], url)
                if record.get("html")
                else normalized(record.get("text") or "")
            )
            if not markdown.strip():
                raise WlhError("bridge_document_text_missing")
            lineage[0]["role"] = "primary"
            units = [
                RawUnitSource(
                    markdown=markdown,
                    input=0,
                    quality=ctx.text_quality(markdown),
                    parsers=["markdown", "quality"],
                )
            ]
        for unit in units:
            if (
                unit.quality["recommended_route"]
                not in self.config["parser"]["quality_accept_routes"]
            ):
                raise WlhError("quality_check_failed:" + unit.quality["recommended_route"])
        return [
            RawRecord(
                namespace=self.NAMESPACE,
                external_key=key,
                title=record["title"],
                doc_type=record.get("doc_type") or self.config["metadata"]["document_type"],
                document_number=scalar(record.get("doc_number")),
                issuer=scalar(record.get("issuer")),
                published_at=day(record.get("published_at")),
                effective_at=day(record.get("effective_at")),
                expired_at=day(record.get("expired_at")),
                status=scalar(record.get("status")),
                source_name=self.config["metadata"]["source_name"],
                detail_url=url,
                units=units,
                inputs=lineage,
                recheck_locator=target["identity"]["recheck_locator"],
            )
        ]


def html_records(data, acquisition, *, containers, fields, namespace):
    tree, _, _ = parse_html(data, acquisition.get("charset"))
    nodes = tree.xpath(containers) or [tree]
    records = []
    for index, node in enumerate(nodes):
        values = {key: first(node, xpath) for key, xpath in fields.items()}
        title = values.get("title")
        if not title:
            raise WlhError("bridge_title_missing")
        identity = values.pop("external_key", None) or values.get("doc_number")
        if not identity:
            raise WlhError("bridge_identity_missing")
        body = node.xpath(
            './/*[contains(concat(" ",normalize-space(@class)," ")," tenor ") or contains(concat(" ",normalize-space(@class)," ")," body ")]'
        )
        import lxml.etree as etree

        fragment = etree.tostring(body[0] if body else node, encoding="unicode", method="html")
        records.append(
            {
                **values,
                "external_key": identity,
                "html": fragment,
                "detail_url": acquisition["actual_url"],
            }
        )
    return records
