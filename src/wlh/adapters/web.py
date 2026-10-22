"""Generic web Adapter: scoped link discovery, detail pages, volumes and attachments.

Without ``documents.detail_url_patterns`` every fetched in-scope response is a single-file
document (the historical crawler behaviour). With patterns, matching pages become
documents whose attachment links are required inputs (volumes or independent
attachments); other pages are navigation only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, cast
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

import lxml.etree as etree
from lxml import html  # type: ignore[import-untyped]

from wlh.adapters import Base
from wlh.adapters.web_pagination import aspnet_next_page
from wlh.core import WlhError, canonical, digest, public_url
from wlh.engine.scope import guard
from wlh.engine.transport import blocked
from wlh.extraction.markdown import html_to_markdown, text_to_markdown
from wlh.extraction.office import detect_real_format

etree: Any
html: Any
TEXT_KINDS: set[str] = {"html", "txt"}
BINARY_KINDS: set[str] = {"pdf", "doc", "docx", "rtf", "xls", "xlsx", "zip"}


def decode(data: bytes, charset: str | None) -> tuple[str, str]:
    detected: str | None = charset
    if not detected:
        match: re.Match[bytes] | None = re.search(br'<meta\b[^>]*charset=["\']?\s*([a-zA-Z0-9._-]+)', data[:4096], re.I)
        if match:
            detected = match.group(1).decode("ascii", errors="ignore")
    for encoding in (detected, "utf-8"):
        if encoding:
            try:
                return data.decode(encoding), encoding
            except (LookupError, UnicodeDecodeError):
                continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def parse_html(data: bytes, charset: str | None) -> tuple[Any, str, str]:
    text, encoding = decode(data, charset)
    try:
        tree: Any = html.document_fromstring(text)
    except (etree.ParserError, ValueError):
        raise WlhError("html_unparseable") from None
    return tree, text, encoding


def clean_url(base: str, href: str | None) -> str | None:
    if not href:
        return None
    cleaned: str = href.strip()
    if cleaned.lower().startswith("javascript:") or re.match(r"^[A-Za-z_]\w*\s*\(", cleaned):
        js_match: re.Match[str] | None = re.search(
            r"""['"]((?:https?://|/)[^'"]+)['"]""", cleaned
        )
        if not js_match:
            return None
        cleaned = str(js_match.group(1) or "").strip()
    if not cleaned or cleaned.lower().startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
        return None
    try:
        base_parts: Any = urlsplit(base)
        parts: Any = urlsplit(urljoin(base, cleaned))
        if parts.scheme not in ("http", "https"):
            return None
        scheme: str = (
            "https"
            if parts.scheme == "http"
            and base_parts.scheme == "https"
            and parts.netloc == base_parts.netloc
            else parts.scheme
        )
        query: str = (
            "iframe=true"
            if base_parts.query == "iframe=true"
            and parts.netloc == base_parts.netloc
            and parts.path.endswith("/index.do")
            and "/item/" in parts.path
            and not parts.query
            else parts.query
        )
        return public_url(
            urlunsplit((scheme, parts.netloc, parts.path or "/", query, ""))
        )
    except (WlhError, ValueError):
        return None


def file_name(url: str) -> str:
    parts: Any = urlsplit(url)
    query_name = parse_qs(parts.query).get("file_name", [""])[0]
    name = PurePosixPath(query_name or unquote(parts.path)).name
    return name or "file"


DATE_FIELDS: set[str] = {"published_at", "effective_at", "expired_at"}


def parse_date(value: str, fmt: str | None = None) -> str | None:
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        return None
    if fmt:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            raise WlhError("document_date_unparseable") from None
    zh_match: re.Match[str] | None = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if zh_match:
        try:
            y, m, d = (int(part) for part in zh_match.groups())
            return datetime(y, m, d).date().isoformat()
        except ValueError:
            raise WlhError("document_date_unparseable") from None
    for candidate in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text[:10], candidate).date().isoformat()
        except ValueError:
            continue
    for candidate in ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, candidate).date().isoformat()
        except ValueError:
            continue
    raise WlhError("document_date_unparseable")


def field_value(tree: Any, text: str | None, rule: Mapping[str, Any], *, is_date: bool = False) -> Any:
    if rule.get("value") is not None:
        return rule["value"]
    value: Any = text
    if rule.get("xpath"):
        found: Any = tree.xpath(rule["xpath"])
        if isinstance(found, list):
            if not found:
                return None
            first: Any = cast(list[Any], found)[0]
            value = first if isinstance(first, str) else first.text_content()
        elif isinstance(found, str):
            value = found
        elif found is None or isinstance(found, bool):
            return None
        else:
            value = str(found)
    if value is None:
        return None
    # Preserve ideographic spaces in source metadata.
    value = re.sub(r"[^\S\u3000]+", " ", str(value)).strip()
    if rule.get("regex"):
        match: re.Match[str] | None = re.search(cast(str, rule["regex"]), value)
        if not match:
            return None
        value = match.groupdict().get("value") or (match.group(1) if match.groups() else match[0])
    if not value.strip():
        return None
    if rule.get("date_format") or is_date:
        return parse_date(value, rule.get("date_format"))
    return value.strip() or None


class Adapter(Base):
    CONTRACT: str = "wlh.adapter.web.v3"
    PLANNING_OPTIONS: tuple[str, ...] = ("max_pages",)

    @staticmethod
    def validate_options(options: Mapping[str, Any]) -> None:
        allowed = {"max_pages"}
        if set(options) - allowed:
            raise ValueError("unexpected_adapter_options")
        pages: Any = options.get("max_pages", 1)
        if type(pages) is not int or not 1 <= pages <= 10000:
            raise ValueError("invalid_max_pages")

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.max_pages: int = config["adapter_options"].get("max_pages", 1)
        rules = config["documents"]
        self.details: list[re.Pattern[str]] = [re.compile(p) for p in rules["detail_url_patterns"]]
        self.attachment: re.Pattern[str] = re.compile(rules["attachment_url_pattern"])
        self.volume: re.Pattern[str] | None = re.compile(rules["volume_pattern"]) if rules["volume_pattern"] else None

    def is_detail(self, url: str) -> bool:
        return not self.details or any(p.search(url) for p in self.details)

    def in_scope(self, url: str) -> bool:
        try:
            guard(self.config, url)
            return True
        except WlhError:
            return False

    def parse(self, ctx: Any, task: Mapping[str, Any], acquisition: Mapping[str, Any]) -> dict[str, Any]:
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
        requests: list[dict[str, Any]] = []
        attachments: list[dict[str, Any]] = []
        truncated: str | None = None
        if kind == "html":
            tree, _, _ = parse_html(data, acquisition["charset"])
            seen: set[str] = set()
            for anchor in tree.xpath(
                "//a[@href] | //a[@docurl] | //a[@data-url] | //a[@data-href] | //a[@onclick] | //frame[@src] | //iframe[@src]"
            ):
                raw_href: str | None = (
                    anchor.get("href")
                    or anchor.get("docurl")
                    or anchor.get("data-url")
                    or anchor.get("data-href")
                )
                if (
                    not raw_href
                    or raw_href.strip() in ("#", "")
                    or raw_href.strip().lower().startswith("javascript:void")
                ):
                    raw_href = anchor.get("onclick") or anchor.get("src")
                link: str | None = clean_url(url, raw_href)
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
                    if (
                        not self.details
                        or self.is_detail(link)
                        or task["depth"] + 1 < self.config["fetch"]["max_depth"]
                    ):
                        requests.append({"url": link, "role": "page", "depth": task["depth"] + 1})
            if not detail and ctx.follow_links(task):
                page = cast(Mapping[str, Any], request.get("meta") or {}).get("page", 1)
                next_req, truncated = aspnet_next_page(tree, url, page, self.max_pages, self.config)
                if next_req is not None:
                    requests.append(next_req)
                if not truncated:
                    wlh_trunc = tree.xpath("//*[@data-wlh-truncated]/@data-wlh-truncated")
                    if wlh_trunc:
                        truncated = str(wlh_trunc[0])
        targets: list[dict[str, Any]] = []
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
        return {
            "requests": requests,
            "targets": targets,
            **({"truncated": truncated} if truncated else {}),
        }

    def deliverable_text(self, ctx: Any, data: bytes, kind: str, acquisition: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None, list[str]]:
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

    def html_markdown(self, tree: Any, acquisition: Mapping[str, Any]) -> str:
        rule: Any = self.config["documents"]["content_xpath"]
        nodes = tree.xpath(rule) if rule else [tree.body if tree.body is not None else tree]
        nodes = [n for n in nodes if isinstance(n, etree._Element)]
        node_set = set(nodes)
        nodes = [n for n in nodes if not any(anc in node_set for anc in n.iterancestors())]
        if not nodes:
            if blocked(etree.tostring(tree, encoding="utf-8"), self.config):
                raise WlhError("target_content_blocked")
            raise WlhError("content_region_missing")
        for node in nodes:
            for bad in node.xpath(".//script|.//style|.//noscript|.//template"):
                bad.drop_tree()
        fragment = "".join(etree.tostring(n, encoding="unicode", method="html") for n in nodes)
        if blocked(fragment.encode("utf-8"), self.config):
            raise WlhError("target_content_blocked")
        base = acquisition["actual_url"] or acquisition["request_url"]
        return html_to_markdown("<html><body>" + fragment + "</body></html>", base)

    def detail_values(self, data: bytes, kind: str, acquisition: Mapping[str, Any], url: str) -> tuple[dict[str, Any], str | None, str | None]:
        """Declared field rules on the archived detail input; subclasses map source tables."""
        fields = self.config["documents"]["fields"]
        values: dict[str, Any] = {}
        if kind == "html":
            tree, _, encoding = parse_html(data, acquisition["charset"])
            for name, rule in fields.items():
                values[name] = field_value(tree, tree.text_content(), rule, is_date=(name in DATE_FIELDS))
            h1: str | None = next(
                (
                    re.sub(r"\s+", " ", node.text_content()).strip()
                    for node in tree.xpath("//h1")
                    if re.sub(r"\s+", " ", node.text_content()).strip()
                ),
                None,
            )
            page_title: str | None = next(
                (
                    re.sub(r"\s+", " ", node.text_content()).strip()
                    for node in tree.xpath("//title")
                    if re.sub(r"\s+", " ", node.text_content()).strip()
                ),
                None,
            )
            title: str | None = values.get("title") or h1 or page_title
            return values, title, encoding
        for name, rule in fields.items():
            if rule.get("value") is not None:
                values[name] = rule["value"]
            elif not rule.get("xpath") and rule.get("regex"):
                values[name] = field_value(None, url, rule, is_date=(name in DATE_FIELDS))
        return values, values.get("title") or file_name(url), acquisition["charset"]

    def build(self, ctx: Any, target: Mapping[str, Any], inputs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        detail, attachments = inputs[0], inputs[1:]
        acquisition = detail["acquisition"]
        data = ctx.read(acquisition)
        kind = detect_real_format(data, "html" if acquisition["mime_type"] == "text/html" else "")
        if (
            kind in TEXT_KINDS
            and not (kind == "html" and self.config["documents"]["content_xpath"])
            and blocked(data, self.config)
        ):
            # Challenge pages, login shells and listings never become document originals.
            raise WlhError("target_content_blocked")
        meta = dict(self.config["metadata"])
        url = acquisition["actual_url"] or acquisition["request_url"]
        values, title, encoding = self.detail_values(data, kind, acquisition, url)
        candidate_inputs: list[dict[str, Any]] = [
            {
                "acquisition_id": acquisition["id"],
                "archive_ref": acquisition["archive_ref"],
                "role": "primary",
                "locator": {"url": public_url(url), "kind": "whole_response"},
                "charset": encoding,
                "base_url": public_url(url),
            }
        ]
        deliverables: list[dict[str, Any]] = []
        parsers = {"markdown"}
        quality_fail: list[str] = []

        def add(
            data: bytes, kind: str, item_acquisition: Mapping[str, Any], input_index: int,
            role: str, title: str | None = None, order: int | None = None, source_url: str | None = None,
        ) -> None:
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

        files: list[dict[str, Any]] = []
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
            match: re.Match[str] | None = (
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
            role: str = (
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
        dates: dict[str, Any] = {
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
    def input_for(file: Mapping[str, Any], role: str, order: int | None) -> dict[str, Any]:
        acquisition = file["acquisition"]
        return {
            "acquisition_id": acquisition["id"],
            "archive_ref": acquisition["archive_ref"],
            "role": role,
            "locator": {"url": file["url"], "kind": "whole_response"},
            "charset": acquisition["charset"],
            "volume_order": order,
        }
