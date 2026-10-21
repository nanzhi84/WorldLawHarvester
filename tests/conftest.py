"""Black-box test fixtures for WorldLawHarvester (wlh).

All tests run against real PostgreSQL, real S3 CAS (MinIO), real local HTTP servers,
and real local extraction binaries (PyMuPDF, LibreOffice, Tesseract) — zero mocks.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent


def _ensure_dev_env() -> dict[str, str]:
    """Ensure environment variables point to local PostgreSQL and MinIO S3 CAS."""
    env = {
        "WLH_DATABASE_URL": os.environ.get(
            "WLH_DATABASE_URL", "postgresql://wlh:wlh@127.0.0.1:15432/wlh"
        ),
        "WLH_S3_ENDPOINT": os.environ.get("WLH_S3_ENDPOINT", "http://127.0.0.1:19000"),
        "WLH_S3_BUCKET": os.environ.get("WLH_S3_BUCKET", "wlh-archive"),
        "WLH_S3_ACCESS_KEY": os.environ.get("WLH_S3_ACCESS_KEY", "wlh"),
        "WLH_S3_SECRET_KEY": os.environ.get("WLH_S3_SECRET_KEY", "wlhsecret"),
        "WLH_S3_REGION": os.environ.get("WLH_S3_REGION", "us-east-1"),
        "WLH_ALLOW_LOOPBACK": "1",
        "WLH_BR_OCR_URL": os.environ.get("WLH_BR_OCR_URL", "http://127.0.0.1:59999/api/v1"),
    }
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


@pytest.fixture()
def initialized_db(wlh_env: dict[str, str]) -> dict[str, str]:
    """Initialize a clean wlh schema in PostgreSQL before the test."""
    import psycopg

    with psycopg.connect(wlh_env["WLH_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS wlh CASCADE")

    from wlh.db import initialize

    res = initialize()
    assert res["status"] in {"initialized", "already_initialized"}
    return wlh_env


def make_native_pdf(path: Path, pages_text: list[str]) -> Path:
    """Create a real multi-page PDF with native text layer using PyMuPDF (fitz)."""
    import fitz

    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page(width=595, height=842)
        page.insert_textbox(
            fitz.Rect(50, 50, 545, 792),
            text,
            fontsize=12,
            fontname="helv",
        )
    doc.save(str(path))
    doc.close()
    return path


def make_scanned_pdf(path: Path, pages_text: list[str]) -> Path:
    """Create a real image-only scanned PDF (0 native chars, ink_ratio > 0.003)."""
    import fitz

    # First render text onto a temporary PDF, rasterize to pixmap, and insert as image
    temp_doc = fitz.open()
    for text in pages_text:
        p = temp_doc.new_page(width=595, height=842)
        p.insert_textbox(
            fitz.Rect(50, 80, 545, 760),
            text,
            fontsize=18,
            fontname="helv",
        )
    scan_doc = fitz.open()
    for page in temp_doc:
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        scan_page = scan_doc.new_page(width=595, height=842)
        scan_page.insert_image(fitz.Rect(0, 0, 595, 842), stream=img_bytes)
    temp_doc.close()
    scan_doc.save(str(path))
    scan_doc.close()
    return path


def run_wlh_cli(*args: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Execute `python -m wlh` as a black-box subprocess."""
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    proc = subprocess.run(
        ["uv", "run", "python", "-m", "wlh", *args],
        cwd=str(ROOT_DIR),
        env=merged_env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
