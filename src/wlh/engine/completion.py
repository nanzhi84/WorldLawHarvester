"""Finalize the operational Run; each valid resource response commits independently."""

from __future__ import annotations

from typing import Any

from wlh.db import connect, fenced, locked_run
from wlh.engine.run_state import TERMINAL_STATES


def finish_run(run_id: str, epoch: int) -> dict[str, Any]:
    from wlh.engine.runs import end_run, receipt

    with connect() as c:
        source, run = locked_run(c, run_id)
        if run["status"] not in TERMINAL_STATES:
            fenced(source, run, epoch)
            end_run(c, source, run, "failed" if run["errors"] else "complete", None)
    return receipt(run_id)
