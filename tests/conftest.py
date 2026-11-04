"""Black-box test fixtures for WorldLawHarvester (wlh).

All tests run against real PostgreSQL, real S3 CAS (MinIO), real local HTTP servers,
and real local extraction binaries (PyMuPDF, LibreOffice, Tesseract) — zero mocks.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
ROOT = ROOT_DIR


def _is_valid_fixtures_dir(path: Path) -> bool:
    """Return True if path exists as a directory and contains readable dd-* fixtures."""
    try:
        if not path.is_dir():
            return False
        return any(item.name.startswith("dd-") and item.is_dir() for item in path.iterdir())
    except OSError:
        return False


def _find_source_fixtures_dir() -> Path | None:
    """Locate a valid tests/fixtures directory from parent repo, git common dir, or known paths."""
    # 1. Environment variable override
    env_dir = os.environ.get("WLH_FIXTURES_DIR")
    if env_dir:
        cand = Path(env_dir)
        if _is_valid_fixtures_dir(cand):
            return cand

    # 2. Git common dir (resolves parent git repo root in git worktrees)
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            common_git = Path(proc.stdout.strip())
            if not common_git.is_absolute():
                common_git = (ROOT_DIR / common_git).resolve()
            cand = common_git.parent / "tests" / "fixtures"
            if _is_valid_fixtures_dir(cand):
                return cand
    except Exception:
        pass

    # 3. Git worktree list
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.startswith("worktree "):
                    wt_path = Path(line.split(" ", 1)[1].strip())
                    cand = wt_path / "tests" / "fixtures"
                    if _is_valid_fixtures_dir(cand):
                        return cand
    except Exception:
        pass

    # 4. Known local fallback paths
    for cand in (
        Path("/Users/yoryon/WorldLawHarvester/tests/fixtures"),
        ROOT_DIR.parent / "WorldLawHarvester" / "tests" / "fixtures",
        ROOT_DIR.parent / "tests" / "fixtures",
    ):
        if _is_valid_fixtures_dir(cand):
            return cand

    return None


def ensure_worktree_fixtures() -> None:
    """Auto-heal missing or broken `tests/fixtures` in worktrees via dynamic symlinking."""
    fixtures_dir = ROOT_DIR / "tests" / "fixtures"
    if _is_valid_fixtures_dir(fixtures_dir):
        return

    source_dir = _find_source_fixtures_dir()
    if not source_dir:
        return

    if fixtures_dir.is_symlink() or fixtures_dir.is_file():
        fixtures_dir.unlink()
    elif fixtures_dir.is_dir():
        for item in list(fixtures_dir.iterdir()):
            if item.is_symlink() or item.is_file():
                try:
                    item.unlink()
                except OSError:
                    pass
            elif item.is_dir():
                try:
                    item.rmdir()
                except OSError:
                    pass
        try:
            fixtures_dir.rmdir()
        except OSError:
            pass

    if not fixtures_dir.exists() and not fixtures_dir.is_symlink():
        fixtures_dir.symlink_to(source_dir.resolve(), target_is_directory=True)
    elif fixtures_dir.is_dir():
        for src_item in source_dir.iterdir():
            if src_item.name.startswith("dd-") and src_item.is_dir():
                target_link = fixtures_dir / src_item.name
                if not target_link.exists() and not target_link.is_symlink():
                    try:
                        target_link.symlink_to(src_item.resolve(), target_is_directory=True)
                    except OSError:
                        pass


def pytest_configure(config: pytest.Config) -> None:
    """Pytest startup hook: ensure worktree fixtures exist before test collection."""
    ensure_worktree_fixtures()


def _close_pooled_connections() -> None:
    try:
        from wlh.storage.db import close_pool

        close_pool()
    except Exception:
        pass


_TEST_DB_NAME_RE = re.compile(r"^wlh_test(_.*)?$")
ALLOWED_POSTGRES_DATABASES = frozenset({"postgres", "wlh", "wlh_samples", "wlh_test"})
ALLOWED_S3_BUCKETS = frozenset({"wlh-archive", "wlh-archive-test"})


def ensure_test_db_and_bucket(env: dict[str, str]) -> None:
    """Verify isolated test DB/bucket names, create them if missing, and prune stray stores."""
    from typing import Any
    from urllib.parse import urlsplit, urlunsplit

    import boto3  # type: ignore[import-untyped]
    import psycopg
    from psycopg import sql

    parsed = urlsplit(env["WLH_DATABASE_URL"])
    db_name = parsed.path.lstrip("/")
    if not _TEST_DB_NAME_RE.match(db_name):
        raise RuntimeError(
            f"Refusing to run tests against non-whitelisted PostgreSQL database: {db_name!r}"
        )
    bucket_name = env["WLH_S3_BUCKET"]
    if bucket_name != "wlh-archive-test":
        raise RuntimeError(
            f"Refusing to run tests against non-whitelisted S3 bucket: {bucket_name!r}"
        )

    admin_dsn = urlunsplit((parsed.scheme, parsed.netloc, "/postgres", "", ""))
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        all_dbs = [
            str(r[0])
            for r in conn.execute(
                "SELECT datname FROM pg_database WHERE datistemplate = false"
            ).fetchall()
        ]
        for existing_db in all_dbs:
            if existing_db not in ALLOWED_POSTGRES_DATABASES and not _TEST_DB_NAME_RE.match(
                existing_db
            ):
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(existing_db)
                    )
                )
        if db_name not in all_dbs:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))

    boto3_mod: Any = boto3
    s3_client: Any = boto3_mod.client(
        "s3",
        endpoint_url=env["WLH_S3_ENDPOINT"],
        aws_access_key_id=env["WLH_S3_ACCESS_KEY"],
        aws_secret_access_key=env["WLH_S3_SECRET_KEY"],
        region_name=env["WLH_S3_REGION"],
    )
    existing_buckets = {str(b.get("Name", "")) for b in s3_client.list_buckets().get("Buckets", [])}
    for existing_bucket in sorted(existing_buckets):
        if existing_bucket and existing_bucket not in ALLOWED_S3_BUCKETS:
            paginator = s3_client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=existing_bucket):
                contents = page.get("Contents", [])
                if contents:
                    s3_client.delete_objects(
                        Bucket=existing_bucket,
                        Delete={
                            "Objects": [{"Key": str(o["Key"])} for o in contents],
                            "Quiet": True,
                        },
                    )
            s3_client.delete_bucket(Bucket=existing_bucket)
    if bucket_name not in existing_buckets:
        s3_client.create_bucket(Bucket=bucket_name)


def _ensure_dev_env() -> dict[str, str]:
    """Force environment variables to isolated test PostgreSQL (`wlh_test`) and MinIO bucket (`wlh-archive-test`)."""
    ensure_worktree_fixtures()
    test_db_url = os.environ.get(
        "WLH_TEST_DATABASE_URL", "postgresql://wlh:wlh@127.0.0.1:15432/wlh_test"
    )
    test_bucket = os.environ.get("WLH_TEST_S3_BUCKET", "wlh-archive-test")
    env = {
        "WLH_DATABASE_URL": test_db_url,
        "WLH_S3_ENDPOINT": os.environ.get("WLH_S3_ENDPOINT", "http://127.0.0.1:19000"),
        "WLH_S3_BUCKET": test_bucket,
        "WLH_S3_ACCESS_KEY": os.environ.get("WLH_S3_ACCESS_KEY", "wlh"),
        "WLH_S3_SECRET_KEY": os.environ.get("WLH_S3_SECRET_KEY", "wlhsecret"),
        "WLH_S3_REGION": os.environ.get("WLH_S3_REGION", "us-east-1"),
        "WLH_ALLOW_LOOPBACK": "1",
        "WLH_BR_OCR_URL": os.environ.get("WLH_BR_OCR_URL", "http://127.0.0.1:59999/api/v1"),
    }
    ensure_test_db_and_bucket(env)
    for k, v in env.items():
        os.environ[k] = v
    return env


@pytest.fixture(scope="session", autouse=True)
def wlh_env(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    env = _ensure_dev_env()
    work_dir = tmp_path_factory.mktemp("wlh_work")
    os.environ["WLH_WORK_DIR"] = str(work_dir)
    env["WLH_WORK_DIR"] = str(work_dir)
    return env


_PYRAMID_MARKERS_BY_FILE: dict[str, str] = {
    "test_architecture_contract.py": "contract",
    "test_storage_and_config.py": "contract",
    "test_runbook_rendering.py": "contract",
    "test_runbook_publication.py": "contract",
    "test_runbook_sample_store.py": "contract",
    "test_source_document_qualification.py": "contract",
    "test_benchmark.py": "contract",
    "test_benchmark_completion.py": "contract",
    "test_benchmark_processor_suite.py": "contract",
    "test_document_extraction.py": "extractor",
    "test_html_extraction.py": "extractor",
    "test_office_extraction.py": "extractor",
    "test_delivery_quality.py": "extractor",
    "test_document_titles.py": "replay",
    "test_legal_document_layout.py": "replay",
    "test_legal_html_presentation.py": "replay",
    "test_legal_xml_tables.py": "replay",
    "test_legacy_legal_tables.py": "replay",
    "test_kfs_content_fidelity.py": "replay",
    "test_nta_pdf_body.py": "replay",
    "test_archive_storage.py": "pipeline",
    "test_pipeline_and_api.py": "pipeline",
    "test_collection_concurrency.py": "pipeline",
    "test_session_replay.py": "pipeline",
    "test_source_collection_failures.py": "pipeline",
    "test_japan_pipeline_recovery.py": "pipeline",
    "test_benchmark_cli.py": "pipeline",
    "test_source_adapters.py": "replay",
    "test_fixture_replay.py": "replay",
    "test_bridge_adapters.py": "replay",
    "test_japan_sources.py": "replay",
    "test_japan_court_catalog.py": "replay",
    "test_taiwan_source_delivery.py": "replay",
    "test_e2e_worktrees_acceptance.py": "contract",
}
PYRAMID_MARKERS_BY_FILE = _PYRAMID_MARKERS_BY_FILE


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Tag every test item with its test-pyramid marker (`contract`, `extractor`, `pipeline`, `replay`)."""
    for item in items:
        fname = Path(str(item.fspath)).name
        marker_name = _PYRAMID_MARKERS_BY_FILE.get(fname)
        if marker_name:
            item.add_marker(getattr(pytest.mark, marker_name))


