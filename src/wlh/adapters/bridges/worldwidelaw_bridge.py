"""World Wide Law records and CongBao HTML discovery through the controlled session."""

import re

from wlh.adapters.bridges.common import RecordAdapter, day, items, payload
from wlh.adapters.web import Adapter as WebAdapter
from wlh.adapters.web import parse_html
from wlh.extraction.office import detect_real_format


class JsonAdapter(RecordAdapter):
    CONTRACT = "wlh.adapter.worldwidelaw-json.v1"
    NAMESPACE = "worldwidelaw/document"

    def records(self, data, acquisition):
        return [
            {
                "external_key": str(
                    r.get("_id") or r.get("id") or r.get("document_number") or r["url"]
                ),
                "title": r["title"],
                "doc_number": r.get("document_number") or r.get("number"),
                "issuer": r.get("issuing_authority") or r.get("authority") or r.get("issuer"),
                "published_at": r.get("date") or r.get("publication_date"),
                "effective_at": r.get("effective_date"),
                "detail_url": r.get("url"),
                "text": r.get("text") or r.get("content"),
                "downloads": [{"url": u}] if (u := r.get("pdf_url") or r.get("docx_url")) else [],
            }
            for r in items(payload(data), ("records", "results", "data"))
        ]


class Adapter(WebAdapter):
    CONTRACT = "wlh.adapter.worldwidelaw.v2"

    def __init__(self, config):
        super().__init__(config)
        self.details = self.details or [
            re.compile(r"/detail/|/van-ban/[^/?#]+\.htm$|[?&](?:docid|ItemID)=")
        ]
        self.detail_url = None
        self.json_adapter = JsonAdapter(config)

    def is_detail(self, url):
        return url == self.detail_url or super().is_detail(url)

    def parse(self, ctx, task, acquisition):
        data = ctx.read(acquisition)
        if detect_real_format(data) == "json":
            return self.json_adapter.parse(ctx, task, acquisition)
        if detect_real_format(data) == "html":
            tree, _, _ = parse_html(data, acquisition.get("charset"))
            if tree.xpath(
                '//*[contains(@class,"doc-number")]|//td[contains(.,"Số ký hiệu")]'
                '|//*[contains(@class,"name") and contains(.,"Số, ký hiệu")]'
            ):
                self.detail_url = acquisition["actual_url"]
        result = super().parse(ctx, task, acquisition)
        if result["targets"]:
            result["requests"] = []
        elif any(self.is_detail(r["url"]) for r in result["requests"]):
            limit = self.config["budget"]["documents"]
            details = [r for r in result["requests"] if self.is_detail(r["url"])]
            result["requests"] = details[:limit]
        return result

    def detail_values(self, data, kind, acquisition, url):
        values, title, encoding = super().detail_values(data, kind, acquisition, url)
        if kind != "html":
            return values, title, encoding
        tree, _, _ = parse_html(data, acquisition.get("charset"))
        classes = {
            "document_number": ("doc-number", "doc-num"),
            "issuer": ("issuer", "authority"),
            "published_at": ("published-at", "pub-date"),
            "effective_at": ("effective-at",),
            "status": ("status",),
        }
        labels = {
            "Số ký hiệu": "document_number",
            "Số, ký hiệu": "document_number",
            "Số hiệu": "document_number",
            "Cơ quan ban hành": "issuer",
            "Ngày ban hành": "published_at",
            "Ngày có hiệu lực": "effective_at",
            "Ngày hiệu lực": "effective_at",
        }
        for name, names in classes.items():
            for css in names:
                found = tree.xpath(
                    '//*[contains(concat(" ",normalize-space(@class)," "),$cls)]',
                    cls=" " + css + " ",
                )
                if found:
                    values.setdefault(name, " ".join(found[0].itertext()).strip())
                    break
        for row in tree.xpath("//tr[count(td)=2]"):
            cells = [" ".join(n.itertext()).strip().rstrip(":") for n in row.xpath("./td")]
            if cells[0] in labels:
                values.setdefault(labels[cells[0]], cells[1])
        # Live CongBao metadata uses name/value div rows instead of table cells.
        for row in tree.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," row ")]'):
            names = row.xpath('./*[contains(concat(" ",normalize-space(@class)," ")," name ")]')
            contents = row.xpath('./*[contains(concat(" ",normalize-space(@class)," ")," value ")]')
            if names and contents:
                label = " ".join(names[0].text_content().split()).rstrip(":")
                value = " ".join(contents[0].text_content().split())
                if label in labels and value and not values.get(labels[label]):
                    values[labels[label]] = value
        if not values.get("document_number") and title:
            number = re.search(r"\b\d+/\d{4}/[\wĐđ]+(?:-[\wĐđ]+)*", title)
            if number:
                values["document_number"] = number[0]
        for name in ("published_at", "effective_at", "expired_at"):
            if values.get(name):
                values[name] = day(values[name])
        return values, title, encoding

    def build(self, ctx, target, inputs):
        if detect_real_format(ctx.read(inputs[0]["acquisition"])) == "json":
            return self.json_adapter.build(ctx, target, inputs)
        return super().build(ctx, target, inputs)


WorldWideLawBridge = Adapter
