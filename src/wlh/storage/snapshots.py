"""Explicit immutable global captures, serialized operator publication, queries, and overrides."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from os import PathLike
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from wlh.core import WlhError, uid
from wlh.storage.cas import Objects
from wlh.storage.db import DbConn, connect, j, one
from wlh.storage.document_store import verify_document

COLUMNS = (
    "title",
    "markdown_file",
    "doc_type",
    "acquired_at",
    "original_file",
    "volume_order",
    "doc_number",
    "issuer",
    "published_at",
    "effective_at",
    "expired_at",
    "status",
    "source_id",
    "source_name",
    "jurisdiction",
    "detail_url",
    "source_record_hash",
    "markdown_hash",
    "original_hash",
)


def project_canonical(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, (date, datetime)) else value
        for key in COLUMNS
        for value in [row.get(key)]
    }


PROJECTORS = {"canonical": project_canonical}

OUTPUT_MAX_BYTES = 8 * 1024 * 1024
MARKDOWN_MEDIA_TYPE = "text/markdown; charset=utf-8"
MAX_BYTES = 2 * 1024 * 1024
MAX_OVERRIDES = 10000

ROW_QUERY = """SELECT i.source_id,i.resource_id,COALESCE(s.config->'storage'->'metadata'->>'jurisdiction', s.config->'cleaners'->'metadata'->>'jurisdiction', s.config->'cleaners'->'defaults'->>'jurisdiction', s.config->'metadata'->>'jurisdiction') AS jurisdiction,d.*
FROM wlh.snapshot_item i
JOIN wlh.source s ON s.id=i.source_id
JOIN wlh.document_row d ON d.version_id=i.version_id
WHERE i.snapshot_id=%(snapshot)s AND i.source_id = ANY(%(sources)s)
AND (i.source_id COLLATE "C", i.resource_id COLLATE "C", d.row_index)
    > (%(source_after)s COLLATE "C", %(resource_after)s COLLATE "C", %(row_after)s)
ORDER BY i.source_id COLLATE "C", i.resource_id COLLATE "C", d.row_index
LIMIT %(limit)s"""


def uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise WlhError("invalid_version_overrides")
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise WlhError("invalid_version_overrides") from None
    return value


def normalize(overrides: Any) -> list[dict[str, str]]:
    if not isinstance(overrides, list) or len(cast(list[Any], overrides)) > MAX_OVERRIDES:
        raise WlhError("invalid_version_overrides")
    result: dict[str, str] = {}
    for item in cast(list[Any], overrides):
        if not isinstance(item, dict) or set(cast(dict[str, Any], item)) != {"resource_id", "version_id"}:
            raise WlhError("invalid_version_overrides")
        resource_id, version_id = uuid(item["resource_id"]), uuid(item["version_id"])
        if resource_id in result:
            raise WlhError("duplicate_override_resource")
        result[resource_id] = version_id
    return [{"resource_id": r, "version_id": v} for r, v in sorted(result.items())]


def unique_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, value in pairs:
        if key in data:
            raise WlhError("duplicate_override_key")
        data[key] = value
    return data


def load(path: str | PathLike[str]) -> list[dict[str, str]]:
    try:
        with Path(path).open("rb") as file:
            data = file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise WlhError("version_overrides_too_large")
        value = json.loads(data, object_pairs_hook=unique_pairs)
        if not isinstance(value, dict) or set(cast(dict[str, Any], value)) != {"overrides"}:
            raise WlhError("invalid_version_overrides")
        return normalize(value["overrides"])
    except (OSError, ValueError, RecursionError):
        raise WlhError("invalid_version_overrides") from None


def snapshot_rows(
    c: DbConn,
    snapshot_id: str,
    sources: Sequence[str],
    after: tuple[str, str, int] = ("", "", 0),
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Stable row order: source_id, resource_id, row_index (C collation)."""
    return c.execute(
        ROW_QUERY,
        {
            "snapshot": snapshot_id,
            "sources": list(sources),
            "source_after": after[0],
            "resource_after": after[1],
            "row_after": after[2],
            "limit": limit,
        },
    ).fetchall()


def file_entry(
    c: DbConn, snapshot_id: str, sha256: str, sources: Sequence[str]
) -> dict[str, Any] | None:
    """A downloadable file of the published Snapshot within the caller's Sources."""
    return c.execute(
        """SELECT sha256,kind,media_type,file_name,byte_size,source_id FROM wlh.snapshot_file
        WHERE snapshot_id=%s AND sha256=%s AND source_id = ANY(%s)
        ORDER BY kind DESC, source_id COLLATE "C" LIMIT 1""",
        (snapshot_id, sha256, list(sources)),
    ).fetchone()


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
