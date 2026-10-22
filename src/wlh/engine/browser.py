"""Rendered DOM acquisition; all browser HTTP inputs use the controlled transport.

Raw main responses, redirects and subresources have independent HTTP Acquisition facts.
The rendered UTF-8 DOM is an additional representation, not an extra network request.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

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
        facts: dict[str | None, str] = {}
        pending: set[asyncio.Task[Any]] = set()

        async def route_input(event: dict[str, Any]) -> None:
            current = cast(asyncio.Task[Any], asyncio.current_task())
            pending.add(current)
            request_id = event["requestId"]
            browser_request = event["request"]
            try:
                if browser_request["url"].startswith(("data:", "blob:", "about:")):
                    await session.send("Fetch.continueRequest", {"requestId": request_id})
                    return
                method = browser_request["method"]
                if method not in {"GET", "HEAD", "OPTIONS"}:
                    # Script actions have no Adapter declaration of query idempotence.
                    # Such queries must be planned explicitly through Transport.acquire.
                    raise WlhError(
                        "post_replay_semantics_required"
                        if method == "POST"
                        else "browser_method_not_supported"
                    )
                target = guard(self.config, browser_request["url"])
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
                row: dict[str, Any] = await self.transport.exchange(
                    target,
                    browser_request["method"],
                    body,
                    raw_key,
                    "content",
                    {
                        "rendered_acquisition_id": identity,
                        "resource_type": event["resourceType"],
                    },
                    previous,
                    "redirect" if previous else None,
                    headers={
                        name: value
                        for name, value in browser_request.get("headers", {}).items()
                        if name.lower()
                        not in {"accept-encoding", "host", "connection", "content-length"}
                    },
                )
                facts[request_id] = row["id"]
                if row["state"] != "archived":
                    raise WlhError(row["error"] or "network_failed")
                if row["outcome"] not in {"ok", "redirect"}:
                    raise WlhError(row["error"] or "network_failed")
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
                        "body": base64.b64encode(
                            self.transport.objects.get(row["archive_ref"])
                        ).decode("ascii"),
                    },
                )
            except WlhError as exc:
                errors.append(exc.code)
                with contextlib.suppress(Exception):
                    await session.send(
                        "Fetch.failRequest",
                        {"requestId": request_id, "errorReason": "BlockedByClient"},
                    )
            except asyncio.CancelledError:
                errors.append("interrupted")
                raise
            except Exception:
                errors.append("network_failed")
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
            self.budget.active()
            if errors:
                raise WlhError(errors[0])
            html = await page.content()
            status = response.status if response else None
            actual = page.url
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
