"""Session facade for upstream adapters: verified CAS responses only."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from wlh.core import WlhError, request_key
from wlh.fetcher.transport import budget_cfg as _budget_cfg
from wlh.storage.cas import Objects
from wlh.storage.db import connect


@dataclass(frozen=True)
class ArchivedResponse:
    acquisition: Mapping[str, Any]
    content: bytes

    @property
    def status_code(self) -> int:
        return self.acquisition["http_status"]

    @property
    def url(self) -> str:
        return self.acquisition["actual_url"] or self.acquisition["request_url"]

    @property
    def text(self) -> str:
        return self.content.decode(self.acquisition.get("charset") or "utf-8")

    def json(self) -> Any:
        return json.loads(self.content)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise WlhError("upstream_http_error")


class CasSessionProxy:
    def __init__(
        self,
        transport: Any,
        mode: str = "collect",
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        archived: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.transport, self.mode, self.loop = transport, mode, loop
        self.archived: Mapping[str, Mapping[str, Any]] = archived or {}
        self.objects: Any = Objects()

    def read(self, acquisition: Mapping[str, Any]) -> ArchivedResponse:
        if acquisition.get("state") != "archived":
            raise WlhError("input_not_archived")
        return ArchivedResponse(acquisition, self.objects.get(acquisition["archive_ref"]))

    async def acquire(self, request: Any) -> Mapping[str, Any]:
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

    def request(
        self,
        method: str,
        url: str,
        *,
        data: Mapping[str, Any] | None = None,
        idempotent: bool = False,
    ) -> ArchivedResponse:
        request: dict[str, Any] = {
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
        timeout = _budget_cfg(self.transport.config)["seconds"]
        try:
            return self.read(future.result(timeout=timeout))
        except TimeoutError:
            future.cancel()
            raise WlhError("bridge_request_timeout") from None

    def get(self, url: str) -> ArchivedResponse:
        return self.request("GET", url)

    def post(
        self,
        url: str,
        data: Mapping[str, Any] | None = None,
        *,
        idempotent: bool = False,
    ) -> ArchivedResponse:
        return self.request("POST", url, data=data, idempotent=idempotent)
