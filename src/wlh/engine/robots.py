"""Robots policy is fetched through the same bounded transport, before content."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from protego import Protego  # type: ignore[import-untyped]

from wlh.core import WlhError


class Robots:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config: Mapping[str, Any] = config
        self.cache: dict[str, Any] = {}
        self.lock: asyncio.Lock = asyncio.Lock()

    async def check(
        self, url: str, fetch: Callable[[str], Awaitable[tuple[int, bytes]]]
    ) -> None:
        if not self.config["robots"]:
            return
        p = urlsplit(url)
        origin = urlunsplit((p.scheme, p.netloc, "", "", ""))
        async with self.lock:
            if origin not in self.cache:
                status, body = await fetch(origin + "/robots.txt")
                if status in (404, 410):
                    policy = Protego.parse("")
                elif status == 200:
                    # Longest matching Allow/Disallow wins, independent of line order.
                    policy = Protego.parse(body.decode("utf-8", errors="replace"))
                else:
                    raise WlhError("robots_unavailable")
                self.cache[origin] = policy
        if not self.cache[origin].can_fetch(url, self.config["user_agent"]):
            raise WlhError("robots_denied")
