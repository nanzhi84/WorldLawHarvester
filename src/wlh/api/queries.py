"""Read-only published-snapshot queries behind GET /results and GET /files/{sha256}."""

import json
import re
import time
from urllib.parse import urlencode

from wlh.api.security import ApiError, Cursor
from wlh.core import WlhError, canonical
from wlh.db import connect
from wlh.domain.snapshot_queries import file_entry, snapshot_rows
from wlh.engine.objects import Objects
from wlh.projections import PROJECTORS


def published(c):
    pointer = c.execute(
        "SELECT published_snapshot_id FROM wlh.publication WHERE singleton"
    ).fetchone()
    if pointer is None or pointer["published_snapshot_id"] is None:
        raise ApiError(503, "snapshot_not_published")
    return pointer["published_snapshot_id"]


def results(settings, tokens, caller, source_id, limit, cursor_value, projection="template_16col"):
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
    items, size = [], 2
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
        query = {"limit": limit, "cursor": next_cursor, "projection": projection}
        if source_id is not None:
            query = {"source_id": source_id, **query}
        link = settings.public_base_url + "/results?" + urlencode(query)
    # Template column order is preserved (canonical JSON would sort the keys).
    body = json.dumps(
        {"snapshot_id": snapshot, "items": items, "next_cursor": next_cursor},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    if len(body) > settings.page_max_bytes:
        raise WlhError("archive_too_large")
    return body, link


def file(settings, caller, sha256, deadline):
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
    body = Objects().get(sha256, max_bytes=settings.file_max_bytes, deadline=deadline)
    return entry, body
