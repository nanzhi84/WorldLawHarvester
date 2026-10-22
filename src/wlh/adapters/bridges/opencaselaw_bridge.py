"""OpenCaseLaw Decision records and Swiss Bundesgericht HTML."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urljoin

from wlh.adapters.bridges.common import RecordAdapter, html_records, items, payload


class Adapter(RecordAdapter):
    CONTRACT = "wlh.adapter.opencaselaw.v1"
    NAMESPACE = "opencaselaw/decision"

    def next_requests(self, data: bytes, acquisition: Mapping[str, Any]) -> list[dict[str, Any]]:
        if not data.lstrip().startswith((b"{", b"[")):
            return []
        entries = items(payload(data), ("decisions", "results"))
        limit: int = self.config["budget"]["documents"]
        if len(entries) > limit:
            self.truncated = "document_budget_exceeded"
        requests: list[dict[str, Any]] = []
        for r in entries[:limit]:
            if (
                r.get("decision_id")
                and not (r.get("full_text") or r.get("text") or r.get("pdf_url"))
            ):
                target = urljoin(acquisition["actual_url"], f"/api/decisions/{r['decision_id']}")
                requests.append({"url": target, "role": "page", "depth": 1})
        return requests

    def records(self, data: bytes, acquisition: Mapping[str, Any]) -> list[dict[str, Any]]:
        if data.lstrip().startswith((b"{", b"[")):
            entries = items(payload(data), ("decisions", "results"))
            limit: int = self.config["budget"]["documents"]
            if len(entries) > limit:
                self.truncated = "document_budget_exceeded"
            return [
                {
                    "external_key": str(r.get("decision_id") or r.get("id") or r["docket_number"]),
                    "title": r.get("title")
                    or r.get("citation_string_de")
                    or r.get("docket_number"),
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
        return html_records(
            data,
            acquisition,
            containers='//*[@class="decision" or @class="content"]',
            namespace=self.NAMESPACE,
            fields={
                "title": ".//h1//text()|.//h2//text()",
                "external_key": "./@data-id",
                "doc_number": './/*[@class="docket" or @class="docket-number"]//text()',
                "issuer": './/*[@class="court"]//text()',
                "published_at": './/*[@class="date"]//text()',
                "doc_type": 'string("案例")',
            },
        )


OpenCaseLawBridge = Adapter
