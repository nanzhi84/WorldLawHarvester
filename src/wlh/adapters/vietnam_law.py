"""dd-492 VietnamLawAdapter: ASP.NET DoPostBack listing, detail metadata, attachments.

Listing pages, POST pagination, detail pages and originals (PDF/DOC/DOCX/RTF/ZIP) are all
controlled Acquisitions archived in CAS; the task queue and page watermark live in WLH's
persistent tasks and frontier checkpoint (no SQLite queue, no batch runner).
"""

import re
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlsplit

from lxml import html

from wlh.adapters.web import Adapter as WebAdapter
from wlh.adapters.web import parse_html
from wlh.core import WlhError, public_url
from wlh.engine.scope import guard

LISTING_GRID = '//table[contains(@id,"grvDocument")]'
ATTACHMENT = re.compile(r"(?i)\.(?:pdf|docx?|rtf|zip|xlsx?)$")
DETAIL_LABELS = {
    "Số ký hiệu": "document_number",
    "Ngày ban hành": "published_at",
    "Cơ quan ban hành": "issuer",
    "Loại văn bản": "kind",
    "Trích yếu": "abstract",
    "Ngày hiệu lực": "effective_at",
    "Ngày có hiệu lực": "effective_at",
    "Ngày hết hiệu lực": "expired_at",
    "Tình trạng hiệu lực": "status",
}


def listing(raw, url):
    """One listing page: detail links, page number and the next DoPostBack event."""
    tree = html.fromstring(raw)
    forms = tree.xpath('//form[@method="post" or @method="POST"]')
    grids = tree.xpath(LISTING_GRID)
    if not forms or not grids:
        raise WlhError("document_list_not_found")
    form, grid = forms[0], grids[0]
    fields = {}
    for node in form.xpath(".//input[@name] | .//select[@name] | .//textarea[@name]"):
        if node.tag == "select":
            options = node.xpath("./option[@selected]") or node.xpath("./option")[:1]
            fields[node.get("name")] = options[0].get("value", "") if options else ""
        elif node.get("type", "").lower() not in {"submit", "button", "image", "checkbox", "radio"}:
            fields[node.get("name")] = node.get("value", "")
    details = []
    for link in grid.xpath(".//a/@href"):
        if re.search(r"(?:\?|&)docid=\d+", link):
            target = public_url(urljoin(url, link))
            if target not in details:
                details.append(target)
    events = []
    for link in grid.xpath(".//a/@href"):
        match = re.search(r"__doPostBack\('([^']+)','(Page\$[^']+)'\)", link)
        if match:
            events.append(match.groups())
    current = grid.xpath(".//tr[last()]//td/span/text()")
    if not details or not current or not str(current[-1]).strip().isdigit():
        raise WlhError("document_page_identity_missing")
    number = int(current[-1])
    following = next((e for e in events if e[1] == f"Page${number + 1}"), None)
    if following is None and any(e[1] == "Page$Last" for e in events):
        raise WlhError("document_next_page_not_found")
    return {
        "number": number,
        "details": details,
        "fields": fields,
        "action": urljoin(url, form.get("action", "")),
        "next": following,
    }


def docid(url):
    values = parse_qs(urlsplit(url).query).get("docid")
    if not values or not values[0].isdigit():
        raise WlhError("document_identity_missing")
    return values[0]


