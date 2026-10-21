"""Session facade for upstream adapters: verified CAS responses only.

Async engine code uses acquire. Synchronous upstream plugins may use get/post from
an executor thread with the owning event loop supplied. No global monkeypatching.
"""

import asyncio
import json
from dataclasses import dataclass

from wlh.core import WlhError
from wlh.db import connect
from wlh.engine.objects import Objects
from wlh.engine.transport import request_key


@dataclass(frozen=True)
class ArchivedResponse:
    acquisition: dict
    content: bytes

    @property
    def status_code(self):
        return self.acquisition["http_status"]

    @property
    def url(self):
        return self.acquisition["actual_url"] or self.acquisition["request_url"]

    @property
    def text(self):
        return self.content.decode(self.acquisition.get("charset") or "utf-8")

    def json(self):
        return json.loads(self.content)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise WlhError("upstream_http_error")


class CasSessionProxy:
    def __init__(self, transport, mode="collect", *, loop=None, archived=None):
        self.transport, self.mode, self.loop = transport, mode, loop
        self.archived = archived or {}
        self.objects = Objects()

    def read(self, acquisition):
        if acquisition.get("state") != "archived":
            raise WlhError("input_not_archived")
        return ArchivedResponse(acquisition, self.objects.get(acquisition["archive_ref"]))

    async def acquire(self, request):
        if hasattr(request, "model_dump"):
            request = request.model_dump(exclude_none=True)
        key = request_key(request)
        previous = self.archived.get(key)
        if previous:
            self.read(previous)
            return previous
        if self.mode == "reparse":
            raise WlhError("network_disabled_in_reparse")
        with connect() as c:
            previous = c.execute(
                """SELECT * FROM wlh.acquisition WHERE run_id=%s AND request_key=%s
                AND state='archived' AND outcome='ok' AND role='content'
                ORDER BY acquired_at DESC LIMIT 1""",
                (self.transport.recorder.run_id, key),
            ).fetchone()
        if previous:
            self.read(previous)
            return previous
        return await self.transport.acquire(request)

    def request(self, method, url, *, data=None, idempotent=False):
        request = {
            "method": method.upper(),
            "url": url,
            "form": data,
            "idempotent": idempotent,
            "role": "page",
        }
        if self.mode == "reparse":
            previous = self.archived.get(request_key(request))
            if previous is None:
                raise WlhError("network_disabled_in_reparse")
            return self.read(previous)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise WlhError("synchronous_bridge_requires_executor_thread")
        if self.loop is None or not self.loop.is_running():
            raise WlhError("bridge_owner_loop_required")
        future = asyncio.run_coroutine_threadsafe(self.acquire(request), self.loop)
        try:
            return self.read(future.result(timeout=self.transport.config["budget"]["seconds"]))
        except TimeoutError:
            future.cancel()
            raise WlhError("bridge_request_timeout") from None

    def get(self, url):
        return self.request("GET", url)

    def post(self, url, data=None, *, idempotent=False):
        return self.request("POST", url, data=data, idempotent=idempotent)
