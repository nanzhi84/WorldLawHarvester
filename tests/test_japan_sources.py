"""Japanese source delivery contracts derived from official responses, not parser code."""

from __future__ import annotations

import base64
import json
import re
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

import pytest
import yaml

from .source_replay_support import (
    ROOT,
    assert_offline_reparse,
    collect_artifact,
    normalized,
    recorded_scope_routes,
    rendered_table_rows,
    replay_config,
    source_server,
)


def _egov_routes(*, truncated: bool = False) -> dict[str, tuple[bytes, str]]:
    directory = ROOT / "tests" / "fixtures" / "dd-517"
    records: list[dict[str, Any]] = json.loads((directory / "laws-v2-selected.json").read_text())
    first: dict[str, Any] = {"total_count": 2, "count": 1, "laws": [records[0]]}
    if not truncated:
        first["next_offset"] = 1
    terminal = {"total_count": 2, "count": 1, "laws": [records[1]]}
    routes = {
        "/": ((directory / "egov-home.html").read_bytes(), "text/html; charset=utf-8"),
        "/api/2/laws?offset=0": (json.dumps(first).encode(), "application/json"),
        "/api/2/laws?offset=1": (json.dumps(terminal).encode(), "application/json"),
    }
    for record, filename in zip(records, ["lawdata-v2-108DF0000000054.json", "lawdata-v2-pending.json"]):
        routes[f"/api/2/law_data/{record['revision_info']['law_revision_id']}"] = (
            (directory / filename).read_bytes(), "application/json"
        )
    return routes


