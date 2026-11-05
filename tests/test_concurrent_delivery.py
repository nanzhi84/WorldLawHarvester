"""Bounded CAS delivery through real CLI, local HTTP, PostgreSQL and object storage."""

from __future__ import annotations

import hashlib
import os
import re
import signal
import time
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import psycopg
import pytest

from .concurrent_delivery_support import (
    SOURCE,
    S3Observer,
    cli_environment,
    delivered,
    finish_cli,
    fixture_documents,
    legal_server,
    receipt,
    source_config,
    start_cli,
    stored_objects,
)
from .source_replay_support import normalized

pytestmark = pytest.mark.pipeline
ARTIFACTS = Path(
    os.environ.get("WLH_CAS_ACCEPTANCE_ARTIFACT_DIR", "artifacts/cas-integration-20261105/latest")
)


def _directory(name: str) -> Path:
    path = ARTIFACTS / "delivery" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _closed_report(
    observed: dict[str, Any], workers: int, *, failed: bool = False
) -> dict[str, Any]:
    result = observed["result"]
    raw_report = result.get("report", {}).get("cas_io")
    assert isinstance(raw_report, dict), (
        "CLI final report must expose cas_io lifecycle and capacity"
    )
    report = cast(dict[str, Any], raw_report)
    assert report["workers"] == workers, report
    assert 0 <= report["max_active"] <= workers, report
    assert report["active"] == report["pending"] == 0 and report["closed"] is True, report
    raw_errors = report["worker_errors"]
    assert isinstance(raw_errors, dict), report
    errors = cast(dict[str, int], raw_errors)
    assert all(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", key) and isinstance(value, int) and value > 0
        for key, value in errors.items()
    ), "Worker errors must expose only exception-type counts"
    assert bool(errors) is failed, report
    return report


