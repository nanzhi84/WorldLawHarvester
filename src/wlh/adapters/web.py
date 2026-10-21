"""Generic web Adapter: scoped link discovery, detail pages, volumes and attachments.

Without ``documents.detail_url_patterns`` every fetched in-scope response is a single-file
document (the historical crawler behaviour). With patterns, matching pages become
documents whose attachment links are required inputs (volumes or independent
attachments); other pages are navigation only.
"""

import re
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

import lxml.etree as etree
from lxml import html

from wlh.adapters import Base
from wlh.core import WlhError, canonical, digest, public_url
from wlh.engine.scope import guard
from wlh.engine.transport import blocked
from wlh.extraction.markdown import html_to_markdown, text_to_markdown
from wlh.extraction.office import detect_real_format

TEXT_KINDS = {"html", "txt"}
BINARY_KINDS = {"pdf", "doc", "docx", "rtf", "xls", "xlsx", "zip"}


def decode(data, charset):
    for encoding in (charset, "utf-8"):
        if encoding:
            try:
                return data.decode(encoding), encoding
            except (LookupError, UnicodeDecodeError):
                continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def parse_html(data, charset):
    text, encoding = decode(data, charset)
    try:
        tree = html.document_fromstring(text)
    except (etree.ParserError, ValueError):
        raise WlhError("html_unparseable") from None
    return tree, text, encoding


def clean_url(base, href):
    if not href:
        return None
    href = href.strip()
    if href.lower().startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
        return None
    try:
        parts = urlsplit(urljoin(base, href))
        if parts.scheme not in ("http", "https"):
            return None
        return public_url(
            urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))
        )
    except (WlhError, ValueError):
        return None


def file_name(url):
    parts = urlsplit(url)
    query_name = parse_qs(parts.query).get("file_name", [""])[0]
    name = PurePosixPath(query_name or unquote(parts.path)).name
    return name or "file"


def field_value(tree, text, rule):
    if rule.get("value") is not None:
        return rule["value"]
    value = text
    if rule.get("xpath"):
        found = tree.xpath(rule["xpath"])
        if isinstance(found, list):
            if not found:
                return None
            first = found[0]
            value = first if isinstance(first, str) else first.text_content()
        elif isinstance(found, str):
            value = found
        elif found is None or isinstance(found, bool):
            return None
        else:
            value = str(found)
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    if rule.get("regex"):
        match = re.search(rule["regex"], value)
        if not match:
            return None
        value = match.groupdict().get("value") or (match.group(1) if match.groups() else match[0])
    if rule.get("date_format"):
        try:
            value = datetime.strptime(value.strip(), rule["date_format"]).date().isoformat()
        except ValueError:
            raise WlhError("document_date_unparseable") from None
    return value.strip() or None


