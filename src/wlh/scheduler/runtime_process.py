"""Run subprocess entrypoint and ownership watchdog."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import threading
import time
from collections.abc import Mapping
from types import FrameType
from typing import Any, NoReturn

from wlh.core import WlhError
from wlh.processor.manifest import require_runtime
from wlh.scheduler.runs import run_context
from wlh.scheduler.runtime import Runtime
from wlh.storage.db import connect, record_error


def watch_parent(parent: int) -> NoReturn:
    while True:
        if os.getppid() != parent:
            os.killpg(os.getpgrp(), signal.SIGKILL)
        time.sleep(0.2)


async def run(context: Mapping[str, Any]) -> None:
    try:
        require_runtime(context["config"], context["parser_manifest"])
    except WlhError as exc:
        record_error(context["id"], context["attempt_epoch"], exc.code)
        with connect() as c:
            c.execute(
                """UPDATE wlh.run SET status='finishing' WHERE id=%s AND attempt_epoch=%s
                AND status='running'""",
                (context["id"], context["attempt_epoch"]),
            )
        return
    runtime = Runtime(context)
    task = asyncio.current_task()

    def cancel(signum: int, frame: FrameType | None) -> None:
        runtime.budget.cancelled = True
        if task is not None:
            task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, cancel)
    await runtime.execute()


def main() -> None:
    run_id, epoch, parent = sys.argv[1:]
    threading.Thread(target=watch_parent, args=(int(parent),), daemon=True).start()
    asyncio.run(run(run_context(run_id, int(epoch))))


if __name__ == "__main__":
    main()
