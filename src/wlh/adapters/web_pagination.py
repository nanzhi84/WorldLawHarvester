"""ASP.NET pagination requests for the web adapter."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urljoin

from wlh.engine.scope import guard


def aspnet_next_page(
    tree: Any, url: str, page: int, max_pages: int, config: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    forms = tree.xpath('//form[@method="post" or @method="POST"]')
    if not forms:
        return None, None
    form = forms[0]
    events: list[tuple[str, ...]] = []
    for href in form.xpath(".//a/@href"):
        match: re.Match[str] | None = re.search(r"__doPostBack\('([^']+)','(Page\$[^']+)'\)", href)
        if match:
            events.append(match.groups())
    following: tuple[str, ...] | None = next((e for e in events if e[1] == f"Page${page + 1}"), None)
    if following is None:
        return None, None
    if page >= max_pages:
        return None, "page_limit_exceeded"
    fields: dict[str, str] = {}
    for node in form.xpath(".//input[@name] | .//select[@name] | .//textarea[@name]"):
        if node.tag == "select":
            options = node.xpath("./option[@selected]") or node.xpath("./option")[:1]
            fields[node.get("name")] = options[0].get("value", "") if options else ""
        elif node.get("type", "").lower() not in {"submit", "button", "image", "checkbox", "radio"}:
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
