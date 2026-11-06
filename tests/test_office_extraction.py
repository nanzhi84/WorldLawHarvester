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
        from wlh.processor.extractors.office import extract_office

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
        from wlh.processor.extractors.office import extract_office

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
        from wlh.processor.extractors.archive import unpack_zip

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

        from wlh.processor.extractors.archive import unpack_zip
        from wlh.processor.extractors.quirks.zip_salvage import salvage_zip_prefix

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
        self, tmp_path: Path
    ) -> None:
        """A legacy OLE2 `.doc` containing statutory prose and `\x07`-delimited table cells extracts a Markdown table in pure Python without an external Office suite."""
        from wlh.processor.extractors.office import extract_office

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
        self, tmp_path: Path
    ) -> None:
        """A scanned-image legacy OLE2 `.doc` with only a `\x08\r` drawing anchor and an embedded bitmap in `Data` extracts text via local OCR without an external Office suite."""
        import zlib
        from typing import Any

        import fitz  # type: ignore[import-untyped]

        from wlh.processor.extractors.office import extract_office

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


LOCAL_ENGLISH_OCR = {
    "engine": "local",
    "languages": "eng",
    "model": "br-ocr-v1",
    "timeout_seconds": 60,
    "poll_seconds": 0.2,
    "max_pages": 10,
    "dpi": 150,
    "max_page_pixels": 20000000,
    "max_result_bytes": 16777216,
}


