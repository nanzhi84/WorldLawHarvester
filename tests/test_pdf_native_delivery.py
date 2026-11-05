"""Visible native PDF delivery and visually blank text-layer rejection, via CLI and CAS."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import fitz  # type: ignore[import-untyped]
import psycopg
import pytest
import yaml

from .conftest import run_wlh_cli
from .source_replay_support import ARTIFACTS, collect_artifact, normalized, source_server

pytestmark = pytest.mark.pipeline
SOURCE = "dd-993801"
TITLE = "Public Records Preservation Regulation 2026"
PDF_PATH = "/records-regulation.pdf"


def _original(directory: Path, appearance: str) -> tuple[bytes, list[str]]:
    """Build a finite original with independent source text and visible-page evidence."""
    pdf: Any = fitz
    document = pdf.open()
    paragraphs: list[str] = []
    for page_number in range(2):
        page = document.new_page(width=595, height=842)
        if appearance != "blank":
            for offset in range(8):
                number = page_number * 8 + offset + 1
                paragraph = (
                    f"Article {number}. The authority shall preserve original legal records, "
                    "publish complete annual reports, and provide public access to every "
                    f"statutory instrument. RECORDCLAUSE{number:03d}END"
                )
                paragraphs.append(paragraph)
                remaining = page.insert_textbox(
                    pdf.Rect(50, 60 + offset * 90, 545, 140 + offset * 90),
                    paragraph,
                    fontsize=11,
                    fontname="helv",
                    color=(1, 1, 1) if appearance == "white" else (0, 0, 0),
                    render_mode=3 if appearance == "hidden" else 0,
                )
                assert remaining >= 0, "The original fixture must contain every supplied clause"
        pixels = page.get_pixmap(colorspace=pdf.csGRAY, alpha=False)
        pixels.save(str(directory / f"original-page-{page_number + 1}.png"))
        if appearance == "visible":
            assert min(pixels.samples) < 100, "Visible original unexpectedly rendered blank"
        else:
            assert min(pixels.samples) == 255, "Negative original must be visually blank"
        if appearance in {"visible", "hidden", "white"}:
            assert len(page.get_text().strip()) > 1000, "Text-layer fixture lost its dense body"
    payload: bytes = document.tobytes(no_new_id=True, deflate=True)
    document.close()
    (directory / "original.pdf").write_bytes(payload)
    (directory / "original-evidence.json").write_text(
        json.dumps(
            {
                "scope": "Complete synthetic two-page regulation; no live-source coverage claim",
                "appearance": appearance,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "expected_paragraphs": paragraphs if appearance == "visible" else [],
            },
            indent=2,
        )
    )
    return payload, paragraphs


def _config(directory: Path, origin: str) -> Path:
    config = {
        "id": SOURCE,
        "scheduler": {
            "scope": "Complete finite native-PDF visibility acceptance fixture",
            "entry_urls": [origin + "/index.html"],
            "allowed_hosts": ["127.0.0.1"],
            "allowed_path_prefixes": ["/"],
            "concurrency": 1,
            "budget": {"requests": 5, "documents": 2, "seconds": 60},
        },
        "fetcher": {"delay": 0, "retries": 0, "robots": False},
        "processor": {
            "parser": {
                "type": "html",
                "documents": {"detail_url_patterns": [r"\.pdf$"], "binary_title_from_link": True},
            }
        },
        "cleaners": {"require_complete_text": True},
        "storage": {
            "archive_approved": True,
            "metadata": {
                "source_name": "Native PDF visibility acceptance",
                "document_type": "法律法规",
                "language": "en",
                "jurisdiction": "INTL",
            },
        },
    }
    path = directory / f"{SOURCE}.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    return path


def _persisted(env: dict[str, str]) -> list[tuple[str, str, str]]:
    with psycopg.connect(env["WLH_DATABASE_URL"]) as connection:
        return connection.execute(
            "SELECT d.version_id, d.markdown_hash, d.original_hash FROM wlh.document_row d "
            "JOIN wlh.resource r ON r.current_version_id=d.version_id "
            "JOIN wlh.frontier f ON f.id=r.frontier_id WHERE f.source_id=%s",
            (SOURCE,),
        ).fetchall()


def _collect(
    directory: Path, appearance: str, env: dict[str, str]
) -> tuple[dict[str, Any], list[dict[str, Any]], Path, list[str]]:
    from wlh.storage.cas import Objects

    directory.mkdir(parents=True, exist_ok=True)
    payload, expected = _original(directory, appearance)
    digest = hashlib.sha256(payload).hexdigest()
    catalog = f'<html><body><a href="{PDF_PATH}">{TITLE}</a></body></html>'.encode()
    routes = {"/index.html": (catalog, "text/html"), PDF_PATH: (payload, "application/pdf")}
    with source_server(routes, []) as (origin, requests):
        config = _config(directory, origin)
        report, rows = collect_artifact(
            SOURCE, config, env, "collect", requests, output_dir=directory
        )
        assert PDF_PATH in requests and set(requests) == {"/index.html", PDF_PATH}, report
        with psycopg.connect(env["WLH_DATABASE_URL"]) as connection:
            archived = connection.execute(
                "SELECT archive_ref FROM wlh.acquisition WHERE request_url=%s AND state='archived'",
                (origin + PDF_PATH,),
            ).fetchall()
        assert archived == [(digest,)], "Complete source PDF was not archived"
        assert Objects().get(digest) == payload, "CAS original must match supplied source bytes"
    return report, rows, config, expected


def _reparse(config: Path, env: dict[str, str]) -> tuple[int, dict[str, Any]]:
    code, stdout, stderr = run_wlh_cli("reparse", str(config), "--no-progress", env=env)
    result: dict[str, Any] = json.loads(stdout)
    (config.parent / "reparse.json").write_text(
        json.dumps({"exit_code": code, "result": result, "stderr": stderr}, indent=2)
    )
    assert result["report"]["external_requests"] == 0, result
    return code, result


class TestNativePdfDelivery:
    def test_visible_dense_native_pages_deliver_complete_clauses_and_reparse_archived_original(
        self, initialized_db: dict[str, str]
    ) -> None:
        directory = ARTIFACTS / "pdf-native-visibility" / "visible"
        report, rows, config, expected = _collect(directory, "visible", initialized_db)
        assert report["exit_code"] == 0 and report["result"]["status"] == "complete", report
        assert len(rows) == 1, "The complete original must yield one delivered legal document"
        markdown = str(rows[0]["markdown"])
        body = normalized(markdown)
        positions: list[int] = []
        for paragraph in expected:
            clause = normalized(paragraph)
            assert body.count(clause) == 1, f"Missing or duplicated complete clause: {paragraph}"
            positions.append(body.index(clause))
        assert positions == sorted(positions), "The delivered clauses changed original page order"
        assert rows[0]["title"] == TITLE
        before = _persisted(initialized_db)
        assert len(before) == 1
        code, result = _reparse(config, initialized_db)
        assert code == 0 and result["status"] == "complete", result
        assert _persisted(initialized_db) == before, "Offline reparse changed the delivered version"
        (directory / "checks.json").write_text(
            json.dumps(
                {
                    "complete_clauses": len(expected),
                    "ordered": True,
                    "cas_exact": True,
                    "offline_reparse": True,
                    "version_and_markdown_unchanged": True,
                },
                indent=2,
            )
        )

    @pytest.mark.parametrize("appearance", ["blank", "hidden", "white"])
    def test_visually_blank_pages_with_or_without_dense_text_do_not_deliver_invented_body(
        self, initialized_db: dict[str, str], appearance: str
    ) -> None:
        directory = ARTIFACTS / "pdf-native-visibility" / appearance
        report, rows, config, _ = _collect(directory, appearance, initialized_db)
        assert rows == [], "A visually blank original must not publish its invisible text layer"
        assert report["exit_code"] != 0 and report["result"]["status"] != "complete", report
        assert report["result"].get("errors"), "Rejection must report its extraction failure"
        code, result = _reparse(config, initialized_db)
        assert _persisted(initialized_db) == [], "Offline reparse bypassed the visibility gate"
        assert code != 0 and result["status"] != "complete", result
        (directory / "checks.json").write_text(
            json.dumps(
                {"cas_exact": True, "collect_blocked": True, "offline_reparse_blocked": True},
                indent=2,
            )
        )
