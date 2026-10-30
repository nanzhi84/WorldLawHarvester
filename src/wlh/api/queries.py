"""Read-only published-snapshot queries behind GET /results and GET /files/{sha256}."""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import urlencode

from wlh.api.security import ApiError, Caller, Cursor, Settings, Tokens
from wlh.core import WlhError, canonical
from wlh.storage.cas import Objects
from wlh.storage.db import DbConn, connect
from wlh.storage.snapshots import PROJECTORS, file_entry, snapshot_rows


def published(c: DbConn) -> str:
    pointer: dict[str, Any] | None = c.execute(
        "SELECT published_snapshot_id FROM wlh.publication WHERE singleton"
    ).fetchone()
    if pointer is None or pointer["published_snapshot_id"] is None:
        raise ApiError(503, "snapshot_not_published")
    return pointer["published_snapshot_id"]


def results(
    settings: Settings,
    tokens: Tokens,
    caller: Caller,
    source_id: str | None,
    limit: int,
    cursor_value: str | None,
    projection: str = "canonical",
) -> tuple[bytes, str | None]:
    project = PROJECTORS[projection]
    if source_id is not None:
        caller.authorize(source_id)
    sources = [source_id] if source_id is not None else list(caller.sources)
    with connect() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        snapshot = published(c)
        after = ("", "", 0)
        cursor = tokens.decode(cursor_value, Cursor) if cursor_value is not None else None
        if cursor:
            if (
                cursor.projection != projection
                or cursor.caller != caller.id
                or cursor.source != (source_id or "")
                or not c.execute(
                    "SELECT 1 FROM wlh.snapshot WHERE id=%s AND sealed", (cursor.snapshot,)
                ).fetchone()
            ):
                raise ApiError(400, "invalid_cursor")
            # A paginated read stays on the snapshot it started with.
            snapshot = cursor.snapshot
            parts = cursor.after.split("\x1f")
            if len(parts) != 3 or not parts[2].isdigit():
                raise ApiError(400, "invalid_cursor")
            after = (parts[0], parts[1], int(parts[2]))
        rows = snapshot_rows(c, snapshot, sources, after, limit + 1)
    items: list[dict[str, Any]] = []
    size = 2
    for row in rows[:limit]:
        item = project(row)
        size += len(canonical(item)) + 1
        if size > settings.page_max_bytes:
            raise WlhError("archive_too_large")
        items.append(item)
    next_cursor = link = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = tokens.encode(
            Cursor(
                caller=caller.id,
                projection=projection,
                source=source_id or "",
                snapshot=snapshot,
                after="\x1f".join((last["source_id"], last["resource_id"], str(last["row_index"]))),
                issued_at=cursor.issued_at if cursor else int(time.time()),
            )
        )
        query = {"limit": limit, "cursor": next_cursor}
        if source_id is not None:
            query = {"source_id": source_id, **query}
        link = settings.public_base_url + "/results?" + urlencode(query)
    # Template column order is preserved (canonical JSON would sort the keys).
    body: bytes = json.dumps(
        {"snapshot_id": snapshot, "items": items, "next_cursor": next_cursor},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    if len(body) > settings.page_max_bytes:
        raise WlhError("archive_too_large")
    return body, link


def file(
    settings: Settings, caller: Caller, sha256: str, deadline: float
) -> tuple[dict[str, Any], bytes]:
    if not re.fullmatch(r"[0-9a-f]{64}", sha256 or ""):
        raise ApiError(404, "file_not_found")
    with connect() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        snapshot = published(c)
        entry = file_entry(c, snapshot, sha256, caller.sources)
    if entry is None:
        raise ApiError(404, "file_not_found")
    if entry["byte_size"] > settings.file_max_bytes:
        raise WlhError("archive_too_large")
    objects = Objects()
    body = objects.get(sha256, max_bytes=settings.file_max_bytes, deadline=deadline)
    return entry, body
