"""Rendered DOM acquisition; all browser HTTP inputs use the controlled transport.

Raw main responses, redirects and subresources have independent HTTP Acquisition facts.
The rendered UTF-8 DOM is an additional representation, not an extra network request.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

from wlh.core import WlhError
from wlh.engine.scope import guard
from wlh.engine.transport import REDIRECTS, Budget, Recorder, Transport, request_key

if TYPE_CHECKING:
    from playwright.async_api import Browser as PlaywrightBrowser
    from playwright.async_api import Playwright, Response


class Browser:
    def __init__(
        self, config: Mapping[str, Any], budget: Budget, recorder: Recorder, transport: Transport
    ) -> None:
        self.config: Mapping[str, Any]
        self.budget: Budget
        self.recorder: Recorder
        self.transport: Transport
        self.config, self.budget, self.recorder, self.transport = (
            config,
            budget,
            recorder,
            transport,
        )
        self.lock: asyncio.Lock = asyncio.Lock()
        self.playwright: Playwright | None = None
        self.browser: PlaywrightBrowser | None = None
        self.loopback_cookies: set[tuple[str, str, str]] = set()
        self.script_cache: dict[str, dict[str, Any]] = {}

    def _detect_html_loopback(self, url: str, data: bytes) -> str | None:
        text = data.decode("utf-8", errors="replace")
        if "_afrLoop=" not in url and "AdfLoopbackUtils.runLoopback" in text:
            match = re.search(r"['\"]_afrLoop['\"]\s*,\s*['\"](\d+)['\"]", text)
            if match:
                sep = "&" if "?" in url else "?"
                return f"{url}{sep}_afrLoop={match.group(1)}&_afrWindowMode=0"
        if len(data) <= 2048 and "location.reload" in text:
            match = re.search(r'document\.cookie\s*=\s*["\']([^=;"\']+)=([^;"\']+)', text)
            if match and self.transport.session is not None:
                from yarl import URL

                name, value = match.group(1).strip(), match.group(2).strip()
                key = (urlsplit(url).hostname or "", name, value)
                if key not in self.loopback_cookies:
                    self.loopback_cookies.add(key)
                    self.transport.session.cookie_jar.update_cookies({name: value}, URL(url))
                    return url
        return None

    async def start(self) -> None:
        async with self.lock:
            if self.browser is None:
                from playwright.async_api import async_playwright

                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(
                    headless=True,
                    args=["--host-resolver-rules=MAP * ~NOTFOUND"],
                )

    async def close(self) -> None:
        if self.browser is not None:
            await self.browser.close()
        if self.playwright is not None:
            await self.playwright.stop()

    async def _exchange_with_retry(
        self,
        url: str,
        method: str,
        body: bytes | None,
        key: str,
        role: str,
        meta: Mapping[str, Any],
        parent: str | None,
        relation: str | None,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {}
        cur_parent, cur_rel = parent, relation
        retries: int = int(self.config["fetch"]["retries"])
        for attempt in range(retries + 1):
            row = await self.transport.exchange(
                url,
                method,
                body,
                key,
                role,
                meta,
                cur_parent,
                cur_rel,
                headers=headers,
            )
            retry = (row["state"] == "failed" and row["error"] == "network_failed") or (
                row["state"] == "archived" and int(row.get("http_status") or 0) >= 500
            )
            if not retry or attempt == retries:
                return row
            cur_parent, cur_rel = row["id"], "retry"
            await asyncio.sleep(self.config["fetch"]["retry_backoff_seconds"] * 2**attempt)
        return row

    async def acquire(self, request: Mapping[str, Any]) -> dict[str, Any]:
        await self.start()
        assert self.browser is not None
        await self.transport.start()
        url = guard(self.config, request["url"])
        self.budget.active()
        key = request_key(request)
        identity: str = self.recorder.start(
            key, "rendered_dom", "content", "GET", url, {"role": request.get("role", "page")}
        )
        context: Any = None
        session: Any = None
        errors: list[str] = []
        subrequest_errors: list[str] = []
        facts: dict[str | None, str] = {}
        pending: set[asyncio.Task[Any]] = set()
        main_doc_done = False

        async def route_input(event: dict[str, Any]) -> None:
            nonlocal main_doc_done
            current = cast(asyncio.Task[Any], asyncio.current_task())
            pending.add(current)
            request_id = event["requestId"]
            browser_request = event["request"]
            rtype = str(event.get("resourceType") or "")
            try:
                if browser_request["url"].startswith(("data:", "blob:", "about:")):
                    await session.send("Fetch.continueRequest", {"requestId": request_id})
                    return
                if rtype not in {"Document", "Script", "XHR", "Fetch"}:
                    await session.send(
                        "Fetch.failRequest",
                        {"requestId": request_id, "errorReason": "BlockedByClient"},
                    )
                    return
                method = browser_request["method"]
                allowed_methods = (
                    {"GET", "HEAD", "OPTIONS", "POST"}
                    if rtype in {"XHR", "Fetch"}
                    else {"GET", "HEAD", "OPTIONS"}
                )
                if method not in allowed_methods:
                    # Script actions have no Adapter declaration of query idempotence.
                    # Such queries must be planned explicitly through Transport.acquire.
                    raise WlhError(
                        "post_replay_semantics_required"
                        if method == "POST"
                        else "browser_method_not_supported"
                    )
                try:
                    target = guard(self.config, browser_request["url"])
                except WlhError:
                    if main_doc_done:
                        await session.send(
                            "Fetch.failRequest",
                            {"requestId": request_id, "errorReason": "BlockedByClient"},
                        )
                        return
                    raise
                req_headers = {
                    name: value
                    for name, value in browser_request.get("headers", {}).items()
                    if name.lower()
                    not in {"accept-encoding", "host", "connection", "content-length"}
                }
                if rtype == "Script" and method == "GET" and target in self.script_cache:
                    row: dict[str, Any] = self.script_cache[target]
                else:
                    await self.transport.robots.check(target, self.transport.fetch_policy)
                    entries = browser_request.get("postDataEntries")
                    body: bytes | None = (
                        b"".join(base64.b64decode(entry["bytes"]) for entry in entries)
                        if entries
                        else browser_request.get("postData", "").encode("utf-8") or None
                    )
                    raw_key: str = request_key(
                        {
                            "url": target,
                            "method": browser_request["method"],
                            "role": "browser_http",
                            "form": {"body_hash": hashlib.sha256(body).hexdigest()} if body else None,
                        }
                    )
                    previous = facts.get(event.get("redirectedRequestId"))
                    row = await self._exchange_with_retry(
                        target,
                        browser_request["method"],
                        body,
                        raw_key,
                        "content",
                        {
                            "rendered_acquisition_id": identity,
                            "resource_type": rtype,
                        },
                        previous,
                        "redirect" if previous else None,
                        headers=req_headers,
                    )
                facts[request_id] = row["id"]
                if row["state"] != "archived":
                    raise WlhError(row["error"] or "network_failed")
                if row["outcome"] not in {"ok", "redirect"}:
                    raise WlhError(row["error"] or "network_failed")
                if rtype == "Script" and method == "GET":
                    self.script_cache[target] = row
                raw_bytes = self.transport.objects.get(row["archive_ref"])
                while (
                    row["state"] == "archived"
                    and row["outcome"] == "ok"
                    and row.get("mime_type") == "text/html"
                    and len(raw_bytes) <= 16384
                    and (loopback := self._detect_html_loopback(target, raw_bytes))
                ):
                    target = guard(self.config, loopback)
                    await self.transport.robots.check(target, self.transport.fetch_policy)
                    hop_key = request_key({"url": target, "method": "GET", "role": "browser_http"})
                    hop_headers = {
                        k: v for k, v in req_headers.items() if k.lower() != "cookie"
                    }
                    row = await self._exchange_with_retry(
                        target,
                        "GET",
                        None,
                        hop_key,
                        "content",
                        {
                            "rendered_acquisition_id": identity,
                            "resource_type": rtype,
                        },
                        row["id"],
                        "redirect",
                        headers=hop_headers,
                    )
                    facts[request_id] = row["id"]
                    if row["state"] != "archived":
                        raise WlhError(row["error"] or "network_failed")
                    if row["outcome"] not in {"ok", "redirect"}:
                        raise WlhError(row["error"] or "network_failed")
                    raw_bytes = self.transport.objects.get(row["archive_ref"])
                if rtype == "Document" and row["outcome"] == "ok":
                    main_doc_done = True
                # Transport archives decoded HTTP entity bytes. Chromium must not decode
                # them again; preserve semantic headers, including redirects and cookies.
                headers: list[dict[str, str]] = [
                    {"name": name, "value": value}
                    for name, value in row["headers"]
                    if name.lower()
                    not in {"content-encoding", "content-length", "transfer-encoding", "connection"}
                ]
                await session.send(
                    "Fetch.fulfillRequest",
                    {
                        "requestId": request_id,
                        "responseCode": row["http_status"],
                        "responseHeaders": headers,
                        "body": base64.b64encode(raw_bytes).decode("ascii"),
                    },
                )
            except WlhError as exc:
                if (rtype == "Document" and not main_doc_done) or exc.code in {
                    "request_budget_exceeded",
                    "response_byte_budget_exceeded",
                    "time_budget_exceeded",
                    "document_budget_exceeded",
                    "stale_attempt",
                    "inactive_attempt",
                    "run_not_started",
                    "interrupted",
                    "post_replay_semantics_required",
                    "browser_method_not_supported",
                    "robots_disallowed",
                }:
                    errors.append(exc.code)
                elif exc.code != "request_out_of_scope":
                    subrequest_errors.append(exc.code)
                with contextlib.suppress(Exception):
                    await session.send(
                        "Fetch.failRequest",
                        {"requestId": request_id, "errorReason": "BlockedByClient"},
                    )
            except asyncio.CancelledError:
                errors.append("interrupted")
                raise
            except Exception:
                if rtype == "Document" and not main_doc_done:
                    errors.append("network_failed")
                else:
                    subrequest_errors.append("network_failed")
                with contextlib.suppress(Exception):
                    await session.send(
                        "Fetch.failRequest", {"requestId": request_id, "errorReason": "Failed"}
                    )
            finally:
                pending.discard(current)

        try:
            context = await self.browser.new_context(
                user_agent=self.config["user_agent"],
                accept_downloads=False,
                ignore_https_errors=False,
                service_workers="block",
            )
            page = await context.new_page()
            session = await context.new_cdp_session(page)
            # Fetch.requestPaused reports every redirect hop. Playwright route handlers
            # only see the first hop, allowing later scripts to escape acquisition.
            # https://github.com/ChromeDevTools/devtools-protocol/blob/master/json/browser_protocol.json
            def callback(event: dict[str, Any]) -> asyncio.Task[None]:
                return asyncio.ensure_future(route_input(event))

            session.on("Fetch.requestPaused", callback)
            await session.send(
                "Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}
            )
            response: Response | None = await page.goto(
                url,
                wait_until=self.config["fetch"]["wait_until"],
                timeout=self.config["fetch"]["page_timeout_ms"],
            )
            while pending:
                self.budget.active()
                await asyncio.gather(*tuple(pending))
            for _ in range(3):
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("networkidle", timeout=5000)
                while pending:
                    self.budget.active()
                    await asyncio.gather(*tuple(pending))
            self.budget.active()
            if errors:
                raise WlhError(errors[0])
            html = await page.content()
            status = response.status if response else None
            actual = page.url
            max_pages = int(self.config.get("adapter_options", {}).get("max_pages", 1))
            if max_pages > 1:
                page_links: list[str] = list(
                    dict.fromkeys(re.findall(r"<a\b[^>]*>.*?</a>", html, flags=re.I | re.S))
                )
                pagination_error: str | None = None
                for _ in range(max_pages - 1):
                    if await page.locator(".next-page").count() <= 0:
                        break
                    err_before = len(subrequest_errors)
                    try:
                        await page.locator(".next-page").first.click(timeout=5000)
                    except Exception:
                        pagination_error = "network_failed"
                        break
                    with contextlib.suppress(Exception):
                        await page.wait_for_timeout(1500)
                        await page.wait_for_load_state("networkidle", timeout=5000)
                    while pending:
                        self.budget.active()
                        await asyncio.gather(*tuple(pending))
                    self.budget.active()
                    if errors:
                        raise WlhError(errors[0])
                    if len(subrequest_errors) > err_before:
                        pagination_error = subrequest_errors[err_before]
                        break
                    cur_html = await page.content()
                    for tag in re.findall(r"<a\b[^>]*>.*?</a>", cur_html, flags=re.I | re.S):
                        if tag not in page_links:
                            page_links.append(tag)
                html = await page.content()
                has_more = await page.locator(".next-page").count() > 0
                trunc_reason = (
                    pagination_error
                    or (subrequest_errors[0] if subrequest_errors else None)
                    or ("page_limit_exceeded" if has_more else None)
                )
                if page_links or trunc_reason:
                    trunc_attr = (
                        f" data-wlh-truncated='{trunc_reason}'" if trunc_reason else ""
                    )
                    extra = (
                        f"<div data-wlh-pages='true'{trunc_attr}>"
                        + "".join(page_links)
                        + "</div>"
                    )
                    if "</body>" in html:
                        html = html.replace("</body>", f"{extra}</body>", 1)
                    else:
                        html += extra
            elif subrequest_errors:
                extra = f"<div data-wlh-truncated='{subrequest_errors[0]}'></div>"
                if "</body>" in html:
                    html = html.replace("</body>", f"{extra}</body>", 1)
                else:
                    html += extra
            data = html.encode("utf-8")
            if len(data) > self.config["budget"]["response_bytes"]:
                return self.recorder.finish(
                    identity, "failed", outcome="blocked", error="response_byte_budget_exceeded"
                )
            self.budget.active()
            ref = self.transport.objects.put(data)
            self.transport.objects.get(ref)
            self.budget.active()
        except asyncio.CancelledError:
            self.recorder.finish(identity, "cancelled", outcome="cancelled", error="interrupted")
            raise
        except Exception as exc:
            code: str = (
                exc.code
                if isinstance(exc, WlhError)
                else (errors[0] if errors else "network_failed")
            )
            if code in {"stale_attempt", "inactive_attempt", "run_not_started"}:
                raise WlhError(code) from exc
            state = "cancelled" if code == "interrupted" else "failed"
            return self.recorder.finish(identity, state, outcome="blocked", error=code)
        finally:
            for task in tuple(pending):
                task.cancel()
            if pending:
                await asyncio.gather(*tuple(pending), return_exceptions=True)
            if context is not None:
                with contextlib.suppress(Exception):
                    await context.close()
        ok = status is not None and 200 <= status < 300 and status not in REDIRECTS
        return self.recorder.finish(
            identity,
            "archived",
            status=status,
            outcome="ok" if ok else "http_error",
            error=None if ok else "http_" + str(status),
            actual_url=actual,
            mime_type="text/html",
            charset="utf-8",
            representation="rendered_dom_utf8",
            byte_size=len(data),
            body_hash=ref,
            archive_ref=ref,
        )
