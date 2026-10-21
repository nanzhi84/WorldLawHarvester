import os
import pwd
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, LiteralString, cast

import psycopg
from psycopg.rows import dict_row
from psycopg.sql import SQL
from psycopg.types.json import Jsonb

from wlh.core import WlhError, uid
from wlh.engine.run_state import eligibility_error

j = Jsonb


def dsn():
    return os.environ.get("WLH_DATABASE_URL", "postgresql://wlh:wlh@127.0.0.1:15432/wlh")


@contextmanager
def connect() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with psycopg.Connection[dict[str, Any]].connect(
        dsn(),
        row_factory=dict_row,
        connect_timeout=5,
        options="-c statement_timeout=15000 -c lock_timeout=5000",
    ) as connection:
        yield connection


def one(connection, query, params=()) -> dict[str, Any]:
    value = connection.execute(query, params).fetchone()
    if value is None:
        raise WlhError("row_not_found")
    return value


def decision(connection, source, kind, payload, key=None):
    identity = uid()
    value = connection.execute(
        "INSERT INTO wlh.decision(id, source_id, kind, actor, payload, idempotency_key) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(idempotency_key) DO NOTHING RETURNING id",
        (identity, source, kind, pwd.getpwuid(os.getuid()).pw_name, j(payload), key),
    ).fetchone()
    return (
        value["id"]
        if value
        else one(connection, "SELECT id FROM wlh.decision WHERE idempotency_key=%s", (key,))["id"]
    )


def locked_run(connection, run_id):
    # Every current-state writer uses Source -> Run -> Document lock order.
    source_id = one(connection, "SELECT source_id FROM wlh.run WHERE id=%s", (run_id,))["source_id"]
    source = one(connection, "SELECT * FROM wlh.source WHERE id=%s FOR UPDATE", (source_id,))
    run = one(connection, "SELECT * FROM wlh.run WHERE id=%s FOR UPDATE", (run_id,))
    return source, run


def fenced(source, run, epoch):
    if run["attempt_epoch"] != epoch:
        raise WlhError("stale_attempt")
    if run["deadline"] <= datetime.now(timezone.utc):
        raise WlhError("deadline_exceeded")
    if run["status"] not in ("running", "finishing"):
        raise WlhError("inactive_attempt")
    code = eligibility_error(source, run)
    if code:
        raise WlhError(code)
    if source["write_seq"] != run["run_seq"]:
        raise WlhError("run_not_started")


def record_error(run_id, epoch, code):
    with connect() as connection:
        connection.execute(
            "UPDATE wlh.run SET errors=errors || %s WHERE id=%s AND attempt_epoch=%s AND status IN ('running','finishing')",
            (j([code]), run_id, epoch),
        )


def initialize():
    """Install one atomic baseline and queue schema; never silently upgrade a database."""
    from importlib.resources import files

    import procrastinate

    from wlh.core import digest

    sql = files("wlh").joinpath("sql/schema.sql").read_text()
    checksum = digest(sql.encode())
    with connect() as c:
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended('wlh:init',0))")
        if one(c, "SELECT to_regclass('wlh.migration') AS name")["name"]:
            require_current(c)
            status = "already_initialized"
        else:
            if c.execute("SELECT 1 FROM pg_namespace WHERE nspname='wlh'").fetchone():
                raise WlhError("schema_incomplete")
            c.execute(SQL(cast(LiteralString, sql)))
            c.execute("INSERT INTO wlh.migration(version,checksum) VALUES(1,%s)", (checksum,))
            status = "initialized"
        if not one(c, "SELECT to_regclass('public.procrastinate_jobs') AS name")["name"]:
            c.execute("SET LOCAL search_path TO public")
            app = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=dsn()))
            c.execute(SQL(app.schema_manager.get_schema()))
    return {"status": status, "schema": "wlh", "version": 1}


def require_current(connection=None):
    from importlib.resources import files

    from wlh.core import digest

    if connection is None:
        with connect() as c:
            return require_current(c)
    if not connection.execute("SELECT to_regclass('wlh.migration') AS name").fetchone()["name"]:
        raise WlhError("database_not_initialized")
    row = connection.execute("SELECT version,checksum FROM wlh.migration").fetchone()
    checksum = digest(files("wlh").joinpath("sql/schema.sql").read_bytes())
    if not row or row["version"] != 1 or row["checksum"] != checksum:
        raise WlhError("schema_version_mismatch")
