"""Freeze an explicit offline reparse input set: current Versions' archived inputs only."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from wlh.db import DbConn


def plan_reparse(c: DbConn, source: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    budget = int(config["budget"]["documents"])
    rows: list[dict[str, Any]] = c.execute(
        """SELECT r.id AS resource_id,r.identity_namespace AS namespace,r.external_key,
        r.public_url,r.recheck_locator,v.id AS version_id,v.inputs_ref,v.parser_fingerprint
        FROM wlh.resource r JOIN wlh.frontier f ON f.id=r.frontier_id
        JOIN wlh.version v ON v.id=r.current_version_id
        WHERE f.source_id=%s ORDER BY r.id LIMIT %s""",
        (source["id"], budget + 1),
    ).fetchall()
    recovery_target_rows: list[dict[str, Any]] = c.execute(
        """SELECT DISTINCT ON (
            t.payload->'identity'->>'namespace',
            t.payload->'identity'->>'external_key'
        ) t.id, t.payload
        FROM wlh.task t
        JOIN wlh.run ru ON ru.id=t.run_id
        WHERE ru.source_id=%s AND ru.mode='collect' AND t.kind='target'
          AND t.payload->'identity'->>'namespace' IS NOT NULL
          AND t.payload->'identity'->>'external_key' IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM wlh.resource r JOIN wlh.frontier f ON f.id=r.frontier_id
              WHERE f.source_id=%s
                AND r.identity_namespace=t.payload->'identity'->>'namespace'
                AND r.external_key=t.payload->'identity'->>'external_key'
                AND r.current_version_id IS NOT NULL
          )
          AND EXISTS (SELECT 1 FROM wlh.task_input ti WHERE ti.target_id=t.id)
          AND NOT EXISTS (
              SELECT 1 FROM wlh.task_input ti
              JOIN wlh.task req ON req.id=ti.request_id
              LEFT JOIN wlh.acquisition a ON a.id=req.acquisition_id
              WHERE ti.target_id=t.id
                AND (a.id IS NULL OR a.state!='archived' OR a.outcome!='ok')
          )
        ORDER BY
            t.payload->'identity'->>'namespace',
            t.payload->'identity'->>'external_key',
            ru.run_seq DESC,
            t.seq ASC
        LIMIT %s""",
        (source["id"], source["id"], budget + 1),
    ).fetchall()
    recovery_targets: list[dict[str, Any]] = []
    if recovery_target_rows:
        tids = [str(r["id"]) for r in recovery_target_rows]
        input_rows: list[dict[str, Any]] = c.execute(
            """SELECT ti.target_id, req.key AS request_key, req.acquisition_id
            FROM wlh.task_input ti JOIN wlh.task req ON req.id=ti.request_id
            WHERE ti.target_id=ANY(%s) ORDER BY ti.target_id, ti.position""",
            (tids,),
        ).fetchall()
        by_target: dict[str, dict[str, str]] = {}
        for ir in input_rows:
            if ir.get("acquisition_id"):
                by_target.setdefault(str(ir["target_id"]), {})[str(ir["request_key"])] = str(
                    ir["acquisition_id"]
                )
        for tr in recovery_target_rows:
            req_map = by_target.get(str(tr["id"]), {})
            payload_val = tr.get("payload")
            payload: dict[str, Any] = (
                cast(dict[str, Any], payload_val)
                if isinstance(payload_val, dict)
                else dict[str, Any]()
            )
            raw_inputs_untyped = payload.get("inputs")
            raw_inputs: list[dict[str, Any]] = (
                [
                    cast(dict[str, Any], item)
                    for item in cast(list[object], raw_inputs_untyped)
                    if isinstance(item, dict)
                ]
                if isinstance(raw_inputs_untyped, list)
                else []
            )
            if not raw_inputs or not all(
                isinstance(key := item.get("request_key"), str) and key in req_map
                for item in raw_inputs
            ):
                continue
            recovery_targets.append(
                {
                    "identity": payload.get("identity"),
                    "context": payload.get("context") or {},
                    "inputs": [
                        {**item, "acquisition_id": req_map[str(item["request_key"])]}
                        for item in raw_inputs
                    ],
                }
            )
    recovery_requests: list[dict[str, Any]] = c.execute(
        """SELECT DISTINCT ON (req.key)
            req.id, req.key, req.payload, req.depth, req.acquisition_id
        FROM wlh.task req
        JOIN wlh.run ru ON ru.id=req.run_id
        JOIN wlh.acquisition a ON a.id=req.acquisition_id
        WHERE ru.source_id=%s AND ru.mode='collect'
          AND req.kind='request' AND req.state='failed'
          AND a.state='archived' AND a.outcome='ok'
          AND NOT EXISTS (SELECT 1 FROM wlh.task_input ti WHERE ti.request_id=req.id)
        ORDER BY req.key, ru.run_seq DESC, req.seq ASC
        LIMIT %s""",
        (source["id"], budget + 1),
    ).fetchall()
    if len(rows) > budget and (recovery_targets or recovery_requests):
        keep_current = 1 if rows else 0
        selected = rows[:keep_current]
        rem = max(0, budget - len(selected))
        selected_recovery_targets = recovery_targets[:rem]
        truncated = len(recovery_targets) > len(selected_recovery_targets)
    else:
        selected = rows[:budget]
        rem = max(0, budget - len(selected))
        selected_recovery_targets = recovery_targets[:rem]
        truncated = len(rows) > len(selected) or len(recovery_targets) > len(
            selected_recovery_targets
        )
    return {
        "selection": "current_versions",
        "network": "disabled",
        "resource_count": len(selected),
        "truncated": truncated,
        "resources": selected,
        "recovery_targets": selected_recovery_targets,
        "recovery_requests": recovery_requests[: max(0, budget - len(selected))],
    }
