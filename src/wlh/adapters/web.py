"""Generic web Adapter: scoped link discovery, detail pages, volumes and attachments.

Without ``documents.detail_url_patterns`` every fetched in-scope response is a single-file
document (the historical crawler behaviour). With patterns, matching pages become
documents whose attachment links are required inputs (volumes or independent
attachments); other pages are navigation only.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import unquote, urlsplit, urlunsplit

from wlh.adapters import Base
from wlh.adapters.web_document import (
    BINARY_KINDS,
    GENERIC_ATTACHMENT_LABELS,
    TEXT_KINDS,
    WebDocumentBuilder,
)
from wlh.adapters.web_fields import (
    DATE_FIELDS,
    clean_url,
    decode,
    field_value,
    file_name,
    parse_date,
    parse_html,
)
from wlh.adapters.web_pagination import aspnet_next_page
from wlh.core import WlhError, public_url
from wlh.engine.scope import guard
from wlh.extraction.html_images import inline_glyph_text
from wlh.extraction.markdown import clean_html_tree
from wlh.extraction.office import detect_real_format


class Adapter(WebDocumentBuilder, Base):
    CONTRACT: str = "wlh.adapter.web.v7"
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
        if re.search(r"/service/(?:JP|MP)/(?:\d+/)?index\.html$", urlsplit(url).path):
            return False
        return not self.details or any(p.search(url) for p in self.details)

    def plan_recheck(
        self, resources: Sequence[Mapping[str, Any]], state: dict[str, Any]
    ) -> dict[str, Any]:
        result = super().plan_recheck(resources, state)
        for target in result["targets"]:
            locator = target["identity"]["recheck_locator"]
            context_url = locator.get("title_context_url")
            if self.config["documents"].get("binary_title_from_link") and context_url:
                target["inputs"][0]["request"]["meta"] = {"title_context_url": context_url}
                target["inputs"].append(self.title_context(context_url, locator["url"], 0))
        return result

    @staticmethod
    def title_context(url: str, document_url: str, depth: int) -> dict[str, Any]:
        return {
            "request": {"url": url, "role": "title_context", "depth": depth},
            "role": "title_context", "document_url": public_url(document_url),
        }

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
        if parts.path.lower().endswith("/hot/addhotlaw.ashx") and parts.query:
            m_pcode = re.search(
                r"(?:^|&)pcode=([A-Za-z0-9]+)", parts.query, flags=re.IGNORECASE
            )
            if m_pcode:
                link = urlunsplit(
                    (
                        parts.scheme,
                        parts.netloc,
                        "/LawClass/LawAll.aspx",
                        f"pcode={m_pcode.group(1)}",
                        "",
                    )
                )
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
        inline_images: list[str] = []
        truncated: str | None = None
        wlh_trunc_code: str | None = None
        if kind == "html":
            tree, _, _ = parse_html(data, acquisition["charset"])
            qualification = self.config["documents"].get("detail_xpath")
            if detail and qualification:
                detail = bool(tree.xpath(qualification))
            if detail and re.search(
                r"/service/(?:JP|MP)/(?:\d+/)?index\.html$", urlsplit(url).path
            ):
                detail = False
            if ctx.runtime.mode == "collect":
                wlh_trunc = tree.xpath("//*[@data-wlh-truncated]/@data-wlh-truncated")
                if wlh_trunc:
                    wlh_trunc_code = str(wlh_trunc[0])
                    truncated = truncated or wlh_trunc_code
            seen: set[str] = set()
            detail_paths: set[str] = set()
            unexpanded_paths: set[str] = set()
            if not detail and tree.xpath("//iframe[@id='iframe-data' and @src]"):
                anchors = tree.xpath("//iframe[@id='iframe-data' and @src]")
            elif not detail and tree.xpath("//table[@id='jud']//a[@href]"):
                anchors = tree.xpath(
                    "//table[@id='jud']//a[@href]"
                    + (" | //a[@id='hlNext' and @href]" if self.max_pages > 1 else "")
                )
            else:
                anchors = tree.xpath(
                    "//a[@href] | //a[@docurl] | //a[@data-url] | //a[@data-href] | //a[@onclick] | //frame[@src] | //iframe[@src]"
                )
            for anchor in anchors:
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
                            'ancestor::*[@id="vblienquan" or contains(@class, "vblienquan") or contains(@class, "Side_Links")]'
                        ):
                            continue
                        text = re.sub(r"\s+", " ", anchor.text_content()).strip()
                        if text.lower() in GENERIC_ATTACHMENT_LABELS:
                            alt_nodes = anchor.xpath(
                                "preceding-sibling::*[contains(@class, 'file_name')][1]"
                                " | ../preceding-sibling::*[contains(@class, 'file_name')][1]"
                            )
                            alt_text = (
                                re.sub(r"\s+", " ", alt_nodes[0].text_content()).strip()
                                if alt_nodes
                                else re.sub(
                                    r"\s*[(（][^()（）]*(?:檔案下載|另開新視窗)[^()（）]*[)）]\s*$",
                                    "",
                                    str(anchor.get("title") or "").strip(),
                                ).strip()
                            )
                            if alt_text and alt_text.lower() not in GENERIC_ATTACHMENT_LABELS:
                                text = alt_text
                        attachments.append({"url": link, "text": text})
                    elif not self.details or self.is_detail(link):
                        if ctx.follow_links(task):
                            follow: dict[str, Any] = {
                                "url": link, "role": "page", "depth": task["depth"] + 1,
                            }
                            if self.config["documents"].get("binary_title_from_link"):
                                follow["meta"] = {"title_context_url": url}
                            requests.append(follow)
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
            elif self.config["documents"].get("require_complete_text"):
                img_tree, _, _ = parse_html(data, acquisition["charset"])
                content_xpath = self.config["documents"].get("content_xpath")
                region_nodes = (
                    img_tree.xpath(content_xpath)
                    if content_xpath
                    else [img_tree.body if img_tree.body is not None else img_tree]
                )
                seen_imgs: set[str] = set()
                for r_node in region_nodes:
                    clean_html_tree(r_node)
                    for img in r_node.xpath("descendant-or-self::img"):
                        if inline_glyph_text(img) is not None:
                            continue
                        img_link = self.resolve_link(url, img.get("src"))
                        if img_link and img_link not in seen_imgs and self.in_scope(img_link):
                            seen_imgs.add(img_link)
                            inline_images.append(img_link)
        elif kind == "json":
            json_text, _ = decode(data, acquisition["charset"])
            try:
                parsed_json = json.loads(json_text)
            except ValueError:
                parsed_json = None
            if isinstance(parsed_json, dict):
                json_obj = cast(dict[str, Any], parsed_json)
                seen_json_links: set[str] = set()
                if self.details and detail:
                    ds_raw = json_obj.get("dataset")
                    ds_obj: dict[str, Any] = (
                        cast(dict[str, Any], ds_raw) if isinstance(ds_raw, dict) else json_obj
                    )
                    fs_raw = ds_obj.get("filesetLists")
                    if isinstance(fs_raw, list):
                        for fs_item in cast(list[Any], fs_raw):
                            if not isinstance(fs_item, dict):
                                continue
                            fs_map = cast(dict[str, Any], fs_item)
                            fs_id = fs_map.get("fileSetId")
                            if fs_id is None:
                                continue
                            att_link = self.resolve_link(url, f"/api/FilesetLists/{fs_id}/file")
                            if (
                                att_link
                                and att_link not in seen_json_links
                                and self.in_scope(att_link)
                                and (
                                    self.attachment.search(unquote(att_link))
                                    or self.attachment.search(file_name(att_link))
                                )
                            ):
                                seen_json_links.add(att_link)
                                desc = str(
                                    fs_map.get("resourceDescription")
                                    or ds_obj.get("title")
                                    or ""
                                ).strip()
                                attachments.append({"url": att_link, "text": desc})
                elif not detail:
                    pl_raw = json_obj.get("pagedList")
                    if isinstance(pl_raw, dict):
                        pl_obj = cast(dict[str, Any], pl_raw)
                        items_raw = pl_obj.get("items")
                        has_bearer = bool(os.environ.get("JUDICIAL_OPENDATA_BEARER_TOKEN"))
                        if isinstance(items_raw, list):
                            for it_raw in cast(list[Any], items_raw):
                                if not isinstance(it_raw, dict):
                                    continue
                                it_map = cast(dict[str, Any], it_raw)
                                if it_map.get("categoryDataset") == "B" and not has_bearer:
                                    continue
                                did = it_map.get("datasetId")
                                if did is None:
                                    continue
                                det_link = self.resolve_link(url, f"/api/Datasets/{did}")
                                if (
                                    det_link
                                    and det_link not in seen_json_links
                                    and self.in_scope(det_link)
                                    and ctx.follow_links(task)
                                ):
                                    seen_json_links.add(det_link)
                                    requests.append(
                                        {"url": det_link, "role": "page", "depth": task["depth"] + 1}
                                    )
        targets: list[dict[str, Any]] = []
        if detail and not wlh_trunc_code and (kind in TEXT_KINDS | BINARY_KINDS):
            inputs = [{"request": request, "role": "detail"}]
            locator: dict[str, Any] = {"url": public_url(request["url"]), "role": "page"}
            context_url = cast(Mapping[str, Any], request.get("meta") or {}).get("title_context_url")
            if kind in BINARY_KINDS and self.config["documents"].get("binary_title_from_link"):
                if context_url:
                    locator["title_context_url"] = public_url(context_url)
                    inputs.append(self.title_context(context_url, request["url"], task["depth"]))
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
            for img_url in inline_images:
                inputs.append(
                    {
                        "request": {
                            "url": img_url,
                            "role": "attachment",
                            "depth": task["depth"],
                        },
                        "role": "inline_image",
                        "image_url": public_url(img_url),
                    }
                )
            targets.append(
                {
                    "identity": {
                        "namespace": "url",
                        "external_key": public_url(request["url"]),
                        "public_url": public_url(request["url"]),
                        "recheck_locator": locator,
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