class TestLegacyBinaryWordExtraction:
    def test_table_row_ending_with_empty_cell_keeps_three_aligned_columns_and_next_row_unshifted(
        self, tmp_path: Path
    ) -> None:
        """A `.doc` row `Name\\x07Value\\x07\\x07\\x07` (two cells, one empty cell, row end) stays a 3-column row and the following row starts in column one."""
        from tests.office_fixture_support import build_msdoc_bytes, markdown_table_rows
        from wlh.processor.extractors.office import extract_office

        body_text = (
            "DECISION ON FEE SCHEDULE 2026\r"
            "Name\x07Value\x07\x07\x07"
            "Alpha\x07One\x07Note\x07\x07"
            "Bravo\x07Two\x07Remark\x07\x07"
            "Article 2. This decision takes effect upon signing.\r"
        )
        doc_path = tmp_path / "trailing_empty_cell.doc"
        doc_path.write_bytes(build_msdoc_bytes(body_text))

        md, info = extract_office(doc_path, kind="doc")
        rows = markdown_table_rows(md)
        assert rows[0] == ["Name", "Value", ""]
        assert rows[1] == ["---", "---", "---"]
        assert ["Alpha", "One", "Note"] in rows
        assert ["Bravo", "Two", "Remark"] in rows
        assert all(len(row) == 3 for row in rows)
        assert "DECISION ON FEE SCHEDULE 2026" in md
        assert "Article 2. This decision takes effect upon signing." in md
        assert info["extractor"] == "pure_python_ole2_msdoc"

    def test_data_stream_with_several_png_and_jpeg_blips_returns_every_image_standalone_in_order(
        self,
    ) -> None:
        """A `.doc` `Data` stream holding PNG (0xF01E) and JPEG (0xF01D / 0xF02A) BLIP records yields each image exactly once, byte-identical and never concatenated."""
        from tests.office_fixture_support import (
            build_msdoc_bytes,
            officeart_blip_in_fbse,
            render_text_image,
        )
        from wlh.processor.extractors.office import extract_ole2_data_images, parse_ole2_streams

        images = [
            render_text_image("PAGE ONE ALPHA", "png"),
            render_text_image("PAGE TWO BRAVO", "jpg"),
            render_text_image("PAGE THREE CHARLIE", "png"),
            render_text_image("PAGE FOUR DELTA", "jpg"),
        ]
        data_stream = (
            b"\x00" * 68
            + officeart_blip_in_fbse(0xF01E, 0x6E0, images[0])
            + b"\x00" * 68
            + officeart_blip_in_fbse(0xF01D, 0x46A, images[1])
            + officeart_blip_in_fbse(0xF01E, 0x6E0, images[2])
            + b"\x00" * 68
            + officeart_blip_in_fbse(0xF02A, 0x6E2, images[3])
        )
        raw = build_msdoc_bytes("\x08\r", data_stream=data_stream)

        extracted = extract_ole2_data_images(parse_ole2_streams(raw))
        assert extracted == images

    def test_scanned_doc_with_one_embedded_image_per_page_recovers_text_of_every_page(
        self, tmp_path: Path
    ) -> None:
        """A scanned `.doc` whose `Data` stream holds a PNG page and a JPEG page is OCR'd locally and both pages' text reaches the Markdown."""
        from tests.office_fixture_support import (
            build_msdoc_bytes,
            officeart_blip_in_fbse,
            render_text_image,
        )
        from wlh.processor.extractors.office import extract_office

        page_one = render_text_image(
            "STATUTORY NOTICE 2026 PAGE ONE ALPHA\n"
            "Article 1. Public agencies shall publish every decree online.",
            "png",
        )
        page_two = render_text_image(
            "STATUTORY NOTICE 2026 PAGE TWO BRAVO\n"
            "Article 2. Eligible households receive a fifty percent reduction.",
            "jpg",
        )
        data_stream = officeart_blip_in_fbse(0xF01E, 0x6E0, page_one) + officeart_blip_in_fbse(
            0xF01D, 0x46A, page_two
        )
        doc_path = tmp_path / "two_page_scan.doc"
        doc_path.write_bytes(build_msdoc_bytes("\x08\r", data_stream=data_stream))

        md, info = extract_office(doc_path, kind="doc", ocr=LOCAL_ENGLISH_OCR)
        upper = md.upper()
        assert "PAGE ONE ALPHA" in upper
        assert "PAGE TWO BRAVO" in upper
        assert info["extractor"] == "msdoc_embedded_image_ocr"

    def test_compressed_8bit_piece_with_tcvn3_bytes_recovers_vietnamese_without_replacement_characters(
        self, tmp_path: Path
    ) -> None:
        """An `FcCompressed` 8-bit piece carrying TCVN3 bytes (including 0x81/0x8D/0x8F/0x90/0x9D) is decoded losslessly: no U+FFFD, CP1252 quotes mapped, TCVN3 recovered."""
        from tests.office_fixture_support import build_msdoc_bytes
        from wlh.processor.extractors.office import extract_msdoc_pure, extract_office

        compressed = (
            b"Lu\xcbt Doanh nghi\xd6p s\xe8 59/2020/QH14 \xa7i\xd2u 1 Ph\xb5m vi \xaei\xd2u ch\xd8nh\r"
            b"\x93Ref\x94 \x81\x8d\x8f\x90\x9d 2026\r"
        )
        raw = build_msdoc_bytes(compressed_text=compressed)

        pure_md, _ = extract_msdoc_pure(raw)
        assert "\ufffd" not in pure_md
        assert "\u201cRef\u201d" in pure_md

        doc_path = tmp_path / "tcvn3_compressed.doc"
        doc_path.write_bytes(raw)
        md, info = extract_office(doc_path, kind="doc")
        assert "\ufffd" not in md
        assert "Luật Doanh nghiệp" in md
        assert info.get("legacy_encoding") == "tcvn3"

    def test_word6_doc_without_table_stream_extracts_fib_fc_min_mac_text(
        self, tmp_path: Path
    ) -> None:
        """An early Word 6.0/95 `.doc` lacking `1Table`/`0Table` extracts text from `WordDocument[fcMin:fcMac]`."""
        import struct

        from tests.office_fixture_support import build_ole2_compound
        from wlh.processor.extractors.office import extract_office

        body = (
            b"MINISTRY OF JUSTICE DECREE 1995 ON CIVIL REGISTRATION\r"
            b"Article 1. Civil status registries shall be maintained by local authorities.\r"
        )
        fc_min = 0x0100
        fc_mac = fc_min + len(body)
        wdoc = bytearray(fc_mac + 64)
        struct.pack_into("<HH", wdoc, 0x0000, 0xA5DC, 0x0065)  # Word 6.0 wIdent, nFib
        struct.pack_into("<II", wdoc, 0x0018, fc_min, fc_mac)  # fcMin, fcMac
        wdoc[fc_min:fc_mac] = body

        raw = build_ole2_compound([("WordDocument", bytes(wdoc))])
        doc_path = tmp_path / "word6_legacy.doc"
        doc_path.write_bytes(raw)

        md, info = extract_office(doc_path, kind="doc")
        assert "MINISTRY OF JUSTICE DECREE 1995 ON CIVIL REGISTRATION" in md
        assert "Article 1. Civil status registries" in md
        assert info["extractor"] == "pure_python_ole2_msdoc"

    def test_corrupted_piece_table_offset_outside_word_document_bounds_falls_back_gracefully(
        self, tmp_path: Path
    ) -> None:
        """A `.doc` whose `1Table` piece table offset points beyond `WordDocument` bounds raises `doc_text_unusable` without images and falls back to `msdoc_embedded_image_ocr` when BLIP images exist."""
        import struct

        import pytest

        from tests.office_fixture_support import (
            build_ole2_compound,
            officeart_blip_in_fbse,
            render_text_image,
        )
        from wlh.core import WlhError
        from wlh.processor.extractors.office import extract_office

        # Piece points to byte offset 0x00F00000 (far outside the 4096-byte WordDocument stream)
        plc_pcd = struct.pack("<II", 0, 80) + struct.pack("<HIH", 0, 0x00F00000, 0)
        clx = b"\x02" + struct.pack("<I", len(plc_pcd)) + plc_pcd
        wdoc = bytearray(0x0200)
        struct.pack_into("<HH", wdoc, 0x0000, 0xA5EC, 0x00C1)
        struct.pack_into("<H", wdoc, 0x000A, 0x0200)
        struct.pack_into("<I", wdoc, 0x004C, 80)
        struct.pack_into("<II", wdoc, 0x01A2, 0, len(clx))

        raw_no_img = build_ole2_compound([("WordDocument", bytes(wdoc)), ("1Table", clx)])
        with pytest.raises(WlhError) as exc_info:
            extract_office(raw_no_img, kind="doc")
        assert exc_info.value.code == "doc_text_unusable"

        page_png = render_text_image(
            "EMERGENCY TAX RELIEF ORDER 2026\nArticle 1. All filing deadlines are extended.",
            "png",
        )
        data_stream = officeart_blip_in_fbse(0xF01E, 0x6E0, page_png)
        raw_with_img = build_ole2_compound(
            [("WordDocument", bytes(wdoc)), ("1Table", clx), ("Data", data_stream)]
        )
        md, info = extract_office(raw_with_img, kind="doc", ocr=LOCAL_ENGLISH_OCR)
        assert "EMERGENCY TAX RELIEF ORDER 2026" in md.upper()
        assert info["extractor"] == "msdoc_embedded_image_ocr"
        assert "doc_stream_missing" in info["attempts"]


