"""Rendered DOM acquisition; all browser HTTP inputs use the controlled transport.

Raw main responses, redirects and subresources have independent HTTP Acquisition facts.
The rendered UTF-8 DOM is an additional representation, not an extra network request.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
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
                if event.get("resourceType") != "Document":
                    await session.send(
                        "Fetch.failRequest",
                        {"requestId": request_id, "errorReason": "BlockedByClient"},
                    )
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
                req_headers = {
                    name: value
                    for name, value in browser_request.get("headers", {}).items()
                    if name.lower()
                    not in {"accept-encoding", "host", "connection", "content-length"}
                }
                row: dict[str, Any] = await self._exchange_with_retry(
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
                    headers=req_headers,
                )
                facts[request_id] = row["id"]
                if row["state"] != "archived":
                    raise WlhError(row["error"] or "network_failed")
                if row["outcome"] not in {"ok", "redirect"}:
                    raise WlhError(row["error"] or "network_failed")
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
                            "resource_type": event["resourceType"],
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
                if not facts or event.get("redirectedRequestId") in facts:
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
            if "handlerToanVanDuThao" in html and (
                m_lan := re.search(r"let\s+lanDuThaoId\s*=\s*['\"]([^'\"]+)['\"]", html)
            ):
                lan_id = m_lan.group(1)
                m_tt = re.search(r"let\s+TrangThaiThongQua\s*=\s*(\d+)", html)
                tt = m_tt.group(1) if m_tt else "0"
                sub_urls = [
                    f"{actual}?handler=ToanVanDuThao&lanDuThaoId={lan_id}&trangThaiThongQua={tt}",
                    f"{actual}?handler=Files&FileAttachmentType=DuThaoLuat_LanDuThaoLuat&LtsEntityIds={lan_id}",
                ]
                parts: list[str] = []
                for raw_sub_url in sub_urls:
                    try:
                        sub_url = guard(self.config, raw_sub_url)
                        await self.transport.robots.check(sub_url, self.transport.fetch_policy)
                        sub_key = request_key(
                            {"url": sub_url, "method": "GET", "role": "browser_http"}
                        )
                        sub_row = await self._exchange_with_retry(
                            sub_url,
                            "GET",
                            None,
                            sub_key,
                            "content",
                            {
                                "rendered_acquisition_id": identity,
                                "resource_type": "Fetch",
                            },
                            None,
                            None,
                        )
                        if sub_row["state"] == "archived" and sub_row["outcome"] == "ok":
                            sub_bytes = self.transport.objects.get(sub_row["archive_ref"])
                            while (
                                sub_row["state"] == "archived"
                                and sub_row["outcome"] == "ok"
                                and sub_row.get("mime_type") == "text/html"
                                and len(sub_bytes) <= 16384
                                and (loopback := self._detect_html_loopback(sub_url, sub_bytes))
                            ):
                                sub_url = guard(self.config, loopback)
                                await self.transport.robots.check(
                                    sub_url, self.transport.fetch_policy
                                )
                                hop_key = request_key(
                                    {"url": sub_url, "method": "GET", "role": "browser_http"}
                                )
                                sub_row = await self._exchange_with_retry(
                                    sub_url,
                                    "GET",
                                    None,
                                    hop_key,
                                    "content",
                                    {
                                        "rendered_acquisition_id": identity,
                                        "resource_type": "Fetch",
                                    },
                                    sub_row["id"],
                                    "redirect",
                                )
                                if sub_row["state"] != "archived" or sub_row["outcome"] != "ok":
                                    break
                                sub_bytes = self.transport.objects.get(sub_row["archive_ref"])
                            if sub_row["state"] == "archived" and sub_row["outcome"] == "ok":
                                parts.append(sub_bytes.decode("utf-8", errors="replace"))
                    except WlhError as exc:
                        if exc.code in {"stale_attempt", "inactive_attempt", "run_not_started"}:
                            raise
                if parts:
                    extra_html = "\n".join(parts)
                    if re.search(r'<div\b[^>]*\bid=["\']nav-home["\'][^>]*>', html, re.I):
                        html = re.sub(
                            r'(<div\b[^>]*\bid=["\']nav-home["\'][^>]*>)',
                            lambda m: m.group(1) + "\n" + extra_html,
                            html,
                            count=1,
                            flags=re.I,
                        )
                    elif "</body>" in html:
                        html = html.replace(
                            "</body>", f'<div id="nav-home">{extra_html}</div></body>', 1
                        )
                    else:
                        html += f'<div id="nav-home">{extra_html}</div>'
            if "/zhengce/xxgk/gjgzk/" in actual:
                try:
                    api_url = guard(
                        self.config,
                        "https://sousuoht.www.gov.cn/athena/forward/BD8730CDDA12515E2D9E1B21AA11C0D6",
                    )
                    await self.transport.robots.check(api_url, self.transport.fetch_policy)
                    api_body = json.dumps(
                        {
                            "code": "18258ab0ac9",
                            "preference": None,
                            "searchFields": [
                                {
                                    "fieldName": "f_202321807875",
                                    "searchWord": "部门规章",
                                    "searchType": "TERM",
                                    "withHighLight": True,
                                },
                                {"fieldName": "f_202321360426", "searchWord": "", "withHighLight": True},
                                {"fieldName": "f_202321758948", "searchWord": "", "withHighLight": True},
                                {"fieldName": "f_202321423473", "searchType": "TERM", "withHighLight": True},
                                {"fieldName": "f_202321159816", "searchWord": "", "searchType": "TERM"},
                                {"fieldName": "f_20232380533", "searchType": "TERM", "withHighLight": True},
                                {"fieldName": "f_202328191239", "withHighLight": True, "searchType": "TERM"},
                                {"fieldName": "f_20221110222856", "withHighLight": True, "searchType": "TERM"},
                            ],
                            "sorts": [{}, {"sortField": "f_202321915922", "sortOrder": "DESC"}],
                            "resultFields": [
                                "f_202355832506",
                                "f_20232124962",
                                "f_202321124775",
                                "f_202321159816",
                                "f_202321360426",
                                "f_202321423473",
                                "f_202321758948",
                                "f_202321807875",
                                "f_202321864401",
                                "f_202321915922",
                                "f_202323394765",
                                "f_202328191239",
                                "f_202344311304",
                                "f_2023425676953",
                                "f_2023425808265",
                                "f_202321136868",
                                "f_20232380533",
                                "f_20232151076",
                                "doc_pub_url",
                            ],
                            "trackTotalHits": "true",
                            "tableName": "t_1860c735d31",
                            "pageSize": 15,
                            "pageNo": 1,
                            "granularity": "ALL",
                        },
                        ensure_ascii=False,
                    ).encode("utf-8")
                    api_key = request_key(
                        {
                            "url": api_url,
                            "method": "POST",
                            "role": "browser_http",
                            "form": {"body_hash": hashlib.sha256(api_body).hexdigest()},
                        }
                    )
                    api_headers = {
                        "Referer": "https://www.gov.cn/",
                        "athenaappname": "%E8%A7%84%E7%AB%A0%E5%BA%93",
                        "athenaappkey": (
                            "E%2FQRus35opRkACauSyCo2t37RrSeSLi6meog4HmvjhLtUuXR%2BkVXD59MNLxT681E"
                            "IZh7vCOScxRJlO%2BJHQQBpdkZxuR%2FzsW8r%2FDMc9ze4Gwehp9oDvQMpwnS53QbtYPl"
                            "%2B5lpYi5wEFSef0uGt%2F4hXfxiw1dIvlqAc7ioMJeSM6o%3D"
                        ),
                        "Content-Type": "application/json;charset=UTF-8",
                    }
                    api_row = await self._exchange_with_retry(
                        api_url,
                        "POST",
                        api_body,
                        api_key,
                        "content",
                        {
                            "rendered_acquisition_id": identity,
                            "resource_type": "XHR",
                        },
                        None,
                        None,
                        headers=api_headers,
                    )
                    if api_row["state"] == "archived" and api_row["outcome"] == "ok":
                        api_bytes = self.transport.objects.get(api_row["archive_ref"])
                        api_json = json.loads(api_bytes.decode("utf-8", errors="replace"))
                        if isinstance(api_json, dict):
                            res_obj = cast(dict[str, Any], api_json).get("result")
                            data_obj = (
                                cast(dict[str, Any], res_obj).get("data")
                                if isinstance(res_obj, dict)
                                else None
                            )
                            items_list = (
                                cast(dict[str, Any], data_obj).get("list")
                                if isinstance(data_obj, dict)
                                else None
                            )
                            if isinstance(items_list, list):
                                injected = "".join(
                                    f'<div class="depart_m"><a href="{cast(dict[str, Any], it)["doc_pub_url"]}">{cast(dict[str, Any], it).get("f_202321360426", "")}</a></div>'
                                    for it in cast(list[Any], items_list)
                                    if isinstance(it, dict)
                                    and cast(dict[str, Any], it).get("doc_pub_url")
                                )
                                if injected:
                                    if "</body>" in html:
                                        html = html.replace("</body>", f"{injected}</body>", 1)
                                    else:
                                        html += injected
                except WlhError as exc:
                    if exc.code in {"stale_attempt", "inactive_attempt", "run_not_started"}:
                        raise
                except (ValueError, KeyError, TypeError):
                    pass
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
