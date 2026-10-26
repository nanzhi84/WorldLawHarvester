"""Generic web Adapter: scoped link discovery, detail pages, volumes and attachments.

Without ``documents.detail_url_patterns`` every fetched in-scope response is a single-file
document (the historical crawler behaviour). With patterns, matching pages become
documents whose attachment links are required inputs (volumes or independent
attachments); other pages are navigation only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import unquote, urlsplit, urlunsplit

import lxml.etree as etree

from wlh.adapters import Base
from wlh.adapters.web_fields import (
    DATE_FIELDS,
    clean_url,
    decode,
    enrich_bare_title,
    extract_detail_fields,
    field_value,
    file_name,
    parse_date,
    parse_html,
)
from wlh.adapters.web_pagination import aspnet_next_page
from wlh.core import WlhError, canonical, digest, public_url
from wlh.engine.scope import guard
from wlh.engine.transport import blocked
from wlh.extraction.markdown import clean_html_tree, html_to_markdown, text_to_markdown
from wlh.extraction.office import detect_real_format

etree: Any
TEXT_KINDS: set[str] = {"html", "txt"}
BINARY_KINDS: set[str] = {"pdf", "doc", "docx", "rtf", "xls", "xlsx", "zip"}
GENERIC_ATTACHMENT_LABELS: set[str] = {
    "tải file đính kèm",
    "tai file dinh kem",
    "tải về",
    "tai ve",
    "tải văn bản",
    "tai van ban",
    "tải xuống",
    "tai xuong",
    "download",
    "attachment",
    "attachments",
    "file đính kèm",
    "file dinh kem",
    "tệp đính kèm",
    "tep dinh kem",
    "xem chi tiết",
    "xem toàn văn",
    "đính kèm",
    "dinh kem",
}


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
        self.volume: re.Pattern[str] | None = (
            re.compile(rules["volume_pattern"]) if rules["volume_pattern"] else None
        )

    def is_detail(self, url: str) -> bool:
        return not self.details or any(p.search(url) for p in self.details)

    def in_scope(self, url: str) -> bool:
        try:
            guard(self.config, url)
            return True
        except WlhError:
            return False

    def resolve_link(self, base: str, raw_href: str | None) -> str | None:
        link = clean_url(base, raw_href)
        if not link:
            return None
        base_parts = urlsplit(base)
        parts = urlsplit(link)
        if (
            base_parts.query
            and not parts.query
            and parts.netloc == base_parts.netloc
            and not self.is_detail(link)
        ):
            with_query = urlunsplit(
                (parts.scheme, parts.netloc, parts.path, base_parts.query, "")
            )
            if self.is_detail(with_query):
                return with_query
        return link

    def parse(
        self, ctx: Any, task: Mapping[str, Any], acquisition: Mapping[str, Any]
    ) -> dict[str, Any]:
        request = task["payload"]
        if acquisition["outcome"] != "ok":
            return {"requests": [], "targets": []}
        role = request.get("role", "page")
        if role != "page":
            return {"requests": [], "targets": []}
        data = ctx.read(acquisition)
        kind = detect_real_format(data, "html" if acquisition["mime_type"] == "text/html" else "")
        url: str = str(acquisition["actual_url"] or acquisition["request_url"] or "")
        detail = self.is_detail(url)
        requests: list[dict[str, Any]] = []
        attachments: list[dict[str, Any]] = []
        truncated: str | None = None
        wlh_trunc_code: str | None = None
        if kind == "html":
            tree, _, _ = parse_html(data, acquisition["charset"])
            if ctx.runtime.mode == "collect":
                wlh_trunc = tree.xpath("//*[@data-wlh-truncated]/@data-wlh-truncated")
                if wlh_trunc:
                    wlh_trunc_code = str(wlh_trunc[0])
                    truncated = truncated or wlh_trunc_code
            seen: set[str] = set()
            detail_paths: set[str] = set()
            unexpanded_paths: set[str] = set()
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
                link: str | None = self.resolve_link(url, raw_href)
                if not link or link in seen or not self.in_scope(link):
                    continue
                seen.add(link)
                if self.is_detail(link):
                    detail_paths.add(urlsplit(link).path)
                if self.attachment.search(unquote(link)) or self.attachment.search(file_name(link)):
                    if self.details and detail:
                        if anchor.xpath(
                            'ancestor::*[@id="vblienquan" or contains(@class, "vblienquan")]'
                        ):
                            continue
                        text = re.sub(r"\s+", " ", anchor.text_content()).strip()
                        attachments.append({"url": link, "text": text})
                    elif not self.details or self.is_detail(link):
                        if ctx.follow_links(task):
                            requests.append(
                                {"url": link, "role": "page", "depth": task["depth"] + 1}
                            )
                        elif (
                            ctx.runtime.mode == "collect"
                            and not detail
                            and urlsplit(link).path != urlsplit(url).path
                            and link not in self.config["entry_urls"]
                        ):
                            unexpanded_paths.add(urlsplit(link).path)
                elif ctx.follow_links(task):
                    if (
                        not self.details
                        or self.is_detail(link)
                        or task["depth"] + 1 < self.config["fetch"]["max_depth"]
                    ):
                        requests.append({"url": link, "role": "page", "depth": task["depth"] + 1})
                    elif (
                        not detail
                        and urlsplit(link).path != urlsplit(url).path
                        and link not in self.config["entry_urls"]
                    ):
                        unexpanded_paths.add(urlsplit(link).path)
                elif (
                    ctx.runtime.mode == "collect"
                    and not detail
                    and urlsplit(link).path != urlsplit(url).path
                    and link not in self.config["entry_urls"]
                ):
                    unexpanded_paths.add(urlsplit(link).path)
            if (not self.details or not detail_paths) and (unexpanded_paths - detail_paths):
                truncated = truncated or "max_depth_exceeded"
            if not detail:
                page = cast(Mapping[str, Any], request.get("meta") or {}).get("page", 1)
                if ctx.follow_links(task):
                    next_req, asp_trunc = aspnet_next_page(
                        tree, url, page, self.max_pages, self.config
                    )
                    if next_req is not None:
                        requests.insert(0, next_req)
                    truncated = truncated or asp_trunc
                elif ctx.runtime.mode == "collect":
                    next_req, asp_trunc = aspnet_next_page(
                        tree, url, page, self.max_pages, self.config
                    )
                    if next_req is not None or asp_trunc:
                        truncated = truncated or asp_trunc or "max_depth_exceeded"
        targets: list[dict[str, Any]] = []
        if detail and not wlh_trunc_code and (kind in TEXT_KINDS | BINARY_KINDS):
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
        if self.details:
            requests.sort(
                key=lambda r: 0 if r.get("method") == "POST" or self.is_detail(r["url"]) else 1
            )
        return {
            "requests": requests,
            "targets": targets,
            **({"truncated": truncated} if truncated else {}),
        }

    def deliverable_text(
        self, ctx: Any, data: bytes, kind: str, acquisition: Mapping[str, Any]
    ) -> tuple[str, dict[str, Any] | None, list[str]]:
        if kind == "html":
            tree, _, _ = parse_html(data, acquisition["charset"])
            return self.html_markdown(tree, acquisition), None, ["markdown", "quality"]
        if kind == "txt":
            text, _ = decode(data, acquisition["charset"])
            return text_to_markdown(text), None, ["markdown", "quality"]
        if kind in BINARY_KINDS:
            result = ctx.extract(data, kind)
            return result["markdown"], result, result["parsers"]
        raise WlhError("unsupported_document_type")

    def html_markdown(self, tree: Any, acquisition: Mapping[str, Any]) -> str:
        wlh_trunc = tree.xpath("//*[@data-wlh-truncated]/@data-wlh-truncated")
        if wlh_trunc:
            raise WlhError(str(wlh_trunc[0]))
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
            clean_html_tree(node)
        fragment = "".join(etree.tostring(n, encoding="unicode", method="html") for n in nodes)
        if blocked(fragment.encode("utf-8"), self.config):
            raise WlhError("target_content_blocked")
        base = acquisition["actual_url"] or acquisition["request_url"]
        return html_to_markdown("<html><body>" + fragment + "</body></html>", base)

    def detail_values(
        self,
        data: bytes,
        kind: str,
        acquisition: Mapping[str, Any],
        url: str,
        text: str | None = None,
    ) -> tuple[dict[str, Any], str | None, str | None]:
        """Declared field rules on the archived detail input; subclasses map source tables."""
        return extract_detail_fields(
            self.config["documents"]["fields"], data, kind, acquisition, url, text
        )

    def build(
        self, ctx: Any, target: Mapping[str, Any], inputs: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        detail, attachments = inputs[0], inputs[1:]
        acquisition = detail["acquisition"]
        data = ctx.read(acquisition)
        kind = detect_real_format(data, "html" if acquisition["mime_type"] == "text/html" else "")
        meta = dict(self.config["metadata"])
        url: str = str(acquisition["actual_url"] or acquisition["request_url"] or "")
        values, title, encoding = self.detail_values(data, kind, acquisition, url)
        if (
            kind in TEXT_KINDS
            and not (kind == "html" and self.config["documents"]["content_xpath"])
            and blocked(data, self.config)
        ):
            raise WlhError("target_content_blocked")
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
            data: bytes,
            kind: str,
            item_acquisition: Mapping[str, Any],
            input_index: int,
            role: str,
            title: str | None = None,
            order: int | None = None,
            source_url: str | None = None,
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
            source = public_url(att["actual_url"] or att["request_url"])
            if not (
                self.attachment.search(unquote(source))
                or self.attachment.search(file_name(source))
            ):
                continue
            body = ctx.read(att)
            att_kind = detect_real_format(body)
            if att_kind in TEXT_KINDS and blocked(body, self.config):
                raise WlhError("target_content_blocked")
            if att_kind not in BINARY_KINDS | TEXT_KINDS:
                raise WlhError("unsupported_attachment_type")
            text = (item.get("text") or "").strip()
            use_link_text = (
                self.config["documents"]["attachment_title"] == "link_text"
                and text.lower() not in GENERIC_ATTACHMENT_LABELS
                and not text.lower().startswith("download this version")
            )
            base_name = file_name(source)
            clean_base_name = re.sub(
                r"(?:^|[-_\s]+)[0-9a-f]{32}(?=\.[a-z0-9]{2,5}$|$)",
                "",
                base_name,
                flags=re.IGNORECASE,
            )
            clean_base_name = re.sub(
                r"\.(?:pdf|docx?|rtf|xlsx?|zip)$",
                "",
                clean_base_name,
                flags=re.IGNORECASE,
            ).strip(" -_.")
            if use_link_text and text:
                att_title = text
            elif title and clean_base_name and clean_base_name.lower() not in {"file", "download"}:
                att_title = f"{title} - {clean_base_name}"
            elif title:
                att_title = title
            else:
                att_title = clean_base_name or base_name
            match: re.Match[str] | None = (
                (self.volume.search(text) or self.volume.search(base_name))
                if self.volume
                else None
            )
            files.append(
                {
                    "acquisition": att,
                    "body": body,
                    "kind": att_kind,
                    "url": source,
                    "title": att_title,
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
            if kind in BINARY_KINDS and deliverables:
                values, title, encoding = self.detail_values(
                    data, kind, acquisition, url, deliverables[0]["markdown"]
                )
        else:
            candidate_inputs[0]["role"] = "context"
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
        if not use_body and deliverables:
            att_vals, _, _ = self.detail_values(
                data, kind, acquisition, url, deliverables[0]["markdown"]
            )
            for k, v in att_vals.items():
                if not values.get(k) and v:
                    values[k] = v
            title = enrich_bare_title(
                title,
                values.get("document_number"),
                tree=None,
                body_text=deliverables[0]["markdown"],
            )
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


__all__ = [
    "Adapter",
    "BINARY_KINDS",
    "DATE_FIELDS",
    "TEXT_KINDS",
    "clean_url",
    "decode",
    "field_value",
    "file_name",
    "parse_date",
    "parse_html",
]
