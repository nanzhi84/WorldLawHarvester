"""Attempt request budgets and short fenced Acquisition transactions."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, cast

from wlh.core import WlhError, safe_url, uid
from wlh.storage.db import connect, fenced, j, locked_run


def budget_cfg(config: Mapping[str, Any]) -> Mapping[str, Any]:
    sched = config.get("scheduler")
    if isinstance(sched, Mapping):
        sched_map = cast(Mapping[str, Any], sched)
        if isinstance(sched_map.get("budget"), Mapping):
            return cast(Mapping[str, Any], sched_map["budget"])
    budget = config.get("budget")
    if isinstance(budget, Mapping):
        return cast(Mapping[str, Any], budget)
    return {"requests": 200, "seconds": 300, "response_bytes": 15000000, "documents": 25}


class Budget:
    """Frozen request/time budget shared by every real external request of one attempt."""

    def __init__(
        self, config: Mapping[str, Any], used: int, deadline: str | datetime | None
    ) -> None:
        self.config: Mapping[str, Any] = config
        self.used: int = used
        self.started: float = time.monotonic()
        self.deadline: datetime | None = (
            datetime.fromisoformat(deadline) if isinstance(deadline, str) else deadline
        )
        self.cancelled: bool = False
        self.parent_pid: int = os.getppid()

    def active(self) -> None:
        if os.getppid() != self.parent_pid:
            raise WlhError("interrupted")
        if self.cancelled:
            raise WlhError("interrupted")
        budget = budget_cfg(self.config)
        if time.monotonic() - self.started > budget["seconds"]:
            raise WlhError("crawl_time_budget_exceeded")
        if self.deadline is not None and datetime.now(timezone.utc) > self.deadline:
            raise WlhError("deadline_exceeded")

    def request(self) -> None:
        self.active()
        budget = budget_cfg(self.config)
        if self.used >= budget["requests"]:
            raise WlhError("request_budget_exceeded")
        self.used += 1


class Recorder:
    """Short fenced transactions for Acquisition state; failure never claims an archive."""

    def __init__(self, run_id: str, epoch: int, source_id: str) -> None:
        self.run_id: str = run_id
        self.epoch: int = epoch
        self.source_id: str = source_id

    def start(
        self,
        key: str,
        kind: str,
        role: str,
        method: str,
        url: str,
        meta: Mapping[str, Any],
        parent: str | None = None,
        relation: str | None = None,
    ) -> str:
        identity = uid()
        with connect() as c:
            source, run = locked_run(c, self.run_id)
            fenced(source, run, self.epoch)
            if run["mode"] == "reparse":
                raise WlhError("network_disabled_in_reparse")
            if kind == "http":
                c.execute(
                    "UPDATE wlh.run SET external_requests=external_requests+1 WHERE id=%s",
                    (self.run_id,),
                )
            c.execute(
                """INSERT INTO wlh.acquisition(id,source_id,run_id,attempt_epoch,request_key,
                acquisition_kind,role,http_method,request_url,request_meta,parent_acquisition_id,
                parent_relation,state) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'running')""",
                (
                    identity,
                    self.source_id,
                    self.run_id,
                    self.epoch,
                    key,
                    kind,
                    role,
                    method,
                    safe_url(url),
                    j(meta),
                    parent,
                    relation,
                ),
            )
        return identity

    def finish(self, identity: str, state: str, **values: Any) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "http_status": values.get("status"),
            "outcome": values.get("outcome"),
            "error": values.get("error"),
            "actual_url": safe_url(values["actual_url"]) if values.get("actual_url") else None,
            "mime_type": values.get("mime_type"),
            "charset": values.get("charset"),
            "representation": values.get("representation"),
            "byte_size": values.get("byte_size"),
            "body_hash": values.get("body_hash"),
            "archive_ref": values.get("archive_ref"),
        }
        with connect() as c:
            source, run = locked_run(c, self.run_id)
            fenced(source, run, self.epoch)
            row: dict[str, Any] | None = c.execute(
                """UPDATE wlh.acquisition SET state=%s,acquired_at=clock_timestamp(),
                http_status=%s,outcome=%s,error=%s,actual_url=%s,mime_type=%s,charset=%s,
                representation=%s,byte_size=%s,body_hash=%s,archive_ref=%s
                WHERE id=%s AND state='running' AND attempt_epoch=%s RETURNING *""",
                (state, *fields.values(), identity, self.epoch),
            ).fetchone()
        if row is None:
            raise WlhError("stale_attempt")
        return row
