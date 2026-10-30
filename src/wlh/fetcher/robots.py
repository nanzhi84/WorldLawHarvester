"""Robots policy is fetched through the same bounded transport, before content."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from protego import Protego  # type: ignore[import-untyped]

from wlh.core import WlhError


def _fetcher_cfg(config: Mapping[str, Any]) -> Mapping[str, Any]:
    fetcher = config.get("fetcher")
    if isinstance(fetcher, Mapping):
        return cast(Mapping[str, Any], fetcher)
    fetch = config.get("fetch")
    if isinstance(fetch, Mapping):
        return cast(Mapping[str, Any], fetch)
    return config


class Robots:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config: Mapping[str, Any] = config
        self.cache: dict[str, Any] = {}
        self.lock: asyncio.Lock = asyncio.Lock()

    async def check(
        self, url: str, fetch: Callable[[str], Awaitable[tuple[int, bytes]]]
    ) -> None:
        f_cfg = _fetcher_cfg(self.config)
        robots_enabled = f_cfg.get("robots", self.config.get("robots", True))
        if not robots_enabled:
            return
        user_agent = str(
            f_cfg.get("user_agent")
            or self.config.get("user_agent")
            or "WLH/0.1 (+authorized archival crawler)"
        )
        p = urlsplit(url)
        origin = urlunsplit((p.scheme, p.netloc, "", "", ""))
        async with self.lock:
            if origin not in self.cache:
                status, body = await fetch(origin + "/robots.txt")
                if status in (404, 410):
                    policy = Protego.parse("")
                elif status == 200:
                    policy = Protego.parse(body.decode("utf-8", errors="replace"))
                else:
                    raise WlhError("robots_unavailable")
                self.cache[origin] = policy
        if not self.cache[origin].can_fetch(url, user_agent):
            raise WlhError("robots_denied")
