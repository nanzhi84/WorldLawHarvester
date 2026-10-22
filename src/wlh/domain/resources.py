"""Stable document identity and immutable, transactional Observation/Version timelines."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from wlh.core import WlhError, uid
from wlh.db import DbConn, connect, fenced, j, locked_run, one

FINGERPRINTS = ("input_fingerprint", "parser_fingerprint", "content_hash")


def resource(
    c: DbConn,
    frontier_id: str,
    namespace: str,
    external_key: str,
    public_url: str | None,
    recheck_locator: Mapping[str, Any],
) -> dict[str, Any]:
    if not namespace or not external_key:
        raise WlhError("document_identity_required")
    c.execute(
        """INSERT INTO wlh.resource(id,frontier_id,identity_namespace,external_key,public_url,
        recheck_locator) VALUES(%s,%s,%s,%s,%s,%s)
        ON CONFLICT(frontier_id,identity_namespace,external_key) DO NOTHING""",
        (uid(), frontier_id, namespace, external_key, public_url, j(recheck_locator)),
    )
    row: dict[str, Any] = one(
        c,
        """SELECT * FROM wlh.resource WHERE frontier_id=%s AND identity_namespace=%s
        AND external_key=%s FOR UPDATE""",
        (frontier_id, namespace, external_key),
    )
    if row["public_url"] != public_url or row["recheck_locator"] != recheck_locator:
        c.execute(
            "UPDATE wlh.resource SET public_url=%s,recheck_locator=%s WHERE id=%s",
            (public_url, j(recheck_locator), row["id"]),
        )
    return row


def check_acquisitions(
    c: DbConn, source: Mapping[str, Any], run: Mapping[str, Any], ids: Sequence[str]
) -> None:
    rows: list[dict[str, Any]] = c.execute(
        "SELECT id,source_id,run_id,state FROM wlh.acquisition WHERE id=ANY(%s)", (ids,)
    ).fetchall()
    if len(rows) != len(set(ids)):
        raise WlhError("input_acquisition_missing")
    for row in rows:
        if row["source_id"] != source["id"] or row["state"] != "archived":
            raise WlhError("input_acquisition_not_owned")
        # Normal collection only uses this Run's inputs. Offline reparse explicitly
        # freezes the same Source's existing input set (wlh.run.reparse_plan).
        if run["mode"] != "reparse" and row["run_id"] != run["id"]:
            raise WlhError("input_acquisition_not_owned")


def finish_target(
    c: DbConn,
    target_id: str | None,
    state: str,
    observation_id: str | None = None,
    error: str | None = None,
    epoch: int | None = None,
) -> None:
    if target_id:
        c.execute(
            """UPDATE wlh.task SET state=%s,observation_id=%s,error=%s,attempt_epoch=%s,
            updated_at=clock_timestamp() WHERE id=%s AND kind='target'""",
            (state, observation_id, error, epoch, target_id),
        )


def evidence(c: DbConn, observation_id: str, ids: Sequence[str], relation: str) -> None:
    for acquisition_id in sorted(set(ids)):
        c.execute(
            """INSERT INTO wlh.observation_evidence(observation_id,acquisition_id,relation)
            VALUES(%s,%s,%s) ON CONFLICT DO NOTHING""",
            (observation_id, acquisition_id, relation),
        )


def conflict(
    c: DbConn,
    run_id: str,
    epoch: int,
    resource_id: str,
    reason: str,
    existing: str | None,
    attempted: Mapping[str, Any],
) -> None:
    c.execute(
        """INSERT INTO wlh.delivery_conflict(id,run_id,resource_id,attempt_epoch,reason,
        existing_observation_id,attempted) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
        (uid(), run_id, resource_id, epoch, reason, existing, j(attempted)),
    )
    c.execute("UPDATE wlh.run SET errors=errors || %s WHERE id=%s", (j([reason]), run_id))


def schedule(
    c: DbConn, run: Mapping[str, Any], resource_id: str, outcome: str, success: bool
) -> None:
    interval: int = (
        run["config"]["recheck_seconds"] if success else min(60, run["config"]["recheck_seconds"])
    )
    c.execute(
        """UPDATE wlh.resource SET last_checked_at=now(),last_outcome=%s,
        next_check=now()+(%s * interval '1 second') WHERE id=%s""",
        (outcome, interval, resource_id),
    )