class TestJapanLegislationCatalog:
    @pytest.mark.parametrize(("filename", "wrong_word", "legal_word", "reading"), [
        ("lawdata-v2-ruby-annotation.json", "隧ずい道", "隧道", "ずい"),
        ("lawdata-v2-school-table.json", "へヽきヽ", "へき", None),
    ])
    def test_reading_annotations_and_amending_law_labels_preserve_legal_meaning(
        self, initialized_db: dict[str, str], tmp_path: Path,
        filename: str, wrong_word: str, legal_word: str, reading: str | None,
    ) -> None:
        """Real Ruby readings/emphasis must not be concatenated into legal words.

        Amendment numbers in SupplProvision attributes belong with their 附則 labels;
        retaining only identical bare headings erases which amendment each clause serves.
        """
        name = "japan-law-annotations-" + filename.removesuffix(".json")
        directory = ROOT / "tests" / "fixtures" / "dd-517"
        payload = (directory / filename).read_bytes()
        official: dict[str, Any] = json.loads(payload)
        record = {k: official[k] for k in ("law_info", "revision_info")}
        routes = {
            "/": ((directory / "egov-home.html").read_bytes(), "text/html"),
            "/api/2/laws?offset=0": (json.dumps({"total_count": 1, "count": 1, "laws": [record]}).encode(), "application/json"),
            f"/api/2/law_data/{record['revision_info']['law_revision_id']}": (payload, "application/json"),
        }
        with source_server(routes, ["laws.e-gov.go.jp"]) as (origin, requests):
            config = replay_config("dd-517", origin, tmp_path)
            report, rows = collect_artifact("dd-517", config, initialized_db, name, requests)
            assert len(rows) == 1, report
            markdown = rows[0]["markdown"]
            assert wrong_word not in markdown, "Ruby annotation was inserted into the operative word"
            base = re.sub(r"<rt[^>]*>.*?</rt>", "", markdown, flags=re.S)
            if reading:
                assert reading in markdown, "The reading annotation must remain available separately"
                base = base.replace(f"（{reading}）", "").replace(f"({reading})", "")
            base = re.sub(r"<[^>]+>", "", base)
            assert legal_word in normalized(base)
            tree = ElementTree.fromstring(base64.b64decode(official["law_full_text"]))
            amendments = [p.get("AmendLawNum") for p in tree.findall(".//SupplProvision") if p.get("AmendLawNum")]
            lines = markdown.splitlines()
            contexts = "\n".join("\n".join(lines[i:i + 3]) for i, line in enumerate(lines) if "附則" in normalized(line))
            assert amendments
            for amendment in amendments:
                assert amendment is not None and normalized(amendment) in normalized(contexts), amendment
            assert report["result"]["status"] == "complete", report
        assert_offline_reparse(config, initialized_db, name)

    @pytest.mark.parametrize(("filename", "clause"), [
        ("conflicting-enforcement", "この法律は、昭和四十二年六月一日から施行する。"),
        ("standard-time", "明治二十一年一月一日ヨリ東経百三十五度ノ子午線ノ時ヲ以テ本邦一般ノ標準時ト定ム"),
        ("gazette-start", "官報本年七月一日ヨリ発行候条此旨相達候事"),
    ])
    def test_law_metadata_conflicting_with_explicit_commencement_clause_leaves_date_unknown(
        self, initialized_db: dict[str, str], tmp_path: Path, filename: str, clause: str
    ) -> None:
        """Real 保管金規則 wrapper says May 31, but the latest clause says June 1.

        Conflicting official evidence must not be silently converted to a certain
        effective date. The body and both pieces of original evidence remain archived.
        """
        name = f"japan-legislation-date-{filename}"
        directory = ROOT / "tests" / "fixtures" / "dd-517"
        payload = (directory / f"lawdata-v2-{filename}.json").read_bytes()
        official: dict[str, Any] = json.loads(payload)
        record = {k: official[k] for k in ("law_info", "revision_info")}
        routes = {
            "/": ((directory / "egov-home.html").read_bytes(), "text/html; charset=utf-8"),
            "/api/2/laws?offset=0": (
                json.dumps({"total_count": 1, "count": 1, "laws": [record]}).encode(),
                "application/json",
            ),
            f"/api/2/law_data/{record['revision_info']['law_revision_id']}": (
                payload, "application/json"
            ),
        }
        with source_server(routes, ["laws.e-gov.go.jp"]) as (origin, requests):
            config = replay_config("dd-517", origin, tmp_path)
            report, rows = collect_artifact("dd-517", config, initialized_db, name, requests)
            assert len(rows) == 1, report
            assert clause in rows[0]["markdown"]
            assert rows[0]["effective_at"] is None, "Conflicting metadata cannot certify legal effect"
            assert str(rows[0]["published_at"]) == official["law_info"]["promulgation_date"]
            assert report["result"]["status"] == "complete", report
        assert_offline_reparse(config, initialized_db, name)

    def test_paged_law_catalog_delivers_complete_text_and_uses_authoritative_enforcement_dates(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """Two exact official records form a bounded two-page catalog.

        Pre-implementation failures: v1 category-only discovery, missing second page,
        loss of Japanese sentence text, listing date overriding law_data's explicit
        unknown effective date, and treating an omitted terminal next_offset as error.
        The source's real 9,567-law denominator must be verified separately.
        """
        name = "japan-legislation-catalog"
        with source_server(_egov_routes(), ["laws.e-gov.go.jp"]) as (origin, requests):
            config = replay_config("dd-517", origin, tmp_path)
            report, rows = collect_artifact("dd-517", config, initialized_db, name, requests)
            assert len(rows) == 2, report
            directory = ROOT / "tests" / "fixtures" / "dd-517"
            for filename in ("lawdata-v2-108DF0000000054.json", "lawdata-v2-pending.json"):
                official: dict[str, Any] = json.loads((directory / filename).read_text())
                law = next(r for r in rows if r["title"] == official["revision_info"]["law_title"])
                tree = ElementTree.fromstring(base64.b64decode(official["law_full_text"]))
                sentences = ["".join(node.itertext()) for node in tree.findall(".//Sentence")]
                assert sentences
                missing = [s for s in sentences if normalized(s) not in normalized(law["markdown"])]
                assert not missing, f"Official law sentences lost: {missing[:3]}"
                assert str(law["published_at"]) == official["law_info"]["promulgation_date"]
                expected_effect = official["revision_info"]["amendment_enforcement_date"]
                assert (str(law["effective_at"]) if law["effective_at"] else None) == expected_effect
                assert "\ufffd" not in law["markdown"]
            assert any("offset=1" in path for path in requests), requests
            assert report["result"]["status"] == "complete", report
        assert_offline_reparse(config, initialized_db, name)

    def test_catalog_missing_next_page_before_total_is_met_cannot_report_complete(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """A synthetically interrupted catalog preserves count evidence and cannot claim full success."""
        name = "japan-legislation-truncated"
        with source_server(_egov_routes(truncated=True), ["laws.e-gov.go.jp"]) as (origin, requests):
            config = replay_config("dd-517", origin, tmp_path)
            report, _rows = collect_artifact("dd-517", config, initialized_db, name, requests)
            assert any(path.startswith("/api/2/laws") for path in requests), requests
            assert report["result"].get("status") != "complete", report
            assert report["exit_code"] != 0, report


class TestJapanTaxAppealCatalog:
    def test_ruling_with_required_image_only_annexes_cannot_deliver_incomplete_text_as_success(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """Actual JP55 ruling relies on four figure-only HTML annexes.

        This isolated public Source contract predates the generic image-completeness
        gate. A long main text does not compensate for missing substantive annexes.
        No source parser/extractor is imported and no response is mocked.
        """
        name = "japan-ruling-required-images"
        directory = ROOT / "tests" / "fixtures" / "dd-582"
        base = "/service/JP/55/01/"
        routes = {base + "index.html": ((directory / "kfs-case-55-01.html").read_bytes(), "text/html")}
        for number in range(1, 5):
            routes[base + f"{number:02}.html"] = (
                (directory / f"kfs-case-55-01-{number:02}.html").read_bytes(), "text/html"
            )
        with source_server(routes, ["www.kfs.go.jp"]) as (origin, requests):
            config = tmp_path / "dd-946.yaml"
            cfg = {
                "id": "dd-946", "adapter": "web", "archive_approved": True,
                "scope": "Isolated real Japanese ruling with required image-only legal annexes",
                "entry_urls": [origin + base + "index.html"], "allowed_hosts": ["127.0.0.1"],
                "allowed_path_prefixes": [base], "robots": False, "delay": 0,
                "fetch": {"mode": "http", "max_depth": 2, "retries": 0},
                "budget": {"requests": 20, "documents": 10, "seconds": 60, "response_bytes": 10485760},
                "metadata": {"source_name": "国税不服審判所", "document_type": "税务资料", "language": "ja", "jurisdiction": "JP"},
                "documents": {
                    "detail_url_patterns": [r"/service/JP/55/01/index\.html$"],
                    "attachment_url_pattern": r"/service/JP/55/01/0[1-4]\.html$",
                    "require_complete_text": True,
                    "detail_body": "always", "content_xpath": '//*[@id="main"]',
                    "fields": {"title": {"xpath": "(//h1)[1]"}},
                },
            }
            config.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
            report, rows = collect_artifact("dd-946", config, initialized_db, name, requests)
            assert {base + "index.html", *(base + f"{n:02}.html" for n in range(1, 5))} <= set(requests)
            assert report["result"].get("status") != "complete", report
            assert rows == [], "Missing substantive figure annexes cannot commit a complete ruling"
            assert "html_images_unverified" in json.dumps(report, ensure_ascii=False)

    def test_early_and_recent_decisions_and_older_digest_only_cases_keep_body_and_dates(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """JP decisions and MP thematic digests are distinct, truthfully named documents.

        Failure modes before implementation: newest-quarter-only collection, loss of
        pre-1992 digest-only decisions, CP932 text corruption, menu leakage, and using
        one decision's date as the effect date of a multi-case digest.
        """
        name = "japan-tax-appeal-catalog"
        with source_server(recorded_scope_routes("dd-582"), ["www.kfs.go.jp"]) as (origin, requests):
            config = replay_config("dd-582", origin, tmp_path)
            report, rows = collect_artifact("dd-582", config, initialized_db, name, requests)
            suffixes = ["/JP/43/01/index.html", "/JP/142/01/index.html", "/MP/01/0101000000.html"]
            selected = [next((r for r in rows if r["detail_url"].endswith(s)), None) for s in suffixes]
            assert all(selected), report
            for row in rows:
                assert row["effective_at"] is None
                assert "\ufffd" not in row["markdown"]
                assert "サイト内検索" not in row["markdown"]
                assert "トップに戻る" not in row["markdown"]
            early, recent, digest = selected
            assert early is not None and recent is not None and digest is not None
            assert str(early["published_at"]) == "1992-02-24"
            assert str(recent["published_at"]) == "2026-03-04"
            assert "当審判所の調査によってもこれを不相当とする理由は認められない" in early["markdown"]
            assert "ロードサービス業" in recent["markdown"]
            assert "要旨" in digest["title"]
            assert digest["published_at"] is None
            assert "昭和45年11月6日裁決" in digest["markdown"]
            assert "昭和47年" in digest["markdown"]
            assert "請求人の相続放棄の申述は有効" in digest["markdown"]
            assert any(row["detail_url"].endswith("/JP/119/01/index.html") for row in rows)
            assert not any(row["detail_url"].endswith("/JP/55/01/index.html") for row in rows)
            assert "html_images_unverified" in str(report), report
            assert report["result"]["status"] != "complete", report
            assert not report["result"]["report"]["counts"].get("request_failed"), report
        assert_offline_reparse(config, initialized_db, name)


class TestJapanTaxGuidanceCatalog:
    def test_vertical_form_without_preserved_cell_relationships_is_explicitly_blocked(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """Real NTA application form has vertical labels and merged table cells.

        Text-layer success is insufficient when a grid becomes isolated CJK letters.
        This boundary was observed visually before the PDF layout gate implementation.
        """
        name = "japan-vertical-tax-form"
        path = "/law/jimu-unei/shotoku/shinkoku/170331/besshi04.pdf"
        payload = (ROOT / "tests" / "fixtures" / "dd-520" / "nta-jimu-attachment-04.pdf").read_bytes()
        with source_server({path: (payload, "application/pdf")}, ["www.nta.go.jp"]) as (origin, requests):
            config = tmp_path / "dd-945.yaml"
            cfg = {
                "id": "dd-945", "adapter": "web", "archive_approved": True,
                "scope": "Isolated complete original NTA form, no claim of source census",
                "entry_urls": [origin + path], "allowed_hosts": ["127.0.0.1"],
                "allowed_path_prefixes": ["/law/"], "robots": False, "delay": 0,
                "fetch": {"mode": "http", "max_depth": 1, "retries": 0},
                "budget": {"requests": 4, "documents": 2, "seconds": 60, "response_bytes": 10485760},
                "metadata": {"source_name": "国税庁", "document_type": "税务资料", "language": "ja", "jurisdiction": "JP"},
                "documents": {"detail_url_patterns": [r"/besshi04\.pdf$"], "require_complete_text": True},
            }
            config.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
            report, rows = collect_artifact("dd-945", config, initialized_db, name, requests)
            assert path in requests, report
            assert report["result"].get("status") != "complete", report
            assert rows == [], "Unverified form layout must not become the current legal document"
            assert {"pdf_layout_unverified", "pdf_images_unverified"}.intersection(report["result"].get("errors", [])), report

    def test_official_tax_guidance_branches_preserve_answers_tables_and_referenced_text(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """Tax answers, written replies, notices, directions, and interpretations are reachable.

        A cover letter saying 別紙のとおり is insufficient without its referenced text;
        table cells and final qualifications are externally visible contract content.
        """
        name = "japan-tax-guidance-catalog"
        with source_server(recorded_scope_routes("dd-520"), ["www.nta.go.jp"]) as (origin, requests):
            config = replay_config("dd-520", origin, tmp_path)
            report, rows = collect_artifact("dd-520", config, initialized_db, name, requests)
            directory_paths = {
                "/law/index.htm", "/law/tsutatsu/menu.htm",
                "/law/bunshokaito/01.htm", "/law/shitsugi/01.htm",
                "/about/organization/tokyo/bunshokaito/index.htm",
                "/law/tsutatsu/kihon/shotoku/01.htm", "/law/tsutatsu/kihon/kihatsu/01.htm",
                "/law/joho-zeikaishaku/sonota/sonota.htm",
                "/law/joho-zeikaishaku/shotoku/shinkoku/shinkoku.htm",
                "/law/jimu-unei/jimu.htm", "/law/kokuji/kokuji.htm",
                "/law/bunshokaito/shotoku/02.htm", "/law/bunshokaito/gensen/03.htm",
                "/law/shitsugi/shotoku/01.htm",
            }
            assert not directory_paths.intersection(urlsplit(str(row["detail_url"])).path for row in rows), (
                "Tax navigation and case directories cannot be delivered as substantive guidance"
            )
            branches = ["/tsutatsu/", "/jimu-unei/", "/kokuji/", "/bunshokaito/", "/shitsugi/", "/joho-zeikaishaku/"]
            for branch in branches:
                assert any(branch in row["detail_url"] for row in rows), (branch, report)
            joined = "\n".join(row["markdown"] for row in rows)
            for row in rows:
                assert "\ufffd" not in row["markdown"]
                assert "本文へジャンプ" not in row["markdown"]
                assert row["effective_at"] is None
            answer = next(row for row in rows if "/shitsugi/shotoku/01/07.htm" in row["detail_url"])
            assert "【照会要旨】" in answer["markdown"] and "【回答要旨】" in answer["markdown"]
            assert "外交官として国内に居住していた期間も含めて判定" in answer["markdown"]
            notice = next(row for row in rows if "/kokuji/r0810/02.htm" in row["detail_url"])
            assert "岡崎税務署" in notice["markdown"]
            assert normalized("愛知県岡崎市羽根二丁目2番地1岡崎合同庁舎") in normalized(notice["markdown"])
            table = rendered_table_rows(
                notice["markdown"],
                lambda cell: normalized(unescape(re.sub(r"<[^>]+>", "", cell))),
            )
            assert any(len(row) == 8 and row[2] == normalized("愛知県岡崎市羽根二丁目2番地1岡崎合同庁舎")
                       and row[6] == normalized("愛知県岡崎市羽根町字北乾地50‐1岡崎合同庁舎") for row in table), (
                "The new address belongs under 改正後 and the old address under 改正前"
            )
            assert "この回答内容は国税庁としての見解" in joined
            assert "産科医療特別給付事業" in joined
            assert any("another.htm" in path for path in requests), "Referenced written question was not fetched"
            assert any("/tokyo/bunshokaito/" in row["detail_url"] for row in rows)
            assert not any(row["detail_url"].endswith("170331/index.htm") for row in rows), (
                "The parent guidance cannot deliver successfully when its required form is unverified"
            )
            assert {"pdf_layout_unverified", "pdf_images_unverified"}.intersection(report["result"].get("errors", [])), report
            assert report["result"]["status"] != "complete", report
            assert not report["result"]["report"]["counts"].get("request_failed"), report
        assert_offline_reparse(config, initialized_db, name)
