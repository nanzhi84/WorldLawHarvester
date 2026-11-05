"""Controlled external acquisition: scope, robots, budgets, redirects, retries, CAS, fencing."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping, Sequence
from email.message import Message
from pathlib import Path
from typing import Any, cast
from urllib.parse import urljoin, urlsplit

import aiohttp

from wlh.cleaners.text_cleaner import BLOCKERS, blocked
from wlh.core import WlhError, digest, guard, public_url, request_key
from wlh.fetcher.acquisition import Budget, Recorder, budget_cfg
from wlh.fetcher.challenges import resolve_challenge_redirect
from wlh.fetcher.network import EXTRA_INTERMEDIATE_CAS_PEM, Resolver, client_ssl_context
from wlh.fetcher.proxy import EphemeralProxy
from wlh.fetcher.robots import Robots
from wlh.storage.cas import Objects
from wlh.storage.cas_io import CasIO

REDIRECTS = {301, 302, 303, 307, 308}
RETRYABLE = {"network_failed", "http_5xx", "timeout"}
FATAL_ERRORS = {
    "interrupted",
    "stale_attempt",
    "inactive_attempt",
    "run_not_started",
    "request_budget_exceeded",
    "crawl_time_budget_exceeded",
    "deadline_exceeded",
}


def fetcher_cfg(config: Mapping[str, Any]) -> Mapping[str, Any]:
    fetcher = config.get("fetcher")
    if isinstance(fetcher, Mapping):
        return cast(Mapping[str, Any], fetcher)
    fetch = config.get("fetch")
    if isinstance(fetch, Mapping):
        return cast(Mapping[str, Any], fetch)
    return {}


_fetcher_cfg = fetcher_cfg


def _concurrency(config: Mapping[str, Any]) -> int:
    sched = config.get("scheduler")
    if isinstance(sched, Mapping):
        sched_map = cast(Mapping[str, Any], sched)
        if "concurrency" in sched_map:
            return int(sched_map["concurrency"])
    return int(config.get("concurrency", 2))


def media(
    headers: Mapping[str, str],
) -> tuple[str, str | tuple[str | None, str | None, str] | None]:
    message = Message()
    message["content-type"] = headers.get("Content-Type", "application/octet-stream")
    return message.get_content_type(), message.get_param("charset")


class Transport:
    def __init__(self, config: Mapping[str, Any], budget: Budget, recorder: Recorder) -> None:
        self.config: Mapping[str, Any] = config
        self.budget: Budget = budget
        self.recorder: Recorder = recorder
        self.robots: Robots = Robots(config)
        self.session: aiohttp.ClientSession | None = None
        self.slots: asyncio.Semaphore = asyncio.Semaphore(_concurrency(config))
        self.pace: asyncio.Lock = asyncio.Lock()
        self.last: float = 0.0
        self.objects: Objects = Objects()
        self.cas_io = CasIO()
        self.loopback_cookies: set[tuple[str, str, str]] = set()
        self.ephemeral_proxy: EphemeralProxy | None = None
        self.proxy_url: str | None = None
        self.start_lock: asyncio.Lock = asyncio.Lock()

    def challenge_hooks(self) -> Sequence[str]:
        f_cfg = _fetcher_cfg(self.config)
        ch = f_cfg.get("challenges")
        if isinstance(ch, Sequence):
            return [str(x) for x in cast(Sequence[Any], ch)]
        return ("oracle_adf_loopback", "js_cookie_reload")

    _challenge_hooks = challenge_hooks

    def _detect_html_loopback(self, url: str, data: bytes) -> str | None:
        if len(data) > 4096:
            return None
        return resolve_challenge_redirect(
            url,
            data,
            enabled=self.challenge_hooks(),
            session=self.session,
            seen_cookies=self.loopback_cookies,
        )

    async def start(self) -> None:
        async with self.start_lock:
            if self.session is None:
                for env_file in (Path.cwd() / ".env", Path(__file__).resolve().parents[3] / ".env"):
                    if env_file.is_file():
                        for line in env_file.read_text(encoding="utf-8").splitlines():
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                k, _, v = line.partition("=")
                                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
                f_cfg = _fetcher_cfg(self.config)
                proxy_cfg = f_cfg.get("proxy")
                if isinstance(proxy_cfg, Mapping) and self.ephemeral_proxy is None:
                    proxy_mgr = EphemeralProxy(cast(Mapping[str, Any], proxy_cfg))
                    self.proxy_url = await proxy_mgr.start()
                    self.ephemeral_proxy = proxy_mgr
                ssl_ctx = client_ssl_context()
                page_timeout_ms = int(f_cfg.get("page_timeout_ms", 30000))
                total_timeout = page_timeout_ms / 1000
                user_agent = str(
                    f_cfg.get("user_agent")
                    or self.config.get("user_agent")
                    or "WLH/0.1 (+authorized archival crawler)"
                )
                trusted_hosts = (
                    self.ephemeral_proxy.trusted_hosts if self.ephemeral_proxy is not None else ()
                )
                session_headers: dict[str, str] = {"User-Agent": user_agent}
                if os.environ.get("WLH_ALLOW_LOOPBACK") == "1" and self.config.get("id"):
                    session_headers["X-WLH-Source-Id"] = str(self.config["id"])
                self.session = aiohttp.ClientSession(
                    headers=session_headers,
                    cookie_jar=aiohttp.CookieJar(unsafe=True),
                    trust_env=(os.environ.get("WLH_ALLOW_LOOPBACK") != "1" and not self.proxy_url),
                    connector=aiohttp.TCPConnector(
                        limit=_concurrency(self.config),
                        resolver=Resolver(trusted_proxy_hosts=trusted_hosts),
                        force_close=False,
                        ssl=ssl_ctx,
                    ),
                    timeout=aiohttp.ClientTimeout(
                        total=total_timeout,
                        sock_connect=min(10.0, total_timeout),
                        sock_read=min(60.0, total_timeout),
                    ),
                    auto_decompress=True,
                )

    async def close(self) -> None:
        await self.cas_io.close()
        if self.session is not None:
            await self.session.close()
            self.session = None
        if self.ephemeral_proxy is not None:
            await self.ephemeral_proxy.close()
            self.ephemeral_proxy = None
            self.proxy_url = None

    async def __aenter__(self) -> Transport:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def paced(self) -> None:
        f_cfg = _fetcher_cfg(self.config)
        delay = float(f_cfg.get("delay", self.config.get("delay", 1.0)))
        async with self.pace:
            wait = self.last + delay - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self.last = time.monotonic()

    async def fetch_policy(self, url: str) -> tuple[int, bytes]:
        key = request_key({"url": url, "role": "policy"})
        f_cfg = _fetcher_cfg(self.config)
        retries = int(f_cfg.get("retries", 2))
        backoff = float(f_cfg.get("retry_backoff_seconds", 0.5))
        robots_redirects = f_cfg.get(
            "robots_redirect_urls", self.config.get("robots_redirect_urls", ())
        )
        acquisition: dict[str, Any] = {}
        for attempt in range(retries + 1):
            acquisition = await self.exchange(url, "GET", None, key, "policy", {}, None, None)
            if acquisition["state"] == "archived":
                break
            if attempt == retries:
                break
            await asyncio.sleep(backoff * 2**attempt)
        location = acquisition.get("location")
        if acquisition["state"] == "archived" and acquisition["outcome"] == "redirect" and location:
            try:
                target = public_url(urljoin(url, location))
            except WlhError:
                raise WlhError("robots_unavailable") from None
            if target in robots_redirects:
                try:
                    guard(self.config, target, policy=True)
                except WlhError:
                    raise WlhError("robots_unavailable") from None
                acquisition = await self.exchange(
                    target, "GET", None, key, "policy", {}, acquisition["id"], "redirect"
                )
        if acquisition["state"] != "archived":
            if acquisition.get("error") in FATAL_ERRORS or acquisition["state"] == "cancelled":
                raise WlhError(acquisition.get("error") or "interrupted")
            raise WlhError("robots_unavailable")
        return acquisition["http_status"], self.objects.get(acquisition["archive_ref"])

    async def exchange(
        self,
        url: str,
        method: str,
        form: Mapping[str, Any] | bytes | None,
        key: str,
        role: str,
        meta: Mapping[str, Any],
        parent: str | None,
        relation: str | None,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Exactly one real HTTP request and one Acquisition fact."""
        if self.session is None:
            raise WlhError("transport_not_started")
        budget = budget_cfg(self.config)
        f_cfg = _fetcher_cfg(self.config)
        limit = int(budget["response_bytes"])
        identity: str | None = None
        try:
            async with self.slots:
                await self.paced()
                self.budget.request()
                identity = self.recorder.start(
                    key, "http", role, method, url, meta, parent, relation
                )
                req_headers = dict(headers) if headers else {}
                headers_env = f_cfg.get("headers_from_env")
                if isinstance(headers_env, Mapping):
                    for hdr_name, env_var in cast(Mapping[str, Any], headers_env).items():
                        val = os.environ.get(str(env_var))
                        if val:
                            req_headers.setdefault(hdr_name, val)
                req_kwargs: dict[str, Any] = {
                    "data": form,
                    "headers": req_headers or None,
                    "allow_redirects": False,
                }
                if self.proxy_url:
                    parsed_target = urlsplit(url)
                    target_host = parsed_target.hostname
                    target_port = parsed_target.port or (
                        443 if parsed_target.scheme == "https" else 80
                    )
                    if target_host:
                        await Resolver().resolve(target_host, target_port)
                    req_kwargs["proxy"] = self.proxy_url
                async with self.session.request(method, url, **req_kwargs) as response:
                    body = bytearray()
                    try:
                        async for chunk in response.content.iter_chunked(65536):
                            self.budget.active()
                            body.extend(chunk)
                            if len(body) > limit:
                                return self.recorder.finish(
                                    identity,
                                    "failed",
                                    status=response.status,
                                    outcome="blocked",
                                    error="response_byte_budget_exceeded",
                                    actual_url=str(response.url),
                                )
                    except aiohttp.ClientPayloadError:
                        ext_quirks = cast(
                            dict[str, Any],
                            cast(dict[str, Any], self.config.get("processor") or {}).get(
                                "extractor"
                            )
                            or {},
                        ).get("quirks") or ("zip_salvage",)
                        if (
                            200 <= response.status < 300
                            and bytes(body[:4]) == b"PK\x03\x04"
                            and "zip_salvage" in ext_quirks
                        ):
                            from wlh.processor.extractors.quirks.zip_salvage import (
                                salvage_zip_prefix,
                            )

                            body = bytearray(salvage_zip_prefix(bytes(body)))
                        else:
                            raise
                    status, resp_headers, actual = (
                        response.status,
                        response.headers,
                        str(response.url),
                    )
                data = bytes(body)
                self.budget.active()
                ref = await self.cas_io.run(self.objects.put, data)
                self.budget.active()
        except WlhError as exc:
            if identity is None or exc.code in {
                "stale_attempt",
                "inactive_attempt",
                "run_not_started",
            }:
                raise
            state = "cancelled" if exc.code == "interrupted" else "failed"
            return self.recorder.finish(identity, state, outcome="blocked", error=exc.code)
        except asyncio.CancelledError:
            if identity is not None:
                self.recorder.finish(
                    identity, "cancelled", outcome="cancelled", error="interrupted"
                )
            raise
        except (aiohttp.ClientError, TimeoutError, OSError):
            if identity is None:
                raise
            return self.recorder.finish(
                identity, "failed", outcome="network_failed", error="network_failed"
            )
        mime_type, charset = media(resp_headers)
        outcome: str = (
            "ok" if 200 <= status < 300 else "redirect" if status in REDIRECTS else "http_error"
        )
        row: dict[str, Any] = self.recorder.finish(
            identity,
            "archived",
            status=status,
            outcome=outcome,
            error=None if outcome != "http_error" else "http_" + str(status),
            actual_url=actual,
            mime_type=mime_type,
            charset=charset,
            representation="http_entity_body",
            byte_size=len(data),
            body_hash=ref,
            archive_ref=ref,
        )
        row["location"] = resp_headers.get("Location")
        row["headers"] = list(resp_headers.items())
        return row

    async def acquire(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Redirect hops and bounded retries are separate facts linked to their parent."""
        await self.start()
        url = guard(self.config, request["url"])
        await self.robots.check(url, self.fetch_policy)
        method = request.get("method", "GET")
        form = request.get("form")
        if method == "POST" and not request.get("idempotent"):
            raise WlhError("post_replay_semantics_required")
        key = request_key(request)
        meta: dict[str, Any] = {"role": request.get("role", "page")}
        if form:
            meta.update(form_digest=digest(form), form_fields=sorted(form))
        f_cfg = _fetcher_cfg(self.config)
        retries = int(f_cfg.get("retries", 2))
        backoff = float(f_cfg.get("retry_backoff_seconds", 0.5))
        row: dict[str, Any] = {}
        parent, relation = None, None
        for attempt in range(retries + 1):
            current, hop_method, hop_form = url, method, form
            for _ in range(11):
                row = await self.exchange(
                    current, hop_method, hop_form, key, "content", meta, parent, relation
                )
                if (
                    row["state"] == "archived"
                    and row["outcome"] == "ok"
                    and int(row.get("byte_size") or 0) <= 4096
                    and str(row.get("mime_type") or "").startswith("text/html")
                ):
                    data = self.objects.get(row["archive_ref"])
                    loopback = self._detect_html_loopback(current, data)
                    if loopback:
                        try:
                            current = guard(self.config, loopback)
                            await self.robots.check(current, self.fetch_policy)
                        except WlhError as exc:
                            if exc.code in FATAL_ERRORS:
                                raise
                            row = dict(row, outcome="blocked", error=exc.code)
                            break
                        parent, relation = row["id"], "redirect"
                        continue
                if row["state"] != "archived" or row["outcome"] != "redirect":
                    break
                target = urljoin(current, row.get("location") or "")
                if not row.get("location"):
                    break
                try:
                    current = guard(self.config, target)
                    await self.robots.check(current, self.fetch_policy)
                except WlhError as exc:
                    if exc.code in FATAL_ERRORS:
                        raise
                    row = dict(row, outcome="blocked", error=exc.code)
                    break
                parent, relation = row["id"], "redirect"
                if row["http_status"] in {301, 302, 303}:
                    hop_method, hop_form = "GET", None
            else:
                row = dict(row, outcome="blocked", error="redirect_limit_exceeded")
            retry: bool = (row["state"] == "failed" and row["error"] == "network_failed") or (
                row["state"] == "archived" and int(row["http_status"] or 0) >= 500
            )
            if not retry or attempt == retries:
                return row
            parent, relation = row["id"], "retry"
            await asyncio.sleep(backoff * 2**attempt)
        return row


def host_of(url: str) -> str | None:
    return urlsplit(url).hostname


__all__ = [
    "BLOCKERS",
    "EXTRA_INTERMEDIATE_CAS_PEM",
    "FATAL_ERRORS",
    "REDIRECTS",
    "RETRYABLE",
    "Budget",
    "Recorder",
    "Transport",
    "blocked",
    "host_of",
    "media",
    "request_key",
]
