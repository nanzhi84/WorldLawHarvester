"""Juriscraper opinion dictionaries and court HTML lists -> archived legal records."""

from wlh.adapters.bridges.common import RecordAdapter, items, payload, scalar
from wlh.adapters.web import parse_html
from wlh.core import WlhError


class Adapter(RecordAdapter):
    CONTRACT = "wlh.adapter.juriscraper.v1"
    NAMESPACE = "juriscraper/opinion"

    def records(self, data, acquisition):
        if data.lstrip().startswith((b"{", b"[")):
            value = payload(data)
            court = value.get("court_name") if isinstance(value, dict) else None
            entries = items(value, ("cases", "opinions", "results"))
            if isinstance(value, dict) and isinstance(value.get("case_names"), list):
                entries = [
                    {
                        key: values[i]
                        for key, values in value.items()
                        if isinstance(values, list) and i < len(values)
                    }
                    for i in range(len(value["case_names"]))
                ]
            records = []
            for item in entries:
                name = item.get("case_name") or item.get("case_names") or item.get("name")
                number = item.get("docket_number") or item.get("docket_numbers")
                url = item.get("download_url") or item.get("download_urls")
                if not name or not url:
                    raise WlhError("juriscraper_opinion_invalid")
                records.append(
                    {
                        "external_key": str(number or url),
                        "title": name,
                        "doc_number": scalar(number),
                        "issuer": item.get("court") or court,
                        "published_at": item.get("date_filed") or item.get("case_dates"),
                        "status": item.get("precedential_status")
                        or item.get("precedential_statuses"),
                        "detail_url": item.get("detail_url") or acquisition["actual_url"],
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
