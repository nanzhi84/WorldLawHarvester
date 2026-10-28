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
        requests_list = result.get("requests", [])
        targets_list = result.get("targets", [])
        candidate_req_keys = list({
            *(request_key(req) for req in requests_list),
            *(request_key(item["request"]) for t in targets_list for item in t.get("inputs", [])),
        })
        request_ids: dict[str, str] = (
            {
                r["key"]: r["id"]
                for r in c.execute(
                    "SELECT key, id FROM wlh.task WHERE run_id=%s AND kind='request' AND key = ANY(%s)",
                    (self.run_id, candidate_req_keys),
                ).fetchall()
            }
            if candidate_req_keys
            else {}
        )

        def ensure_request(req: Mapping[str, Any], req_depth: int) -> str:
            rkey = request_key(req)
            existing_req_id = request_ids.get(rkey)
            if existing_req_id is not None:
                return existing_req_id
            rid = self.insert_request(c, req, req_depth)
            request_ids[rkey] = rid
            return rid

        for request in requests_list:
            ensure_request(request, request.get("depth", depth))
        skipped_budget = False

        def identity_pair(t_key: str, t_payload: Mapping[str, Any]) -> tuple[str, str]:
            ident = cast(Mapping[str, Any], t_payload.get("identity") or {})
            ns = ident.get("namespace")
            ext = ident.get("external_key")
            return (
                str(ns) if ns is not None else t_key,
                str(ext) if ext is not None else t_key,
            )

        existing_targets: dict[str, dict[str, Any]] = {}
        active_identities: set[tuple[str, str]] = set()
        if targets_list:
            cand_tkeys = list({target_key(t) for t in targets_list})
            matched = c.execute(
                "SELECT id FROM wlh.task WHERE run_id=%s AND kind='target' AND key = ANY(%s) LIMIT 1",
                (self.run_id, cand_tkeys),
            ).fetchone()
            active_rows_row = c.execute(
                "SELECT count(*) AS n FROM wlh.task WHERE run_id=%s AND kind='target' AND state NOT IN ('failed','skipped')",
                (self.run_id,),
            ).fetchone()
            active_rows = int(active_rows_row["n"]) if active_rows_row else 0
            if (
                matched is not None
                or self.truncated == "document_budget_exceeded"
                or active_rows + len(targets_list) >= self.config["budget"]["documents"]
            ):
                existing_targets = {
                    row["key"]: row
                    for row in c.execute(
                        "SELECT * FROM wlh.task WHERE run_id=%s AND kind='target' FOR UPDATE",
                        (self.run_id,),
                    ).fetchall()
                }
                active_identities = {
                    identity_pair(row["key"], row["payload"])
                    for row in existing_targets.values()
                    if row["state"] not in ("failed", "skipped")
                }
        new_targets: list[tuple[Any, ...]] = []
        new_inputs: list[tuple[str, str, int]] = []
        for target in targets_list:
            key = target_key(target)
            count: int = len(active_identities)
            exists: dict[str, Any] | None = existing_targets.get(key)
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
                    active_identities.add(identity_pair(key, exists["payload"]))
                    continue
                # A later occurrence is a separate candidate, even for the same identity.
                # Delivery validates all three fingerprints against the immutable fact.
                key = digest(
                    {"algorithm": "wlh.delivery-task-replay.v1", "target": key, "payload": payload}
                )
                if key in existing_targets:
                    continue
            state = "pending"
            if not exists and count >= self.config["budget"]["documents"]:
                state = "skipped"
            target_id = uid()
            row_err = "document_budget_exceeded" if state == "skipped" else None
            new_targets.append((target_id, self.run_id, key, j(payload), state, row_err))
            existing_targets[key] = {
                "id": target_id,
                "run_id": self.run_id,
                "kind": "target",
                "key": key,
                "payload": payload,
                "state": state,
                "error": row_err,
            }
            if state == "skipped":
                skipped_budget = True
                if self.truncated in non_error_trunc:
                    self.truncated = "document_budget_exceeded"
                self.checkpoint["truncated"] = self.truncated
                continue
            active_identities.add(identity_pair(key, payload))
            for position, item in enumerate(target["inputs"]):
                request_id: str = ensure_request(
                    item["request"], item["request"].get("depth", depth)
                )
                new_inputs.append((target_id, request_id, position))
        if new_targets or new_inputs:
            with c.cursor() as cur:
                if new_targets:
                    cur.executemany(
                        """INSERT INTO wlh.task(id,run_id,kind,key,payload,state,error)
                        VALUES(%s,%s,'target',%s,%s,%s,%s)""",
                        new_targets,
                    )
                if new_inputs:
                    cur.executemany(
                        """INSERT INTO wlh.task_input(target_id,request_id,position)
                        VALUES(%s,%s,%s) ON CONFLICT DO NOTHING""",
                        new_inputs,
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
            if len(active_identities) >= self.config["budget"]["documents"]:
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
