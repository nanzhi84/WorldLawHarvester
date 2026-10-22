"""Explicit immutable global captures and serialized operator publication."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import UUID

from wlh.core import WlhError, uid
from wlh.db import DbConn, connect, j, one
from wlh.domain.document import verify_document
from wlh.domain.snapshot_overrides import normalize
from wlh.engine.objects import Objects

OUTPUT_MAX_BYTES = 8 * 1024 * 1024
MARKDOWN_MEDIA_TYPE = "text/markdown; charset=utf-8"


def identity(value: str) -> str:
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise WlhError("invalid_snapshot_id") from None
    return value


def bounded[T](value: T) -> T:
    if len(json.dumps(value, ensure_ascii=False, default=str).encode()) > OUTPUT_MAX_BYTES:
        raise WlhError("snapshot_output_too_large")
    return value


def header(c: DbConn, snapshot_id: str) -> dict[str, Any]:
    value: dict[str, Any] | None = c.execute(
        "SELECT * FROM wlh.snapshot WHERE id=%s AND sealed", (identity(snapshot_id),)
    ).fetchone()
    if value is None:
        raise WlhError("snapshot_not_found")
    return value


def published(c: DbConn) -> str | None:
    return one(c, "SELECT published_snapshot_id FROM wlh.publication WHERE singleton")[
        "published_snapshot_id"
    ]


def coverage(c: DbConn, source_id: str) -> dict[str, Any]:
    """Frozen coverage description; an unknown denominator is reported as UNKNOWN."""
    run: dict[str, Any] | None = c.execute(
        """SELECT id,mode,status,report,completed_at FROM wlh.run WHERE source_id=%s
        AND status IN ('complete','failed') ORDER BY completed_at DESC NULLS LAST LIMIT 1""",
        (source_id,),
    ).fetchone()
    if run is None:
        return {"latest_run_id": None, "site_population": "UNKNOWN", "known_gaps": ["never_run"]}
    report: Mapping[str, Any] = run["report"] or {}
    counts: Mapping[str, Any] = report.get("counts") or {}
    gaps: list[str] = sorted(
        {
            *(report.get("errors") or []),
            *(cast(dict[str, Any], report.get("coverage") or {}).get("known_gaps") or []),
            *([report["truncated"]] if report.get("truncated") else []),
        }
    )
    return {
        "latest_run_id": run["id"],
        "mode": run["mode"],
        "status": run["status"],
        "completed_at": run["completed_at"].isoformat() if run["completed_at"] else None,
        "selected_scope_enumeration": cast(dict[str, Any], report.get("coverage") or {}).get(
            "selected_scope_enumeration", "incomplete"
        ),
        "site_population": "UNKNOWN",
        "counts": {k: counts.get(k) for k in sorted(counts)},
        "targets": report.get("targets") or {},
        "known_gaps": gaps[:200],
    }


def capture(
    overrides: Sequence[Mapping[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Freeze relational IDs in one MVCC view, then release the transaction for CAS I/O."""
    with connect() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        sources: list[dict[str, Any]] = c.execute(
            "SELECT s.id,f.id AS frontier_id FROM wlh.source s JOIN wlh.frontier f ON f.source_id=s.id"
        ).fetchall()
        for source in sources:
            source["coverage"] = coverage(c, source["id"])
        for item in overrides:
            if not c.execute(
                "SELECT 1 FROM wlh.version WHERE id=%s AND resource_id=%s",
                (item["version_id"], item["resource_id"]),
            ).fetchone():
                raise WlhError("invalid_override_version")
        selected: list[dict[str, Any]] = c.execute(
            """SELECT f.source_id,f.id AS frontier_id,r.id AS resource_id,v.id AS version_id,v.result_ref
            FROM wlh.frontier f JOIN wlh.resource r ON r.frontier_id=f.id
            LEFT JOIN jsonb_to_recordset(%s::jsonb) AS o(resource_id text,version_id text) ON o.resource_id=r.id
            JOIN wlh.version v ON v.id=COALESCE(o.version_id,r.current_version_id)
            AND v.resource_id=r.id""",
            (j(overrides),),
        ).fetchall()
        rows: list[dict[str, Any]] = c.execute(
            "SELECT * FROM wlh.document_row WHERE version_id=ANY(%s)",
            ([i["version_id"] for i in selected],),
        ).fetchall()
    return sources, selected, rows


