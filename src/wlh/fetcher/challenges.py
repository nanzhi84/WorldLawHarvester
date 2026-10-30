"""Declarative HTTP challenge / anti-bot redirect hooks (Stage 2 Fetcher)."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlsplit

from yarl import URL

from wlh.config import CHALLENGE_HOOKS


def oracle_adf_loopback(
    url: str,
    data: bytes,
    session: Any = None,
    seen_cookies: set[tuple[str, str, str]] | None = None,
) -> str | None:
    """Resolve Oracle ADF ``AdfLoopbackUtils.runLoopback`` challenge by appending ``_afrLoop``."""
    if len(data) > 16384 or "_afrLoop=" in url:
        return None
    text = data.decode("utf-8", errors="replace")
    if "AdfLoopbackUtils.runLoopback" not in text:
        return None
    match = re.search(r"['\"]_afrLoop['\"]\s*,\s*['\"](\d+)['\"]", text)
    if not match:
        return None
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}_afrLoop={match.group(1)}&_afrWindowMode=0"


def js_cookie_reload(
    url: str,
    data: bytes,
    session: Any = None,
    seen_cookies: set[tuple[str, str, str]] | None = None,
) -> str | None:
    """Resolve lightweight ``document.cookie = ...; location.reload()`` challenge."""
    if len(data) > 2048:
        return None
    text = data.decode("utf-8", errors="replace")
    if "location.reload" not in text:
        return None
    match = re.search(r'document\.cookie\s*=\s*["\']([^=;"\']+)=([^;"\']+)', text)
    if not match or session is None:
        return None
    name, value = match.group(1).strip(), match.group(2).strip()
    key = (urlsplit(url).hostname or "", name, value)
    if seen_cookies is not None:
        if key in seen_cookies:
            return None
        seen_cookies.add(key)
    session.cookie_jar.update_cookies({name: value}, URL(url))
    return url


CHALLENGES: dict[str, Callable[..., str | None]] = {
    "oracle_adf_loopback": oracle_adf_loopback,
    "js_cookie_reload": js_cookie_reload,
}

assert set(CHALLENGES) == set(CHALLENGE_HOOKS)


def resolve_challenge_redirect(
    url: str,
    data: bytes,
    enabled: Sequence[str] = ("oracle_adf_loopback", "js_cookie_reload"),
    *,
    session: Any = None,
    seen_cookies: set[tuple[str, str, str]] | None = None,
) -> str | None:
    for name in enabled:
        hook = CHALLENGES.get(name)
        if hook is None:
            continue
        target = hook(url, data, session=session, seen_cookies=seen_cookies)
        if target is not None:
            return target
    return None


__all__ = [
    "CHALLENGES",
    "js_cookie_reload",
    "oracle_adf_loopback",
    "resolve_challenge_redirect",
]
