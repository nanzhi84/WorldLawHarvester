"""Controlled external acquisition: scope, robots, budgets, redirects, retries, CAS, fencing."""

from __future__ import annotations

import asyncio
import os
import ssl
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from typing import Any, cast
from urllib.parse import urljoin, urlsplit

import aiohttp

from wlh.cleaners.text_cleaner import BLOCKERS, blocked
from wlh.core import WlhError, digest, guard, public_url, request_key, safe_url, uid
from wlh.fetcher.challenges import resolve_challenge_redirect
from wlh.fetcher.network import Resolver
from wlh.fetcher.proxy import EphemeralProxy
from wlh.fetcher.robots import Robots
from wlh.storage.cas import Objects
from wlh.storage.db import connect, fenced, j, locked_run

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

EXTRA_INTERMEDIATE_CAS_PEM = """-----BEGIN CERTIFICATE-----
MIIETjCCAzagAwIBAgINAe5fIh38YjvUMzqFVzANBgkqhkiG9w0BAQsFADBMMSAw
HgYDVQQLExdHbG9iYWxTaWduIFJvb3QgQ0EgLSBSMzETMBEGA1UEChMKR2xvYmFs
U2lnbjETMBEGA1UEAxMKR2xvYmFsU2lnbjAeFw0xODExMjEwMDAwMDBaFw0yODEx
MjEwMDAwMDBaMFAxCzAJBgNVBAYTAkJFMRkwFwYDVQQKExBHbG9iYWxTaWduIG52
LXNhMSYwJAYDVQQDEx1HbG9iYWxTaWduIFJTQSBPViBTU0wgQ0EgMjAxODCCASIw
DQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAKdaydUMGCEAI9WXD+uu3Vxoa2uP
UGATeoHLl+6OimGUSyZ59gSnKvuk2la77qCk8HuKf1UfR5NhDW5xUTolJAgvjOH3
idaSz6+zpz8w7bXfIa7+9UQX/dhj2S/TgVprX9NHsKzyqzskeU8fxy7quRU6fBhM
abO1IFkJXinDY+YuRluqlJBJDrnw9UqhCS98NE3QvADFBlV5Bs6i0BDxSEPouVq1
lVW9MdIbPYa+oewNEtssmSStR8JvA+Z6cLVwzM0nLKWMjsIYPJLJLnNvBhBWk0Cq
o8VS++XFBdZpaFwGue5RieGKDkFNm5KQConpFmvv73W+eka440eKHRwup08CAwEA
AaOCASkwggElMA4GA1UdDwEB/wQEAwIBhjASBgNVHRMBAf8ECDAGAQH/AgEAMB0G
A1UdDgQWBBT473/yzXhnqN5vjySNiPGHAwKz6zAfBgNVHSMEGDAWgBSP8Et/qC5F
JK5NUPpjmove4t0bvDA+BggrBgEFBQcBAQQyMDAwLgYIKwYBBQUHMAGGImh0dHA6
Ly9vY3NwMi5nbG9iYWxzaWduLmNvbS9yb290cjMwNgYDVR0fBC8wLTAroCmgJ4Yl
aHR0cDovL2NybC5nbG9iYWxzaWduLmNvbS9yb290LXIzLmNybDBHBgNVHSAEQDA+
MDwGBFUdIAAwNDAyBggrBgEFBQcCARYmaHR0cHM6Ly93d3cuZ2xvYmFsc2lnbi5j
b20vcmVwb3NpdG9yeS8wDQYJKoZIhvcNAQELBQADggEBAJmQyC1fQorUC2bbmANz
EdSIhlIoU4r7rd/9c446ZwTbw1MUcBQJfMPg+NccmBqixD7b6QDjynCy8SIwIVbb
0615XoFYC20UgDX1b10d65pHBf9ZjQCxQNqQmJYaumxtf4z1s4DfjGRzNpZ5eWl0
6r/4ngGPoJVpjemEuunl1Ig423g7mNA2eymw0lIYkN5SQwCuaifIFJ6GlazhgDEw
fpolu4usBCOmmQDo8dIm7A9+O4orkjgTHY+GzYZSR+Y0fFukAj6KYXwidlNalFMz
hriSqHKvoflShx8xpfywgVcvzfTO3PYkz6fiNJBonf6q8amaEsybwMbDqKWwIX7e
SPY=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFWjCCA0KgAwIBAgISEdK7udcjGJ5AXwqdLdDfJWfRMA0GCSqGSIb3DQEBDAUA
MEYxCzAJBgNVBAYTAkJFMRkwFwYDVQQKExBHbG9iYWxTaWduIG52LXNhMRwwGgYD
VQQDExNHbG9iYWxTaWduIFJvb3QgUjQ2MB4XDTE5MDMyMDAwMDAwMFoXDTQ2MDMy
MDAwMDAwMFowRjELMAkGA1UEBhMCQkUxGTAXBgNVBAoTEEdsb2JhbFNpZ24gbnYt
c2ExHDAaBgNVBAMTE0dsb2JhbFNpZ24gUm9vdCBSNDYwggIiMA0GCSqGSIb3DQEB
AQUAA4ICDwAwggIKAoICAQCsrHQy6LNl5brtQyYdpokNRbopiLKkHWPd08EsCVeJ
OaFV6Wc0dwxu5FUdUiXSE2te4R2pt32JMl8Nnp8semNgQB+msLZ4j5lUlghYruQG
vGIFAha/r6gjA7aUD7xubMLL1aa7DOn2wQL7Id5m3RerdELv8HQvJfTqa1VbkNud
316HCkD7rRlr+/fKYIje2sGP1q7Vf9Q8g+7XFkyDRTNrJ9CG0Bwta/OrffGFqfUo
0q3v84RLHIf8E6M6cqJaESvWJ3En7YEtbWaBkoe0G1h6zD8K+kZPTXhc+CtI4wSE
y132tGqzZfxCnlEmIyDLPRT5ge1lFgBPGmSXZgjPjHvjK8Cd+RTyG/FWaha/LIWF
zXg4mutCagI0GIMXTpRW+LaCtfOW3T3zvn8gdz57GSNrLNRyc0NXfeD412lPFzYE
+cCQYDdF3uYM2HSNrpyibXRdQr4G9dlkbgIQrImwTDsHTUB+JMWKmIJ5jqSngiCN
I/onccnfxkF0oE32kRbcRoxfKWMxWXEM2G/CtjJ9++ZdU6Z+Ffy7dXxd7Pj2Fxzs
x2sZy/N78CsHpdlseVR2bJ0cpm4O6XkMqCNqo98bMDGfsVR7/mrLZqrcZdCinkqa
ByFrgY/bxFn63iLABJzjqls2k+g9vXqhnQt2sQvHnf3PmKgGwvgqo6GDoLclcqUC
4wIDAQABo0IwQDAOBgNVHQ8BAf8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zAdBgNV
HQ4EFgQUA1yrc4GHqMywptWU4jaWSf8FmSwwDQYJKoZIhvcNAQEMBQADggIBAHx4
7PYCLLtbfpIrXTncvtgdokIzTfnvpCo7RGkerNlFo048p9gkUbJUHJNOxO97k4Vg
JuoJSOD1u8fpaNK7ajFxzHmuEajwmf3lH7wvqMxX63bEIaZHU1VNaL8FpO7XJqti
2kM3S+LGteWygxk6x9PbTZ4IevPuzz5i+6zoYMzRx6Fcg0XERczzF2sUyQQCPtIk
pnnpHs6i58FZFZ8d4kuaPp92CC1r2LpXFNqD6v6MVenQTqnMdzGxRBF6XLE+0xRF
FRhiJBPSy03OXIPBNvIQtQ6IbbjhVp+J3pZmOUdkLG5NrmJ7v2B0GbhWrJKsFjLt
rWhV/pi60zTe9Mlhww6G9kuEYO4Ne7UyWHmRVSyBQ7N0H3qqJZ4d16GLuc1CLgSk
ZoNNiTW2bKg2SnkheCLQQrzRQDGQob4Ez8pn7fXwgNNgyYMqIgXQBztSvwyeqiv5
u+YfjyW6hY0XHgL+XVAEV8/+LbzvXMAaq7afJMbfc2hIkCwU9D9SGuTSyxTDYWnP
4vkYxboznxSjBF25cfe1lNj2M8FawTSLfJvdkzrnE6JwYZ+vj+vYxXX4M2bUdGc6
N3ec592kD3ZDZopD8p/7DEJ4Y9HiD2971KE9dJeFt0g5QdYg/NA6s/rob8SKunE3
vouXsXgxT7PntgMTzlSdriVZzH81Xwj3QEUxeCp6
-----END CERTIFICATE-----"""


