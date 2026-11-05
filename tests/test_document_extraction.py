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

    def test_remote_br_ocr_service_polls_status_transitions_and_falls_back_on_error_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`br-ocr-v1` submits `/file_parse`, polls `/status/{uuid}` across `PROCESSING -> SUCCESS`, fetches `/result/{uuid}`, and falls back cleanly to local Tesseract when `/status/{uuid}` returns `FAILED`."""
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from typing import Any

        from wlh.processor.extractors.pdf.scan_detector import extract_pdf

        scanned_pdf = make_scanned_pdf(
            tmp_path / "br_ocr_scan.pdf",
            [
                "SUPREME COURT OF THE UNITED STATES\nOPINION NO. 26-700\nJudgment Affirmed on October 30, 2026."
            ],
        )

        state: dict[str, Any] = {
            "mode": "success",
            "status_polls": 0,
            "submitted_payloads": [],
        }

        class _BrOcrHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def _send_json(self, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                if self.path == "/api/v1/file_parse":
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = json.loads(self.rfile.read(length).decode("utf-8"))
                    state["submitted_payloads"].append(raw)
                    self._send_json(
                        {
                            "code": 200,
                            "data": {"task_uuid": str(raw.get("task_uuid") or "task-1")},
                        }
                    )
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_GET(self) -> None:
                if self.path.startswith("/api/v1/status/"):
                    state["status_polls"] += 1
                    if state["mode"] == "failed":
                        self._send_json({"code": 200, "data": {"task_status": "FAILED"}})
                    elif state["status_polls"] == 1:
                        self._send_json({"code": 200, "data": {"task_status": "PROCESSING"}})
                    else:
                        self._send_json({"code": 200, "data": {"task_status": "SUCCESS"}})
                elif self.path.startswith("/api/v1/result/"):
                    self._send_json(
                        {
                            "code": 200,
                            "data": {
                                "whole_text_md": (
                                    "# SUPREME COURT OF THE UNITED STATES\n\n"
                                    "OPINION NO. 26-700 — Judgment Affirmed on October 30, 2026."
                                ),
                                "page_parse_json": [
                                    {
                                        "page_idx": 0,
                                        "type": "text",
                                        "text": (
                                            "SUPREME COURT OF THE UNITED STATES "
                                            "OPINION NO. 26-700 Judgment Affirmed on October 30, 2026."
                                        ),
                                    }
                                ],
                            },
                        }
                    )
                else:
                    self.send_response(404)
                    self.end_headers()

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _BrOcrHandler)
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()

        try:
            monkeypatch.setenv("WLH_BR_OCR_URL", f"http://127.0.0.1:{port}/api/v1")
            ocr_cfg = {
                "engine": "br-ocr-v1",
                "languages": "eng",
                "model": "br-ocr-v1",
                "timeout_seconds": 30,
                "poll_seconds": 0.05,
                "max_pages": 50,
                "dpi": 150,
                "max_page_pixels": 20000000,
                "max_result_bytes": 16777216,
            }
            md_ok, info_ok, _, parsers_ok = extract_pdf(scanned_pdf, ocr_cfg, "latin")
            assert "OPINION NO. 26-700" in md_ok
            assert info_ok["method"] == "ocr"
            assert info_ok["ocr"]["engine"] == "br-ocr-v1"
            assert state["status_polls"] >= 2
            assert len(state["submitted_payloads"]) == 1
            assert "ocr" in parsers_ok

            state["mode"] = "failed"
            state["status_polls"] = 0
            md_fb, info_fb, _, _ = extract_pdf(scanned_pdf, ocr_cfg, "latin")
            assert "SUPREME COURT" in md_fb.upper()
            assert info_fb["ocr"]["engine"] == "tesseract"
            assert info_fb["ocr"]["fallback_from"] == "br-ocr-v1"
            assert info_fb["ocr"]["fallback_reason"] == "br_ocr_failed"
        finally:
            srv.shutdown()
            thread.join(timeout=2.0)
            srv.server_close()

    def test_digital_born_pdf_with_small_top_cover_emblem_extracts_cleanly(
        self, tmp_path: Path
    ) -> None:
        """A digital-born PDF with a full native text cover page (>= 350 chars) and a small top emblem image extracts cleanly without raising `pdf_images_unverified`."""
        from typing import Any

        import fitz  # type: ignore[import-untyped]

        from wlh.processor.extractors.pdf.scan_detector import analyze, extract_pdf

        fitz_mod: Any = fitz
        emblem_doc = fitz_mod.open()
        ep = emblem_doc.new_page(width=100, height=100)
        ep.draw_circle(fitz_mod.Point(50, 50), 40, color=(0.1, 0.2, 0.5), fill=(0.2, 0.4, 0.7))
        emblem_bytes = ep.get_pixmap(dpi=150).tobytes("png")
        emblem_doc.close()

        doc = fitz_mod.open()
        p1 = doc.new_page(width=595, height=842)
        p1.insert_image(fitz_mod.Rect(260, 100, 324, 164), stream=emblem_bytes)
        p1.insert_textbox(
            fitz_mod.Rect(50, 200, 545, 780),
            "INLAND REVENUE DEPARTMENT HONG KONG\n"
            "DEPARTMENTAL INTERPRETATION AND PRACTICE NOTES NO. 10 (REVISED)\n"
            "THE CHARGE TO PROFITS TAX — TIME BASIS OF ASSESSMENT\n"
            "These notes are issued for the information of taxpayers and their tax representatives. "
            "They contain the Department's interpretation and practices in relation to the law as it "
            "stood at the date of publication. Taxpayers are reminded that their right of objection "
            "to the Commissioner and appeal to the Board of Review or the Courts is not affected by "
            "the application of these notes. First issued in December 1982 and revised in July 2026.",
            fontsize=12,
            fontname="helv",
        )
        p2 = doc.new_page(width=595, height=842)
        p2.insert_textbox(
            fitz_mod.Rect(50, 80, 545, 760),
            "Section 18B of the Inland Revenue Ordinance governs the basis period for computation "
            "of assessable profits in respect of any year of assessment commencing on or after 1 April.",
            fontsize=12,
            fontname="helv",
        )
        pdf_path = tmp_path / "dipn_cover_emblem.pdf"
        doc.save(str(pdf_path))
        doc.close()

        analysis, _ = analyze(pdf_path)
        assert analysis["suspected_scan_pages"] == []

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
        md, info, _, _ = extract_pdf(pdf_path, ocr_cfg, "latin")
        assert info["method"] == "native"
        assert "DEPARTMENTAL INTERPRETATION AND PRACTICE NOTES" in md

    def test_hybrid_pdf_with_full_page_scanned_appendix_and_hidden_text_layer_triggers_hybrid_ocr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A digital-born PDF followed by a full-page scanned appendix image (>= 95% coverage) with a legacy hidden text layer triggers hybrid OCR on the appendix page."""
        from typing import Any

        import fitz  # type: ignore[import-untyped]

        from wlh.processor.extractors.pdf.scan_detector import analyze, extract_pdf

        monkeypatch.setenv("WLH_BR_OCR_URL", "http://127.0.0.1:59999/api/v1")

        fitz_mod: Any = fitz
        scan_img_doc = fitz_mod.open()
        sp = scan_img_doc.new_page(width=595, height=842)
        sp.insert_textbox(
            fitz_mod.Rect(50, 80, 545, 760),
            "APPENDIX A LEGAL NOTICE 316 OF 1989 DOUBLE TAXATION RELIEF\n"
            "Arrangement between the Government of Hong Kong and the United States of America "
            "for the avoidance of double taxation on income from shipping operations.",
            fontsize=18,
            fontname="helv",
        )
        scan_bytes = sp.get_pixmap(dpi=150).tobytes("png")
        scan_img_doc.close()

        doc = fitz_mod.open()
        p1 = doc.new_page(width=595, height=842)
        p1.insert_textbox(
            fitz_mod.Rect(50, 80, 545, 760),
            "INLAND REVENUE DEPARTMENT HONG KONG\n"
            "DEPARTMENTAL INTERPRETATION AND PRACTICE NOTES NO. 19\n"
            "AGREEMENT BETWEEN THE UNITED STATES OF AMERICA AND HONG KONG IN RESPECT OF THE "
            "TAXATION OF SHIPPING PROFITS\n"
            "These notes are issued for the information of shipowners and their tax representatives. "
            "They explain the reciprocal tax exemption arrangement concluded by exchange of notes and "
            "given statutory effect under Section 49 of the Inland Revenue Ordinance.",
            fontsize=12,
            fontname="helv",
        )
        p2 = doc.new_page(width=595, height=842)
        p2.insert_image(fitz_mod.Rect(0, 0, 595, 842), stream=scan_bytes)
        p2.insert_textbox(
            fitz_mod.Rect(50, 80, 545, 760),
            "APPENDIX A LEGAL NOTICE 316 OF 1989 INLAND REVENUE ORDINANCE (CHAPTER 112) "
            "SPECIFICATION OF ARRANGEMENTS FOR AVOIDANCE OF DOUBLE TAXATION ON SHIPPING INCOME "
            "MADE BY THE GOVERNOR IN COUNCIL UNDER SECTION 49 OF THE INLAND REVENUE ORDINANCE "
            "PUBLISHED IN THE GAZETTE OF HONG KONG AS LEGAL SUPPLEMENT NO. 2 TO ISSUE NO. 38 "
            "AND EFFECTIVE FOR ANY YEAR OF ASSESSMENT COMMENCING ON OR AFTER 1 APRIL 1989 "
            "IN RESPECT OF PROFITS DERIVED FROM THE INTERNATIONAL OPERATION OF SHIPS.",
            fontsize=10,
            fontname="helv",
        )
        pdf_path = tmp_path / "dipn_hybrid_scanned_appendix.pdf"
        doc.save(str(pdf_path))
        doc.close()

        analysis, _ = analyze(pdf_path)
        assert analysis["suspected_scan_pages"] == [2]

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
        assert info["method"] == "hybrid_ocr"
        assert "ocr" in parsers
        assert "DOUBLE TAXATION" in md.upper()

    def test_digital_born_pdf_with_wrapped_dotted_leader_lines_and_merged_rates_extracts_cleanly(
        self, tmp_path: Path
    ) -> None:
        """A depreciation-rate schedule where single-line rates are merged and wrapped continuation lines end with dotted leaders extracts cleanly without raising `pdf_layout_unverified`."""
        from typing import Any

        import fitz  # type: ignore[import-untyped]

        from wlh.processor.extractors.pdf.scan_detector import extract_pdf

        fitz_mod: Any = fitz
        doc = fitz_mod.open()
        p = doc.new_page(width=595, height=842)
        p.insert_text(
            fitz_mod.Point(60, 100),
            "18. Bar syphon apparatus ................................................ 30%",
            fontsize=11,
            fontname="helv",
        )
        p.insert_text(
            fitz_mod.Point(60, 130),
            "26. Plastic manufacturing machinery and plant including                   30%",
            fontsize=11,
            fontname="helv",
        )
        p.insert_text(
            fitz_mod.Point(80, 146),
            "moulds ..................................................................",
            fontsize=11,
            fontname="helv",
        )
        p.insert_text(
            fitz_mod.Point(60, 176),
            "29. Sulfuric acid manufacturing machinery and plant                       30%",
            fontsize=11,
            fontname="helv",
        )
        p.insert_text(
            fitz_mod.Point(80, 192),
            "which has a lining of lead or platinum ..................................",
            fontsize=11,
            fontname="helv",
        )
        pdf_path = tmp_path / "dipn07_wrapped_leaders.pdf"
        doc.save(str(pdf_path))
        doc.close()

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
        md, info, _, _ = extract_pdf(pdf_path, ocr_cfg, "latin")
        assert info["method"] == "native"
        assert "Plastic manufacturing machinery" in md
        assert "Sulfuric acid manufacturing machinery" in md

    def test_digital_born_pdf_with_ruled_table_trailing_space_overhang_extracts_cleanly(
        self, tmp_path: Path
    ) -> None:
        """A ruled table cell whose text span has trailing whitespace overhanging the right border by ~6pt extracts cleanly as a Markdown table."""
        from typing import Any

        import fitz  # type: ignore[import-untyped]

        from wlh.processor.extractors.pdf.scan_detector import extract_pdf

        fitz_mod: Any = fitz
        doc = fitz_mod.open()
        p = doc.new_page(width=595, height=842)
        cells = [
            (fitz_mod.Rect(80, 650, 143, 668), "Borrower"),
            (fitz_mod.Rect(143, 650, 186, 668), "Facility"),
            (fitz_mod.Rect(80, 668, 143, 686), "Company A"),
            (fitz_mod.Rect(143, 668, 186, 686), "Loan-2    "),
        ]
        for rect, label in cells:
            p.draw_rect(rect, color=(0, 0, 0), width=1)
            p.insert_text(
                fitz_mod.Point(rect.x0 + 3, rect.y0 + 13),
                label,
                fontsize=10,
                fontname="helv",
            )
        pdf_path = tmp_path / "dipn52_ruled_cell_overhang.pdf"
        doc.save(str(pdf_path))
        doc.close()

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
        md, info, _, _ = extract_pdf(pdf_path, ocr_cfg, "latin")
        assert info["method"] == "native"
        assert "| Company A | Loan-2 |" in md

    def test_digital_born_pdf_with_wide_ruled_table_column_and_late_stream_header_extracts_cleanly(
        self, tmp_path: Path
    ) -> None:
        """A 2-column ruled table whose right column exceeds 60% page width does not trigger a false-positive callout-box error when a top header appears later in the PDF stream."""
        from typing import Any

        import fitz  # type: ignore[import-untyped]

        from wlh.processor.extractors.pdf.scan_detector import extract_pdf

        fitz_mod: Any = fitz
        doc = fitz_mod.open()
        p = doc.new_page(width=595, height=842)
        r0c0 = fitz_mod.Rect(60, 120, 170, 260)
        r0c1 = fitz_mod.Rect(170, 120, 540, 260)
        r1c0 = fitz_mod.Rect(60, 260, 170, 360)
        r1c1 = fitz_mod.Rect(170, 260, 540, 360)
        for rect in (r0c0, r0c1, r1c0, r1c1):
            p.draw_rect(rect, color=(0, 0, 0), width=1)

        p.insert_textbox(
            fitz_mod.Rect(65, 130, 165, 250),
            "Core R&D activities",
            fontsize=11,
            fontname="helv",
        )
        p.insert_textbox(
            fitz_mod.Rect(175, 130, 535, 250),
            "Systematic, investigative or experimental activities carried on for the purpose "
            "of resolving scientific or technological uncertainty and generating new knowledge.",
            fontsize=11,
            fontname="helv",
        )
        p.insert_textbox(
            fitz_mod.Rect(65, 270, 165, 350),
            "Supporting activities",
            fontsize=11,
            fontname="helv",
        )
        p.insert_textbox(
            fitz_mod.Rect(175, 270, 535, 350),
            "Activities directly related to and undertaken for the dominant purpose of supporting "
            "the core R&D activities described above.",
            fontsize=11,
            fontname="helv",
        )
        # Header emitted later in the PDF content stream above the table
        p.insert_text(
            fitz_mod.Point(450, 65),
            "Appendix 1",
            fontsize=12,
            fontname="helv",
        )
        pdf_path = tmp_path / "dipn55_wide_table_late_header.pdf"
        doc.save(str(pdf_path))
        doc.close()

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
        md, info, _, _ = extract_pdf(pdf_path, ocr_cfg, "latin")
        assert info["method"] == "native"
        assert "Core R&D activities" in md
        assert "Systematic, investigative or experimental activities" in md