class TestSpreadsheetExtraction:
    def test_trailing_empty_column_trim_cuts_every_row_at_last_nonempty_column(self) -> None:
        """The shared Office table helper trims each row to the widest column that holds a value."""
        from wlh.processor.extractors.office.tables import trim_trailing_empty_cols

        rows = [
            ["Category", "Rate", "", "", ""],
            ["Standard", "", "", "", ""],
            ["Foreign", "21", "", "", ""],
        ]
        assert trim_trailing_empty_cols(rows) == [
            ["Category", "Rate"],
            ["Standard", ""],
            ["Foreign", "21"],
        ]

    def test_xlsx_sheet_with_formatted_far_right_column_yields_compact_two_column_table(
        self, tmp_path: Path
    ) -> None:
        """An `.xlsx` sheet whose column 300 carries fill formatting / an empty string but no value is rendered as a compact 2-column Markdown table."""
        import io
        from typing import Any

        import openpyxl  # type: ignore[import-untyped]
        from openpyxl.styles import PatternFill  # type: ignore[import-untyped]

        from tests.office_fixture_support import markdown_table_rows
        from wlh.processor.extractors.office import extract_office, extract_xlsx

        workbook: Any = openpyxl.Workbook()
        sheet: Any = workbook.active
        sheet.title = "Tax"
        sheet.append(["Category", "Rate"])
        sheet.append(["Standard", 20])
        sheet.append(["Foreign", 21])
        sheet.cell(row=1, column=300).value = ""
        for row_idx in (1, 2, 3):
            sheet.cell(row=row_idx, column=300).fill = PatternFill("solid", fgColor="FFFF00")
        buf = io.BytesIO()
        workbook.save(buf)
        raw = buf.getvalue()

        reread: Any = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        assert all(len(r) == 300 for r in reread.worksheets[0].iter_rows(values_only=True))
        reread.close()

        md, extractor = extract_xlsx(raw)
        rows = markdown_table_rows(md)
        assert rows[0] == ["Category", "Rate"]
        assert rows[1] == ["---", "---"]
        assert ["Standard", "20"] in rows
        assert ["Foreign", "21"] in rows
        assert all(len(row) == 2 for row in rows)
        assert extractor == "openpyxl"

        xlsx_path = tmp_path / "wide_format.xlsx"
        xlsx_path.write_bytes(raw)
        facade_md, info = extract_office(xlsx_path, kind="xlsx")
        assert all(len(row) == 2 for row in markdown_table_rows(facade_md))
        assert info["extractor"] == "openpyxl"

    def test_xls_sheet_with_empty_string_cells_in_last_column_yields_compact_two_column_table(
        self, tmp_path: Path
    ) -> None:
        """A BIFF8 `.xls` sheet whose rows reach column 256 only through empty-string cells is rendered as a compact 2-column Markdown table."""
        from typing import Any

        import xlrd  # type: ignore[import-untyped]

        from tests.office_fixture_support import build_biff8_xls_bytes, markdown_table_rows
        from wlh.processor.extractors.office import extract_office, extract_xls

        raw = build_biff8_xls_bytes(
            "Tax",
            {
                (0, 0): "Category",
                (0, 1): "Rate",
                (1, 0): "Standard",
                (1, 1): 20.0,
                (2, 0): "Foreign",
                (2, 1): 21.0,
                (0, 255): "",
                (1, 255): "",
                (2, 255): "",
            },
        )
        book: Any = xlrd.open_workbook(file_contents=raw)
        assert book.sheet_by_index(0).ncols == 256

        md, extractor = extract_xls(raw)
        rows = markdown_table_rows(md)
        assert rows[0] == ["Category", "Rate"]
        assert rows[1] == ["---", "---"]
        assert ["Standard", "20"] in rows
        assert ["Foreign", "21"] in rows
        assert all(len(row) == 2 for row in rows)
        assert extractor == "xlrd"

        xls_path = tmp_path / "wide_format.xls"
        xls_path.write_bytes(raw)
        facade_md, info = extract_office(xls_path, kind="xls")
        assert all(len(row) == 2 for row in markdown_table_rows(facade_md))
        assert info["extractor"] == "xlrd"


