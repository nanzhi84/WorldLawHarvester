"""Controlled external acquisition: scope, robots, budgets, redirects, retries, CAS, fencing.

External I/O never holds a database transaction. Each actual request is a running
Acquisition committed in a short fenced transaction; bytes are archived and read back
before the terminal state (with archive_ref) is committed.
"""

import asyncio
import os
import re
import ssl
import time
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import aiohttp

from wlh.core import WlhError, digest, public_url, safe_url, uid
from wlh.db import connect, fenced, j, locked_run
from wlh.engine.network import Resolver
from wlh.engine.objects import Objects
from wlh.engine.robots import Robots
from wlh.engine.scope import guard

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
BLOCKERS = (
    r'<input\b[^>]*type=["\']?password',
    r"cf-chl-|g-recaptcha|\bhcaptcha\b",
    r"<title[^>]*>[^<]*(?:sign in|log in|login|captcha|access denied|just a moment)",
    r"verify you are human|checking your browser",
)
# Public intermediate CA (GlobalSign RSA OV SSL CA 2018 -> GlobalSign Root CA - R3)
# omitted by certain government attachment CDNs (e.g., g7.cdnchinhphu.vn).
# GlobalSign Root R46 is also required by www.nta.go.jp.
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


def request_key(request):
    form = request.get("form")
    return digest(
        {
            "algorithm": "wlh.request-key.v1",
            "method": request.get("method", "GET"),
            "url": public_url(request["url"]),
            "form": digest(form) if form else None,
            "role": request.get("role", "page"),
        }
    )


def media(headers):
    message = Message()
    message["content-type"] = headers.get("Content-Type", "application/octet-stream")
    return message.get_content_type(), message.get_param("charset")


def blocked(body, config):
    text = body[:262144].decode("utf-8", errors="replace")
    return any(re.search(p, text, re.I) for p in (*BLOCKERS, *config["blocked_patterns"]))


class Budget:
    """Frozen request/time budget shared by every real external request of one attempt."""

    def __init__(self, config, used, deadline):
        self.config = config
        self.used = used
        self.started = time.monotonic()
        self.deadline = datetime.fromisoformat(deadline) if isinstance(deadline, str) else deadline
        self.cancelled = False
        self.parent_pid = os.getppid()

    def active(self):
        if os.getppid() != self.parent_pid:
            raise WlhError("interrupted")
        if self.cancelled:
            raise WlhError("interrupted")
        if time.monotonic() - self.started > self.config["budget"]["seconds"]:
            raise WlhError("crawl_time_budget_exceeded")
        if self.deadline is not None and datetime.now(timezone.utc) > self.deadline:
            raise WlhError("deadline_exceeded")

    def request(self):
        self.active()
        if self.used >= self.config["budget"]["requests"]:
            raise WlhError("request_budget_exceeded")
        self.used += 1


