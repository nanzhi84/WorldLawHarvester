from __future__ import annotations

import os
import pwd
import threading
from collections.abc import Generator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timezone
from typing import Any, LiteralString, cast
from urllib.parse import urlsplit

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.sql import SQL
from psycopg.types.json import Jsonb

from wlh.core import WlhError, uid
from wlh.engine.run_state import eligibility_error

j = Jsonb
DbConn = psycopg.Connection[dict[str, Any]]
_POOL_LOCK = threading.Lock()
_CONN_POOL: dict[tuple[int, str], list[DbConn]] = {}


def dsn() -> str:
    url = os.environ.get("WLH_DATABASE_URL", "postgresql://wlh:wlh@127.0.0.1:15432/wlh")
    if "PYTEST_CURRENT_TEST" in os.environ:
        db_name = urlsplit(url).path.lstrip("/")
        if not (db_name.endswith("_test") or db_name.startswith("test_")):
            raise WlhError("test_database_isolation_violation")
    return url


def _acquire_conn(url: str) -> DbConn:
    pool_key = (os.getpid(), url)
    with _POOL_LOCK:
        bucket = _CONN_POOL.get(pool_key)
        while bucket:
            conn = bucket.pop()
            if (
                not conn.closed
                and not conn.autocommit
                and conn.info.transaction_status == TransactionStatus.IDLE
            ):
                return conn
            try:
                conn.close()
            except Exception:
                pass
    return psycopg.Connection[dict[str, Any]].connect(
        url,
        row_factory=dict_row,
        connect_timeout=5,
        options="-c statement_timeout=15000 -c lock_timeout=5000",
    )


def _release_conn(url: str, conn: DbConn, *, ok: bool) -> None:
    if conn.closed:
        return
    if conn.autocommit:
        try:
            conn.close()
        except Exception:
            pass
        return
    try:
        if ok:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return
    if conn.info.transaction_status != TransactionStatus.IDLE:
        try:
            conn.close()
        except Exception:
            pass
        return
    pool_key = (os.getpid(), url)
    with _POOL_LOCK:
        bucket = _CONN_POOL.setdefault(pool_key, [])
        if len(bucket) < 4:
            bucket.append(conn)
            return
    try:
        conn.close()
    except Exception:
        pass


@contextmanager
def _connect_ctx(url: str) -> Generator[DbConn, None, None]:
    connection = _acquire_conn(url)
    ok = False
    try:
        yield connection
        ok = True
    finally:
        _release_conn(url, connection, ok=ok)


def connect() -> AbstractContextManager[DbConn]:
    return _connect_ctx(dsn())


def one(
    connection: DbConn, query: Any, params: Sequence[Any] | Mapping[str, Any] = ()
) -> dict[str, Any]:
    value = connection.execute(query, params).fetchone()
    if value is None:
        raise WlhError("row_not_found")
    return value


def decision(
    connection: DbConn,
    source: str | None,
    kind: str,
    payload: Any,
    key: str | None = None,
) -> str:
    identity = uid()
    value = connection.execute(
        "INSERT INTO wlh.decision(id, source_id, kind, actor, payload, idempotency_key) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(idempotency_key) DO NOTHING RETURNING id",
        (identity, source, kind, pwd.getpwuid(os.getuid()).pw_name, j(payload), key),
    ).fetchone()
    return str(
        value["id"]
        if value
        else one(connection, "SELECT id FROM wlh.decision WHERE idempotency_key=%s", (key,))["id"]
    )


def locked_run(connection: DbConn, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    # Every current-state writer uses Source -> Run -> Document lock order.
    source_id = one(connection, "SELECT source_id FROM wlh.run WHERE id=%s", (run_id,))["source_id"]
    source = one(connection, "SELECT * FROM wlh.source WHERE id=%s FOR UPDATE", (source_id,))
    run = one(connection, "SELECT * FROM wlh.run WHERE id=%s FOR UPDATE", (run_id,))
    return source, run


def fenced(source: Mapping[str, Any], run: Mapping[str, Any], epoch: int) -> None:
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


def record_error(run_id: str, epoch: int, code: str) -> None:
    with connect() as connection:
        connection.execute(
            "UPDATE wlh.run SET errors=errors || %s WHERE id=%s AND attempt_epoch=%s AND status IN ('running','finishing')",
            (j([code]), run_id, epoch),
        )


def initialize() -> dict[str, Any]:
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


def require_current(connection: DbConn | None = None) -> None:
    from importlib.resources import files

    from wlh.core import digest

    if connection is None:
        with connect() as c:
            return require_current(c)
    reg_row = connection.execute("SELECT to_regclass('wlh.migration') AS name").fetchone()
    if not reg_row or not reg_row["name"]:
        raise WlhError("database_not_initialized")
    row = connection.execute("SELECT version,checksum FROM wlh.migration").fetchone()
    checksum = digest(files("wlh").joinpath("sql/schema.sql").read_bytes())
    if not row or row["version"] != 1 or row["checksum"] != checksum:
        raise WlhError("schema_version_mismatch")
