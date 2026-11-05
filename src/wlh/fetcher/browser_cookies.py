"""Copy source-scoped cookies between controlled HTTP and a browser context."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from yarl import URL

logger = logging.getLogger(__name__)


async def seed_cookies(
    context: Any, session: aiohttp.ClientSession | None, url: str, hosts: set[str]
) -> None:
    if session is None:
        return
    try:
        target_host = urlsplit(url).hostname or ""
        cookies: list[dict[str, str]] = []
        for cookie in session.cookie_jar:
            raw_domain = cookie.get("domain") or ""
            clean_domain = raw_domain.lstrip(".")
            if clean_domain and clean_domain in hosts:
                domain = raw_domain
            elif not clean_domain and target_host in hosts:
                domain = target_host
            else:
                continue
            cookies.append(
                {
                    "name": cookie.key,
                    "value": cookie.value,
                    "domain": domain,
                    "path": cookie.get("path") or "/",
                }
            )
        if cookies:
            await context.add_cookies(cookies)
    except Exception as exc:
        logger.debug("Failed to inject cookies into browser context for %s: %s", url, exc)


async def capture_cookies(
    context: Any, session: aiohttp.ClientSession | None, url: str, hosts: set[str]
) -> None:
    if session is None:
        return
    try:
        for cookie in await context.cookies():
            domain = cookie.get("domain", "").lstrip(".")
            if domain in hosts:
                session.cookie_jar.update_cookies(
                    {cookie["name"]: cookie["value"]}, URL(f"https://{domain}")
                )
    except Exception as exc:
        logger.debug("Failed to collect cookies from browser context for %s: %s", url, exc)