class Adapter(WebAdapter):
    CONTRACT = "wlh.adapter.vietnam-law.v3"
    PLANNING_OPTIONS = ("max_pages",)

    @staticmethod
    def validate_options(options):
        allowed = {"max_pages"}
        if set(options) - allowed:
            raise ValueError("unexpected_adapter_options")
        pages = options.get("max_pages", 1)
        if type(pages) is not int or not 1 <= pages <= 10000:
            raise ValueError("invalid_max_pages")

    def __init__(self, config):
        super().__init__(config)
        self.max_pages = config["adapter_options"].get("max_pages", 1)
        self.portal = None
        if urlsplit(config["entry_urls"][0]).hostname in {"congbao.chinhphu.vn", "vbpl.vn"}:
            from wlh.adapters.bridges.worldwidelaw_bridge import Adapter as PortalAdapter

            self.portal = PortalAdapter(config)

    def plan(self, state):
        if self.portal:
            return self.portal.plan(state)
        url = self.config["entry_urls"][0]
        if re.search(r"(?:^|[?&])docid=\d+", urlsplit(url).query):
            return {
                "requests": [{"url": url, "role": "detail", "depth": 0}],
                "targets": [],
                "state": state,
            }
        return {
            "requests": [
                {
                    "url": self.config["entry_urls"][0],
                    "role": "listing",
                    "depth": 0,
                    "meta": {"page": 1},
                }
            ],
            "targets": [],
            "state": state,
        }

    def target(self, detail_url, attachments=()):
        key = docid(detail_url)
        inputs = [{"request": {"url": detail_url, "role": "detail", "depth": 1}, "role": "detail"}]
        for item in attachments:
            inputs.append(
                {
                    "request": {"url": item["url"], "role": "attachment", "depth": 1},
                    "role": "attachment",
                    "text": item["text"],
                }
            )
        return {
            "identity": {
                "namespace": "vanban.chinhphu.vn/docid",
                "external_key": key,
                "public_url": detail_url,
                "recheck_locator": {"url": detail_url, "role": "detail", "docid": key},
            },
            "inputs": inputs,
        }

    def plan_recheck(self, resources, state):
        if self.portal:
            return self.portal.plan_recheck(resources, state)
        targets = []
        for resource in resources:
            locator = resource["recheck_locator"]
            if not locator.get("url"):
                raise WlhError("recheck_locator_unsupported")
            target = self.target(locator["url"])
            target["identity"]["public_url"] = resource["public_url"]
            targets.append(target)
        return {"requests": [], "targets": targets, "state": state}

    def parse(self, ctx, task, acquisition):
        if self.portal:
            return self.portal.parse(ctx, task, acquisition)
        request = task["payload"]
        role = request.get("role")
        if acquisition["outcome"] != "ok" or role == "attachment":
            return {"requests": [], "targets": []}
        data = ctx.read(acquisition)
        url = public_url(request["url"])
        if role == "listing":
            expected = (request.get("meta") or {}).get("page", 1)
            page = listing(data, self.config["entry_urls"][0])
            if page["number"] != expected:
                raise WlhError("document_pagination_did_not_advance")
            result = {
                "requests": [],
                "targets": [self.target(detail) for detail in page["details"]],
                "state": {"last_listing_page": page["number"], "last_page": page["next"] is None},
            }
            if page["next"] is not None and page["number"] >= self.max_pages:
                result["truncated"] = "page_limit_exceeded"
            if page["next"] is not None and page["number"] < self.max_pages:
                target, argument = page["next"]
                guard(self.config, page["action"])
                result["requests"].append(
                    {
                        "url": page["action"],
                        "method": "POST",
                        # The POST only changes the public listing position; replay is safe.
                        "idempotent": True,
                        "form": dict(
                            page["fields"], __EVENTTARGET=target, __EVENTARGUMENT=argument
                        ),
                        "role": "listing",
                        "depth": 0,
                        "meta": {"page": page["number"] + 1},
                    }
                )
            return result
        if role == "detail":
            tree, _, _ = parse_html(data, acquisition["charset"])
            if not tree.xpath('//td[contains(normalize-space(.),"Số ký hiệu")]'):
                raise WlhError("document_detail_structure_changed")
            attachments = []
            for anchor in tree.xpath("//a[@href]"):
                link = urljoin(url, anchor.get("href").strip())
                if not ATTACHMENT.search(urlsplit(link).path):
                    continue
                try:
                    link = guard(self.config, link)
                except WlhError:
                    raise WlhError("attachment_out_of_scope") from None
                if link not in [a["url"] for a in attachments]:
                    text = re.sub(r"\s+", " ", anchor.text_content()).strip()
                    attachments.append({"url": link, "text": text})
            return {"requests": [], "targets": [self.target(url, attachments)]}
        raise WlhError("unexpected_request_role")

    def detail_values(self, data, kind, acquisition, url):
        if kind != "html":
            raise WlhError("document_detail_structure_changed")
        tree, _, encoding = parse_html(data, acquisition["charset"])
        values = {}
        for row in tree.xpath("//tr[count(td)=2]"):
            label, value = (
                re.sub(r"\s+", " ", td.text_content()).strip() for td in row.xpath("td")
            )
            name = DETAIL_LABELS.get(label)
            if name and value and name not in values:
                values[name] = value
        if not values.get("document_number"):
            raise WlhError("document_detail_structure_changed")
        for field in ("published_at", "effective_at", "expired_at"):
            if values.get(field):
                try:
                    values[field] = datetime.strptime(values[field], "%d-%m-%Y").date().isoformat()
                except ValueError:
                    try:
                        values[field] = (
                            datetime.strptime(values[field], "%d/%m/%Y").date().isoformat()
                        )
                    except ValueError:
                        raise WlhError("document_date_unparseable") from None
        titles = [
            re.sub(r"\s+", " ", t).strip()
            for t in tree.xpath('//h4[contains(@class,"title")]//text()|//title/text()')
            if t.strip()
        ]
        title = titles[0] if titles else None
        if not title:
            title = " ".join(v for v in (values.get("kind"), values.get("document_number")) if v)
        return values, title, encoding

    def build(self, ctx, target, inputs):
        if self.portal:
            return self.portal.build(ctx, target, inputs)
        return super().build(ctx, target, inputs)