class TestOfflineExtractorAndContainerContracts:
    def test_preparsed_dom_root_converts_to_identical_markdown_as_html_string(self) -> None:
        """Passing a pre-parsed DOM root to html_to_markdown produces identical Markdown to parsing the raw HTML string."""
        from typing import Any

        from wlh.processor.extractors.html.dom_tree import document_tree
        from wlh.processor.extractors.html.html_to_md import html_to_markdown

        raw_html = (
            "<!DOCTYPE html><html><body>"
            "<nav>Menu</nav>"
            "<main>"
            "<h1>Clean Air Regulation</h1>"
            "<p>Article 1. Ambient air quality standards shall apply nationwide.</p>"
            "<h2>Section 2. Emission Limits</h2>"
            "<table>"
            "<thead><tr><th>Pollutant</th><th>Limit</th></tr></thead>"
            "<tbody><tr><td>SO2</td><td>50 ug/m3</td></tr></tbody>"
            "</table>"
            "</main>"
            "</body></html>"
        )
        root = document_tree(raw_html)
        html_to_md_fn: Any = html_to_markdown
        md_from_str = html_to_md_fn(raw_html, "https://example.gov/law/1")
        md_from_tree = html_to_md_fn(root, "https://example.gov/law/1")
        assert md_from_tree == md_from_str
        assert "Clean Air Regulation" in md_from_tree
        assert "Menu" not in md_from_tree

    def test_pdf_scan_detector_accepts_preopened_document_handle_and_matches_path_analysis(
        self, tmp_path: Path
    ) -> None:
        """The PDF scan detector accepts a pre-opened document handle and produces the same analysis tuple as a filesystem Path."""
        from typing import Any

        import fitz as pdf_lib  # type: ignore[import-untyped]

        from wlh.processor.extractors.pdf.scan_detector import analyze

        pdf_path = make_native_pdf(
            tmp_path / "doc.pdf",
            [
                "Article 1. Environmental standards. " * 15,
                "Article 2. Compliance rules. " * 15,
            ],
        )
        pdf_mod: Any = pdf_lib
        analyze_fn: Any = analyze
        analysis_path, native_path = analyze_fn(pdf_path)
        with pdf_mod.open(stream=pdf_path.read_bytes(), filetype="pdf") as doc:
            analysis_doc, native_doc = analyze_fn(doc)
            assert not doc.is_closed

        assert (analysis_doc, native_doc) == (analysis_path, native_path)
        assert analysis_doc["page_count"] == 2
        assert analysis_doc["suspected_scan_pages"] == []

    def test_batch_json_and_large_xml_containers_populate_and_reuse_records_cache(self) -> None:
        """Batch JSON payloads and large (>1 MB) multi-record XML containers populate and reuse `_records_cache` keyed by archive_ref and actual_url."""
        import json

        from wlh.processor.parsers.json.parser import JsonParser
        from wlh.processor.parsers.xml.parser import XmlParser

        json_parser = JsonParser(
            {
                "id": "dd-901",
                "scheduler": {
                    "entry_urls": ["https://example.gov/api/laws?page=1"],
                    "allowed_hosts": ["example.gov"],
                },
                "processor": {
                    "parser": {
                        "type": "json",
                        "options": {
                            "items_path": "data.items",
                            "detail_url_template": "https://example.gov/api/laws/{id}",
                        },
                    }
                },
                "storage": {"metadata": {"document_type": "法律法规"}},
            }
        )
        payload_bytes = json.dumps(
            {
                "data": {
                    "items": [
                        {
                            "id": "101",
                            "title": "Clean Air Act 101",
                            "document_number": "No. 101",
                            "date": "2026-01-10",
                            "text": "Article 1. Emission standards for industrial boilers.",
                        },
                        {
                            "id": "102",
                            "title": "Water Quality Act 102",
                            "document_number": "No. 102",
                            "date": "2026-01-11",
                            "text": "Article 1. Effluent discharge limits for rivers.",
                        },
                        {
                            "id": "103",
                            "title": "Soil Protection Act 103",
                            "document_number": "No. 103",
                            "date": "2026-01-12",
                            "text": "Article 1. Remediation standards for contaminated land.",
                        },
                    ]
                }
            }
        ).encode("utf-8")
        json_acq = {
            "archive_ref": "sha256:1111",
            "actual_url": "https://example.gov/api/laws?page=1",
            "request_url": "https://example.gov/api/laws?page=1",
            "charset": "utf-8",
        }
        recs = json_parser.records(payload_bytes, json_acq)
        assert len(recs) == 3
        json_cache = getattr(json_parser, "_records_cache", {})
        assert any(
            k == ("sha256:1111", "https://example.gov/api/laws?page=1")
            or (
                isinstance(k, tuple)
                and k[:2] == ("sha256:1111", "https://example.gov/api/laws?page=1")
            )
            for k in json_cache
        )
        assert json_parser.records(payload_bytes, json_acq) == recs

        xml_parser = XmlParser(
            {
                "id": "dd-902",
                "scheduler": {
                    "entry_urls": ["https://example.gov/laws.xml"],
                    "allowed_hosts": ["example.gov"],
                },
                "processor": {"parser": {"type": "xml"}},
                "storage": {"metadata": {"document_type": "法律法规"}},
            }
        )
        chunk = "Article 1. Statutory environmental protection provision. " * 9500
        xml_bytes = (
            "<?xml version='1.0' encoding='utf-8'?>"
            "<Laws>"
            "<Law id='law-1'>"
            "<LawName>Environmental Law 1</LawName>"
            "<LawNo>Act No. 1</LawNo>"
            "<LawURL>https://example.gov/laws/1</LawURL>"
            f"<LawContent>{chunk}</LawContent>"
            "</Law>"
            "<Law id='law-2'>"
            "<LawName>Environmental Law 2</LawName>"
            "<LawNo>Act No. 2</LawNo>"
            "<LawURL>https://example.gov/laws/2</LawURL>"
            f"<LawContent>{chunk}</LawContent>"
            "</Law>"
            "</Laws>"
        ).encode("utf-8")
        xml_acq = {
            "archive_ref": "sha256:2222",
            "actual_url": "https://example.gov/laws.xml",
            "request_url": "https://example.gov/laws.xml",
            "charset": "utf-8",
        }
        xml_recs = xml_parser.records(xml_bytes, xml_acq)
        assert len(xml_recs) == 2
        assert sum(len(str(r.get("text") or "")) for r in xml_recs) > 1_050_000
        xml_cache = getattr(xml_parser, "_records_cache", {})
        assert any(
            k == ("sha256:2222", "https://example.gov/laws.xml")
            or (isinstance(k, tuple) and k[:2] == ("sha256:2222", "https://example.gov/laws.xml"))
            for k in xml_cache
        )
        assert xml_parser.records(xml_bytes, xml_acq) == xml_recs