class Recorder:
    """Short fenced transactions for Acquisition state; failure never claims an archive."""

    def __init__(self, run_id, epoch, source_id):
        self.run_id, self.epoch, self.source_id = run_id, epoch, source_id

    def start(self, key, kind, role, method, url, meta, parent=None, relation=None):
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

    def finish(self, identity, state, **values):
        fields = {
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
            row = c.execute(
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
    def __init__(self, config, budget, recorder):
        self.config, self.budget, self.recorder = config, budget, recorder
        self.robots = Robots(config)
        self.session = None
        self.slots = asyncio.Semaphore(config["concurrency"])
        self.pace = asyncio.Lock()
        self.last = 0.0
        self.objects = Objects()

    async def start(self):
        if self.session is None:
            for env_file in (Path.cwd() / ".env", Path(__file__).resolve().parents[3] / ".env"):
                if env_file.is_file():
                    for line in env_file.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, _, v = line.partition("=")
                            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
            ssl_ctx = (
                ssl.create_default_context(cafile=os.environ["SSL_CERT_FILE"])
                if os.environ.get("SSL_CERT_FILE")
                else ssl.create_default_context()
            )
            ssl_ctx.load_verify_locations(cadata=EXTRA_INTERMEDIATE_CAS_PEM)
            self.session = aiohttp.ClientSession(
                headers={"User-Agent": self.config["user_agent"]},
                trust_env=(os.environ.get("WLH_ALLOW_LOOPBACK") != "1"),
                connector=aiohttp.TCPConnector(
                    limit=self.config["concurrency"],
                    resolver=Resolver(),
                    force_close=True,
                    ssl=ssl_ctx,
                ),
                timeout=aiohttp.ClientTimeout(total=self.config["fetch"]["page_timeout_ms"] / 1000),
                auto_decompress=True,
            )

    async def close(self):
        if self.session is not None:
            await self.session.close()

    async def paced(self):
        async with self.pace:
            wait = self.last + self.config["delay"] - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self.last = time.monotonic()

    async def fetch_policy(self, url):
        key = request_key({"url": url, "role": "policy"})
        acquisition = await self.exchange(url, "GET", None, key, "policy", {}, None, None)
        location = acquisition.get("location")
        if acquisition["state"] == "archived" and acquisition["outcome"] == "redirect" and location:
            # Only operator-approved exact robots destinations; never widens content scope.
            try:
                target = public_url(urljoin(url, location))
            except WlhError:
                raise WlhError("robots_unavailable") from None
            if target in self.config["robots_redirect_urls"]:
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

    async def exchange(self, url, method, form, key, role, meta, parent, relation, *, headers=None):
        """Exactly one real HTTP request and one Acquisition fact."""
        if self.session is None:
            raise WlhError("transport_not_started")
        self.budget.request()
        identity = self.recorder.start(key, "http", role, method, url, meta, parent, relation)
        limit = self.config["budget"]["response_bytes"]
        try:
            async with self.slots:
                await self.paced()
                self.budget.active()
                req_headers = dict(headers) if headers else {}
                if urlsplit(url).hostname == "v3.openstates.org" and os.environ.get("OPENSTATES_API_KEY"):
                    req_headers.setdefault("X-API-KEY", os.environ["OPENSTATES_API_KEY"])
                async with self.session.request(
                    method, url, data=form, headers=req_headers or None, allow_redirects=False
                ) as response:
                    body = bytearray()
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
                    status, headers, actual = response.status, response.headers, str(response.url)
            data = bytes(body)
            self.budget.active()
            ref = self.objects.put(data)
            self.objects.get(ref)  # Read back before any database reference is committed.
            self.budget.active()
        except WlhError as exc:
            if exc.code in {"stale_attempt", "inactive_attempt", "run_not_started"}:
                raise
            state = "cancelled" if exc.code == "interrupted" else "failed"
            return self.recorder.finish(identity, state, outcome="blocked", error=exc.code)
        except asyncio.CancelledError:
            self.recorder.finish(identity, "cancelled", outcome="cancelled", error="interrupted")
            raise
        except (aiohttp.ClientError, TimeoutError, OSError):
            return self.recorder.finish(
                identity, "failed", outcome="network_failed", error="network_failed"
            )
        mime_type, charset = media(headers)
        outcome = (
            "ok" if 200 <= status < 300 else "redirect" if status in REDIRECTS else "http_error"
        )
        row = self.recorder.finish(
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
        row["location"] = headers.get("Location")
        row["headers"] = list(headers.items())
        return row

    async def acquire(self, request):
        """Redirect hops and bounded retries are separate facts linked to their parent."""
        await self.start()
        url = guard(self.config, request["url"])
        await self.robots.check(url, self.fetch_policy)
        method = request.get("method", "GET")
        form = request.get("form")
        if method == "POST" and not request.get("idempotent"):
            raise WlhError("post_replay_semantics_required")
        key = request_key(request)
        meta = {"role": request.get("role", "page")}
        if form:
            meta.update(form_digest=digest(form), form_fields=sorted(form))
        parent, relation = None, None
        for attempt in range(self.config["fetch"]["retries"] + 1):
            current, hop_method, hop_form = url, method, form
            for _ in range(11):
                row = await self.exchange(
                    current, hop_method, hop_form, key, "content", meta, parent, relation
                )
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
            retry = (row["state"] == "failed" and row["error"] == "network_failed") or (
                row["state"] == "archived" and int(row["http_status"] or 0) >= 500
            )
            if not retry or attempt == self.config["fetch"]["retries"]:
                return row
            parent, relation = row["id"], "retry"
            await asyncio.sleep(self.config["fetch"]["retry_backoff_seconds"] * 2**attempt)
        return row


def host_of(url):
    return urlsplit(url).hostname
