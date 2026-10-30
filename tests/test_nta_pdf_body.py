"""Complete NTA PDF body contracts, independently derived from original-page images.

The entry response is an explicitly finite one-document protocol fixture. Original
parents are provenance only: their other mandatory PDFs are not silently omitted
from a purported complete parent-document replay. Production rules are unchanged.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psycopg
import yaml
from lxml import html as lxml_html  # type: ignore[import-untyped]

from .conftest import run_wlh_cli
from .source_replay_support import (
    ARTIFACTS,
    ROOT,
    assert_offline_reparse,
    collect_artifact,
    normalized,
    replay_config,
    source_server,
)

FIXTURES = ROOT / "tests/fixtures/dd-520"
ENTRY = "/law/index.htm"


def _collect(
    case: str, directory: Path, env: dict[str, str]
) -> tuple[dict[str, Any], list[dict[str, Any]], Path, str]:
    if not (FIXTURES / "pdf-body-provenance.json").is_file():
        import pytest

        pytest.skip("Private fixture directory tests/fixtures/dd-520 not present")
    manifest: dict[str, Any] = json.loads((FIXTURES / "pdf-body-provenance.json").read_text())
    evidence: dict[str, Any] = manifest["cases"][case]
    payload = (FIXTURES / evidence["pdf"]).read_bytes()
    parent = (FIXTURES / evidence["parent"]).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == evidence["pdf_sha256"]
    assert hashlib.sha256(parent).hexdigest() == evidence["parent_sha256"]
    path = urlsplit(str(evidence["url"])).path
    lxml_module: Any = lxml_html
    dom: Any = lxml_module.fromstring(parent.decode(evidence["parent_encoding"]))
    anchors: list[Any] = dom.xpath('//a[@href=$path]', path=path)
    assert len(anchors) == 1
    assert normalized(anchors[0].text_content()) == normalized(evidence["official_link_label"])
    catalog = (
        '<!doctype html><html lang="ja"><meta charset="utf-8"><body>'
        '<!-- Finite one-document protocol fixture; no NTA coverage claim. -->'
        '<h1>法令等</h1><div id="bodyArea">'
        f'<a href="{html.escape(path)}">{html.escape(evidence["official_link_label"])}</a>'
        '</div></body></html>'
    ).encode()
    routes = {ENTRY: (catalog, "text/html; charset=utf-8"), path: (payload, "application/pdf")}
    name = f"nta-pdf-body-{case}"
    with source_server(routes, ["www.nta.go.jp"]) as (origin, requests):
        config = replay_config("dd-520", origin, directory)
        cfg: dict[str, Any] = yaml.safe_load(config.read_text())
        cfg["scheduler"]["budget"].update(requests=12, documents=4, seconds=90)
        config.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
        report, rows = collect_artifact("dd-520", config, env, name, requests)
        artifact = ARTIFACTS / name
        (artifact / "input-provenance.json").write_text(
            json.dumps({"scope": manifest["purpose"], **evidence}, ensure_ascii=False, indent=2)
        )
        (artifact / "entry-protocol-fixture.html").write_bytes(catalog)
        (artifact / "original.pdf").write_bytes(payload)
        assert ENTRY in requests and path in requests, report
        assert set(requests) <= {ENTRY, path, "/robots.txt"}, requests
        with psycopg.connect(env["WLH_DATABASE_URL"]) as connection:
            archived = connection.execute(
                "SELECT archive_ref FROM wlh.acquisition "
                "WHERE request_url=%s AND state='archived'", (origin + path,)
            ).fetchall()
        assert (evidence["pdf_sha256"],) in archived, "Complete official PDF was not archived"
        for row in rows:
            assert row["detail_url"] == origin + path
            assert row["original_hash"] == evidence["pdf_sha256"]
    return report, rows, config, name


def _blocked(report: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    """A content rejection must be explicit; network errors are not a quality gate."""
    errors = " ".join(str(code) for code in report["result"].get("errors", [])).lower()
    content_error = re.search(
        r"(?:pdf|text|glyph|encoding|font|ocr|quality).*"
        r"(?:unverified|unreadable|invalid|corrupt|failed|missing|unavailable|blocked|rejected)",
        errors,
    )
    return (
        not rows
        and report["exit_code"] != 0
        and report["result"].get("status") not in {None, "complete"}
        and content_error is not None
    )


def _save_checks(name: str, checks: dict[str, Any]) -> None:
    (ARTIFACTS / name / "body-checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2) + "\n"
    )


def _reparse(config: Path, env: dict[str, str], name: str, blocked: bool) -> None:
    if not blocked:
        assert_offline_reparse(config, env, name)
        return
    code, stdout, stderr = run_wlh_cli("reparse", str(config), "--no-progress", env=env)
    result: dict[str, Any] = json.loads(stdout)
    (ARTIFACTS / name / "reparse.json").write_text(
        json.dumps({"exit_code": code, "result": result, "stderr": stderr}, indent=2)
    )
    assert result["report"]["external_requests"] == 0
    with psycopg.connect(env["WLH_DATABASE_URL"]) as connection:
        count = connection.execute("SELECT count(*) FROM wlh.document_row").fetchone()
    assert count == (0,), "Offline reparse must not turn rejected raw text into a delivered version"
    assert code != 0 and result.get("status") not in {None, "complete"}
    assert (
        _blocked({"exit_code": code, "result": result}, [])
        or "zero_targets_discovered" in result.get("errors", [])
    ), "Reparse may have no committed resource, but must not silently succeed"


class TestNtaPdfBody:
    def test_native_layer_with_unreadable_characters_recovers_visible_japanese_or_blocks_delivery(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """All three original pages are intact, including the page-three comparison table.

        A nonempty native layer, stripped controls, or a copied directory title do
        not prove that the visible Japanese body survived. Anchors span all pages.
        """
        report, rows, config, name = _collect("unreadable-native", tmp_path, initialized_db)
        blocked = _blocked(report, rows)
        markdown = rows[0]["markdown"] if len(rows) == 1 else ""
        controls = Counter(
            f"U+{ord(char):04X}" for char in markdown
            if (ord(char) < 32 and char not in "\n\r\t") or 127 <= ord(char) <= 159
        )
        anchors = [
            "特定地域内にある土地等の評価",
            "その土地等の状況は、課税時期の現況によることに留意する。",
            "国税局長が調整率を別途定めている場合には",
            "特定非常災害発生日以後に取得",
            "土地等の評価で個別に減額",
            "宅地造成費相当額から算定した金額とする方法が考えられる。",
        ]
        missing = [anchor for anchor in anchors if normalized(anchor) not in normalized(markdown)]
        checks = {
            "explicitly_blocked": blocked, "row_count": len(rows),
            "control_characters": dict(controls), "replacement_characters": markdown.count("\ufffd"),
            "missing_visually_verified_anchors": missing,
        }
        _save_checks(name, checks)
        if not blocked:
            assert len(rows) == 1, report
            assert not controls and "\ufffd" not in markdown, checks
            assert not missing, "Removing bad glyphs cannot replace recovery of the visible Japanese"
            assert report["result"]["status"] == "complete", report
        _reparse(config, initialized_db, name, blocked)

    def test_boxed_clause_continuation_preserves_order_and_commentary_boundary_or_blocks_delivery(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """Page two's イ–ホ and ⑵ remain inside the clause, before 【解説】.

        Commentary 3 continues from the bottom of page two to page three. Moving
        the boxed list into that sentence changes its meaning despite retaining text.
        """
        report, rows, config, name = _collect("boxed-continuation", tmp_path, initialized_db)
        blocked = _blocked(report, rows)
        markdown = rows[0]["markdown"] if len(rows) == 1 else ""
        # Standalone printed folios are presentation, not intervening legal content.
        body = normalized(re.sub(r"(?m)^\s*(?:25|26|27)\s*$", "", markdown))
        boundary = body.find(normalized("【解説】"))
        clauses = [
            "イ 賃借人の固定リース料",
            "ロ 指数又はレートに応じて決まる賃借人の変動リース料",
            "ハ 残価保証に係る賃借人による支払見込額",
            "ニ 賃借人が行使することが合理的に確実である購入オプションの行使価額",
            "ホ リースの解約に対する違約金の賃借人による支払額",
            "本文⑵の「賃貸人の会計リース期間」を「賃借人の会計リース期間」と読み替える。",
        ]
        positions = [body.find(normalized(clause)) for clause in clauses]
        continuation = "リースがファイナンス・リースに該当するかどうかの具体的な判定基準については"
        checks = {
            "explicitly_blocked": blocked, "row_count": len(rows),
            "commentary_boundary": boundary, "boxed_clause_positions": positions,
            "boxed_clauses_before_commentary": all(0 <= position < boundary for position in positions),
            "boxed_clauses_in_order": positions == sorted(positions),
            "commentary_sentence_contiguous": normalized(continuation) in body,
            "last_page_body_present": normalized("本通達の注書２において明らかにしている。") in body,
        }
        _save_checks(name, checks)
        if not blocked:
            assert len(rows) == 1, report
            assert checks["boxed_clauses_before_commentary"], checks
            assert checks["boxed_clauses_in_order"] and checks["commentary_sentence_contiguous"], checks
            assert checks["last_page_body_present"], checks
            assert report["result"]["status"] == "complete", report
        _reparse(config, initialized_db, name, blocked)
