"""Juriscraper opinion dictionaries and court HTML lists -> archived legal records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from wlh.adapters.bridges.common import RecordAdapter, items, payload, scalar
from wlh.adapters.web import parse_html
from wlh.core import WlhError


class Adapter(RecordAdapter):
    CONTRACT = "wlh.adapter.juriscraper.v1"
    NAMESPACE = "juriscraper/opinion"

    def records(self, data: bytes, acquisition: Mapping[str, Any]) -> list[dict[str, Any]]:
        if data.lstrip().startswith((b"{", b"[")):
            value: Any = payload(data)
            court: Any = cast(dict[str, Any], value).get("court_name") if isinstance(value, dict) else None
            entries = items(value, ("cases", "opinions", "results"))
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
            records: list[dict[str, Any]] = []
            limit: int = self.config["budget"]["documents"]
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
                opinions: list[Any] = cast(list[Any], raw_opinions) if isinstance(raw_opinions, list) else []
                first_op: dict[str, Any] = (
                    cast(dict[str, Any], opinions[0])
                    if opinions and isinstance(opinions[0], dict)
                    else {}
                )
                local_pdf = (
                    "https://storage.courtlistener.com/" + str(first_op["local_path"]).lstrip("/")
                    if first_op.get("local_path")
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
        tree, _, _ = parse_html(data, acquisition.get("charset"))
        records = []
        for link in tree.xpath('//a[contains(@href,".pdf") or contains(@href,"getopn.pl")]'):
            row = link.xpath("ancestor::tr[1]")
            cells = [" ".join(c.itertext()).strip() for c in row[0].xpath("./td")] if row else []
            title = " ".join(link.itertext()).strip()
            records.append(
                {
                    "external_key": link.get("href"),
                    "title": title or " ".join(cells),
                    "doc_type": "案例",
                    "text": "",
                    "downloads": [{"url": link.get("href")}],
                    "detail_url": acquisition["actual_url"],
                }
            )
        if not records:
            raise WlhError("juriscraper_opinions_missing")
        return records


JuriscraperBridge = Adapter
