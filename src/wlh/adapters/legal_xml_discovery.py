"""Discover full legal XML catalogs and packages within a declared source scope."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import urljoin, urlsplit, urlunsplit

import lxml.etree as etree
from lxml import html as lxml_html  # type: ignore[import-untyped]

from wlh.adapters.legal_xml_values import select
from wlh.core import WlhError, public_url
from wlh.engine.scope import guard

etree: Any
lxml_html: Any

def _normalize_candidate_urls(
    base: str,
    raw_urls: Sequence[str],
    config: Mapping[str, Any],
    patterns: Sequence[re.Pattern[str]] = (),
) -> list[str]:
    base_parts = urlsplit(base)
    urls: list[str] = []
    seen: set[str] = set()
    for href in raw_urls:
        if not href or href.lower().startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
            continue
        parts = urlsplit(urljoin(base, href))
        if parts.scheme not in ("http", "https"):
            continue
        scheme = (
            "https"
            if parts.scheme == "http"
            and base_parts.scheme == "https"
            and parts.netloc == base_parts.netloc
            else parts.scheme
        )
        try:
            candidate = public_url(
                urlunsplit((scheme, parts.netloc, parts.path or "/", parts.query, ""))
            )
            if patterns and not any(p.search(candidate) for p in patterns):
                continue
            guard(config, candidate)
        except (WlhError, ValueError):
            continue
        if candidate not in seen:
            seen.add(candidate)
            urls.append(candidate)
    return urls


def discover(
    data: bytes, acquisition: Mapping[str, Any], config: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str | None]:
    truncated: str | None = None

    def filter_requests(
        base: str, raw_urls: list[str], pagination_urls: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        nonlocal truncated
        patterns = [
            re.compile(p)
            for p in config.get("documents", {}).get("detail_url_patterns", [])
        ]
        urls = _normalize_candidate_urls(base, raw_urls, config, patterns)
        next_pages = _normalize_candidate_urls(base, pagination_urls, config)
        seen = set(urls)
        limit: int = int(config["budget"]["documents"])
        if len(urls) > limit or (len(urls) == limit and bool(next_pages)):
            truncated = "document_budget_exceeded"
        selected = list(urls[:limit])
        if len(selected) < limit:
            selected.extend([u for u in next_pages if u not in seen])
        return [{"url": u, "role": "page"} for u in selected]

    def following() -> list[dict[str, Any]]:
        if data.startswith(b"PK\x03\x04"):
            return []
        base = str(acquisition.get("actual_url") or acquisition.get("request_url") or "")
        if data.lstrip().startswith(b"{"):
            try:
                obj = json.loads(data)
            except ValueError:
                return []
            if not isinstance(obj, dict):
                return []
            typed_obj = cast(dict[str, Any], obj)
            children_root = typed_obj.get("children")
            if not isinstance(children_root, list):
                titles_list = typed_obj.get("titles")
                if isinstance(titles_list, list):
                    raw_urls: list[str] = []
                    for raw_item in cast(list[Any], titles_list):
                        if not isinstance(raw_item, dict):
                            continue
                        typed_item = cast(dict[str, Any], raw_item)
                        if typed_item.get("reserved"):
                            continue
                        num: Any = typed_item.get("number")
                        raw_date: Any = (
                            typed_item.get("up_to_date_as_of")
                            or typed_item.get("latest_issue_date")
                            or ""
                        )
                        date = str(raw_date).strip()
                        if num is not None and date:
                            raw_urls.append(urljoin(base, f"structure/{date}/title-{num}.json"))
                    urls = _normalize_candidate_urls(base, raw_urls, config)
                    return [{"url": u, "role": "page"} for u in urls]
                return []
            m = re.search(r"/structure/(\d{4}-\d{2}-\d{2})/title-(\d+)\.json", base)
            date = m.group(1) if m else str(typed_obj.get("up_to_date_as_of") or "").strip()
            title_num = m.group(2) if m else str(typed_obj.get("identifier") or "").strip()
            if not date or not title_num:
                return []
            raw_urls: list[str] = []

            def walk_parts(nodes: list[Any]) -> None:
                for node in nodes:
                    if not isinstance(node, dict):
                        continue
                    typed_node = cast(dict[str, Any], node)
                    if typed_node.get("type") == "part" and not typed_node.get("reserved"):
                        ident = typed_node.get("identifier")
                        if ident:
                            raw_urls.append(
                                urljoin(base, f"../../full/{date}/title-{title_num}.xml?part={ident}")
                            )
                    children = typed_node.get("children")
                    if isinstance(children, list):
                        walk_parts(cast(list[Any], children))

            walk_parts(cast(list[Any], children_root))
            return filter_requests(base, raw_urls)
        if re.search(br"<!DOCTYPE\s+html\b|<html(?:\s|>)", data[:2048], re.I):
            try:
                doc = lxml_html.document_fromstring(data.decode("utf-8", errors="replace"))
            except (etree.ParserError, ValueError):
                return []
            raw_urls = [
                str(href).strip()
                for href in doc.xpath("//a[@href]/@href")
                if str(href).strip()
            ]
            return filter_requests(base, raw_urls)
        if b"<!ENTITY" in data.upper():
            return []
        try:
            root: Any = etree.fromstring(
                data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
            )
        except etree.XMLSyntaxError:
            return []
        tag = etree.QName(root).localname
        if tag == "feed":
            next_links = [
                str(h).strip()
                for h in root.xpath(".//*[local-name()='link' and @rel='next']/@href")
                if str(h).strip()
            ]
            next_set = set(next_links)
            links = [
                str(h).strip()
                for h in root.xpath(".//*[local-name()='link']/@href")
                if str(h).strip().endswith("/data.xml") and str(h).strip() not in next_set
            ]
            if not links:
                for h in root.xpath(".//*[local-name()='entry']/*[local-name()='link']/@href"):
                    href = str(h).strip()
                    if href and href not in next_set:
                        links.append(
                            href if href.endswith(".xml") else href.rstrip("/") + "/data.xml"
                        )
            return filter_requests(base, links, next_links)
        if tag in {"items", "rss"}:
            links = [
                str(t).strip()
                for t in root.xpath(".//*[local-name()='link']/text()")
                if str(t).strip()
            ]
            for desc in root.xpath(".//*[local-name()='description']/text()"):
                for href in re.findall(r"""href=["']([^"']+)["']""", html.unescape(str(desc))):
                    if href.strip():
                        links.append(href.strip())
            return filter_requests(base, links)
        if tag == "ActsRegsList":
            links = [
                str(t).strip()
                for t in root.xpath(".//*[local-name()='LinkToXML']/text()")
                if str(t).strip()
            ]
            return filter_requests(base, links)
        if (
            tag == "DataRoot"
            and not (select(root, "LawTitle") or root.xpath(".//LawBody"))
            and root.xpath(".//*[local-name()='LawId']/text()")
        ):
            links = [
                urljoin(base, f"../lawdata/{str(law_id).strip()}")
                for law_id in root.xpath(".//*[local-name()='LawId']/text()")
                if str(law_id).strip()
            ]
            return filter_requests(base, links)
        return []

    requests = following()
    return requests, truncated
