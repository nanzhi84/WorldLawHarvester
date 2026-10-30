"""Read-only current-attempt progress; URLs, HTTP responses and documents differ."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Self, cast

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

from wlh.core import safe_url
from wlh.storage.db import DbConn, connect, one


@dataclass(frozen=True)
class ProgressSnapshot:
    run_id: str
    source_id: str
    mode: str
    status: str
    started_at: str
    counts: dict[str, int]
    current_url: str | None


def snapshot(c: DbConn, run: Mapping[str, Any]) -> ProgressSnapshot:
    tasks: dict[str, Any] = cast(
        dict[str, Any],
        c.execute(
            """SELECT count(*) FILTER(WHERE kind='request') AS discovered,
            count(*) FILTER(WHERE kind='request' AND state IN ('archived','done')) AS fetched,
            count(*) FILTER(WHERE kind='request' AND state='pending') AS pending,
            count(*) FILTER(WHERE kind='request' AND state='failed') AS request_failed,
            count(*) FILTER(WHERE kind='target') AS targets,
            count(*) FILTER(WHERE kind='target' AND state IN ('done','failed','skipped')) AS processed,
            count(*) FILTER(WHERE kind='target' AND state='skipped') AS skipped
            FROM wlh.task WHERE run_id=%s""",
            (run["id"],),
        ).fetchone(),
    )
    outcomes: dict[str, Any] = cast(
        dict[str, Any],
        c.execute(
            """SELECT count(*) FILTER(WHERE version_id IS NOT NULL) AS resources,
            count(*) FILTER(WHERE outcome='failed') AS failed,
            count(*) FILTER(WHERE outcome='unchanged') AS unchanged,
            count(*) FILTER(WHERE outcome IN ('created','changed')) AS updated
            FROM wlh.observation WHERE run_id=%s""",
            (run["id"],),
        ).fetchone(),
    )
    requests: int = cast(
        dict[str, Any],
        c.execute(
            "SELECT count(*) AS n FROM wlh.acquisition WHERE run_id=%s AND acquisition_kind='http'",
            (run["id"],),
        ).fetchone(),
    )["n"]
    current: dict[str, Any] | None = c.execute(
        """SELECT payload->>'url' AS url FROM wlh.task WHERE run_id=%s AND kind='request'
        AND state='pending' ORDER BY seq LIMIT 1""",
        (run["id"],),
    ).fetchone()
    counts = dict(tasks, **outcomes, external_requests=requests, active=tasks["pending"])
    return ProgressSnapshot(
        run["id"],
        run["source_id"],
        run["mode"],
        run["status"],
        run["created_at"].isoformat(),
        counts,
        safe_url(current["url"])[:160] if current and current["url"] else None,
    )


def terminal_report(
    c: DbConn, run: Mapping[str, Any], status: str, reason: str | None
) -> dict[str, Any]:
    errors = sorted(set(run["errors"]) | ({reason} if reason else set()))
    report: dict[str, Any] = dict(run["report"], errors=errors, counts=snapshot(c, run).counts)
    report.setdefault(
        "coverage",
        {
            "selected_scope_enumeration": "incomplete",
            "site_population": "UNKNOWN",
            "unknown_coverage": True,
            "absence_semantics": "unobserved_is_unknown_never_deleted",
        },
    )
    return report


class ProgressPanel:
    def __init__(self, enabled: bool) -> None:
        self.console: Console = Console(stderr=True)
        self.enabled: bool = enabled and self.console.is_terminal
        self.live: Live | None = None
        self.started: float = time.monotonic()
        self.sampled: float = 0.0

    def __enter__(self) -> Self:
        if self.enabled:
            try:
                self.live = Live(
                    Text("WLH Initializing"),
                    console=self.console,
                    refresh_per_second=4,
                    transient=False,
                )
                self.live.start()
            except Exception:
                self.live = None
        return self

    def update(self, run_id: str, *, final: bool = False) -> None:
        if not self.live or (not final and time.monotonic() - self.sampled < 1):
            return
        self.sampled = time.monotonic()
        try:
            with connect() as c:
                state = snapshot(c, one(c, "SELECT * FROM wlh.run WHERE id=%s", (run_id,)))
            self.live.update(self.render(state))
        except Exception:
            # Display is never a prerequisite for correct execution.
            self.stop()
            self.live = None

    def render(self, state: ProgressSnapshot) -> Table:
        table = Table.grid(padding=(0, 2))
        elapsed = int(time.monotonic() - self.started)
        table.add_row(
            Text("WLH " + state.mode.title(), style="bold cyan"),
            Text(state.status),
            Text(f"{elapsed // 3600:02}:{elapsed // 60 % 60:02}:{elapsed % 60:02}"),
        )
        table.add_row(Text("Source " + state.source_id), Text(state.run_id[:8]))
        counts = state.counts
        table.add_row(
            Text(f"Discovered {counts['discovered']:,}"),
            Text(f"Fetched {counts['fetched']:,}"),
            Text(f"Resources {counts['resources']:,}"),
        )
        table.add_row(
            Text(f"Failed {counts['failed']:,}"),
            Text(f"Pending {counts['pending']:,}"),
            Text(f"Active {counts['active']:,}"),
        )
        if state.mode == "recheck":
            table.add_row(
                Text(f"Unchanged {counts['unchanged']:,}"), Text(f"Updated {counts['updated']:,}")
            )
        total, done = counts["targets"], counts["processed"]
        filled = int(16 * done / total) if total else 0
        table.add_row(
            Text("Document targets"),
            Text("[" + "█" * filled + "░" * (16 - filled) + "]"),
            Text(f"{done} / {total}"),
        )
        if state.current_url:
            table.add_row(Text("Current"), Text(state.current_url, overflow="ellipsis"))
        return table

    def __exit__(self, *args: Any) -> None:
        self.stop()

    def stop(self) -> None:
        if self.live:
            try:
                self.live.stop()
            except Exception:
                pass
