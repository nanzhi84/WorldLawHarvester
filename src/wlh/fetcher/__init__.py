"""Stage 2 Fetcher: controlled HTTP/browser acquisition, robots, DNS boundary, and ephemeral proxy."""

from __future__ import annotations

from wlh.fetcher.browser import Browser
from wlh.fetcher.browser_runtime import identity as browser_identity
from wlh.fetcher.challenges import CHALLENGES, resolve_challenge_redirect
from wlh.fetcher.network import FAKE_IP_NETWORK, Resolver
from wlh.fetcher.proxy import EphemeralProxy
from wlh.fetcher.robots import Robots
from wlh.fetcher.session import ArchivedResponse, CasSessionProxy
from wlh.fetcher.transport import (
    BLOCKERS,
    FATAL_ERRORS,
    REDIRECTS,
    RETRYABLE,
    Budget,
    Recorder,
    Transport,
    blocked,
    host_of,
    media,
    request_key,
)

__all__ = [
    "ArchivedResponse",
    "BLOCKERS",
    "Browser",
    "Budget",
    "CHALLENGES",
    "CasSessionProxy",
    "EphemeralProxy",
    "FAKE_IP_NETWORK",
    "FATAL_ERRORS",
    "REDIRECTS",
    "RETRYABLE",
    "Recorder",
    "Resolver",
    "Robots",
    "Transport",
    "blocked",
    "browser_identity",
    "host_of",
    "media",
    "request_key",
    "resolve_challenge_redirect",
]
