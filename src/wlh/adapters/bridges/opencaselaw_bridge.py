"""OpenCaseLaw Decision records and Swiss Bundesgericht HTML."""

from wlh.adapters.bridges.common import RecordAdapter, html_records, items, payload


class Adapter(RecordAdapter):
    CONTRACT = "wlh.adapter.opencaselaw.v1"
    NAMESPACE = "opencaselaw/decision"

    def records(self, data, acquisition):
        if data.lstrip().startswith((b"{", b"[")):
            return [
                {
                    "external_key": str(r.get("decision_id") or r.get("id") or r["docket_number"]),
                    "title": r.get("title") or r.get("docket_number"),
                    "doc_type": "案例",
                    "doc_number": r.get("docket_number") or r.get("case_number"),
                    "issuer": r.get("court") or r.get("court_name"),
                    "published_at": r.get("decision_date") or r.get("date"),
                    "text": r.get("full_text") or r.get("text"),
                    "detail_url": r.get("source_url") or r.get("url"),
                    "downloads": [{"url": r["pdf_url"]}] if r.get("pdf_url") else [],
                }
                for r in items(payload(data), ("decisions", "results"))
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