def commit(
    run_id: str,
    epoch: int,
    built: Mapping[str, Any],
    *,
    target_id: str | None = None,
    check_active: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """One run/resource delivery commits once; replay checks input, parser and content."""
    with connect() as c:
        source, run = locked_run(c, run_id)
        fenced(source, run, epoch)
        frontier = one(c, "SELECT * FROM wlh.frontier WHERE source_id=%s", (source["id"],))
        check_acquisitions(c, source, run, built["acquisition_ids"])
        r: dict[str, Any] = resource(
            c,
            frontier["id"],
            built["namespace"],
            built["external_key"],
            built["public_url"],
            built["recheck_locator"],
        )
        attempted = {k: built[k] for k in (*FINGERPRINTS, "version_fingerprint", "result_ref")}
        key = run_id + ":" + r["id"]
        existing: dict[str, Any] | None = c.execute(
            "SELECT * FROM wlh.observation WHERE delivery_key=%s", (key,)
        ).fetchone()
        if existing:
            if existing["outcome"] != "failed" and all(
                existing[k] == built[k] for k in FINGERPRINTS
            ):
                evidence(c, existing["id"], built["acquisition_ids"], "replay")
                finish_target(c, target_id, "done", existing["id"], None, epoch)
                if check_active is not None:
                    check_active()
                return {
                    "status": "replayed",
                    "observation_id": existing["id"],
                    "resource_id": r["id"],
                }
            conflict(c, run_id, epoch, r["id"], "delivery_key_conflict", existing["id"], attempted)
            finish_target(c, target_id, "failed", existing["id"], "delivery_key_conflict", epoch)
            return {"status": "conflict", "observation_id": existing["id"], "resource_id": r["id"]}
        current: dict[str, Any] | None = c.execute(
            "SELECT * FROM wlh.version WHERE id=%s", (r["current_version_id"],)
        ).fetchone()
        oid = uid()
        if current and current["version_fingerprint"] == built["version_fingerprint"]:
            if current["content_hash"] != built["content_hash"]:
                # Same input and parser, different business content: never overwrite.
                c.execute(
                    """INSERT INTO wlh.observation(id,resource_id,run_id,attempt_epoch,
                    delivery_key,outcome,change_reason,error,inputs_ref,input_fingerprint,
                    parser_fingerprint,version_fingerprint,content_hash)
                    VALUES(%s,%s,%s,%s,%s,'failed','nondeterministic','nondeterministic_parser_output',
                    %s,%s,%s,%s,%s)""",
                    (
                        oid,
                        r["id"],
                        run_id,
                        epoch,
                        key,
                        built["inputs_ref"],
                        built["input_fingerprint"],
                        built["parser_fingerprint"],
                        built["version_fingerprint"],
                        built["content_hash"],
                    ),
                )
                evidence(c, oid, built["acquisition_ids"], "input")
                conflict(
                    c, run_id, epoch, r["id"], "nondeterministic_parser_output", None, attempted
                )
                schedule(c, run, r["id"], "failed", False)
                finish_target(c, target_id, "failed", oid, "nondeterministic_parser_output", epoch)
                return {"status": "conflict", "observation_id": oid, "resource_id": r["id"]}
            outcome, reason, version_id = "unchanged", "unchanged", current["id"]
        else:
            if current is None:
                outcome, reason = "created", "first_success"
            else:
                outcome = "changed"
                source_changed = current["input_fingerprint"] != built["input_fingerprint"]
                parser_changed = current["parser_fingerprint"] != built["parser_fingerprint"]
                reason = (
                    "both_changed"
                    if source_changed and parser_changed
                    else "source_changed"
                    if source_changed
                    else "parser_recomputed"
                    if run["mode"] == "reparse"
                    else "parser_changed"
                )
            version_id = uid()
        c.execute(
            """INSERT INTO wlh.observation(id,resource_id,run_id,attempt_epoch,delivery_key,
            outcome,change_reason,inputs_ref,result_ref,input_fingerprint,parser_fingerprint,
            version_fingerprint,content_hash,version_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                oid,
                r["id"],
                run_id,
                epoch,
                key,
                outcome,
                reason,
                built["inputs_ref"],
                built["result_ref"],
                built["input_fingerprint"],
                built["parser_fingerprint"],
                built["version_fingerprint"],
                built["content_hash"],
                version_id,
            ),
        )
        if outcome != "unchanged":
            c.execute(
                """INSERT INTO wlh.version(id,resource_id,sequence,observation_id,result_ref,
                inputs_ref,input_fingerprint,parser_fingerprint,version_fingerprint,content_hash,
                parser_config,change_reason) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    version_id,
                    r["id"],
                    current["sequence"] + 1 if current else 1,
                    oid,
                    built["result_ref"],
                    built["inputs_ref"],
                    built["input_fingerprint"],
                    built["parser_fingerprint"],
                    built["version_fingerprint"],
                    built["content_hash"],
                    j(built["parser_config"]),
                    reason,
                ),
            )
            for row in built["rows"]:
                c.execute(
                    """INSERT INTO wlh.document_row(version_id,row_index,title,markdown_file,
                    doc_type,acquired_at,original_file,volume_order,
                    doc_number,issuer,published_at,effective_at,expired_at,status,
                    source_name,detail_url,source_record_hash,markdown_hash,original_hash,
                    original_media_type,role,locator,extensions) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        version_id,
                        row["row_index"],
                        row["title"],
                        row["markdown_file"],
                        row["doc_type"],
                        row["acquired_at"] + "+00:00",
                        row["original_file"],
                        row["volume_order"],
                        row["doc_number"],
                        row["issuer"],
                        row["published_at"],
                        row["effective_at"],
                        row["expired_at"],
                        row["status"],
                        row["source_name"],
                        row["detail_url"],
                        row["source_record_hash"],
                        row["markdown_hash"],
                        row["original_hash"],
                        row["original_media_type"],
                        row["role"],
                        j(row["locator"]),
                        j(row.get("extensions") or {}),
                    ),
                )
            c.execute(
                "UPDATE wlh.resource SET current_version_id=%s WHERE id=%s", (version_id, r["id"])
            )
        evidence(c, oid, built["acquisition_ids"], "input")
        schedule(c, run, r["id"], outcome, True)
        finish_target(c, target_id, "done", oid, None, epoch)
        if check_active is not None:
            # Process-local cancellation/budget fence immediately before COMMIT.
            check_active()
        return {
            "status": outcome,
            "observation_id": oid,
            "resource_id": r["id"],
            "version_id": version_id,
        }


def fail(
    run_id: str,
    epoch: int,
    identity: Mapping[str, Any] | None,
    error: str,
    *,
    acquisition_ids: Sequence[str] = (),
    target_id: str | None = None,
) -> str | None:
    """Record a failed delivery; never overwrites an existing success or current Version."""
    with connect() as c:
        source, run = locked_run(c, run_id)
        fenced(source, run, epoch)
        observation_id = None
        if identity and identity.get("namespace") and identity.get("external_key"):
            frontier = one(c, "SELECT * FROM wlh.frontier WHERE source_id=%s", (source["id"],))
            r: dict[str, Any] = resource(
                c,
                frontier["id"],
                identity["namespace"],
                identity["external_key"],
                identity.get("public_url"),
                identity.get("recheck_locator") or {"url": identity.get("public_url")},
            )
            key = run_id + ":" + r["id"]
            existing: dict[str, Any] | None = c.execute(
                "SELECT id FROM wlh.observation WHERE delivery_key=%s", (key,)
            ).fetchone()
            if existing:
                observation_id = existing["id"]
            else:
                observation_id = uid()
                c.execute(
                    """INSERT INTO wlh.observation(id,resource_id,run_id,attempt_epoch,
                    delivery_key,outcome,change_reason,error) VALUES(%s,%s,%s,%s,%s,'failed',
                    'failed',%s)""",
                    (observation_id, r["id"], run_id, epoch, key, error),
                )
                ids: list[str] = [
                    row["id"]
                    for row in c.execute(
                        "SELECT id FROM wlh.acquisition WHERE id=ANY(%s) AND source_id=%s",
                        (list(acquisition_ids), source["id"]),
                    )
                ]
                evidence(c, observation_id, ids, "input")
                schedule(c, run, r["id"], "failed", False)
        c.execute("UPDATE wlh.run SET errors=errors || %s WHERE id=%s", (j([error]), run_id))
        finish_target(c, target_id, "failed", observation_id, error, epoch)
        return observation_id


def save_checkpoint(run_id: str, epoch: int, checkpoint: Mapping[str, Any]) -> None:
    with connect() as c:
        source, run = locked_run(c, run_id)
        fenced(source, run, epoch)
        c.execute(
            "UPDATE wlh.frontier SET checkpoint=%s,updated_at=now() WHERE source_id=%s AND run_id=%s",
            (j(checkpoint), source["id"], run_id),
        )