def budget_cfg(config: Mapping[str, Any]) -> Mapping[str, Any]:
    sched = config.get("scheduler")
    if isinstance(sched, Mapping):
        sched_map = cast(Mapping[str, Any], sched)
        if isinstance(sched_map.get("budget"), Mapping):
            return cast(Mapping[str, Any], sched_map["budget"])
    budget = config.get("budget")
    if isinstance(budget, Mapping):
        return cast(Mapping[str, Any], budget)
    return {"requests": 200, "seconds": 300, "response_bytes": 15000000, "documents": 25}


_budget_cfg = budget_cfg


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


class Budget:
    """Frozen request/time budget shared by every real external request of one attempt."""

    def __init__(
        self, config: Mapping[str, Any], used: int, deadline: str | datetime | None
    ) -> None:
        self.config: Mapping[str, Any] = config
        self.used: int = used
        self.started: float = time.monotonic()
        self.deadline: datetime | None = (
            datetime.fromisoformat(deadline) if isinstance(deadline, str) else deadline
        )
        self.cancelled: bool = False
        self.parent_pid: int = os.getppid()

    def active(self) -> None:
        if os.getppid() != self.parent_pid:
            raise WlhError("interrupted")
        if self.cancelled:
            raise WlhError("interrupted")
        budget = _budget_cfg(self.config)
        if time.monotonic() - self.started > budget["seconds"]:
            raise WlhError("crawl_time_budget_exceeded")
        if self.deadline is not None and datetime.now(timezone.utc) > self.deadline:
            raise WlhError("deadline_exceeded")

    def request(self) -> None:
        self.active()
        budget = _budget_cfg(self.config)
        if self.used >= budget["requests"]:
            raise WlhError("request_budget_exceeded")
        self.used += 1