TRUNCATE_TABLES_SQL = """
TRUNCATE TABLE
    wlh.source,
    wlh.decision,
    wlh.run,
    wlh.frontier,
    wlh.acquisition,
    wlh.task,
    wlh.task_input,
    wlh.resource,
    wlh.observation,
    wlh.observation_evidence,
    wlh.delivery_conflict,
    wlh.version,
    wlh.document_row,
    wlh.snapshot,
    wlh.snapshot_source,
    wlh.snapshot_item,
    wlh.snapshot_file,
    wlh.snapshot_publication
RESTART IDENTITY CASCADE;
INSERT INTO wlh.publication (singleton, published_snapshot_id)
VALUES (TRUE, NULL)
ON CONFLICT (singleton) DO UPDATE SET published_snapshot_id = NULL;
DO $$
BEGIN
    IF to_regclass('public.procrastinate_jobs') IS NOT NULL THEN
        TRUNCATE TABLE
            public.procrastinate_jobs,
            public.procrastinate_events,
            public.procrastinate_periodic_defers,
            public.procrastinate_workers
        RESTART IDENTITY CASCADE;
    END IF;
END $$;
"""


@pytest.fixture()
def initialized_db(wlh_env: dict[str, str]) -> Generator[dict[str, str], None, None]:
    """Initialize a clean wlh schema in the isolated test PostgreSQL database before the test via fast TRUNCATE."""
    from urllib.parse import urlsplit

    import psycopg

    db_name = urlsplit(wlh_env["WLH_DATABASE_URL"]).path.lstrip("/")
    if not (
        _TEST_DB_NAME_RE.match(db_name) or db_name.endswith("_test") or db_name.startswith("test_")
    ):
        raise RuntimeError(f"Refusing to reset schema on non-test database: {db_name!r}")

    _close_pooled_connections()
    needs_ddl_init = False
    with psycopg.connect(wlh_env["WLH_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid != pg_backend_pid()"
        )
        conn.execute("DROP SCHEMA IF EXISTS wlh_sandbox CASCADE")
        conn.execute("DROP SCHEMA IF EXISTS wlh_samples_backup CASCADE")
        tbl_cnt_row = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'wlh'"
        ).fetchone()
        if tbl_cnt_row and int(tbl_cnt_row[0]) >= 19:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid != pg_backend_pid()"
            )
            conn.execute(TRUNCATE_TABLES_SQL)
        else:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid != pg_backend_pid()"
            )
            conn.execute("DROP SCHEMA IF EXISTS wlh CASCADE")
            needs_ddl_init = True

    if needs_ddl_init:
        from wlh.storage.db import initialize

        res = initialize()
        assert res["status"] in {"initialized", "already_initialized"}

    try:
        yield wlh_env
    finally:
        _close_pooled_connections()


