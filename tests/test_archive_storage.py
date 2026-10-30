"""Database initialization, test-store isolation and content-addressed archive contracts."""

from __future__ import annotations

import hashlib

import psycopg
import pytest


class TestDatabaseInitialization:
    def test_repeated_init_creates_required_schema_tables_idempotently(
        self, initialized_db: dict[str, str]
    ) -> None:
        """Running database initialization repeatedly succeeds and ensures all `wlh` tables exist."""
        from wlh.storage.db import initialize

        second = initialize()
        assert second["status"] in {"initialized", "already_initialized"}

        expected_tables = {
            "migration",
            "source",
            "frontier",
            "decision",
            "run",
            "acquisition",
            "task",
            "task_input",
            "resource",
            "observation",
            "observation_evidence",
            "delivery_conflict",
            "version",
            "document_row",
            "snapshot",
            "snapshot_source",
            "snapshot_item",
            "snapshot_file",
            "publication",
            "snapshot_publication",
        }
        with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as conn:
            rows = conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'wlh'"
            ).fetchall()
            found = {r[0] for r in rows}
            assert expected_tables.issubset(found), f"Missing tables: {expected_tables - found}"

    def test_pytest_runtime_rejects_non_test_database_and_non_test_s3_bucket(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """During pytest execution, connecting to a non-test database or non-test S3 bucket raises an isolation error."""
        import os
        from urllib.parse import urlsplit

        from wlh.core import WlhError
        from wlh.storage.cas import Objects
        from wlh.storage.db import connect, dsn, initialize

        active_db = urlsplit(os.environ["WLH_DATABASE_URL"]).path.lstrip("/")
        assert active_db.endswith("_test") or active_db.startswith("test_")
        assert active_db not in {"wlh", "wlh_issue4_full"}

        active_bucket = os.environ["WLH_S3_BUCKET"]
        assert active_bucket.endswith(("-test", "_test")) or active_bucket.startswith(
            ("test-", "test_")
        )
        assert active_bucket != "wlh-archive"

        for forbidden_dsn in (
            "postgresql://wlh:wlh@127.0.0.1:15432/wlh",
            "postgresql://wlh:wlh@127.0.0.1:15432/wlh_issue4_full",
        ):
            monkeypatch.setenv("WLH_DATABASE_URL", forbidden_dsn)
            with pytest.raises(WlhError) as exc_dsn:
                dsn()
            assert exc_dsn.value.code == "test_database_isolation_violation"

            with pytest.raises(WlhError) as exc_conn:
                connect()
            assert exc_conn.value.code == "test_database_isolation_violation"

            with pytest.raises(WlhError) as exc_init:
                initialize()
            assert exc_init.value.code == "test_database_isolation_violation"

        monkeypatch.setenv("WLH_S3_BUCKET", "wlh-archive")
        with pytest.raises(WlhError) as exc_obj:
            Objects()
        assert exc_obj.value.code == "test_object_storage_isolation_violation"


class TestContentAddressableStorage:
    def test_valid_payload_returns_sha256_and_reads_back_identical_bytes(
        self, wlh_env: dict[str, str]
    ) -> None:
        """Storing bytes in CAS returns their SHA-256 digest and retrieves the exact payload."""
        from wlh.storage.cas import Objects

        objects = Objects()
        objects.check()

        payload = b"WorldLawHarvester S3 CAS immutability verification payload \xe6\xb3\x95\xe5\xbe\x8b"
        expected_sha = hashlib.sha256(payload).hexdigest()

        returned_sha = objects.put(payload)
        assert returned_sha == expected_sha

        read_back = objects.get(expected_sha)
        assert read_back == payload

    def test_missing_or_malformed_hash_raises_archive_error(
        self, wlh_env: dict[str, str]
    ) -> None:
        """Requesting an unknown or malformed SHA-256 digest raises a deterministic archive error."""
        from wlh.core import WlhError
        from wlh.storage.cas import Objects

        objects = Objects()
        objects.check()

        missing_sha = "f" * 64
        with pytest.raises(WlhError) as exc_info:
            objects.get(missing_sha)
        assert exc_info.value.code == "archive_missing"

        with pytest.raises(WlhError) as exc_info2:
            objects.get("not-a-sha256")
        assert exc_info2.value.code == "archive_invalid"