class TestRichTextTableExtraction:
    def test_plain_font_rtf_with_cell_and_row_controls_yields_markdown_pipe_table(self) -> None:
        """A non-legacy-font RTF whose `\\trowd ... \\cell ... \\row` rows form a 3x3 table is extracted as a Markdown pipe table."""
        from tests.office_fixture_support import markdown_table_rows
        from wlh.processor.extractors.office import extract_rtf

        rtf = (
            b"{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Times New Roman;}}\n"
            b"\\f0\\fs24 MINISTRY OF FINANCE TAX SCHEDULE 2026\\par\n"
            b"\\trowd\\cellx3000\\cellx6000\\cellx9000\n"
            b"\\pard\\intbl Category\\cell Rate\\cell Note\\cell\\row\n"
            b"\\trowd\\cellx3000\\cellx6000\\cellx9000\n"
            b"\\pard\\intbl Standard\\cell 20%\\cell Domestic\\cell\\row\n"
            b"\\trowd\\cellx3000\\cellx6000\\cellx9000\n"
            b"\\pard\\intbl Reduced\\cell 10%\\cell SME\\cell\\row\n"
            b"\\pard Article 2. This schedule takes effect immediately.\\par}"
        )
        md, _ = extract_rtf(rtf)
        rows = markdown_table_rows(md)
        assert rows[:2] == [["Category", "Rate", "Note"], ["---", "---", "---"]]
        assert ["Standard", "20%", "Domestic"] in rows
        assert ["Reduced", "10%", "SME"] in rows
        assert "MINISTRY OF FINANCE TAX SCHEDULE 2026" in md
        assert "Article 2. This schedule takes effect immediately." in md

    def test_tcvn3_font_rtf_with_cell_and_row_controls_yields_recovered_markdown_pipe_table(
        self,
    ) -> None:
        """A `.VnTime` (TCVN3) RTF table built from `\\cell`/`\\row` controls becomes a Markdown pipe table with Vietnamese text recovered."""
        from tests.office_fixture_support import markdown_table_rows
        from wlh.processor.extractors.office import extract_rtf

        rtf = (
            b"{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 .VnTime;}}\n"
            b"\\f0\\fs24 Lu\\'cbt Doanh nghi\\'d6p s\\'e8 59/2020/QH14 \\'a7i\\'d2u 1 Ph\\'b5m vi "
            b"\\'aei\\'d2u ch\\'d8nh\\par\n"
            b"\\trowd\\cellx3000\\cellx6000\n"
            b"\\pard\\intbl Lo\\'b9i\\cell Rate\\cell\\row\n"
            b"\\trowd\\cellx3000\\cellx6000\n"
            b"\\pard\\intbl Standard\\cell 20%\\cell\\row\n"
            b"\\trowd\\cellx3000\\cellx6000\n"
            b"\\pard\\intbl Reduced\\cell 10%\\cell\\row\n"
            b"\\pard \\'a7i\\'d2u 2. Hi\\'d6u l\\'f1c thi h\\'b5nh\\par}"
        )
        md, info = extract_rtf(rtf)
        rows = markdown_table_rows(md)
        assert rows[:2] == [["Loại", "Rate"], ["---", "---"]]
        assert ["Standard", "20%"] in rows
        assert ["Reduced", "10%"] in rows
        assert "Luật Doanh nghiệp" in md
        assert info.get("legacy_encoding") == "tcvn3"