def make_native_pdf(path: Path, pages_text: list[str]) -> Path:
    """Create a real multi-page PDF with native text layer using PyMuPDF (fitz)."""
    from typing import Any

    import fitz  # type: ignore[import-untyped]

    fitz_mod: Any = fitz
    doc = fitz_mod.open()
    for text in pages_text:
        page = doc.new_page(width=595, height=842)
        page.insert_textbox(
            fitz_mod.Rect(50, 50, 545, 792),
            text,
            fontsize=12,
            fontname="helv",
        )
    doc.save(str(path))
    doc.close()
    return path


def make_scanned_pdf(path: Path, pages_text: list[str]) -> Path:
    """Create a real image-only scanned PDF (0 native chars, ink_ratio > 0.003)."""
    from typing import Any

    import fitz  # type: ignore[import-untyped]

    fitz_mod: Any = fitz
    # First render text onto a temporary PDF, rasterize to pixmap, and insert as image
    temp_doc = fitz_mod.open()
    for text in pages_text:
        p = temp_doc.new_page(width=595, height=842)
        p.insert_textbox(
            fitz_mod.Rect(50, 80, 545, 760),
            text,
            fontsize=18,
            fontname="helv",
        )
    scan_doc = fitz_mod.open()
    for page in temp_doc:
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        scan_page = scan_doc.new_page(width=595, height=842)
        scan_page.insert_image(fitz_mod.Rect(0, 0, 595, 842), stream=img_bytes)
    temp_doc.close()
    scan_doc.save(str(path))
    scan_doc.close()
    return path


def run_wlh_cli(*args: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Execute `python -m wlh` as a black-box subprocess."""
    merged_env = {**os.environ, **(env or {})}
    src_dir = str(ROOT_DIR / "src")
    existing_pythonpath = merged_env.get("PYTHONPATH")
    if existing_pythonpath:
        if src_dir not in existing_pythonpath.split(os.pathsep):
            merged_env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing_pythonpath}"
    else:
        merged_env["PYTHONPATH"] = src_dir
    proc = subprocess.run(
        [sys.executable, "-m", "wlh", *args],
        cwd=str(ROOT_DIR),
        env=merged_env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
