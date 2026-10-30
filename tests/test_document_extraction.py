"""PDF native-text classification and OCR fallback contracts."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from .conftest import make_native_pdf, make_scanned_pdf


class TestPdfExtractionAndOcrFallback:
    def test_digital_born_pdf_extracts_text_directly_without_invoking_ocr(
        self, tmp_path: Path
    ) -> None:
        """A digital-born PDF with selectable text is extracted directly without triggering OCR."""
        from wlh.processor.extractors.pdf.scan_detector import analyze, extract_pdf

        native_pdf = make_native_pdf(
            tmp_path / "native.pdf",
            [
                "FEDERAL REGISTER VOL. 91 NO. 10\nPresidential Document 2026-00101 on Open Legal Data.",
                "Section 2. All agencies shall publish machine-readable statutory instruments.",
            ],
        )
        analysis, _ = analyze(native_pdf)
        assert analysis["page_count"] == 2
        assert analysis["suspected_scan_pages"] == []

        ocr_cfg = {
            "engine": "br-ocr-v1",
            "languages": "eng",
            "model": "br-ocr-v1",
            "timeout_seconds": 60,
            "poll_seconds": 0.2,
            "max_pages": 50,
            "dpi": 150,
            "max_page_pixels": 20000000,
            "max_result_bytes": 16777216,
        }
        md_native, info_native, _, parsers_native = extract_pdf(native_pdf, ocr_cfg, "latin")
        assert "Presidential Document 2026-00101" in md_native
        assert info_native["method"] == "native"
        assert info_native["ocr"] is None
        assert "ocr" not in parsers_native

    def test_scanned_pdf_with_unreachable_primary_ocr_falls_back_to_local_ocr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A scanned PDF automatically falls back to local OCR when the remote OCR endpoint is unreachable."""
        from wlh.processor.extractors.pdf.scan_detector import analyze, extract_pdf

        monkeypatch.setenv("WLH_BR_OCR_URL", "http://127.0.0.1:59999/api/v1")
        scanned_pdf = make_scanned_pdf(
            tmp_path / "scanned.pdf",
            [
                "SUPREME COURT OF THE UNITED STATES\nOPINION NO. 25-999\nJudgment Affirmed on October 20, 2026."
            ],
        )
        scan_analysis, _ = analyze(scanned_pdf)
        assert scan_analysis["suspected_scan_pages"] == [1]

        ocr_cfg = {
            "engine": "br-ocr-v1",
            "languages": "eng",
            "model": "br-ocr-v1",
            "timeout_seconds": 60,
            "poll_seconds": 0.2,
            "max_pages": 50,
            "dpi": 150,
            "max_page_pixels": 20000000,
            "max_result_bytes": 16777216,
        }
        t0 = time.monotonic()
        md_scan, info_scan, _, parsers_scan = extract_pdf(scanned_pdf, ocr_cfg, "latin")
        elapsed = time.monotonic() - t0

        assert elapsed < 15.0, f"Fallback took too long: {elapsed:.2f}s"
        assert "SUPREME COURT" in md_scan.upper()
        assert info_scan["method"] == "ocr"
        assert info_scan["ocr"] is not None
        assert info_scan["ocr"]["engine"] == "tesseract"
        assert info_scan["ocr"].get("fallback_from") == "br-ocr-v1"
        assert "ocr" in parsers_scan

    def test_scanned_pdf_with_sparse_signature_text_layer_triggers_ocr_pipeline(
        self, tmp_path: Path
    ) -> None:
        """A scanned PDF with a small digital signature block text layer triggers the OCR pipeline rather than being accepted as pure text."""
        from typing import Any

        import fitz  # type: ignore[import-untyped]

        from wlh.processor.extractors.pdf.scan_detector import analyze, extract_pdf

        fitz_mod: Any = fitz
        temp_doc = fitz_mod.open()
        p = temp_doc.new_page(width=595, height=842)
        p.insert_textbox(
            fitz_mod.Rect(50, 80, 545, 760),
            "OFFICIAL GAZETTE DECREE NO 2026-88\nFull statutory text of the regulation governing maritime security.",
            fontsize=18,
            fontname="helv",
        )
        pix = p.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        temp_doc.close()

        sig_doc = fitz_mod.open()
        p2 = sig_doc.new_page(width=595, height=842)
        p2.insert_image(fitz_mod.Rect(0, 0, 595, 842), stream=img_bytes)
        p2.insert_textbox(
            fitz_mod.Rect(50, 750, 500, 800),
            "Signer: Ministry Office\nSigned at: 2026-10-23 10:00:00\nCertificate ID: 8849-1102",
            fontsize=10,
            fontname="helv",
        )
        pdf_path = tmp_path / "sig_scan.pdf"
        sig_doc.save(str(pdf_path))
        sig_doc.close()

        analysis, _ = analyze(pdf_path)
        assert analysis["suspected_scan_pages"] == [1]

        ocr_cfg = {
            "engine": "br-ocr-v1",
            "languages": "eng",
            "model": "br-ocr-v1",
            "timeout_seconds": 30,
            "poll_seconds": 0.2,
            "max_pages": 50,
            "dpi": 150,
            "max_page_pixels": 20000000,
            "max_result_bytes": 16777216,
        }
        md, info, _, parsers = extract_pdf(pdf_path, ocr_cfg, "latin")
        assert "OFFICIAL GAZETTE" in md.upper()
        assert info["method"] == "ocr"
        assert "ocr" in parsers

    def test_digital_born_pdf_with_cover_image_does_not_trigger_scan_classification(
        self, tmp_path: Path
    ) -> None:
        """Native-page classification is separate from complete delivery.

        The cover image contains title text absent from the native layer, so this
        fixture must not claim text-only extraction is a complete delivery.
        Full-document image blocking is covered by TestLegalDocumentLayout.
        """
        from typing import Any

        import fitz  # type: ignore[import-untyped]

        from wlh.processor.extractors.pdf.scan_detector import analyze

        fitz_mod: Any = fitz
        doc = fitz_mod.open()

        cover_img = fitz_mod.open()
        cp = cover_img.new_page(width=595, height=842)
        cp.insert_textbox(
            fitz_mod.Rect(100, 200, 500, 400),
            "MINISTERIAL REPORT 2026\nANNUAL LEGAL GAZETTE",
            fontsize=24,
            fontname="helv",
        )
        pix = cp.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        cover_img.close()

        p1 = doc.new_page(width=595, height=842)
        p1.insert_image(fitz_mod.Rect(0, 0, 595, 842), stream=img_bytes)
        p1.insert_textbox(
            fitz_mod.Rect(50, 750, 500, 800),
            "Official Publication of the Department of Legal Affairs",
            fontsize=12,
            fontname="helv",
        )

        p2 = doc.new_page(width=595, height=842)
        p2.insert_textbox(
            fitz_mod.Rect(50, 80, 545, 760),
            "Section 1. Purpose and Scope of this statutory regulation.\n"
            "This statutory instrument sets forth the standards for open government data compliance.\n"
            "All public departments shall prepare annual audit reviews and maintain machine-readable archives.\n"
            "The Director shall inspect all registries and certify the accuracy of public gazette records.\n"
            "Any inconsistencies between electronic representations and certified source documents must be recorded.",
            fontsize=14,
            fontname="helv",
        )
        pdf_path = tmp_path / "cover_image_native.pdf"
        doc.save(str(pdf_path))
        doc.close()

        analysis, _ = analyze(pdf_path)
        assert analysis["page_count"] == 2
        assert analysis["suspected_scan_pages"] == [], "Cover page was wrongly suspected as scan!"

    def test_scanned_pdf_with_running_header_and_cropped_scan_image_triggers_ocr(
        self, tmp_path: Path
    ) -> None:
        """A 2-page gazette PDF with a ~50-60 char running header and a ~60% coverage scanned body image on each page triggers OCR."""
        from typing import Any

        import fitz  # type: ignore[import-untyped]

        from wlh.processor.extractors.pdf.scan_detector import analyze, extract_pdf

        fitz_mod: Any = fitz
        temp_doc = fitz_mod.open()
        tp = temp_doc.new_page(width=515, height=520)
        tp.insert_textbox(
            fitz_mod.Rect(20, 40, 495, 480),
            "GOVERNMENT RESOLUTION NO 325 ON DIGITAL INFRASTRUCTURE\n"
            "Article 1. National digital infrastructure standards and public data governance.",
            fontsize=18,
            fontname="helv",
        )
        img_bytes = tp.get_pixmap(dpi=150).tobytes("png")
        temp_doc.close()

        pdf_doc = fitz_mod.open()
        for page_no in (121, 122):
            p = pdf_doc.new_page(width=595, height=842)
            p.insert_textbox(
                fitz_mod.Rect(40, 25, 555, 80),
                f"CONG BAO / So 560 / Ngay 23-10-2026   Trang {page_no}",
                fontsize=11,
                fontname="helv",
            )
            p.insert_image(fitz_mod.Rect(40, 100, 555, 620), stream=img_bytes)
        pdf_path = tmp_path / "gazette_running_header_scan.pdf"
        pdf_doc.save(str(pdf_path))
        pdf_doc.close()

        analysis, _ = analyze(pdf_path)
        assert analysis["suspected_scan_pages"] == [1, 2]

        ocr_cfg = {
            "engine": "br-ocr-v1",
            "languages": "eng",
            "model": "br-ocr-v1",
            "timeout_seconds": 60,
            "poll_seconds": 0.2,
            "max_pages": 50,
            "dpi": 150,
            "max_page_pixels": 20000000,
            "max_result_bytes": 16777216,
        }
        md, info, _, parsers = extract_pdf(pdf_path, ocr_cfg, "latin")
        assert info["method"] == "ocr"
        assert "ocr" in parsers
        assert "RESOLUTION NO 325" in md.upper() or "DIGITAL INFRASTRUCTURE" in md.upper()
