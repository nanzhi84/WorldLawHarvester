"""Bounded, read-only HTTP service: GET /results and GET /files/{sha256} only."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from os import PathLike
from typing import Any
from urllib.parse import quote

import psycopg
from aiohttp import web

from wlh.api import queries as result_query
from wlh.api.security import ApiError, Caller, Settings, Tokens
from wlh.core import WlhError

log: logging.Logger = logging.getLogger(__name__)


class Service:
    def __init__(self, settings: Settings, config_path: str | PathLike[str] | None = None) -> None:
        self.settings, self.tokens = settings, Tokens(settings)
        self.config_path = config_path
        self.pool = ThreadPoolExecutor(max_workers=settings.workers, thread_name_prefix="wlh-api")
        self.slots = asyncio.Semaphore(settings.workers)

    def authenticate(self, authorization: str | None) -> Caller:
        current = self.settings
        if self.config_path:
            try:
                current = Settings.load(self.config_path)
            except WlhError:
                raise ApiError(503, "invalid_api_config") from None
            if current.model_dump(exclude={"callers"}) != self.settings.model_dump(
                exclude={"callers"}
            ):
                raise ApiError(503, "invalid_api_config")
        return current.authenticate(authorization)

    async def run[T](
        self, request: web.Request, function: Callable[..., T], *args: Any, hold_slot: bool = False
    ) -> T:
        request["waiting_for_slot"] = True
        try:
            await asyncio.wait_for(self.slots.acquire(), timeout=1)
        except TimeoutError:
            raise ApiError(503, "server_busy") from None
        request["waiting_for_slot"] = False
        try:
            future = asyncio.get_running_loop().run_in_executor(self.pool, partial(function, *args))
        except BaseException:
            self.slots.release()
            raise

        # A cancelled HTTP coroutine must not free capacity while its thread still runs.
        def finished(value: asyncio.Future[T]) -> None:
            if not hold_slot:
                self.slots.release()
            if not value.cancelled():
                value.exception()  # Consume failures even after a disconnected/timed-out caller.

        future.add_done_callback(finished)
        try:
            return await asyncio.shield(future)
        except BaseException:
            if hold_slot:
                future.add_done_callback(lambda _: self.slots.release())
            raise


SERVICE: web.AppKey[Service] = web.AppKey("service", Service)


def storage_error(exc: WlhError) -> ApiError:
    if exc.code in {"archive_corrupt", "archive_invalid", "archive_missing", "archive_too_large"}:
        return ApiError(500, exc.code)
    if exc.code == "archive_read_timeout":
        return ApiError(503, "request_timeout")
    if exc.code in {"archive_unreadable", "object_storage_unavailable"}:
        return ApiError(503, "object_storage_unavailable")
    return ApiError(500, "internal_error")


@web.middleware
async def boundary(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    route = request.match_info.route.name or "unmatched"
    error = None
    response = None
    try:
        service = request.app[SERVICE]
        missing = request.match_info.http_exception
        if missing is not None:
            # Removed and unknown routes are absent for every caller.
            raise missing
        if route != "healthz":
            request["caller"] = service.authenticate(request.headers.get("Authorization"))
        async with asyncio.timeout(service.settings.request_timeout_seconds):
            response = await handler(request)
    except ApiError as exc:
        error = exc
    except WlhError as exc:
        error = storage_error(exc)
    except psycopg.Error:
        error = ApiError(503, "database_unavailable")
    except TimeoutError:
        # Admission and HTTP deadlines may expire in the same event-loop turn.
        # Report the actual phase instead of whichever timer happened to fire first.
        error = ApiError(
            503, "server_busy" if request.get("waiting_for_slot") else "request_timeout"
        )
    except web.HTTPException as exc:
        error = ApiError(exc.status, "method_not_allowed" if exc.status == 405 else "not_found")
    except Exception as exc:
        log.error("route=%s category=%s", route, type(exc).__name__)
        error = ApiError(500, "internal_error")
    if error is not None or response is None:
        err = error or ApiError(500, "internal_error")
        response = web.json_response({"error": err.code}, status=err.status)
        if err.status == 401:
            response.headers["WWW-Authenticate"] = "Bearer"
        log.warning("route=%s status=%s code=%s", route, err.status, err.code)
    if not response.prepared:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


async def healthz(request: web.Request) -> web.Response:
    if request.query:
        raise ApiError(400, "invalid_parameters")
    return web.json_response({"status": "ok"})


async def results(request: web.Request) -> web.Response:
    query = request.query
    try:
        if set(query) - {"source_id", "limit", "cursor"} or any(
            len(query.getall(key)) != 1 for key in query
        ):
            raise ValueError
        source_id = query.get("source_id")
        if source_id is not None and (
            len(source_id) > 32 or not re.fullmatch(r"dd-[0-9]{3,}", source_id)
        ):
            raise ValueError
        value = query.get("limit", "50")
        if not value.isascii() or not value.isdigit() or len(value) > 3:
            raise ValueError
        limit = int(value)
        if not 1 <= limit <= 100:
            raise ValueError
    except (ValueError, KeyError):
        raise ApiError(400, "invalid_parameters") from None
    service = request.app[SERVICE]
    caller: Caller = request["caller"]
    body, link = await service.run(
        request,
        result_query.results,
        service.settings,
        service.tokens,
        caller,
        source_id,
        limit,
        query.get("cursor"),
        caller.projection,
    )
    response = web.Response(body=body, content_type="application/json")
    if link:
        response.headers["Link"] = "<" + link + '>; rel="next"'
    return response


def disposition(name: str) -> str:
    fallback = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\;' else "_" for ch in name)
    return "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (fallback, quote(name, safe=""))


async def files(request: web.Request) -> web.StreamResponse:
    if request.query:
        raise ApiError(400, "invalid_parameters")
    service = request.app[SERVICE]
    entry, body = await service.run(
        request,
        result_query.file,
        service.settings,
        request["caller"],
        request.match_info["sha256"],
        time.monotonic() + service.settings.request_timeout_seconds,
        hold_slot=True,
    )
    try:
        response: web.StreamResponse = web.StreamResponse(
            headers={
                "Content-Type": entry["media_type"],
                "Content-Length": str(len(body)),
                "ETag": '"' + entry["sha256"] + '"',
                "Content-Disposition": disposition(entry["file_name"]),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            }
        )
        await response.prepare(request)
        for offset in range(0, len(body), 1048576):
            await response.write(body[offset : offset + 1048576])
        await response.write_eof()
        return response
    finally:
        service.slots.release()


async def cleanup(app: web.Application) -> None:
    # All HTTP tasks stop first; submitted work has bounded DB/S3 timeouts and deadlines.
    await asyncio.to_thread(app[SERVICE].pool.shutdown, wait=True, cancel_futures=True)


class ProtocolLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        category = type(record.exc_info[1]).__name__ if record.exc_info else "ProtocolError"
        record.msg, record.args = "protocol_error category=%s", (category,)
        record.exc_info, record.exc_text, record.stack_info = None, None, None
        return True


def application(
    settings: Settings, config_path: str | PathLike[str] | None = None
) -> web.Application:
    app = web.Application(middlewares=[boundary], client_max_size=1024)
    app[SERVICE] = Service(settings, config_path)
    app.router.add_get("/healthz", healthz, name="healthz", allow_head=False)
    app.router.add_get("/results", results, name="results", allow_head=False)
    app.router.add_get("/files/{sha256}", files, name="files", allow_head=False)
    app.on_cleanup.append(cleanup)
    return app


def serve(path: str | PathLike[str] | None, host: str, port: int) -> dict[str, str]:
    from wlh.storage.cas import Objects
    from wlh.storage.db import dsn, require_current

    if not path:
        raise WlhError("WLH_API_CONFIG_required")
    if not 1 <= port <= 65535:
        raise WlhError("invalid_arguments")
    settings = Settings.load(path)
    dsn()
    Objects().check()
    require_current()
    logging.basicConfig(level=logging.WARNING, format="%(name)s %(message)s")
    protocol_logger = logging.getLogger("wlh.api.protocol")
    protocol_logger.addFilter(ProtocolLog())
    web.run_app(
        application(settings, path),
        host=host,
        port=port,
        print=None,
        access_log=None,
        handler_cancellation=True,
        shutdown_timeout=65,
        max_line_size=16384,
        max_field_size=8192,
        logger=protocol_logger,
    )
    return {"status": "stopped", "service": "results-api"}