def _signature(rows: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    return [(str(row["detail_url"]), str(row["version_id"]), row["markdown_hash"]) for row in rows]


class TestConcurrentDocumentDelivery:
    @pytest.mark.parametrize("workers", [None, "1", "8"], ids=["default-four", "one", "eight"])
    def test_complete_shared_documents_collect_and_reparse_with_bounded_closed_storage(
        self,
        initialized_db: dict[str, str],
        workers: str | None,
    ) -> None:
        directory = _directory(f"complete-{workers or 'default'}")
        routes, expected = fixture_documents(directory)
        original_hashes = {hashlib.sha256(payload).hexdigest() for payload, _ in routes.values()}
        capacity = int(workers or "4")
        with S3Observer(initialized_db["WLH_S3_ENDPOINT"], original_hashes) as proxy:
            env = cli_environment(initialized_db, proxy.endpoint, workers)
            with legal_server(routes) as (origin, requests):
                config = source_config(directory, origin)
                observed = finish_cli(start_cli("collect", config, env), directory, "collect")
                collection_transport = proxy.snapshot()
                receipt(directory, "collect-transport", collection_transport)
                receipt(directory, "source-requests", requests)
                assert observed["exit_code"] == 0 and observed["result"]["status"] == "complete", (
                    observed
                )
                assert set(requests) == set(routes), (
                    "Every finite source original must be requested"
                )
            rows = delivered(initialized_db)
            objects = stored_objects(initialized_db)
            with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as connection:
                archived = dict(
                    connection.execute(
                        "SELECT request_url,archive_ref FROM wlh.acquisition "
                        "WHERE source_id=%s AND state='archived'",
                        (SOURCE,),
                    ).fetchall()
                )
            receipt(directory, "delivered-rows", rows)
            receipt(directory, "archived-originals", archived)
            assert len(rows) == len(expected), (
                "Shared hashes must not collapse distinct business targets"
            )
            assert len({row["resource_id"] for row in rows}) == len(expected)
            assert {urlsplit(str(row["detail_url"])).path for row in rows} == set(expected)
            for row in rows:
                path = urlsplit(str(row["detail_url"])).path
                original = routes[path][0]
                digest = hashlib.sha256(original).hexdigest()
                assert archived[row["detail_url"]] == digest
                assert objects[digest] == original
                if row["original_hash"] is not None:
                    assert row["original_hash"] == digest
                markdown = objects[row["markdown_hash"]].decode()
                body = normalized(markdown)
                positions: list[int] = []
                for clause in expected[path]:
                    text = normalized(clause)
                    assert body.count(text) == 1, (path, clause)
                    positions.append(body.index(text))
                assert positions == sorted(positions), path
                (directory / f"{Path(path).stem}.md").write_text(markdown)
            twins = [
                row
                for row in rows
                if urlsplit(str(row["detail_url"])).path
                in {"/laws/record-00.html", "/laws/record-01.html"}
            ]
            assert len({row["markdown_hash"] for row in twins}) == 1
            before = _signature(rows)
            reparsed = finish_cli(start_cli("reparse", config, env), directory, "offline-reparse")
            receipt(directory, "final-transport", proxy.snapshot())
            assert reparsed["exit_code"] == 0 and reparsed["result"]["status"] == "complete", (
                reparsed
            )
            assert reparsed["result"]["report"]["external_requests"] == 0
            assert _signature(delivered(initialized_db)) == before
            assert collection_transport["max_active"] <= capacity + 1, collection_transport
            for kind in ("original:PUT", "document:PUT"):
                peak = collection_transport["max_by_kind"].get(kind, 0)
                assert peak >= (1 if capacity == 1 else 2), (kind, collection_transport)
            report = _closed_report(observed, capacity)
            assert report["max_active"] >= (1 if capacity == 1 else 2)
            _closed_report(reparsed, capacity)
            receipt(
                directory,
                "assertions",
                {
                    "complete_documents": len(rows),
                    "complete_clauses": 128,
                    "shared_hash_distinct_targets": True,
                    "offline_versions_unchanged": True,
                    "external_requests": 0,
                    "bounded_and_closed": True,
                },
            )

    @pytest.mark.parametrize("workers", ["0", "9", "not-a-number", "1.5"])
    def test_invalid_worker_configuration_is_rejected_before_source_fetch(
        self,
        initialized_db: dict[str, str],
        workers: str,
    ) -> None:
        directory = _directory(f"invalid-{workers}")
        routes, _ = fixture_documents(directory, count=2)
        with legal_server(routes) as (origin, requests):
            config = source_config(directory, origin)
            env = cli_environment(initialized_db, initialized_db["WLH_S3_ENDPOINT"], workers)
            observed = finish_cli(start_cli("collect", config, env), directory, "rejected")
            receipt(directory, "source-requests", requests)
            assert observed["exit_code"] != 0, (
                "Invalid worker capacity must not silently become a default"
            )
            assert "WLH_CAS_WORKERS" in observed["stdout"] + observed["stderr"], observed
            assert "invalid_cas_workers" in observed["stdout"] + observed["stderr"], observed
            assert requests == [], "Invalid capacity must be rejected before source collection"
            assert delivered(initialized_db) == []

    @pytest.mark.parametrize("mode", ["fail_put", "corrupt_get"])
    def test_document_storage_failure_after_archive_blocks_unverified_version_and_reports_error(
        self,
        initialized_db: dict[str, str],
        mode: str,
    ) -> None:
        directory = _directory(mode)
        routes, _ = fixture_documents(directory, count=6)
        hashes = {hashlib.sha256(payload).hexdigest() for payload, _ in routes.values()}
        with S3Observer(initialized_db["WLH_S3_ENDPOINT"], hashes, mode) as proxy:
            with legal_server(routes) as (origin, requests):
                config = source_config(directory, origin)
                env = cli_environment(initialized_db, proxy.endpoint)
                observed = finish_cli(start_cli("collect", config, env), directory, "collect")
            transport = proxy.snapshot()
            receipt(directory, "transport", transport)
            receipt(directory, "source-requests", requests)
            with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as connection:
                archived = connection.execute(
                    "SELECT request_url,archive_ref FROM wlh.acquisition WHERE source_id=%s AND state='archived'",
                    (SOURCE,),
                ).fetchall()
                versions = connection.execute("SELECT count(*) FROM wlh.version").fetchone()
            receipt(
                directory,
                "database",
                {"archived": archived, "versions": versions, "rows": delivered(initialized_db)},
            )
            assert len(archived) >= 2, (
                "Failure must happen after successful source-original archiving"
            )
            assert transport["injected"] > 0, "The test must intercept actual document CAS I/O"
            assert versions == (0,) and delivered(initialized_db) == [], (
                "Unverified documents must not commit any version"
            )
            assert observed["exit_code"] != 0 and observed["result"]["status"] != "complete", (
                observed
            )
            assert observed["result"].get("errors"), observed
            _closed_report(observed, 4, failed=True)

    def test_interrupt_with_multiple_active_document_writes_prevents_late_commit_and_drains(
        self,
        initialized_db: dict[str, str],
    ) -> None:
        directory = _directory("interrupt")
        routes, _ = fixture_documents(directory, count=10)
        hashes = {hashlib.sha256(payload).hexdigest() for payload, _ in routes.values()}
        with S3Observer(initialized_db["WLH_S3_ENDPOINT"], hashes, "hold") as proxy:
            with legal_server(routes) as (origin, _requests):
                config = source_config(directory, origin)
                env = cli_environment(initialized_db, proxy.endpoint)
                process = start_cli("collect", config, env)
                try:
                    concurrent_at_cancel = proxy.wait_for_documents(2)
                    receipt(directory, "at-interrupt", proxy.snapshot())
                    start = time.monotonic()
                    os.killpg(process.pid, signal.SIGINT)
                    time.sleep(0.35)
                    proxy.release.set()
                    observed = finish_cli(process, directory, "interrupted", timeout=20)
                    elapsed = time.monotonic() - start
                finally:
                    proxy.release.set()
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.communicate(timeout=5)
            deadline = time.monotonic() + 5
            while proxy.snapshot()["active"] and time.monotonic() < deadline:
                time.sleep(0.025)
            rows = delivered(initialized_db)
            with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as connection:
                versions = connection.execute("SELECT count(*) FROM wlh.version").fetchone()
                runs = connection.execute(
                    "SELECT status FROM wlh.run WHERE source_id=%s", (SOURCE,)
                ).fetchall()
            receipt(
                directory,
                "after-interrupt",
                {
                    "transport": proxy.snapshot(),
                    "elapsed_seconds": elapsed,
                    "versions": versions,
                    "rows": rows,
                    "run_states": runs,
                    "multiple_active_at_cancel": concurrent_at_cancel,
                },
            )
            assert concurrent_at_cancel, (
                "Cancellation must be exercised while multiple document writes are active"
            )
            assert observed["exit_code"] != 0 and elapsed < 20
            assert versions == (0,) and rows == [], (
                "Late storage completions must not publish cancelled versions"
            )
            assert runs and all(row[0] not in {"running", "queued"} for row in runs), runs
            assert proxy.snapshot()["active"] == 0, "Storage requests must drain after cancellation"
