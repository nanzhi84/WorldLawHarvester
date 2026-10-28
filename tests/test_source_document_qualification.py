"""Document eligibility contracts from complete official responses, independent of parsers.

The NTA directory and answers have the same title. A title blacklist or excluding
every page containing links would lose real guidance; only the external document
and navigation outcomes define this contract. No product implementation is read.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from lxml import html  # type: ignore[import-untyped]

from .conftest import run_wlh_cli
from .source_replay_support import (
    ARTIFACTS,
    ROOT,
    Route,
    collect_artifact,
    normalized,
    replay_config,
    source_server,
)

FIXTURES = ROOT / "tests" / "fixtures" / "dd-520" / "qualification"
DIRECTORY = "/law/joho-zeikaishaku/sozoku/091124/index.htm"
ANSWERS = "/law/joho-zeikaishaku/sozoku/091124/qa.htm"
NOTICE = "/law/tsutatsu/kihon/zeirishi/01.htm"
ANNEXES = ["/law/tsutatsu/kihon/zeirishi/02.htm", "/law/tsutatsu/kihon/zeirishi/03.htm"]
NAME = "nta-directory-qualification"


def _recorded_routes() -> tuple[dict[str, Route], dict[str, Any]]:
    manifest: dict[str, Any] = json.loads((FIXTURES / "manifest.json").read_text())
    routes: dict[str, Route] = {}
    for route in manifest["routes"]:
        payload = (FIXTURES / route["fixture"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == route["sha256"]
        assert len(payload) == route["bytes"]
        routes[route["path"]] = payload, route["content_type"]
    return routes, manifest


def _answer_contract() -> tuple[list[str], list[str]]:
    """Read the official answer headings and full substantive paragraphs, not parser output."""
    html_parser: Any = html
    original: Any = html_parser.fromstring((FIXTURES / "answers.html").read_bytes().decode("cp932"))
    body: Any = original.xpath('//*[@id="bodyArea"]')[0]
    for image in body.xpath(".//img"):
        image.text = image.get("alt", "")
        image.drop_tag()
    headings = [str(element.text_content()).strip() for element in body.xpath(".//h3")]
    paragraphs = [str(element.text_content()).strip() for element in body.xpath(".//p")]
    return headings, [text for text in paragraphs if len(normalized(text)) >= 30]


class TestSourceDocumentQualification:
    def test_question_directory_preserves_answer_navigation_without_delivering_link_only_text(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """Fail before implementation if the Q1–Q11 menu becomes a legal document.

        Boundaries: same-title answers and a substantive formal notice with annex
        links must remain deliverable. All nine official payloads are untrimmed;
        unavailable unrelated paths return 404, so this is not a source census.
        Existing required-image/attachment tests remain the independent gate.
        """
        routes, manifest = _recorded_routes()
        artifact = ARTIFACTS / NAME
        artifact.mkdir(parents=True, exist_ok=True)
        (artifact / "original-payloads.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )
        with source_server(routes, ["www.nta.go.jp"]) as (origin, requests):
            config = replay_config("dd-520", origin, tmp_path)
            cfg: dict[str, Any] = yaml.safe_load(config.read_text())
            assert cfg["entry_urls"] == [origin + "/law/index.htm"]
            cfg["budget"].update(requests=1500, documents=1000, seconds=75)
            config.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
            report, rows = collect_artifact("dd-520", config, initialized_db, NAME, requests)

        by_path: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_path.setdefault(urlsplit(str(row["detail_url"])).path, []).append(row)
        answer_rows = by_path.get(ANSWERS, [])
        answer_markdown = "\n".join(str(row["markdown"]) for row in answer_rows)
        answer_text = normalized(answer_markdown)
        headings, paragraphs = _answer_contract()
        missing_headings = [heading for heading in headings if normalized(heading) not in answer_text]
        missing_paragraphs = [text for text in paragraphs if normalized(text) not in answer_text]
        notice_rows = by_path.get(NOTICE, [])
        notice_text = normalized("\n".join(str(row["markdown"]) for row in notice_rows))
        notice_clauses = [
            "官総6－11", "官人6－4", "平成14年4月1日以降は、これにより取り扱われたい",
            "別紙2「廃止通達一欄表」に掲げる法令解釈通達", "新たに税理士法基本通達として制定する",
        ]
        checks = {
            "official_csv_entry_requested": "/law/index.htm" in requests,
            "question_directory_requested": DIRECTORY in requests,
            "question_directory_not_delivered": DIRECTORY not in by_path,
            "answer_page_requested": ANSWERS in requests,
            "same_title_answer_page_delivered": bool(answer_rows),
            "all_22_question_and_answer_headings_preserved": len(headings) == 22 and not missing_headings,
            "all_substantive_answer_paragraphs_preserved": bool(paragraphs) and not missing_paragraphs,
            "answer_date_not_inferred_from_url_or_referenced_law": bool(answer_rows)
            and all(row["effective_at"] is None for row in answer_rows),
            "substantive_notice_with_annex_links_delivered": bool(notice_rows),
            "notice_instruction_and_repeal_content_preserved": all(
                normalized(clause) in notice_text for clause in notice_clauses
            ),
            "both_official_notice_annexes_requested": all(path in requests for path in ANNEXES),
            "both_official_notice_annexes_delivered": all(path in by_path for path in ANNEXES),
            "markdown_has_no_replacement_characters_or_jump_navigation": all(
                "\ufffd" not in str(row["markdown"]) and "本文へジャンプ" not in str(row["markdown"])
                for row in answer_rows + notice_rows
            ),
        }
        code, stdout, stderr = run_wlh_cli("reparse", str(config), "--no-progress", env=initialized_db)
        reparse: dict[str, Any] = json.loads(stdout) if stdout else {}
        (artifact / "reparse.json").write_text(
            json.dumps({"exit_code": code, "result": reparse, "stderr": stderr},
                       ensure_ascii=False, indent=2) + "\n"
        )
        checks["offline_reparse_uses_no_external_source_requests"] = (
            code == 0 and reparse.get("status") == "complete"
            and reparse.get("report", {}).get("external_requests") == 0
        )
        result = {
            "checks": checks,
            "missing_answer_headings": missing_headings,
            "missing_answer_paragraphs": missing_paragraphs,
            "source_payload_count": len(routes),
            "answer_substantive_paragraph_count": len(paragraphs),
            "delivered_paths": sorted(by_path),
            "collect_status": report["result"].get("status"),
            "bounded_replay_limitation": "Unrecorded unrelated paths return 404; no source completion claim.",
        }
        (artifact / "assertions.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
        assert all(checks.values()), result
