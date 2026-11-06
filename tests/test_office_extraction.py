"""Legacy Vietnamese text and disguised Office document extraction contracts."""

from __future__ import annotations

import struct
from pathlib import Path


def _build_ole2_msdoc_bytes(text_utf16_body: str, data_stream: bytes = b"") -> bytes:
    """Build a valid 512-byte-sector OLE2 MS-DOC binary with WordDocument, 1Table, and optional Data streams."""
    enofchain = 0xFFFFFFFE
    fatsect = 0xFFFFFFFD
    freesect = 0xFFFFFFFF

    utf16_bytes = text_utf16_body.encode("utf-16le")
    plc = struct.pack("<II", 0, len(text_utf16_body)) + struct.pack("<HIH", 0, 0x0200, 0)
    clx_bytes = b"\x02" + struct.pack("<I", len(plc)) + plc

    table_size = max(4096, ((len(clx_bytes) + 511) // 512) * 512)
    table_padded = clx_bytes.ljust(table_size, b"\x00")

    word_raw_len = max(4096, ((0x0200 + len(utf16_bytes) + 511) // 512) * 512)
    word_buf = bytearray(word_raw_len)
    struct.pack_into("<HH", word_buf, 0x0000, 0xA5EC, 0x00C1)
    struct.pack_into("<H", word_buf, 0x000A, 0x0200)
    struct.pack_into("<I", word_buf, 0x004C, len(text_utf16_body))
    struct.pack_into("<II", word_buf, 0x01A2, 0, len(clx_bytes))
    word_buf[0x0200 : 0x0200 + len(utf16_bytes)] = utf16_bytes
    word_padded = bytes(word_buf)

    word_sectors = len(word_padded) // 512
    table_sectors = len(table_padded) // 512

    word_start = 2
    table_start = word_start + word_sectors

    if data_stream:
        data_size = max(4096, ((len(data_stream) + 511) // 512) * 512)
        data_padded = data_stream.ljust(data_size, b"\x00")
        data_sectors = len(data_padded) // 512
        data_start = table_start + table_sectors
    else:
        data_padded = b""
        data_sectors = 0
        data_start = enofchain

    fat_entries = [freesect] * 128
    fat_entries[0] = fatsect
    fat_entries[1] = enofchain

    for i in range(word_sectors):
        sec = word_start + i
        fat_entries[sec] = (sec + 1) if i + 1 < word_sectors else enofchain

    for i in range(table_sectors):
        sec = table_start + i
        fat_entries[sec] = (sec + 1) if i + 1 < table_sectors else enofchain

    for i in range(data_sectors):
        sec = data_start + i
        fat_entries[sec] = (sec + 1) if i + 1 < data_sectors else enofchain

    fat_sector = struct.pack("<128I", *fat_entries)

    def _dir_entry(
        name: str,
        mse: int,
        left_sib: int,
        right_sib: int,
        child_sid: int,
        start_sec: int,
        stream_size: int,
    ) -> bytes:
        entry = bytearray(128)
        if name:
            name_bytes = name.encode("utf-16le") + b"\x00\x00"
            entry[0 : len(name_bytes)] = name_bytes
            struct.pack_into("<H", entry, 64, len(name_bytes))
        struct.pack_into("<BB", entry, 66, mse, 1)
        struct.pack_into("<III", entry, 68, left_sib, right_sib, child_sid)
        struct.pack_into("<I", entry, 116, start_sec)
        struct.pack_into("<Q", entry, 120, stream_size)
        return bytes(entry)

    dir_sector = (
        _dir_entry("Root Entry", 5, freesect, freesect, 1, enofchain, 0)
        + _dir_entry(
            "WordDocument",
            2,
            2,
            3 if data_stream else freesect,
            freesect,
            word_start,
            len(word_padded),
        )
        + _dir_entry("1Table", 2, freesect, freesect, freesect, table_start, len(table_padded))
        + (
            _dir_entry("Data", 2, freesect, freesect, freesect, data_start, len(data_padded))
            if data_stream
            else _dir_entry("", 0, freesect, freesect, freesect, freesect, 0)
        )
    )

    header = bytearray(512)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHH", header, 24, 0x003E, 0x0003, 0xFFFE, 9)
    struct.pack_into("<H", header, 32, 6)
    struct.pack_into("<III", header, 40, 0, 1, 1)
    struct.pack_into("<IIIII", header, 52, 0, 4096, enofchain, 0, enofchain)
    struct.pack_into("<I", header, 72, 0)
    struct.pack_into("<I", header, 76, 0)
    for idx in range(1, 109):
        struct.pack_into("<I", header, 76 + idx * 4, freesect)

    return bytes(header) + fat_sector + dir_sector + word_padded + table_padded + data_padded


class TestLegacyVietnameseAndDisguisedOfficeExtraction:
    def test_mixed_unicode_and_tcvn3_lines_recover_vietnamese_diacritics(self) -> None:
        """Line-level TCVN3 recovery preserves existing Unicode headers while converting TCVN3 lines."""
        from wlh.processor.extractors.quirks.vi_tcvn3 import normalize_detected_tcvn3

        unicode_header = "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM"
        tcvn3_line = (
            "Lu\xcbt Doanh nghi\xd6p s\xe8 59/2020/QH14 \xa7i\xd2u 1 Ph\xb5m vi \xaei\xd2u ch\xd8nh"
        )
        mixed_text = f"{unicode_header}\n{tcvn3_line}\n"

        recovered, changed = normalize_detected_tcvn3(mixed_text)
        assert changed is True
        assert unicode_header in recovered
        assert "Luật Doanh nghiệp" in recovered

    def test_disguised_html_and_rtf_doc_files_detect_actual_format_and_extract_text(
        self, tmp_path: Path
    ) -> None:
        """Files named `.doc` that actually contain HTML or TCVN3 RTF are sniffed and extracted accurately."""
        from wlh.processor.extractors.word.office import extract_office

        disguised_html_doc = tmp_path / "disguised_html.doc"
        disguised_html_doc.write_bytes(
            b"<html><body><h1>Circular 01/2026/TT-BTC</h1><p>Tax regulation body text.</p></body></html>"
        )
        md_html, info_html = extract_office(disguised_html_doc, kind="doc")
        assert "Circular 01/2026/TT-BTC" in md_html
        assert info_html["detected_format"] == "html"

        disguised_rtf_doc = tmp_path / "disguised_rtf.doc"
        rtf_bytes = (
            b"{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 .VnTime;}}\n"
            b"\\f0\\fs24 Lu\\'cbt Doanh nghi\\'d6p s\\'e8 59/2020/QH14 \\'a7i\\'d2u 1 Ph\\'b5m vi \\'aei\\'d2u ch\\'d8nh\\par}"
        )
        disguised_rtf_doc.write_bytes(rtf_bytes)
        md_rtf, info_rtf = extract_office(disguised_rtf_doc, kind="doc")
        assert "Luật Doanh nghiệp" in md_rtf
        assert info_rtf["detected_format"] == "rtf"

    def test_scanned_image_word_document_with_only_signature_text_falls_back_to_ocr_or_rejects_stub(
        self, tmp_path: Path
    ) -> None:
        """A `.docx` containing only a 56-char signature text block and an embedded scan image in `word/media/image1.png` triggers OCR fallback or rejects the stub."""
        import inspect
        import zipfile
        from typing import Any

        import fitz  # type: ignore[import-untyped]
        from docx import Document  # type: ignore[import-untyped]
        from docx.shared import Inches  # type: ignore[import-untyped]

        from wlh.core import WlhError
        from wlh.processor.extractors.word.office import extract_office

        fitz_mod: Any = fitz
        img_doc = fitz_mod.open()
        img_page = img_doc.new_page(width=595, height=420)
        img_page.insert_textbox(
            fitz_mod.Rect(30, 40, 565, 380),
            "DECREE NO 325 FULL STATUTORY TEXT ON PUBLIC ADMINISTRATION\n"
            "Article 1. All public agencies shall digitize administrative records.",
            fontsize=18,
            fontname="helv",
        )
        img_path = tmp_path / "scan_body.png"
        img_page.get_pixmap(dpi=150).save(str(img_path))
        img_doc.close()

        sig_text = "TM. CHINH PHU KT. THU TUONG PHO THU TUONG Phan Van Giang"
        assert len(sig_text) == 56

        docx_path = tmp_path / "scanned_sig.docx"
        word_doc: Any = Document()
        word_doc.add_paragraph(sig_text)
        word_doc.add_picture(str(img_path), width=Inches(6.0))
        word_doc.save(str(docx_path))

        with zipfile.ZipFile(docx_path) as zf:
            names = set(zf.namelist())
            assert "word/document.xml" in names
            assert "word/media/image1.png" in names

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
        sig = inspect.signature(extract_office)
        extract_fn: Any = extract_office
        try:
            if "ocr" in sig.parameters:
                md_docx, info_docx = extract_fn(
                    docx_path, kind="docx", ocr=ocr_cfg, script_hint="latin"
                )
            else:
                md_docx, info_docx = extract_office(docx_path, kind="docx")
        except WlhError as exc:
            assert exc.code in {
                "quality_too_short",
                "quality_check_failed",
                "docx_text_unusable",
            }
        else:
            assert info_docx.get("extractor") not in {"python_docx", "docx_xml"}
            assert "DECREE NO 325" in md_docx.upper()

    def test_zip_archive_with_json_and_over_fifty_pdf_members_extracts_json_and_skips_ocr_pdf_companion(
        self, tmp_path: Path
    ) -> None:
        """ZIP archives containing structured JSON plus companion PDFs skip redundant `*_OCR.pdf` overlays and extract JSON cleanly even when total PDF members exceed 50."""
        import json
        import zipfile

        from wlh.config import SourceConfig
        from wlh.processor.extractors.word.office import unpack_zip

        from .conftest import make_native_pdf

        judgment_json_bytes = json.dumps(
            {
                "data": {
                    "ruling_name": "警消人員獎懲累積達二大過免職案",
                    "ruling_content": "警察人員人事條例第三十一條第一項第十一款規定違憲。",
                }
            },
            ensure_ascii=False,
        ).encode("utf-8")
        native_pdf_bytes = make_native_pdf(
            tmp_path / "1_111年憲判字第10號判決主文立場表.pdf",
            ["Constitutional Court Judgment 111-Hsien-Pan-10 Voting Table"],
        ).read_bytes()

        companion_zip = tmp_path / "companion_ocr.zip"
        with zipfile.ZipFile(companion_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("憲法法庭判決-111年憲判字第10號.json", judgment_json_bytes)
            zf.writestr("1_111年憲判字第10號判決主文立場表.pdf", native_pdf_bytes)
            zf.writestr("2_憲法法庭111年憲判字第10號判決_OCR.pdf", native_pdf_bytes)

        companion_members = unpack_zip(
            companion_zip, tmp_path / "unpacked_companion", max_bytes=10 * 1024 * 1024
        )
        companion_paths = [m["path"] for m in companion_members]
        assert "憲法法庭判決-111年憲判字第10號.json" in companion_paths
        assert not any(p.endswith("_OCR.pdf") for p in companion_paths)

        mega_zip = tmp_path / "jy_791.zip"
        json_member_bytes = json.dumps(
            {"title": "釋字第791號", "content": "通姦罪違憲"},
            ensure_ascii=False,
        ).encode("utf-8")
        pdf_payload = b"%PDF-1.4\n" + (b"0123456789abcdef" * 1920)
        with zipfile.ZipFile(mega_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("大法官解釋-釋字第791號.json", json_member_bytes)
            for idx in range(1, 58):
                zf.writestr(f"{idx}_意見書.pdf", pdf_payload)

        members = unpack_zip(mega_zip, tmp_path / "unpacked_mega", max_bytes=1024 * 1024)
        assert [m["path"] for m in members] == ["大法官解釋-釋字第791號.json"]
        assert members[0]["kind"] == "json"

        validated = SourceConfig.model_validate(
            {
                "id": "dd-580",
                "scheduler": {
                    "scope": "OpenData large ZIP archive budget contract test",
                    "entry_urls": ["https://example.gov/api/Datasets?page=1"],
                    "allowed_hosts": ["example.gov"],
                    "budget": {
                        "requests": 50,
                        "documents": 10,
                        "seconds": 300,
                        "response_bytes": 536870912,
                    },
                },
                "fetcher": {"mode": "http", "page_timeout_ms": 1800000},
                "processor": {
                    "parser": {"type": "json"},
                    "extractor": {"quirks": ["zip_salvage"]},
                },
                "storage": {
                    "archive_approved": True,
                    "metadata": {"document_type": "案例"},
                },
            }
        )
        assert validated.scheduler.budget.response_bytes == 536870912
        assert validated.fetcher.page_timeout_ms == 1800000

    def test_salvage_truncated_zip_stream_preserves_crc_verified_json_member_and_builds_valid_zip(
        self, tmp_path: Path
    ) -> None:
        """Truncated ZIP streams salvage intact CRC-verified prefix entries into a valid ZIP archive."""
        import io
        import json
        import os
        import zipfile

        import pytest

        from wlh.processor.extractors.quirks.zip_salvage import salvage_zip_prefix
        from wlh.processor.extractors.word.office import unpack_zip

        json_bytes = json.dumps(
            {"data": {"interpretNo": "793", "interpretContent": "黨產條例合憲"}},
            ensure_ascii=False,
        ).encode("utf-8")
        pdf1_bytes = b"%PDF-1.4\n" + os.urandom(50 * 1024)
        pdf2_bytes = b"%PDF-1.4\n" + os.urandom(100 * 1024)

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("大法官解釋-釋字第793號.json", json_bytes)
            zf.writestr("1_意見書.pdf", pdf1_bytes)
            zf.writestr("2_聲請書.pdf", pdf2_bytes)
            info3 = zf.getinfo("2_聲請書.pdf")
            cut_offset = info3.header_offset + 64 + (info3.compress_size // 2)

        full_zip_bytes = zip_buf.getvalue()
        truncated_bytes = full_zip_bytes[:cut_offset]

        with pytest.raises(zipfile.BadZipFile):
            zipfile.ZipFile(io.BytesIO(truncated_bytes))

        repaired = salvage_zip_prefix(truncated_bytes)
        with zipfile.ZipFile(io.BytesIO(repaired)) as repaired_zf:
            assert repaired_zf.testzip() is None
            assert repaired_zf.namelist() == ["大法官解釋-釋字第793號.json", "1_意見書.pdf"]

        repaired_path = tmp_path / "repaired.zip"
        repaired_path.write_bytes(repaired)
        members = unpack_zip(repaired_path, tmp_path / "out", max_bytes=10 * 1024 * 1024)
        assert [m["path"] for m in members] == ["大法官解釋-釋字第793號.json"]

    def test_legacy_binary_word_doc_with_table_and_prose_extracts_markdown_table_without_external_office_suite(
        self, tmp_path: Path, monkeypatch: object
    ) -> None:
        """A legacy OLE2 `.doc` containing statutory prose and `\x07`-delimited table cells extracts a Markdown table in pure Python without an external Office suite."""
        import pytest

        from wlh.processor.extractors.word.office import extract_office

        mp: pytest.MonkeyPatch = monkeypatch  # type: ignore[assignment]
        mp.setenv("WLH_SOFFICE", "/nonexistent/soffice")

        body_text = (
            "MINISTRY OF FINANCE CIRCULAR 2026 ON CORPORATE TAX\r"
            "Article 1. Standard corporate tax schedule applies as follows:\r"
            "Category\x07Tax Rate\x07\x07"
            "Standard Enterprise\x0720%\x07\x07"
        )
        doc_path = tmp_path / "circular_table.doc"
        doc_path.write_bytes(_build_ole2_msdoc_bytes(body_text))

        md, info = extract_office(doc_path, kind="doc")
        assert "MINISTRY OF FINANCE CIRCULAR 2026" in md
        assert "| Category | Tax Rate |" in md
        assert "| --- | --- |" in md
        assert "| Standard Enterprise | 20% |" in md
        assert info["extractor"] == "pure_python_ole2_msdoc"
        assert info.get("tables_recovered", 0) >= 1

    def test_scanned_image_legacy_binary_word_doc_extracts_embedded_bitmap_via_local_ocr_without_external_office_suite(
        self, tmp_path: Path, monkeypatch: object
    ) -> None:
        """A scanned-image legacy OLE2 `.doc` with only a `\x08\r` drawing anchor and an embedded bitmap in `Data` extracts text via local OCR without an external Office suite."""
        import zlib
        from typing import Any

        import fitz  # type: ignore[import-untyped]
        import pytest

        from wlh.processor.extractors.word.office import extract_office

        mp: pytest.MonkeyPatch = monkeypatch  # type: ignore[assignment]
        mp.setenv("WLH_SOFFICE", "/nonexistent/soffice")

        fitz_mod: Any = fitz
        img_doc = fitz_mod.open()
        img_page = img_doc.new_page(width=595, height=320)
        img_page.insert_textbox(
            fitz_mod.Rect(30, 40, 565, 280),
            "STATUTORY NOTICE 2026 PUBLIC WORKS TAX EXEMPTION\n"
            "Article 5. Eligible households receive a 50 percent reduction.",
            fontsize=18,
            fontname="helv",
        )
        pix = img_page.get_pixmap(dpi=150)
        png_bytes = pix.tobytes("png")
        width, height = pix.width, pix.height
        rgb_samples = pix.samples
        img_doc.close()

        row_stride = ((width * 3 + 3) // 4) * 4
        dib_bits = bytearray(row_stride * height)
        for y in range(height):
            src_row = (height - 1 - y) * width * 3
            dst_row = y * row_stride
            for x in range(width):
                r = rgb_samples[src_row + x * 3]
                g = rgb_samples[src_row + x * 3 + 1]
                b = rgb_samples[src_row + x * 3 + 2]
                dib_bits[dst_row + x * 3 : dst_row + x * 3 + 3] = bytes((b, g, r))

        bmi_header = struct.pack(
            "<IiiHHIIiiII",
            40,
            width,
            height,
            1,
            24,
            0,
            len(dib_bits),
            2835,
            2835,
            0,
            0,
        )
        off_bmi = 80
        cb_bmi = len(bmi_header)
        off_bits = off_bmi + cb_bmi
        cb_bits = len(dib_bits)
        rec_size = off_bits + cb_bits
        emr_stretchdibits = (
            struct.pack(
                "<IIiiiiiiiiiIIIIII",
                81,
                rec_size,
                0,
                0,
                width,
                height,
                0,
                0,
                width,
                height,
                off_bmi,
                cb_bmi,
                off_bits,
                cb_bits,
                0,
                0x00CC0020,
                width,
            )
            + struct.pack("<I", height)
            + bmi_header
            + bytes(dib_bits)
        )
        emr_eof = struct.pack("<IIIII", 14, 20, 0, 16, 20)
        emf_raw = emr_stretchdibits + emr_eof
        emf_compressed = zlib.compress(emf_raw)
        metafile_hdr = struct.pack(
            "<IiiiiiiIBB",
            len(emf_raw),
            0,
            0,
            width,
            height,
            width * 100,
            height * 100,
            len(emf_compressed),
            0,
            254,
        )
        blip_emf_payload = (b"\x11" * 16) + metafile_hdr + emf_compressed
        blip_emf_record = (
            struct.pack("<HHI", (0x03D4 << 4) | 0, 0xF01A, len(blip_emf_payload)) + blip_emf_payload
        )
        blip_png_payload = (b"\x22" * 16) + b"\xff" + png_bytes
        blip_png_record = (
            struct.pack("<HHI", (0x06E0 << 4) | 0, 0xF01E, len(blip_png_payload)) + blip_png_payload
        )

        doc_path = tmp_path / "scanned_notice.doc"
        doc_path.write_bytes(
            _build_ole2_msdoc_bytes("\x08\r", data_stream=blip_emf_record + blip_png_record)
        )

        ocr_cfg = {
            "engine": "local",
            "languages": "eng",
            "model": "br-ocr-v1",
            "timeout_seconds": 30,
            "poll_seconds": 0.2,
            "max_pages": 10,
            "dpi": 150,
            "max_page_pixels": 20000000,
            "max_result_bytes": 16777216,
        }
        md, info = extract_office(doc_path, kind="doc", ocr=ocr_cfg)
        assert "STATUTORY NOTICE 2026" in md.upper()
        assert info["extractor"] == "msdoc_embedded_image_ocr"
