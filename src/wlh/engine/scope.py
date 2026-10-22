"""Shared URL boundary for SDK traversal, HTTP redirects and browser subrequests."""

from __future__ import annotations

import posixpath
from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote, urlsplit

from wlh.core import WlhError, check_address, public_url


def valid_prefix(path: Any) -> bool:
    return (
        isinstance(path, str)
        and path.startswith("/")
        and ".." not in path
        and unquote(path) == path
        and "?" not in path
        and "#" not in path
        and "\\" not in path
    )


def paths_for(config: Mapping[str, Any], host: str | None, path: str) -> bool:
    if not host or host not in config["allowed_hosts"]:
        return False
    scopes = config.get("host_path_scopes", {})
    prefixes = scopes.get(host, []) if scopes else config["allowed_path_prefixes"]
    path = posixpath.normpath(unquote(path))
    return any(path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/") for p in prefixes)


def guard(config: Mapping[str, Any], url: str, *, policy: bool = False) -> str:
    url = public_url(url)
    parsed = urlsplit(url)
    check_address(parsed.hostname)
    # A robots redirect is separate from permission to collect business content.
    # Operators approve exact destinations, including their query strings.
    if policy and url in config.get("robots_redirect_urls", []):
        return url
    allowed_policy = policy and parsed.path == "/robots.txt" and not parsed.query
    if parsed.hostname not in config["allowed_hosts"] or not (
        allowed_policy or paths_for(config, parsed.hostname, parsed.path)
    ):
        raise WlhError("request_out_of_scope")
    return url