class Adapter(Base):
    CONTRACT = "wlh.adapter.web.v3"

    def __init__(self, config):
        super().__init__(config)
        rules = config["documents"]
        self.details = [re.compile(p) for p in rules["detail_url_patterns"]]
        self.attachment = re.compile(rules["attachment_url_pattern"])
        self.volume = re.compile(rules["volume_pattern"]) if rules["volume_pattern"] else None

    def is_detail(self, url):
        return not self.details or any(p.search(url) for p in self.details)

    def in_scope(self, url):
        try:
            guard(self.config, url)
            return True
        except WlhError:
            return False

    def parse(self, ctx, task, acquisition):
        request = task["payload"]
        if acquisition["outcome"] != "ok":
            return {"requests": [], "targets": []}
        role = request.get("role", "page")
        if role != "page":
            return {"requests": [], "targets": []}
        data = ctx.read(acquisition)
        kind = detect_real_format(data, "html" if acquisition["mime_type"] == "text/html" else "")
        url = acquisition["actual_url"] or acquisition["request_url"]
        detail = self.is_detail(url)
        requests, attachments = [], []
        if kind == "html":
            tree, _, _ = parse_html(data, acquisition["charset"])
            seen = set()
            for anchor in tree.xpath("//a[@href]"):
                link = clean_url(url, anchor.get("href"))
                if not link or link in seen or not self.in_scope(link):
                    continue
                seen.add(link)
                if self.attachment.search(unquote(link)) or self.attachment.search(file_name(link)):
                    if self.details and detail:
                        text = re.sub(r"\s+", " ", anchor.text_content()).strip()
                        attachments.append({"url": link, "text": text})
                    elif (not self.details or self.is_detail(link)) and ctx.follow_links(task):
                        requests.append({"url": link, "role": "page", "depth": task["depth"] + 1})
                elif ctx.follow_links(task):
                    requests.append({"url": link, "role": "page", "depth": task["depth"] + 1})
        targets = []
        if detail and (kind in TEXT_KINDS | BINARY_KINDS):
            inputs = [{"request": request, "role": "detail"}]
            for item in attachments:
                inputs.append(
                    {
                        "request": {
                            "url": item["url"],
                            "role": "attachment",
                            "depth": task["depth"],
                        },
                        "role": "attachment",
                        "text": item["text"],
                    }
                )
            targets.append(
                {
                    "identity": {
                        "namespace": "url",
                        "external_key": public_url(request["url"]),
                        "public_url": public_url(request["url"]),
                        "recheck_locator": {"url": public_url(request["url"]), "role": "page"},
                    },
                    "inputs": inputs,
                }
            )
        return {"requests": requests, "targets": targets}

    def deliverable_text(self, ctx, data, kind, acquisition):
        if kind == "html":
            tree, text, _ = parse_html(data, acquisition["charset"])
            return self.html_markdown(tree, acquisition), None, ["markdown", "quality"]
        if kind == "txt":
            text, _ = decode(data, acquisition["charset"])
            return text_to_markdown(text), None, ["markdown", "quality"]
        if kind in BINARY_KINDS:
            result = ctx.extract(data, kind)
            return result["markdown"], result, result["parsers"]
        raise WlhError("unsupported_document_type")

    def html_markdown(self, tree, acquisition):
        rule = self.config["documents"]["content_xpath"]
        nodes = tree.xpath(rule) if rule else [tree.body if tree.body is not None else tree]
        nodes = [n for n in nodes if isinstance(n, etree._Element)]
        if not nodes:
            raise WlhError("content_region_missing")
        for node in nodes:
            for bad in node.xpath(".//script|.//style|.//noscript|.//template"):
                bad.drop_tree()
        fragment = "".join(etree.tostring(n, encoding="unicode", method="html") for n in nodes)
        base = acquisition["actual_url"] or acquisition["request_url"]
        return html_to_markdown("<html><body>" + fragment + "</body></html>", base)

    def detail_values(self, data, kind, acquisition, url):
        """Declared field rules on the archived detail input; subclasses map source tables."""
        fields = self.config["documents"]["fields"]
        values = {}
        if kind == "html":
            tree, _, encoding = parse_html(data, acquisition["charset"])
            for name, rule in fields.items():
                values[name] = field_value(tree, tree.text_content(), rule)
            h1 = next(
                (
                    re.sub(r"\s+", " ", node.text_content()).strip()
                    for node in tree.xpath("//h1")
                    if re.sub(r"\s+", " ", node.text_content()).strip()
                ),
                None,
            )
            page_title = next(
                (
                    re.sub(r"\s+", " ", node.text_content()).strip()
                    for node in tree.xpath("//title")
                    if re.sub(r"\s+", " ", node.text_content()).strip()
                ),
                None,
            )
            title = values.get("title") or h1 or page_title
            return values, title, encoding
        for name, rule in fields.items():
            if rule.get("value") is not None:
                values[name] = rule["value"]
        return values, values.get("title") or file_name(url), acquisition["charset"]

    def build(self, ctx, target, inputs):
        detail, attachments = inputs[0], inputs[1:]
        acquisition = detail["acquisition"]
        data = ctx.read(acquisition)
        kind = detect_real_format(data, "html" if acquisition["mime_type"] == "text/html" else "")
        if kind in TEXT_KINDS and blocked(data, self.config):
            # Challenge pages, login shells and listings never become document originals.
            raise WlhError("target_content_blocked")
        meta = dict(self.config["metadata"])
        url = acquisition["actual_url"] or acquisition["request_url"]
        values, title, encoding = self.detail_values(data, kind, acquisition, url)
        candidate_inputs = [
            {
                "acquisition_id": acquisition["id"],
                "archive_ref": acquisition["archive_ref"],
                "role": "primary",
                "locator": {"url": public_url(url), "kind": "whole_response"},
                "charset": encoding,
                "base_url": public_url(url),
            }
        ]
        deliverables, parsers = [], {"markdown"}
        quality_fail = []

        def add(
            data, kind, item_acquisition, input_index, role, title=None, order=None, source_url=None
        ):
            markdown, result, used = self.deliverable_text(ctx, data, kind, item_acquisition)
            parsers.update(used)
            report = (result or {}).get("quality") or ctx.text_quality(markdown)
            if report["recommended_route"] not in self.config["parser"]["quality_accept_routes"]:
                quality_fail.append(report["recommended_route"])
            for member in (result or {}).get("members", []):
                ref = ctx.store(member["data"])
                candidate_inputs.append(
                    {
                        "acquisition_id": item_acquisition["id"],
                        "archive_ref": ref,
                        "role": "derived",
                        "locator": {
                            "url": public_url(source_url or url),
                            "zip_path": member["path"],
                        },
                    }
                )
            deliverables.append(
                {
                    "role": role,
                    "title": title,
                    "volume_order": order,
                    "markdown": markdown,
                    "input": input_index,
                    "original": {
                        "ref": item_acquisition["archive_ref"],
                        "kind": kind,
                        "byte_size": item_acquisition["byte_size"],
                        "source_url": source_url or public_url(url),
                        "original_name": file_name(source_url or url),
                    }
                    if kind not in TEXT_KINDS
                    else None,
                    "quality": report,
                    "extraction": (result or {}).get("info") or {"kind": kind},
                    "parsers": used,
                }
            )

        files = []
        for item in attachments:
            att = item["acquisition"]
            body = ctx.read(att)
            att_kind = detect_real_format(body)
            if att_kind in TEXT_KINDS and blocked(body, self.config):
                raise WlhError("target_content_blocked")
            if att_kind not in BINARY_KINDS | TEXT_KINDS:
                raise WlhError("unsupported_attachment_type")
            source = public_url(att["actual_url"] or att["request_url"])
            text = item.get("text") or ""
            label = text if self.config["documents"]["attachment_title"] == "link_text" else ""
            match = (
                (self.volume.search(text) or self.volume.search(file_name(source)))
                if self.volume
                else None
            )
            files.append(
                {
                    "acquisition": att,
                    "body": body,
                    "kind": att_kind,
                    "url": source,
                    "title": label or file_name(source),
                    "order": int(match.group(1)) if match else None,
                }
            )
        numbered = [f for f in files if f["order"] is not None]
        volumes = numbered if len(numbered) >= 2 else []
        if volumes:
            orders = [f["order"] for f in volumes]
            if len(set(orders)) != len(orders):
                for f in volumes:
                    f["order"] = None
        others = [f for f in files if f not in volumes]
        body_mode = self.config["documents"]["detail_body"]
        use_body = not volumes and (body_mode == "always" or (body_mode == "auto" and not files))
        if kind not in TEXT_KINDS | BINARY_KINDS:
            raise WlhError("unsupported_document_type")
        if use_body:
            add(data, kind, acquisition, 0, "primary", None)
        else:
            candidate_inputs[0]["role"] = "context"
            # This page is provenance, rather than a delivered original. Only selected
            # metadata and the labeled file relationships participate in the document.
            # Preserve the complete response/locator in lineage, but ignore DOM order.
            candidate_inputs[0]["slice_hash"] = digest(
                {
                    "title": title,
                    "fields": values,
                    "files": sorted(
                        ({k: f[k] for k in ("url", "title", "order")} for f in files),
                        key=canonical,
                    ),
                }
            )
        for f in volumes:
            candidate_inputs.append(self.input_for(f, "volume", f["order"]))
            add(
                f["body"],
                f["kind"],
                f["acquisition"],
                len(candidate_inputs) - 1,
                "volume",
                None,
                f["order"],
                f["url"],
            )
        for position, f in enumerate(others):
            role = (
                "primary"
                if not use_body and not volumes and position == 0
                else "independent_attachment"
            )
            candidate_inputs.append(self.input_for(f, role, None))
            add(
                f["body"],
                f["kind"],
                f["acquisition"],
                len(candidate_inputs) - 1,
                role,
                None if role == "primary" else f["title"],
                None,
                f["url"],
            )
        if quality_fail:
            raise WlhError("quality_check_failed:" + sorted(set(quality_fail))[0])
        if not deliverables:
            raise WlhError("document_deliverables_required")
        if kind in TEXT_KINDS and use_body:
            candidate_inputs[0]["slice_hash"] = digest(
                {"body": deliverables[0]["markdown"], "fields": values, "title": title}
            )
        meta["title"] = title
        for name in ("document_number", "issuer", "status"):
            if values.get(name):
                meta[name] = values[name]
        dates = {
            k: values[k] for k in ("published_at", "effective_at", "expired_at") if values.get(k)
        }
        return [
            {
                **target["identity"],
                "metadata": meta,
                "dates": dates,
                "deliverables": deliverables,
                "inputs": candidate_inputs,
                "parsers": sorted(parsers),
            }
        ]

    @staticmethod
    def input_for(file, role, order):
        acquisition = file["acquisition"]
        return {
            "acquisition_id": acquisition["id"],
            "archive_ref": acquisition["archive_ref"],
            "role": role,
            "locator": {"url": file["url"], "kind": "whole_response"},
            "charset": acquisition["charset"],
            "volume_order": order,
        }