class TestWordTextBoxExtraction:
    def test_document_number_only_inside_text_box_appears_exactly_once_in_markdown(
        self, tmp_path: Path
    ) -> None:
        """A `.docx` whose document number lives only in a text box (`w:txbxContent`, DrawingML choice + VML fallback) surfaces that number once in the Markdown."""
        import io
        from typing import Any

        from docx import Document  # type: ignore[import-untyped]
        from docx.oxml import parse_xml  # type: ignore[import-untyped]

        from wlh.processor.extractors.office import extract_docx, extract_office

        number = "Số: 123/2026/NĐ-CP"
        textbox_run = (
            '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
            ' xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
            ' xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
            ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
            ' xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"'
            ' xmlns:v="urn:schemas-microsoft-com:vml">'
            "<mc:AlternateContent>"
            '<mc:Choice Requires="wps"><w:drawing>'
            '<wp:anchor distT="0" distB="0" distL="0" distR="0" simplePos="0" relativeHeight="1"'
            ' behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1">'
            '<wp:simplePos x="0" y="0"/>'
            '<wp:positionH relativeFrom="column"><wp:posOffset>0</wp:posOffset></wp:positionH>'
            '<wp:positionV relativeFrom="paragraph"><wp:posOffset>0</wp:posOffset></wp:positionV>'
            '<wp:extent cx="2000000" cy="400000"/><wp:wrapNone/>'
            '<wp:docPr id="1" name="Text Box 1"/>'
            "<a:graphic>"
            '<a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
            "<wps:wsp><wps:spPr/><wps:txbx><w:txbxContent>"
            f"<w:p><w:r><w:t>{number}</w:t></w:r></w:p>"
            "</w:txbxContent></wps:txbx><wps:bodyPr/></wps:wsp>"
            "</a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice>"
            "<mc:Fallback><w:pict>"
            '<v:shape id="TextBox1" style="width:150pt;height:30pt"><v:textbox><w:txbxContent>'
            f"<w:p><w:r><w:t>{number}</w:t></w:r></w:p>"
            "</w:txbxContent></v:textbox></v:shape>"
            "</w:pict></mc:Fallback>"
            "</mc:AlternateContent></w:r>"
        )
        word_doc: Any = Document()
        header_paragraph: Any = word_doc.add_paragraph()
        header_paragraph._p.append(parse_xml(textbox_run))
        word_doc.add_paragraph("NGHỊ ĐỊNH QUY ĐỊNH CHI TIẾT VỀ QUẢN LÝ THUẾ")
        word_doc.add_paragraph("Điều 1. Phạm vi điều chỉnh của Nghị định này.")
        buf = io.BytesIO()
        word_doc.save(buf)
        raw = buf.getvalue()

        md = extract_docx(raw)
        assert md.count("123/2026/NĐ-CP") == 1
        assert "NGHỊ ĐỊNH QUY ĐỊNH CHI TIẾT VỀ QUẢN LÝ THUẾ" in md

        docx_path = tmp_path / "textbox_number.docx"
        docx_path.write_bytes(raw)
        facade_md, info = extract_office(docx_path, kind="docx")
        assert facade_md.count("123/2026/NĐ-CP") == 1
        assert info["extractor"] == "python_docx"
