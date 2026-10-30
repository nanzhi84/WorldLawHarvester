"""ASP.NET and list-page pagination requests for HTMLParser."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from wlh.core import guard, public_url
from wlh.processor.parsers.html.fields import clean_url

NEXT_TEXT_MARKERS: tuple[str, ...] = (
    "next",
    "next page",
    "next >",
    "next >>",
    "下一页",
    "下一頁",
    "下页",
    "下頁",
    "次へ",
    "次のページ",
    "tiếp",
    "trang sau",
    "trang tiếp",
    "›",
    "»",
)


def aspnet_next_page(
    tree: Any,
    url: str,
    page: int,
    max_pages: int,
    config: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    forms = tree.xpath('//form[@method="post" or @method="POST"]')
    if not forms:
        return None, None
    form = forms[0]
    events: list[tuple[str, ...]] = []
    for href in form.xpath(".//a/@href"):
        match: re.Match[str] | None = re.search(
            r"__doPostBack\('([^']+)','(Page\$[^']+)'\)", href
        )
        if match:
            events.append(match.groups())
    following: tuple[str, ...] | None = next(
        (e for e in events if e[1] == f"Page${page + 1}"), None
    )
    if following is None:
        return None, None
    if page >= max_pages:
        return None, "page_limit_exceeded"
    fields: dict[str, str] = {}
    for node in form.xpath(".//input[@name] | .//select[@name] | .//textarea[@name]"):
        if node.tag == "select":
            options = node.xpath("./option[@selected]") or node.xpath("./option")[:1]
            fields[node.get("name")] = options[0].get("value", "") if options else ""
        elif node.get("type", "").lower() not in {
            "submit",
            "button",
            "image",
            "checkbox",
            "radio",
        }:
            fields[node.get("name")] = node.get("value", "")
    action = guard(config, urljoin(url, form.get("action", "")))
    target, argument = following
    return {
        "url": action,
        "method": "POST",
        "idempotent": True,
        "form": dict(fields, __EVENTTARGET=target, __EVENTARGUMENT=argument),
        "role": "page",
        "depth": 0,
        "meta": {"page": page + 1},
    }, None


def pagination_requests(
    adapter: Any, tree: Any, base_url: str, depth: int
) -> list[dict[str, Any]]:
    opts_raw = (
        adapter.config.get("processor", {}).get("parser", {}).get("options")
        or adapter.config.get("adapter_options")
    )
    opts: Mapping[str, Any] = cast(Mapping[str, Any], opts_raw) if isinstance(opts_raw, Mapping) else {}
    max_pages = int(opts.get("max_pages", 1))
    if max_pages <= 1:
        return []
    requests: list[dict[str, Any]] = []
    seen: set[tuple[str, str, tuple[tuple[str, str], ...]]] = set()
    for req in _pagination_candidates(adapter, tree, base_url, depth, max_pages):
        form_raw = req.get("form")
        form: Mapping[str, Any] = cast(Mapping[str, Any], form_raw) if isinstance(form_raw, Mapping) else {}
        sig: tuple[str, str, tuple[tuple[str, str], ...]] = (
            str(req.get("method", "GET")),
            str(req["url"]),
            tuple(sorted((k, str(v)) for k, v in form.items())),
        )
        if sig not in seen:
            seen.add(sig)
            requests.append(req)
    return requests


def _pagination_candidates(
    adapter: Any, tree: Any, base_url: str, depth: int, max_pages: int
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for node in tree.xpath(
        "//a[@rel='next' or contains(translate(@class,'NEXT','next'),'next') or @href]"
        " | //input[@type='image' or @type='submit']"
    ):
        text = re.sub(
            r"\s+",
            " ",
            (
                node.text_content()
                or node.get("title")
                or node.get("alt")
                or node.get("value")
                or ""
            ),
        ).strip()
        href = (node.get("href") or "").strip()
        onclick = (node.get("onclick") or "").strip()
        name = (node.get("name") or "").strip()
        is_next = (
            (node.get("rel") or "").lower() == "next"
            or text.lower() in NEXT_TEXT_MARKERS
            or any(m in text.lower() for m in ("下一页", "下一頁", "次へ", "next page"))
            or any(k in name.lower() for k in ("next", "imgbtnnext"))
        )
        if not is_next:
            continue
        postback = _aspnet_postback_request(adapter, tree, node, base_url, depth, max_pages)
        if postback is not None:
            results.append(postback)
            continue
        resolved = clean_url(base_url, href or onclick)
        if resolved and resolved != base_url and adapter.in_scope(resolved):
            results.append({"url": resolved, "role": "page", "depth": depth})

    next_query = _next_query_url(base_url, max_pages)
    if next_query and next_query != base_url and adapter.in_scope(next_query):
        results.append({"url": next_query, "role": "page", "depth": depth})
    return results


def _aspnet_postback_request(
    adapter: Any, tree: Any, node: Any, base_url: str, depth: int, max_pages: int
) -> dict[str, Any] | None:
    forms = node.xpath("ancestor::form[1]") or tree.xpath("//form[1]")
    if not forms:
        return None
    form_node = forms[0]
    method = (form_node.get("method") or "GET").strip().upper()
    if method != "POST":
        return None
    action = clean_url(base_url, form_node.get("action") or base_url) or base_url
    if not adapter.in_scope(action):
        return None
    fields: dict[str, str] = {}
    for inp in form_node.xpath(".//input[@name]"):
        itype = (inp.get("type") or "text").strip().lower()
        iname = (inp.get("name") or "").strip()
        if not iname or itype in {"submit", "button", "image", "reset", "file"}:
            continue
        if itype in {"checkbox", "radio"} and inp.get("checked") is None:
            continue
        fields[iname] = inp.get("value") or ""
    for sel in form_node.xpath(".//select[@name]"):
        sname = (sel.get("name") or "").strip()
        if not sname:
            continue
        opts = sel.xpath(".//option[@selected]") or sel.xpath(".//option[1]")
        if opts:
            fields[sname] = opts[0].get("value") or (opts[0].text_content() or "").strip()

    current_page = 1
    for key, val in fields.items():
        if any(p in key.lower() for p in ("page", "curpage", "pageindex", "pagenum")) and val.isdigit():
            current_page = max(current_page, int(val))
    if current_page >= max_pages:
        return None

    href = (node.get("href") or "") + " " + (node.get("onclick") or "")
    pb = re.search(
        r"__doPostBack\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]\s*\)", href
    )
    if pb:
        fields["__EVENTTARGET"] = pb.group(1)
        fields["__EVENTARGUMENT"] = pb.group(2)
    elif (node.get("type") or "").lower() == "image" and node.get("name"):
        btn = str(node.get("name"))
        fields[f"{btn}.x"] = "8"
        fields[f"{btn}.y"] = "8"
    elif node.get("name"):
        fields[str(node.get("name"))] = node.get("value") or "1"
    else:
        return None

    return {
        "url": action,
        "method": "POST",
        "form": fields,
        "idempotent": True,
        "role": "page",
        "depth": depth,
    }


def _next_query_url(base_url: str, max_pages: int) -> str | None:
    parts: Any = urlsplit(base_url)
    if not parts.query:
        return None
    query = parse_qs(parts.query, keep_blank_values=True)
    for key in ("page", "Page", "p", "pageIndex", "pageNo", "pageNum", "curPage"):
        if key in query and query[key] and query[key][0].isdigit():
            cur = int(query[key][0])
            if 1 <= cur < max_pages:
                query[key] = [str(cur + 1)]
                return public_url(
                    urlunsplit(
                        (parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), "")
                    )
                )
    for key in ("offset", "start"):
        if key in query and query[key] and query[key][0].isdigit():
            cur = int(query[key][0])
            step = int(query.get("limit", query.get("rows", ["20"]))[0])
            if step > 0 and (cur // step) + 1 < max_pages:
                query[key] = [str(cur + step)]
                return public_url(
                    urlunsplit(
                        (parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), "")
                    )
                )
    return None


__all__ = [
    "NEXT_TEXT_MARKERS",
    "aspnet_next_page",
    "pagination_requests",
]
