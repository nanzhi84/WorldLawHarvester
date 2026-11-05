"""Attempt-owned bounded storage work; cancellation retains permits until I/O stops."""

from __future__ import annotations

import asyncio
import functools
import os
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from wlh.core import WlhError


def worker_count() -> int:
    try:
        workers = int(os.environ.get("WLH_CAS_WORKERS", "4"))
        if not 1 <= workers <= 8:
            raise ValueError
        return workers
    except ValueError:
        raise WlhError("invalid_cas_workers") from None


async def _settle(future: asyncio.Future[Any]) -> bool:
    """Drain even through repeated cancellation, without cancelling the blocking job."""
    cancelled = False
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            # The worker records failures; the caller decides which result wins.
            break
    return cancelled


class CasIO:
    def __init__(self) -> None:
        self.workers = worker_count()
        self._executor = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="wlh-cas")
        self._permits = asyncio.Semaphore(self.workers)
        self._pending: set[asyncio.Future[Any]] = set()
        self._lock = threading.Lock()
        self._active = 0
        self._max_active = 0
        self._submitted = 0
        self._completed = 0
        self._errors: Counter[str] = Counter()
        self._closing = False
        self._closed = False

    def _work[T](self, function: Callable[[], T]) -> T:
        with self._lock:
            self._active += 1
            self._max_active = max(self._max_active, self._active)
        try:
            return function()
        except BaseException as exc:
            with self._lock:
                self._errors[type(exc).__name__] += 1
            raise
        finally:
            with self._lock:
                self._active -= 1
                self._completed += 1

    async def run[T](self, function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        async with self._permits:
            if self._closing:
                raise WlhError("cas_io_closed")
            job = asyncio.get_running_loop().run_in_executor(
                self._executor, self._work, functools.partial(function, *args, **kwargs)
            )
            self._pending.add(job)
            self._submitted += 1
            try:
                return await asyncio.shield(job)
            except asyncio.CancelledError:
                # A second signal must not release capacity while the thread still
                # writes. Consume its outcome, retain the cancellation and its fence.
                await _settle(job)
                if not job.cancelled():
                    job.exception()
                raise
            finally:
                self._pending.discard(job)

    async def close(self) -> None:
        if self._closed:
            return
        self._closing = True
        drained = asyncio.gather(*tuple(self._pending), return_exceptions=True)
        cancelled = await _settle(drained)
        self._executor.shutdown(wait=True, cancel_futures=False)
        self._pending.clear()
        self._closed = True
        if cancelled:
            raise asyncio.CancelledError

    def report(self) -> dict[str, Any]:
        with self._lock:
            return {
                "workers": self.workers,
                "max_active": self._max_active,
                "active": self._active,
                "pending": len(self._pending),
                "closed": self._closed,
                "submitted": self._submitted,
                "completed": self._completed,
                "worker_errors": dict(self._errors),
            }
