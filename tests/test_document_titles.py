"""Evidence-backed document titles through complete official directory/PDF inputs."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import psycopg
import yaml

from .source_replay_support import (
    ROOT,
    assert_offline_reparse,
    collect_artifact,
    normalized,
    source_server,
)

CATALOG = "/law/joho-zeikaishaku/shotoku/shinkoku/shinkoku.htm"
PDF = "/law/joho-zeikaishaku/shotoku/shinkoku/0020002-011.pdf"
TITLE = "定期借地権の設定による保証金の経済的利益の課税に係る令和元年分の適正な利率について（情報）"


def _routes() -> dict[str, tuple[bytes, str]]:
    directory = ROOT / "tests/fixtures/dd-520"
    if not (directory / "direct-title-catalog.html").is_file():
        import pytest

        pytest.skip("Private fixture directory tests/fixtures/dd-520 not present")
    return {
        CATALOG: ((directory / "direct-title-catalog.html").read_bytes(), "text/html"),
        PDF: ((directory / "direct-lease-interest.pdf").read_bytes(), "application/pdf"),
    }


def _config(tmp_path: Path, origin: str, *, from_directory: bool) -> Path:
    config: dict[str, Any] = {
        "id": "dd-940", "adapter": "web", "archive_approved": True,
        "entry_urls": [origin + (CATALOG if from_directory else PDF)],
        "allowed_hosts": ["127.0.0.1"], "allowed_path_prefixes": [CATALOG, PDF],
        "scope": "Explicit one-document corpus from full recorded NTA directory; no completeness claim for all NTA documents",
        "robots": False, "delay": 0,
        "fetch": {"mode": "http", "max_depth": 3, "retries": 0},
        "budget": {"requests": 6, "documents": 2, "seconds": 90},
        "metadata": {"source_name": "国税庁", "document_type": "税务资料", "language": "ja"},
        "documents": {
            "detail_url_patterns": [r"\.pdf$"], "require_complete_text": True,
            "binary_title_from_link": True,
        },
    }
    path = tmp_path / "dd-940.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    return path


class TestEvidenceBackedDocumentTitles:
    def test_official_directory_title_survives_complete_pdf_delivery_and_offline_reparse(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """The title is a verified official label, not a numeric download filename."""
        name = "japan-direct-pdf-title"
        routes = _routes()
        with source_server(routes, ["www.nta.go.jp"]) as (origin, requests):
            config = _config(tmp_path, origin, from_directory=True)
            report, rows = collect_artifact("dd-940", config, initialized_db, name, requests)
            assert CATALOG in requests and PDF in requests
            assert len(rows) == 1, report
            row = rows[0]
            assert normalized(TITLE) in normalized(row["title"]), row["title"]
            assert "0020002-011" not in row["title"]
            assert row["detail_url"].endswith(PDF)
            assert row["original_hash"] == hashlib.sha256(routes[PDF][0]).hexdigest()
            for text in (
                "両建の経理の場合の適正な利率は、平均的な長期借入利率によるべきであるが、0.01％としても差し支えない。",
                "令和元年分については、0.01％となる。",
            ):
                assert normalized(text) in normalized(row["markdown"])
            assert row["effective_at"] is None
            assert report["result"]["status"] == "complete", report
        assert_offline_reparse(config, initialized_db, name)
        with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as connection:
            replayed = connection.execute(
                "SELECT d.title, d.markdown_hash, d.original_hash FROM wlh.document_row d "
                "JOIN wlh.resource r ON r.current_version_id=d.version_id "
                "JOIN wlh.frontier f ON f.id=r.frontier_id WHERE f.source_id='dd-940'"
            ).fetchall()
        assert replayed == [(row["title"], row["markdown_hash"], row["original_hash"])]

    def test_direct_binary_without_verified_directory_label_is_explicitly_blocked(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """The same complete readable PDF has no label evidence when entered directly."""
        name = "japan-direct-pdf-without-title-evidence"
        with source_server(_routes(), ["www.nta.go.jp"]) as (origin, requests):
            config = _config(tmp_path, origin, from_directory=False)
            report, rows = collect_artifact("dd-940", config, initialized_db, name, requests)
            assert PDF in requests
            assert rows == [], "A numeric filename must not stand in for missing verified title evidence"
            assert report["result"].get("status") != "complete"
            assert "title" in str(report["result"].get("errors", [])).lower(), report