class Recorder:
    """Short fenced transactions for Acquisition state; failure never claims an archive."""

    def __init__(self, run_id: str, epoch: int, source_id: str) -> None:
        self.run_id: str = run_id
        self.epoch: int = epoch
        self.source_id: str = source_id

    def start(
        self,
        key: str,
        kind: str,
        role: str,
        method: str,
        url: str,
        meta: Mapping[str, Any],
        parent: str | None = None,
        relation: str | None = None,
    ) -> str:
        identity = uid()
        with connect() as c:
            source, run = locked_run(c, self.run_id)
            fenced(source, run, self.epoch)
            if run["mode"] == "reparse":
                raise WlhError("network_disabled_in_reparse")
            if kind == "http":
                c.execute(
                    "UPDATE wlh.run SET external_requests=external_requests+1 WHERE id=%s",
                    (self.run_id,),
                )
            c.execute(
                """INSERT INTO wlh.acquisition(id,source_id,run_id,attempt_epoch,request_key,
                acquisition_kind,role,http_method,request_url,request_meta,parent_acquisition_id,
                parent_relation,state) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'running')""",
                (
                    identity,
                    self.source_id,
                    self.run_id,
                    self.epoch,
                    key,
                    kind,
                    role,
                    method,
                    safe_url(url),
                    j(meta),
                    parent,
                    relation,
                ),
            )
        return identity

    def finish(self, identity: str, state: str, **values: Any) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "http_status": values.get("status"),
            "outcome": values.get("outcome"),
            "error": values.get("error"),
            "actual_url": safe_url(values["actual_url"]) if values.get("actual_url") else None,
            "mime_type": values.get("mime_type"),
            "charset": values.get("charset"),
            "representation": values.get("representation"),
            "byte_size": values.get("byte_size"),
            "body_hash": values.get("body_hash"),
            "archive_ref": values.get("archive_ref"),
        }
        with connect() as c:
            source, run = locked_run(c, self.run_id)
            fenced(source, run, self.epoch)
            row: dict[str, Any] | None = c.execute(
                """UPDATE wlh.acquisition SET state=%s,acquired_at=clock_timestamp(),
                http_status=%s,outcome=%s,error=%s,actual_url=%s,mime_type=%s,charset=%s,
                representation=%s,byte_size=%s,body_hash=%s,archive_ref=%s
                WHERE id=%s AND state='running' AND attempt_epoch=%s RETURNING *""",
                (state, *fields.values(), identity, self.epoch),
            ).fetchone()
        if row is None:
            raise WlhError("stale_attempt")
        return row


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
                ssl_ctx: ssl.SSLContext = (
                    ssl.create_default_context(cafile=os.environ["SSL_CERT_FILE"])
                    if os.environ.get("SSL_CERT_FILE")
                    else ssl.create_default_context()
                )
                ssl_ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
                ssl_ctx.load_verify_locations(cadata=EXTRA_INTERMEDIATE_CAS_PEM)
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
        budget = _budget_cfg(self.config)
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
                        ext_quirks = (
                            cast(
                                dict[str, Any],
                                cast(dict[str, Any], self.config.get("processor") or {}).get(
                                    "extractor"
                                )
                                or {},
                            ).get("quirks")
                            or ("zip_salvage",)
                        )
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
            ref = self.objects.put(data)
            self.objects.get(ref)
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
