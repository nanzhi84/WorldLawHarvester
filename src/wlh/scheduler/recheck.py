"""Freeze due Resources (stable identity, display URL, recheck locator, current Version)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from wlh.storage.db import DbConn


def plan_recheck(c: DbConn, source: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    sched_raw = config.get("scheduler")
    sched: Mapping[str, Any] = (
        cast(Mapping[str, Any], sched_raw) if isinstance(sched_raw, Mapping) else config
    )
    budget_raw = sched.get("budget") or config.get("budget")
    budget_map: Mapping[str, Any] = (
        cast(Mapping[str, Any], budget_raw) if isinstance(budget_raw, Mapping) else {}
    )
    budget = int(budget_map.get("documents", 10))
    rows: list[dict[str, Any]] = c.execute(
        """SELECT r.id,r.identity_namespace AS namespace,r.external_key,r.public_url,
        r.recheck_locator,r.current_version_id FROM wlh.resource r
        JOIN wlh.frontier f ON f.id=r.frontier_id
        WHERE f.source_id=%s ORDER BY r.next_check,r.id LIMIT %s""",
        (source["id"], budget + 1),
    ).fetchall()
    selected = rows[:budget]
    return {
        "selection": "known_resources",
        "resource_count": len(selected),
        "truncated": len(rows) > len(selected),
        "resources": selected,
    }
