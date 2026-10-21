"""Open States v3 / scraper Bill dictionaries, including versions and attachments."""

from wlh.adapters.bridges.common import RecordAdapter, items, payload, scalar
from wlh.core import WlhError


class Adapter(RecordAdapter):
    CONTRACT = "wlh.adapter.openstates.v1"
    NAMESPACE = "openstates/bill"

    def records(self, data, acquisition):
        records = []
        for item in items(payload(data), ("bills", "results")):
            if not item.get("title") or not item.get("identifier"):
                raise WlhError("openstates_bill_invalid")
            downloads = []
            for group in ("versions", "documents"):
                for document in item.get(group) or []:
                    links = document.get("links") or []
                    preferred = next(
                        (v for v in links if v.get("media_type") == "application/pdf"),
                        links[0] if links else None,
                    )
                    if preferred:
                        downloads.append(
                            {
                                "url": preferred["url"],
                                "title": document.get("note") or item["title"],
                                "role": "primary" if not downloads else "independent_attachment",
                            }
                        )
            sources = item.get("sources") or []
            detail = (
                item.get("url")
                or (sources[0].get("url") if sources else None)
                or item.get("openstates_url")
            )
            abstracts = item.get("abstracts") or []
            body = (
                item.get("text")
                or item.get("abstract")
                or "\n\n".join(a.get("abstract", "") for a in abstracts)
            )
            records.append(
                {
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


OpenStatesBridge = Adapter