def create(overrides: Any = None) -> dict[str, Any]:
    overrides = normalize([] if overrides is None else overrides)
    snapshot_id, objects = uid(), Objects()
    sources, selected, rows = capture(overrides)
    sizes: dict[str, int] = {}
    entries: dict[tuple[str, str, str], tuple[str, str]] = {}
    deadline = time.monotonic() + 300
    for item in selected:
        verify_document(objects, item["result_ref"], deadline=deadline, sizes=sizes)
    owners = {i["version_id"]: i["source_id"] for i in selected}
    for row in rows:
        for kind, media_type, name, sha in (
            ("markdown", MARKDOWN_MEDIA_TYPE, row["markdown_file"], row["markdown_hash"]),
            ("original", row["original_media_type"], row["original_file"], row["original_hash"]),
        ):
            if sha:
                if sha not in sizes:
                    sizes[sha] = len(objects.get(sha, deadline=deadline))
                entries.setdefault((owners[row["version_id"]], sha, kind), (media_type, name))
    # Immutable versions keep this frozen selection valid even if collection advances.
    with connect() as c:
        c.execute("INSERT INTO wlh.snapshot(id) VALUES(%s)", (snapshot_id,))
        for source in sources:
            c.execute(
                """INSERT INTO wlh.snapshot_source(snapshot_id,source_id,frontier_id,coverage)
                      VALUES(%s,%s,%s,%s)""",
                (snapshot_id, source["id"], source["frontier_id"], j(source["coverage"])),
            )
        for item in selected:
            c.execute(
                """INSERT INTO wlh.snapshot_item(snapshot_id,source_id,frontier_id,resource_id,version_id)
                      VALUES(%s,%s,%s,%s,%s)""",
                (
                    snapshot_id,
                    item["source_id"],
                    item["frontier_id"],
                    item["resource_id"],
                    item["version_id"],
                ),
            )
        for (source_id, sha, kind), (media_type, name) in entries.items():
            c.execute(
                """INSERT INTO wlh.snapshot_file(snapshot_id,source_id,sha256,kind,media_type,file_name,byte_size)
                      VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (snapshot_id, source_id, sha, kind, media_type, name, sizes[sha]),
            )
        row: dict[str, Any] = one(
            c,
            """UPDATE wlh.snapshot SET sealed=true,source_count=%s,resource_count=%s,
                   version_count=%s,row_count=%s,file_count=%s WHERE id=%s RETURNING *""",
            (len(sources), len(selected), len(owners), len(rows), len(entries), snapshot_id),
        )
        pointer = published(c)
    return dict(row, snapshot_id=snapshot_id, published_snapshot_id=pointer)


def pagination(limit: int, after: str) -> None:
    if not 1 <= limit <= 100 or len(after) > 1000:
        raise WlhError("invalid_arguments")


def listing(limit: int = 20, after: str = "") -> dict[str, Any]:
    pagination(limit, after)
    with connect() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        rows: list[dict[str, Any]] = c.execute(
            """SELECT * FROM wlh.snapshot WHERE sealed AND id COLLATE "C">%s COLLATE "C"
            ORDER BY id COLLATE "C" LIMIT %s""",
            (after, limit + 1),
        ).fetchall()
        pointer = published(c)
    return bounded(
        {
            "snapshots": rows[:limit],
            "next_after": rows[limit - 1]["id"] if len(rows) > limit else None,
            "published_snapshot_id": pointer,
        }
    )


def inspect(
    snapshot_id: str, limit: int = 20, after: str = "", source_after: str = ""
) -> dict[str, Any]:
    pagination(limit, after)
    pagination(limit, source_after)
    with connect() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        row = header(c, snapshot_id)
        items: list[dict[str, Any]] = c.execute(
            """SELECT source_id,resource_id,version_id FROM wlh.snapshot_item
            WHERE snapshot_id=%s AND resource_id COLLATE "C">%s COLLATE "C"
            ORDER BY resource_id COLLATE "C" LIMIT %s""",
            (snapshot_id, after, limit + 1),
        ).fetchall()
        sources: list[dict[str, Any]] = c.execute(
            """SELECT source_id,coverage FROM wlh.snapshot_source
            WHERE snapshot_id=%s AND source_id COLLATE "C">%s COLLATE "C"
            ORDER BY source_id COLLATE "C" LIMIT %s""",
            (snapshot_id, source_after, limit + 1),
        ).fetchall()
        pointer = published(c)
    return bounded(
        dict(
            row,
            snapshot_id=snapshot_id,
            published_snapshot_id=pointer,
            items=items[:limit],
            sources=sources[:limit],
            next_after=items[limit - 1]["resource_id"] if len(items) > limit else None,
            source_next_after=sources[limit - 1]["source_id"] if len(sources) > limit else None,
        )
    )


def verify(snapshot_id: str) -> None:
    """Read sealed immutable references, release SQL, then verify their complete CAS closure."""
    objects = Objects()
    sizes: dict[str, int] = {}
    with connect() as c:
        header(c, snapshot_id)
        documents: list[dict[str, Any]] = c.execute(
            """SELECT v.result_ref FROM wlh.snapshot_item i JOIN wlh.version v ON v.id=i.version_id
            WHERE i.snapshot_id=%s""",
            (snapshot_id,),
        ).fetchall()
        files: list[dict[str, Any]] = c.execute(
            "SELECT DISTINCT sha256 FROM wlh.snapshot_file WHERE snapshot_id=%s", (snapshot_id,)
        ).fetchall()
    for row in documents:
        verify_document(objects, row["result_ref"], deadline=time.monotonic() + 120, sizes=sizes)
    for row in files:
        if row["sha256"] not in sizes:
            objects.get(row["sha256"], deadline=time.monotonic() + 120)


def publish(snapshot_id: str) -> dict[str, Any]:
    verify(identity(snapshot_id))
    with connect() as c:
        pointer = one(c, "SELECT * FROM wlh.publication WHERE singleton FOR UPDATE")
        before = pointer["published_snapshot_id"]
        changed = before != snapshot_id
        if changed:
            c.execute(
                "UPDATE wlh.publication SET published_snapshot_id=%s WHERE singleton",
                (snapshot_id,),
            )
            record: dict[str, Any] = one(
                c,
                """INSERT INTO wlh.snapshot_publication(before_snapshot_id,after_snapshot_id)
                VALUES(%s,%s) RETURNING sequence,published_at""",
                (before, snapshot_id),
            )
        else:
            record = one(
                c,
                "SELECT sequence,published_at FROM wlh.snapshot_publication ORDER BY sequence DESC LIMIT 1",
            )
    return dict(
        record,
        before_snapshot_id=before,
        published_snapshot_id=snapshot_id,
        changed=changed,
    )
