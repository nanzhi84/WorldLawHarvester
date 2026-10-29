"""Black-box acceptance tests for Japanese source pipeline recovery (Issue #6).

Covers:
1. Offline reparse recovery of archived targets that failed initial collection.
2. Japanese e-Gov empty figure placeholder and KFS inline part/symbol image alt-text preservation.
3. Japanese PDF margin control-character resilience, merged-cell ruled table preservation,
   and right-to-left vertical court decision column reconstruction.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import psycopg
import pymupdf as _fitz  # type: ignore[import-untyped]
import yaml
from psycopg.rows import dict_row

from wlh.adapters.web import Adapter as WebAdapter
from wlh.adapters.web_fields import parse_html
from wlh.config import load_file
from wlh.engine.objects import Objects
from wlh.extraction.pdf_scan import extract_pdf

from .conftest import run_wlh_cli
from .source_replay_support import (
    ROOT,
    assert_offline_reparse,
    collect_artifact,
    normalized,
    replay_config,
    source_server,
)

fitz: Any = _fitz

_OCR_CFG: dict[str, Any] = {
    "engine": "br-ocr-v1",
    "languages": "jpn+jpn_vert+eng",
    "model": "br-ocr-v1",
    "timeout_seconds": 30,
    "poll_seconds": 0.2,
    "max_pages": 50,
    "dpi": 200,
    "max_page_pixels": 20000000,
    "max_result_bytes": 16777216,
}


def _read_source_rows(env: dict[str, str], source_id: str) -> list[dict[str, Any]]:
    with psycopg.connect(env["WLH_DATABASE_URL"]) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            rows: list[dict[str, Any]] = cursor.execute(
                "SELECT r.id AS resource_id, d.* FROM wlh.document_row d "
                "JOIN wlh.resource r ON r.current_version_id=d.version_id "
                "JOIN wlh.frontier f ON f.id=r.frontier_id "
                "WHERE f.source_id=%s ORDER BY d.detail_url, d.row_index",
                (source_id,),
            ).fetchall()
    objects = Objects()
    for row in rows:
        row["content_md"] = objects.get(row["markdown_hash"]).decode("utf-8")
    return rows


class TestOfflineReparseArchivedTargetRecovery:
    def test_archived_target_blocked_during_collect_recovers_on_offline_reparse(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """An archived KFS target that failed during collect is recovered offline by reparse."""
        ruling_html = (
            '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            "<title>（令和2年7月1日裁決）</title></head><body>"
            '<div id="main"><div id="contents">'
            "<h1>（令和2年7月1日裁決）</h1>"
            "<h2>主文</h2>"
            "<p>本件審査請求を棄却する。</p>"
            "<h2>理由</h2>"
            '<p><img src="img/parts/no_img/01.gif" alt="1"> (1) '
            "審査請求人の主張及び原処分庁の認定並びに別表一のとおり、本件処分は適法である。</p>"
            "</div></div></body></html>"
        ).encode("utf-8")
        routes: dict[str, tuple[bytes, str]] = {
            "/service/index.html": (
                b'<!doctype html><html><body><a href="/service/JP/index.html">JP</a></body></html>',
                "text/html; charset=utf-8",
            ),
            "/service/JP/index.html": (
                b'<!doctype html><html><body><a href="/service/JP/120/index.html">120</a></body></html>',
                "text/html; charset=utf-8",
            ),
            "/service/JP/120/index.html": (
                b'<!doctype html><html><body><a href="/service/JP/120/01/index.html">Case 01</a></body></html>',
                "text/html; charset=utf-8",
            ),
            "/service/JP/120/01/index.html": (ruling_html, "text/html; charset=utf-8"),
        }
        with source_server(routes, ["www.kfs.go.jp"]) as (origin, requests):
            config = replay_config("dd-582", origin, tmp_path)
            cfg: dict[str, Any] = yaml.safe_load(config.read_text())
            original_xpath = cfg["documents"]["content_xpath"]
            cfg["documents"]["content_xpath"] = '//*[@id="unmatched_during_initial_collect"]'
            config.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
            _, collect_rows = collect_artifact(
                "dd-582", config, initialized_db, "kfs-reparse-recovery-collect", requests
            )
            assert "/service/JP/120/01/index.html" in requests
            assert collect_rows == []
            cfg["documents"]["content_xpath"] = original_xpath
            config.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))

        code, stdout, stderr = run_wlh_cli("reparse", str(config), "--no-progress", env=initialized_db)
        assert code == 0, (stdout, stderr)
        result: dict[str, Any] = json.loads(stdout)
        assert result["status"] == "complete", result
        assert result["report"]["external_requests"] == 0

        rows = _read_source_rows(initialized_db, "dd-582")
        assert len(rows) == 1
        content_md = rows[0]["content_md"]
        for expected in ("(1)", "主文", "理由", "審査請求人", "原処分庁", "別表一"):
            assert expected in content_md, (expected, content_md)


class TestJapaneseInlineGlyphAndEmptyAttachmentHandling:
    def test_egov_law_with_empty_figure_source_and_no_attached_files_delivers_statute_text(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """An e-Gov law with an empty <Fig src=\"\"/> placeholder and empty attached_files delivers its statute text."""
        xml_bytes = (
            '<Law Era="Showa" Lang="ja" LawType="Act" Num="100" Year="25">'
            "<LawNum>昭和二十五年法律第百号</LawNum>"
            "<LawBody>"
            "<LawTitle>テスト適正化法</LawTitle>"
            "<MainProvision>"
            '<Article Num="1">'
            "<ArticleTitle>第一条</ArticleTitle>"
            '<Paragraph Num="1">'
            "<ParagraphNum/>"
            "<ParagraphSentence>"
            "<Sentence>この法律は、テストの適正化を図ることを目的とする。</Sentence>"
            "</ParagraphSentence>"
            "</Paragraph>"
            "</Article>"
            "<FigStruct><Fig src=\"\"/></FigStruct>"
            "</MainProvision>"
            "</LawBody>"
            "</Law>"
        ).encode("utf-8")
        revision_id = "325AC0000000100_20240401_506AC0000000001"
        law_info = {
            "law_type": "Act",
            "law_id": "325AC0000000100",
            "law_num": "昭和二十五年法律第百号",
            "promulgation_date": "1950-04-01",
        }
        revision_info = {
            "law_revision_id": revision_id,
            "law_type": "Act",
            "law_title": "テスト適正化法",
            "amendment_promulgate_date": "2024-04-01",
            "amendment_enforcement_date": "2024-04-01",
            "amendment_enforcement_comment": None,
            "amendment_scheduled_enforcement_date": None,
            "amendment_law_id": "506AC0000000001",
            "amendment_law_num": "令和六年法律第一号",
            "repeal_status": "None",
            "repeal_date": None,
            "current_revision_status": "CurrentEnforced",
        }
        law_payload: dict[str, Any] = {
            "attached_files_info": {"image_data": "", "attached_files": []},
            "law_info": law_info,
            "revision_info": revision_info,
            "law_full_text": base64.b64encode(xml_bytes).decode("ascii"),
        }
        catalog_payload: dict[str, Any] = {
            "total_count": 1,
            "count": 1,
            "laws": [{"law_info": law_info, "revision_info": revision_info}],
        }
        routes: dict[str, tuple[bytes, str]] = {
            "/": (
                b'<!doctype html><html lang="ja"><body><h1>e-Gov</h1></body></html>',
                "text/html; charset=utf-8",
            ),
            "/api/2/laws?offset=0": (
                json.dumps(catalog_payload, ensure_ascii=False).encode("utf-8"),
                "application/json",
            ),
            f"/api/2/law_data/{revision_id}": (
                json.dumps(law_payload, ensure_ascii=False).encode("utf-8"),
                "application/json",
            ),
        }
        with source_server(routes, ["laws.e-gov.go.jp"]) as (origin, requests):
            config = replay_config("dd-517", origin, tmp_path)
            report, rows = collect_artifact(
                "dd-517", config, initialized_db, "egov-empty-fig-placeholder", requests
            )
            assert report["result"].get("status") == "complete", report
            assert "egov_attachments_unverified" not in report["result"].get("errors", [])
            assert len(rows) == 1
            assert "第一条" in rows[0]["markdown"]
            assert "この法律は、テストの適正化を図ることを目的とする。" in rows[0]["markdown"]

    def test_kfs_ruling_with_inline_unit_and_symbol_part_images_preserves_alt_text_in_markdown(
        self,
    ) -> None:
        """KFS ruling HTML with inline part/symbol images preserves their alt text without raising html_images_unverified."""
        html_bytes = (
            '<!doctype html><html lang="ja"><head><meta charset="utf-8"></head><body>'
            '<div id="main"><div id="contents">'
            "<h1>（令和3年5月10日裁決）</h1>"
            "<p>"
            '<img src="img/parts/no_img/01.gif" alt="まる1"> '
            '本件土地の面積は100<img src="img/parts/tani/m2.gif" alt="平方メートル" width="16" height="16">であり、'
            '<img src="img/parts/iroha/i.gif" alt="まるイ" width="16" height="16">の区分及び'
            '<img src="img/kan_ho.gif" alt="○保" width="15" height="15">の記号が付されている。'
            "</p>"
            "</div></div></body></html>"
        ).encode("utf-8")
        cfg = load_file(ROOT / "sources" / "dd-582.yaml").model_dump()
        adapter = WebAdapter(cfg)
        tree, _, _ = parse_html(html_bytes, "utf-8")
        acquisition = {
            "actual_url": "https://www.kfs.go.jp/service/JP/120/01/index.html",
            "request_url": "https://www.kfs.go.jp/service/JP/120/01/index.html",
            "charset": "utf-8",
        }
        markdown_text = adapter.html_markdown(tree, acquisition)
        for expected in ("まる1", "平方メートル", "まるイ", "○保"):
            assert expected in markdown_text, (expected, markdown_text)


