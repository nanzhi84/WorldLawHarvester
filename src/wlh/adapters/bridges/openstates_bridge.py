"""Open States v3 / scraper Bill dictionaries, including versions and attachments."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from wlh.adapters.bridges.common import RecordAdapter, items, payload, scalar
from wlh.core import WlhError


class Adapter(RecordAdapter):
    CONTRACT = "wlh.adapter.openstates.v5"
    NAMESPACE = "openstates/bill"

    def records(self, data: bytes, acquisition: Mapping[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for item in items(payload(data), ("bills", "results")):
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
