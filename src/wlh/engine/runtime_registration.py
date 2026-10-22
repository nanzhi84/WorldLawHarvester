"""Atomic task expansion, replay candidates and bounded Adapter state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from wlh.core import digest, uid
from wlh.db import DbConn, j
from wlh.engine.adapter_context import target_key
from wlh.engine.transport import request_key

if TYPE_CHECKING:
    from wlh.engine.runtime import Runtime

ADAPTER_STATE_SCHEMA = 1


def register(
    self: Runtime, result: Mapping[str, Any], parent: str | None = None, depth: int = 0
) -> None:
    """Persist follow-up requests/targets and complete the parent in one transaction."""

    def apply(c: DbConn) -> None:
        non_error_trunc = (
            None,
            "document_budget_exceeded",
            "max_depth_exceeded",
            "page_limit_exceeded",
        )
        if result.get("truncated") and (
            self.truncated in non_error_trunc
            or result["truncated"] not in non_error_trunc
        ):
            self.truncated = result["truncated"]
            self.checkpoint["truncated"] = self.truncated
        for request in result.get("requests", []):
            self.insert_request(c, request, request.get("depth", depth))
        skipped_budget = False
        for target in result.get("targets", []):
            key = target_key(target)
            count: int = cast(dict[str, Any], c.execute(
                """SELECT count(DISTINCT (
                    COALESCE(payload #>> '{identity,namespace}', key),
                    COALESCE(payload #>> '{identity,external_key}', key)
                )) AS n FROM wlh.task WHERE run_id=%s AND kind='target'
                AND state NOT IN ('failed', 'skipped')""",
                (self.run_id,),
            ).fetchone())["n"]
            exists: dict[str, Any] | None = c.execute(
                """SELECT * FROM wlh.task WHERE run_id=%s AND kind='target' AND key=%s
                FOR UPDATE""",
                (self.run_id, key),
            ).fetchone()
            payload: dict[str, Any] = {
                "identity": target.get("identity"),
                "context": target.get("context") or {},
                "frozen": target.get("frozen"),
                "inputs": [
                    {
                        **{k: v for k, v in item.items() if k != "request"},
                        "request_key": request_key(item["request"]),
                    }
                    for item in target["inputs"]
                ],
            }
            if exists:
                old = exists["payload"]
                if old == payload:
                    continue
                extends = all(item in payload["inputs"] for item in old["inputs"])
                if exists["state"] == "pending" and extends:
                    amend(self, c, exists, target, depth)
                    continue
                # A later occurrence is a separate candidate, even for the same identity.
                # Delivery validates all three fingerprints against the immutable fact.
                key = digest(
                    {"algorithm": "wlh.delivery-task-replay.v1", "target": key, "payload": payload}
                )
                if c.execute(
                    "SELECT 1 FROM wlh.task WHERE run_id=%s AND kind='target' AND key=%s",
                    (self.run_id, key),
                ).fetchone():
                    continue
            state = "pending"
            if not exists and count >= self.config["budget"]["documents"]:
                state = "skipped"
            target_id = uid()
            c.execute(
                """INSERT INTO wlh.task(id,run_id,kind,key,payload,state,error)
                VALUES(%s,%s,'target',%s,%s,%s,%s)""",
                (
                    target_id,
                    self.run_id,
                    key,
                    j(payload),
                    state,
                    "document_budget_exceeded" if state == "skipped" else None,
                ),
            )
            if state == "skipped":
                skipped_budget = True
                if self.truncated in non_error_trunc:
                    self.truncated = "document_budget_exceeded"
                self.checkpoint["truncated"] = self.truncated
                continue
            for position, item in enumerate(target["inputs"]):
                request_id: str = self.insert_request(
                    c, item["request"], item["request"].get("depth", depth)
                )
                c.execute(
                    """INSERT INTO wlh.task_input(target_id,request_id,position)
                    VALUES(%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (target_id, request_id, position),
                )
        if parent is not None:
            c.execute(
                """UPDATE wlh.task SET state='done',attempt_epoch=%s,
                updated_at=clock_timestamp() WHERE id=%s""",
                (self.epoch, parent),
            )
        if "state" in result:
            self.checkpoint["adapter_state"] = {
                "schema": ADAPTER_STATE_SCHEMA,
                **(result["state"] or {}),
            }
        if "state" in result or result.get("truncated") or self.truncated:
            if self.truncated:
                self.checkpoint["truncated"] = self.truncated
            c.execute(
                """UPDATE wlh.frontier SET checkpoint=checkpoint || %s,updated_at=now()
                WHERE source_id=%s AND run_id=%s""",
                (
                    j(
                        {
                            k: self.checkpoint[k]
                            for k in ("adapter_state", "truncated")
                            if k in self.checkpoint
                        }
                    ),
                    self.run["source_id"],
                    self.run_id,
                ),
            )

        if skipped_budget or self.truncated == "document_budget_exceeded":
            active_targets: int = cast(
                dict[str, Any],
                c.execute(
                    """SELECT count(DISTINCT (
                        COALESCE(payload #>> '{identity,namespace}', key),
                        COALESCE(payload #>> '{identity,external_key}', key)
                    )) AS n FROM wlh.task WHERE run_id=%s AND kind='target'
                    AND state NOT IN ('failed', 'skipped')""",
                    (self.run_id,),
                ).fetchone(),
            )["n"]
            if active_targets >= self.config["budget"]["documents"]:
                c.execute(
                    """UPDATE wlh.task r SET state='skipped',error='document_budget_exceeded',
                    attempt_epoch=%s,updated_at=clock_timestamp()
                    WHERE r.run_id=%s AND r.kind='request' AND r.state='pending'
                    AND NOT EXISTS (
                        SELECT 1 FROM wlh.task_input i JOIN wlh.task t ON t.id=i.target_id
                        WHERE i.request_id=r.id AND t.state='pending'
                    )""",
                    (self.epoch, self.run_id),
                )

    self.write(apply)


def amend(
    self: Runtime, c: DbConn, row: Mapping[str, Any], target: Mapping[str, Any], depth: int
) -> None:
    """A not-yet-delivered target gains newly discovered required inputs."""
    payload = row["payload"]
    known = {item["request_key"] for item in payload["inputs"]}
    added: bool = payload.get("identity") != target.get("identity") or payload.get("context") != (
        target.get("context") or {}
    )
    payload["identity"] = target.get("identity")
    payload["context"] = target.get("context") or {}
    for item in target["inputs"]:
        key = request_key(item["request"])
        if key in known:
            continue
        request_id = self.insert_request(c, item["request"], item["request"].get("depth", depth))
        c.execute(
            """INSERT INTO wlh.task_input(target_id,request_id,position) VALUES(%s,%s,%s)
            ON CONFLICT DO NOTHING""",
            (row["id"], request_id, len(payload["inputs"])),
        )
        payload["inputs"].append(
            {**{k: v for k, v in item.items() if k != "request"}, "request_key": key}
        )
        known.add(key)
        added = True
    if added:
        c.execute(
            "UPDATE wlh.task SET payload=%s,updated_at=clock_timestamp() WHERE id=%s",
            (j(payload), row["id"]),
        )