class TestJapaneseAttachmentZipAndImageOcrRecovery:
    def test_egov_law_with_external_figure_zip_attachment_archives_and_delivers_statute(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """An e-Gov law with a non-empty <Fig src> and attached_files fetches the attachment ZIP and delivers statute text."""
        revision_id = "325AC0000000101_20240401_506AC0000000001"
        xml_bytes = (
            '<Law Era="Showa" Lang="ja" LawType="Act" Num="101" Year="25">'
            "<LawNum>昭和二十五年法律第百一号</LawNum>"
            "<LawBody>"
            "<LawTitle>添付様式適正化法</LawTitle>"
            "<MainProvision>"
            '<Article Num="1">'
            "<ArticleTitle>第一条</ArticleTitle>"
            '<Paragraph Num="1">'
            "<ParagraphNum/>"
            "<ParagraphSentence>"
            "<Sentence>この法律は、添付様式の適正化を図ることを目的とする。</Sentence>"
            "</ParagraphSentence>"
            "</Paragraph>"
            "</Article>"
            '<FigStruct><Fig src="./pict/325AC0000000101_001.pdf"/></FigStruct>'
            "</MainProvision>"
            "</LawBody>"
            "</Law>"
        ).encode("utf-8")
        law_info = {
            "law_type": "Act",
            "law_id": "325AC0000000101",
            "law_num": "昭和二十五年法律第百一号",
            "promulgation_date": "1950-04-01",
        }
        revision_info = {
            "law_revision_id": revision_id,
            "law_type": "Act",
            "law_title": "添付様式適正化法",
            "amendment_promulgate_date": "2024-04-01",
            "amendment_enforcement_date": "2024-04-01",
            "amendment_enforcement_comment": None,
            "amendment_scheduled_enforcement_date": None,
            "amendment_law_id": "506AC0000000001",
            "amendment_law_num": "令和六年法律第一号",
            "repeal_status": "None",
            "repeal_date": None,
            "current_revision_status": "CurrentEnforced",
        }
        law_payload: dict[str, Any] = {
            "attached_files_info": {
                "image_data": "",
                "attached_files": [
                    {
                        "law_revision_id": revision_id,
                        "src": "./pict/325AC0000000101_001.pdf",
                    }
                ],
            },
            "law_info": law_info,
            "revision_info": revision_info,
            "law_full_text": base64.b64encode(xml_bytes).decode("ascii"),
        }
        catalog_payload: dict[str, Any] = {
            "total_count": 1,
            "count": 1,
            "laws": [{"law_info": law_info, "revision_info": revision_info}],
        }

        pdf_doc = fitz.open()
        pdf_page = pdf_doc.new_page(width=595, height=842)
        pdf_page.insert_text((72, 100), "様式第一号 申請書", fontsize=14, fontname="japan")
        pdf_bytes: bytes = pdf_doc.tobytes()
        pdf_doc.close()

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("pict/325AC0000000101_001.pdf", pdf_bytes)
        zip_bytes = zip_buf.getvalue()

        routes: dict[str, tuple[bytes, str]] = {
            "/": (
                b'<!doctype html><html lang="ja"><body><h1>e-Gov</h1></body></html>',
                "text/html; charset=utf-8",
            ),
            "/api/2/laws?offset=0": (
                json.dumps(catalog_payload, ensure_ascii=False).encode("utf-8"),
                "application/json",
            ),
            f"/api/2/law_data/{revision_id}": (
                json.dumps(law_payload, ensure_ascii=False).encode("utf-8"),
                "application/json",
            ),
            f"/api/2/attachment/{revision_id}": (
                zip_bytes,
                "application/zip",
            ),
        }
        with source_server(routes, ["laws.e-gov.go.jp"]) as (origin, requests):
            config = replay_config("dd-517", origin, tmp_path)
            report, rows = collect_artifact(
                "dd-517", config, initialized_db, "egov-attachment-zip", requests
            )
            assert f"/api/2/attachment/{revision_id}" in requests
            assert report["result"].get("status") == "complete", report
            assert len(rows) == 1
            rows[0]["content_md"] = rows[0]["markdown"]
            assert "この法律は、添付様式の適正化を図ることを目的とする。" in rows[0]["content_md"]
            assert "pict/325AC0000000101_001.pdf" in rows[0]["content_md"]

        assert_offline_reparse(config, initialized_db, "egov-attachment-zip")

    def test_kfs_ruling_with_diagram_image_archives_image_and_extracts_ocr_text(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """A KFS ruling with a diagram image archives the image, delivers only the ruling resource, and includes the diagram."""
        ruling_html = (
            '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            "<title>（令和2年7月2日裁決）</title></head><body>"
            '<div id="main"><div id="contents">'
            "<h1>（令和2年7月2日裁決）</h1>"
            "<h2>主文</h2><p>本件審査請求を棄却する。</p>"
            "<h2>理由</h2><p>本件取引の関係図は次のとおりである。</p>"
            '<p><img src="img/kankei01.png" width="400" height="120" alt="別図1 取引関係図"></p>'
            "</div></div></body></html>"
        ).encode("utf-8")

        img_doc = fitz.open()
        img_page = img_doc.new_page(width=400, height=120)
        img_page.insert_text((24, 64), "請求人 取引先 課税庁", fontsize=18, fontname="japan")
        png_bytes: bytes = img_page.get_pixmap(dpi=200).tobytes("png")
        img_doc.close()

        routes: dict[str, tuple[bytes, str]] = {
            "/service/index.html": (
                b'<!doctype html><html><body><a href="/service/JP/index.html">JP</a></body></html>',
                "text/html; charset=utf-8",
            ),
            "/service/JP/index.html": (
                b'<!doctype html><html><body><a href="/service/JP/120/index.html">120</a></body></html>',
                "text/html; charset=utf-8",
            ),
            "/service/JP/120/index.html": (
                b'<!doctype html><html><body><a href="/service/JP/120/02/index.html">Case 02</a></body></html>',
                "text/html; charset=utf-8",
            ),
            "/service/JP/120/02/index.html": (ruling_html, "text/html; charset=utf-8"),
            "/service/JP/120/02/img/kankei01.png": (png_bytes, "image/png"),
        }
        with source_server(routes, ["www.kfs.go.jp"]) as (origin, requests):
            config = replay_config("dd-582", origin, tmp_path)
            report, rows = collect_artifact(
                "dd-582", config, initialized_db, "kfs-diagram-ocr", requests
            )
            assert "/service/JP/120/02/img/kankei01.png" in requests
            assert report["result"].get("status") == "complete", report
            assert len(rows) == 1
            assert rows[0]["detail_url"].endswith("/service/JP/120/02/index.html")
            assert not rows[0]["detail_url"].endswith("/service/JP/120/index.html")
            rows[0]["content_md"] = rows[0]["markdown"]
            assert "本件審査請求を棄却する。" in rows[0]["content_md"]
            assert "別図1 取引関係図" in rows[0]["content_md"]

        assert_offline_reparse(config, initialized_db, "kfs-diagram-ocr")


class TestJapanesePdfRecoveryAndLayoutReconstruction:
    def test_pdf_with_blank_control_character_spans_and_margin_folio_delivers_native_japanese_text(
        self, tmp_path: Path
    ) -> None:
        """A Japanese PDF with readable native body text and C0 control chars in a margin folio delivers native text without OCR."""
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        body_lines = [
            (100, "主文"),
            (130, "本件控訴を棄却する。"),
            (160, "控訴費用は控訴人の負担とする。"),
            (200, "事実及び理由"),
            (230, "第一 控訴の趣旨"),
            (260, "１ 控訴人は、原判決を取り消すとの判決を求めた。"),
        ]
        for y, line_text in body_lines:
            page.insert_text((72, y), line_text, fontsize=12, fontname="japan")
        page.insert_text((280, 795), "\x02\x01\x03", fontsize=10, fontname="helv")
        pdf_path = tmp_path / "margin_control_folio.pdf"
        doc.save(str(pdf_path))
        doc.close()

        markdown_text, info, _, parsers = extract_pdf(pdf_path, _OCR_CFG, "generic")
        assert info["analysis"]["suspected_scan_pages"] == []
        assert info["ocr"] is None
        assert "ocr" not in parsers
        assert "本件控訴を棄却する。" in markdown_text
        for ctrl in ("\x01", "\x02", "\x03"):
            assert ctrl not in markdown_text

    def test_ruled_pdf_table_with_merged_cells_preserves_all_cell_values_across_columns(
        self,
    ) -> None:
        """Ruled PDF tables with merged cells render without raising pdf_layout_unverified and preserve cell values."""
        pdf_path = ROOT / "tests" / "fixtures" / "dd-520" / "nta-trust-tax-merged-cell-annex.pdf"
        markdown_text, _, _, _ = extract_pdf(pdf_path, _OCR_CFG, "generic")
        norm_md = normalized(markdown_text)
        for expected in (
            "旧公益信託設定時",
            "受託者変更時",
            "課税関係なし",
            "信託財産の給付時",
            "受給者",
            "贈与税課税",
            "新公益信託設定時",
            "委託者の地位異動時",
        ):
            assert normalized(expected) in norm_md, expected

    def test_vertical_japanese_court_decision_pdf_reconstructs_right_to_left_columns_into_sentences(
        self, tmp_path: Path
    ) -> None:
        """A vertical Japanese court decision PDF reconstructs single-character columns right-to-left into sentences."""
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        columns = [
            (340, "上告代理人〇〇の上告理由について。原判決の認定及び判断は正当として是認することができる。"),
            (380, "理由"),
            (420, "上告費用は上告人の負担とする。"),
            (460, "本件上告を棄却する。"),
            (500, "主文"),
        ]
        for x, col_text in columns:
            for idx, ch in enumerate(col_text):
                page.insert_text((x, 100 + idx * 18), ch, fontsize=12, fontname="japan")
        pdf_path = tmp_path / "vertical_court_decision.pdf"
        doc.save(str(pdf_path))
        doc.close()

        markdown_text, _, _, _ = extract_pdf(pdf_path, _OCR_CFG, "generic")
        for phrase in (
            "主文",
            "本件上告を棄却する。",
            "上告費用は上告人の負担とする。",
            "理由",
            "上告代理人〇〇の上告理由について。",
        ):
            assert phrase in markdown_text, (phrase, markdown_text)
        assert (
            markdown_text.index("主文")
            < markdown_text.index("本件上告を棄却する。")
            < markdown_text.index("上告費用は上告人の負担とする。")
            < markdown_text.index("理由")
            < markdown_text.index("上告代理人〇〇の上告理由について。")
        )
