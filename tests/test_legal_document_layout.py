"""Legal-document layout boundaries from independently reviewed complete originals."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .source_replay_support import (
    ROOT,
    assert_offline_reparse,
    collect_artifact,
    normalized,
    source_server,
)


def _source(path: Path, origin: str, source_id: str, url_path: str, language: str) -> Path:
    config: dict[str, Any] = {
        "id": source_id,
        "scheduler": {
            "scope": "Isolated official PDF layout acceptance; excerpt boundaries explicitly documented in fixture provenance",
            "entry_urls": [origin + url_path],
            "allowed_hosts": ["127.0.0.1"],
            "allowed_path_prefixes": ["/"],
            "max_depth": 1,
            "budget": {"requests": 4, "documents": 2, "seconds": 90, "response_bytes": 10485760},
        },
        "fetcher": {"mode": "http", "delay": 0, "retries": 0, "robots": False},
        "processor": {
            "parser": {
                "type": "html",
                "documents": {"detail_url_patterns": [r"\.pdf$"], "require_complete_text": True},
            }
        },
        "cleaners": {"require_complete_text": True},
        "storage": {
            "archive_approved": True,
            "metadata": {
                "source_name": "Official legal document layout fixture",
                "document_type": "税务资料",
                "language": language,
            },
        },
    }
    file = path / f"{source_id}.yaml"
    file.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    return file


class TestLegalDocumentLayout:
    def test_merged_tax_table_cells_keep_their_column_meaning_or_are_explicitly_blocked(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """Full 15-page official explanation: p2 no-tax cells span two columns.

        A statement spanning 納税主体/課税関係 must not be rendered solely as a
        taxpayer value with an empty tax-treatment column. All text being present
        does not make that altered relationship correct.
        """
        name = "japan-pdf-merged-tax-table"
        url_path = "/law/joho-zeikaishaku/sozoku/r08/pdf/01.pdf"
        fixture_pdf = ROOT / "tests/fixtures/dd-520/nta-trust-tax-merged-cell-annex.pdf"
        if not fixture_pdf.is_file():
            import pytest

            pytest.skip("Private fixture dd-520/nta-trust-tax-merged-cell-annex.pdf not present")
        payload = fixture_pdf.read_bytes()
        with source_server({url_path: (payload, "application/pdf")}, ["www.nta.go.jp"]) as (origin, requests):
            config = _source(tmp_path, origin, "dd-937", url_path, "ja")
            report, rows = collect_artifact("dd-937", config, initialized_db, name, requests)
            assert url_path in requests
            if not rows:
                assert "pdf_layout_unverified" in str(report["result"].get("errors", [])), report
                assert report["result"].get("status") != "complete"
                return
            assert len(rows) == 1
            table = [[normalized(cell) for cell in line.strip().strip("|").split("|")]
                     for line in rows[0]["markdown"].splitlines() if line.count("|") == 4]
            for label in ("旧公益信託設定時", "受託者変更時"):
                assert any(normalized(label) in cells[0] and normalized("課税関係なし") in cells[2]
                           for cells in table), "Merged no-tax statement must remain a tax-treatment statement"
            assert any(normalized("信託財産の給付時") in cells[0] and normalized("受給者") == cells[1]
                       and normalized("贈与税課税") in cells[2] for cells in table)
            assert report["result"]["status"] == "complete", report
        assert_offline_reparse(config, initialized_db, name)

    def test_complete_text_pdf_with_unrendered_substantive_form_images_is_explicitly_blocked(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """Complete 30-page tax explanation includes four form images on pp23–24.

        The images hold table headings, formulas and field relationships; the
        native layer on p23 contains mainly values entered into those forms.
        The whole document must fail if embedded images are neither archived as
        deliverable assets nor accurately rendered in its Markdown.
        """
        name = "japan-pdf-substantive-form-images"
        url_path = "/law/joho-zeikaishaku/sozoku/pdf/0024006-159.pdf"
        fixture_pdf = ROOT / "tests/fixtures/dd-520/tax-forest-explanation-with-form-images.pdf"
        if not fixture_pdf.is_file():
            import pytest

            pytest.skip("Private fixture dd-520/tax-forest-explanation-with-form-images.pdf not present")
        payload = fixture_pdf.read_bytes()
        with source_server({url_path: (payload, "application/pdf")}, ["www.nta.go.jp"]) as (origin, requests):
            config = _source(tmp_path, origin, "dd-941", url_path, "ja")
            report, rows = collect_artifact("dd-941", config, initialized_db, name, requests)
            assert url_path in requests
            assert rows == [], "Text-only Markdown silently discarded substantive form images"
            assert "pdf_images_unverified" in str(report), report
            assert report["result"].get("status") != "complete"

    def test_glossary_terms_keep_their_definitions_or_are_explicitly_blocked(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """Official six-page explanation has three dot-leader term/definition rows.

        Flattening all terms before all definitions destroys the mapping even though
        every text fragment remains. A row-preserving table or explicit block is required.
        """
        name = "japan-glossary-definition-layout"
        url_path = "/law/joho-zeikaishaku/shotoku/shinkoku/251219/pdf/01.pdf"
        fixture_pdf = ROOT / "tests/fixtures/dd-520/nta-joho-detail-attachment.pdf"
        if not fixture_pdf.is_file():
            import pytest

            pytest.skip("Private fixture dd-520/nta-joho-detail-attachment.pdf not present")
        payload = fixture_pdf.read_bytes()
        with source_server({url_path: (payload, "application/pdf")}, ["www.nta.go.jp"]) as (origin, requests):
            config = _source(tmp_path, origin, "dd-942", url_path, "ja")
            report, rows = collect_artifact("dd-942", config, initialized_db, name, requests)
            assert url_path in requests
            if not rows:
                assert "pdf_layout_unverified" in str(report), report
                assert report["result"].get("status") != "complete"
                return
            assert len(rows) == 1
            table = [normalized(line) for line in rows[0]["markdown"].splitlines() if line.count("|") >= 3]
            for term, definition in (("措置法", "租税特別措置法昭和32年法律第26号"),
                                     ("復興財確法", "東日本大震災からの復興のための施策を実施するために必要な財源の確保")):
                assert any(normalized(term) in line and normalized(definition) in line for line in table), term
            assert report["result"]["status"] == "complete", report
        assert_offline_reparse(config, initialized_db, name)
