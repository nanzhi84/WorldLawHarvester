"""Independent public CLI contracts for visible dates and complete co-signers.

Written before implementation. Real packets are opt-in and immutable; isolated
freeze receipts never approve the pending originals or the formal benchmark.
"""

from __future__ import annotations

import copy
import os
import platform
import sys
import tempfile
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.benchmark_markdown_support import (
    HTML,
    ROOT,
    freeze_cases,
    isolated_review,
    read_rows,
    reviewed_case,
    sha256,
    validate_receipts,
    write_json,
)

TITLE = "完整采购公告"
BODY = "本公告的全部采购要求与责任主体均应完整保留，不得增添任何无原文依据的信息。"
BUYER = "广州市番禺城市排水管理有限公司"
AGENT = "广州国讯招标有限公司"
SIGNATURES = f"<p>采购人：{BUYER}</p><p>招标代理机构：{AGENT}</p>"
DECLARATION = "var aid = 'notice'; var tm = '2026-04-13 10:48:34'; var totalpage = '1';"
WRITE = "document.write(tm.substring(0, 16));"
REAL_IDS = ("content_dd-198_cand_201", "content_dd-209_cand_207")
REAL_HASHES = (
    "e899d7fe01199ec1dddd85383cb54537fd066f13aefd53e8f0964026e788c1a5",
    "88c5cf1e409bff7702c904edb61bd27e86869bf9b4f8fd9afb5a275d75253a1c",
)


def scripts(declaration: str, writer: str = WRITE) -> str:
    return f"<script>{declaration}</script><p>发布时间：<script>{writer}</script></p>"


@dataclass(frozen=True)
class MetadataCase:
    name: str
    body: str
    published_at: str | None = "2026-04-13"
    issuer: str | None = None
    accepted: bool = False
    visible_date: str = ""


CASES = (
    MetadataCase(
        "literal-variable-visible-minute",
        scripts(DECLARATION),
        accepted=True,
        visible_date="2026-04-13 10:48",
    ),
    MetadataCase(
        "literal-variable-visible-day",
        scripts(
            "var published = '2026-04-13 10:48:34';", "document.write(published.substring(0, 10));"
        ),
        accepted=True,
        visible_date="2026-04-13",
    ),
    MetadataCase(
        "static-visible-day",
        "<p>发布时间：2026-04-13</p>",
        accepted=True,
        visible_date="2026-04-13",
    ),
    MetadataCase("unused-variable", f"<script>{DECLARATION}</script><p>发布时间：</p>"),
    MetadataCase("missing-declaration", scripts("var unrelated = 'notice';")),
    MetadataCase("declaration-after-output", scripts(WRITE, DECLARATION)),
    MetadataCase("commented-declaration", scripts(f"/* {DECLARATION} */")),
    MetadataCase("commented-output", scripts(DECLARATION, f"// {WRITE}")),
    MetadataCase("dead-output", scripts(DECLARATION, f"if (false) {{ {WRITE} }}")),
    MetadataCase("dead-declaration", scripts(f"if (false) {{ {DECLARATION} }}")),
    MetadataCase("duplicate-declaration", scripts(DECLARATION + "var tm='2027-05-16';")),
    MetadataCase("rewritten-before-output", scripts(DECLARATION, "tm='2027-05-16';" + WRITE)),
    MetadataCase(
        "rewritten-between-scripts",
        scripts(DECLARATION).replace(
            "</script><p>", "</script><script>tm='2027-05-16';</script><p>"
        ),
    ),
    MetadataCase("unknown-declaration-call", scripts(DECLARATION + "rewriteDate();")),
    MetadataCase("unknown-output-call", scripts(DECLARATION, "document.write(formatDate(tm));")),
    MetadataCase("function-local-variable", scripts(f"function unused() {{ {DECLARATION} }}")),
    MetadataCase("computed-date", scripts("var tm = '2026-' + '04-13 10:48:34';")),
    MetadataCase(
        "incomplete-visible-date", scripts(DECLARATION, "document.write(tm.substring(0,7));")
    ),
    MetadataCase(
        "offset-visible-date", scripts(DECLARATION, "document.write(tm.substring(5,10));")
    ),
    MetadataCase("cross-block-date-fragments", "<p>2026-</p><p>04-13</p>"),
    MetadataCase(
        "inert-script-type",
        scripts(DECLARATION).replace("<script>", '<script type="application/json">', 1),
    ),
    MetadataCase("complete-co-signers", SIGNATURES, None, f"{BUYER}、{AGENT}", True),
    MetadataCase("existing-single-signer", SIGNATURES, None, BUYER, True),
    MetadataCase("invented-extra-signer", SIGNATURES, None, f"{BUYER}、{AGENT}、不存在的审批机关"),
    MetadataCase(
        "invented-name-from-source-parts", SIGNATURES, None, f"{BUYER}、广州国讯排水管理有限公司"
    ),
    MetadataCase(
        "cross-block-entity-fragments",
        f"<p>采购人：广州市番禺城市</p><p>排水管理有限公司</p><p>代理：{AGENT}</p>",
        None,
        f"{BUYER}、{AGENT}",
    ),
    MetadataCase(
        "script-only-extra-signer",
        SIGNATURES + "<script>var unused='未署名咨询有限公司';</script>",
        None,
        f"{BUYER}、{AGENT}、未署名咨询有限公司",
    ),
)


@pytest.fixture(scope="session")
def wlh_env() -> dict[str, str]:
    return {}


@pytest.fixture(scope="module")
def metadata_run() -> tuple[Path, Path]:
    base = ROOT / "work/benchmark-expansion-20261106/native-metadata-acceptance"
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=base))
    artifacts = (
        ROOT / "artifacts/benchmark-expansion-20261106/native-metadata-acceptance" / work.name
    )
    write_json(
        artifacts / "environment.json",
        {
            "python": sys.version,
            "platform": platform.platform(),
            "work": str(work),
            "command": "WLH_NATIVE_METADATA_PACKET=work/benchmark-expansion-20261106/"
            "native40-text04-pending/pending-native40-text04.jsonl "
            ".venv/bin/python -m pytest --noconftest -q tests/test_benchmark_native_metadata.py",
            "packet": os.environ.get("WLH_NATIVE_METADATA_PACKET"),
            "prerequisites": "Editable checkout and optional immutable original packet; "
            "no network, credentials, database, S3, model or GPU needed.",
            "scope": "Evidence admission using isolated copies; no formal GT approval.",
        },
    )
    return work, artifacts


@pytest.fixture(scope="module")
def metadata_cases(metadata_run: tuple[Path, Path]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for case in CASES:
        raw = f"<!doctype html><html><body><h1>{TITLE}</h1><p>{BODY}</p>{case.body}</body></html>"
        tail = (
            f"采购人：{BUYER}\n\n招标代理机构：{AGENT}"
            if case.issuer is not None
            else f"发布时间：{case.visible_date}"
        )
        row = reviewed_case(
            metadata_run[0], case.name, raw, "text/html", f"# {TITLE}\n\n{BODY}\n\n{tail}"
        )
        row["ground_truth"]["records"][0].update(
            title=TITLE,
            published_at=case.published_at,
            issuer=case.issuer,
        )
        row["review"] = isolated_review(row["payload_sha256"], row["ground_truth"])
        rows[case.name] = row
    return rows


@pytest.fixture(scope="module")
def receipt_results(
    metadata_run: tuple[Path, Path], metadata_cases: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    work, artifacts = metadata_run
    result = validate_receipts(
        list(metadata_cases.values()), work / "receipts", artifacts / "receipts"
    )
    accepted = {r["id"] for r in result["accepted"]}
    rejected = {r["id"]: r for r in result["rejected"]}
    write_json(
        artifacts / "receipt-contract-results.json",
        [
            {
                "id": c.name,
                "expected_accepted": c.accepted,
                "accepted": c.name in accepted,
                "passed": (c.name in accepted) == c.accepted,
                "reasons": rejected.get(c.name, {}).get("reasons", []),
            }
            for c in CASES
        ],
    )
    return result


class TestBenchmarkNativeMetadata:
    @pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
    def test_receipts_admit_visible_metadata_and_reject_unattested_components(
        self,
        receipt_results: dict[str, Any],
        case: MetadataCase,
    ) -> None:
        accepted = {row["id"] for row in receipt_results["accepted"]}
        assert (case.name in accepted) == case.accepted, receipt_results["rejected"]
        if not case.accepted:
            row = next(row for row in receipt_results["rejected"] if row["id"] == case.name)
            field = "issuer" if case.issuer else "published_at"
            assert f"record_0:unattested_{field}" in row["reasons"], row

    def test_receipts_preserve_complete_input_and_truth(
        self,
        receipt_results: dict[str, Any],
        metadata_cases: dict[str, dict[str, Any]],
    ) -> None:
        assert receipt_results["inputs_unchanged"] and receipt_results["raw_receipts_unchanged"]
        rows = receipt_results["accepted"] + receipt_results["rejected"]
        assert {row["id"] for row in rows} == set(metadata_cases)
        for row in receipt_results["accepted"]:
            assert row["ground_truth"] == metadata_cases[row["id"]]["ground_truth"]

    @pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
    def test_freeze_preserves_admitted_metadata_and_refuses_unsupported_claims(
        self,
        metadata_run: tuple[Path, Path],
        metadata_cases: dict[str, dict[str, Any]],
        case: MetadataCase,
    ) -> None:
        work, artifacts = metadata_run
        result = freeze_cases([metadata_cases[case.name]], work / case.name, artifacts / case.name)
        write_json(
            artifacts / case.name / "contract-result.json",
            {
                "expected_accepted": case.accepted,
                "actual_accepted": result["returncode"] == 0,
                "passed": (result["returncode"] == 0) == case.accepted,
            },
        )
        assert result["inputs_unchanged"]
        assert (result["returncode"] == 0) == case.accepted, result
        assert result["published"] == case.accepted
        if case.accepted:
            assert result["complete_payload_and_truth_preserved"]
        else:
            assert not result["exported"]


@pytest.fixture(scope="module")
def real_packets(metadata_run: tuple[Path, Path]) -> Generator[list[dict[str, Any]], None, None]:
    configured = os.environ.get("WLH_NATIVE_METADATA_PACKET")
    if not configured:
        pytest.skip("Set WLH_NATIVE_METADATA_PACKET to the immutable pending review packet")
    packet_path = Path(configured).resolve()
    before = sha256(packet_path.read_bytes())
    packets = {p["source"]["id"]: p for p in read_rows(packet_path)}
    rows: list[dict[str, Any]] = []
    for ident, expected_hash in zip(REAL_IDS, REAL_HASHES, strict=True):
        packet = packets[ident]
        source = packet["source"]
        assert source["mime_type"] == "text/html"
        assert sha256(source["raw_payload"].encode()) == source["payload_sha256"] == expected_hash
        row = copy.deepcopy(source)
        row["ground_truth"] = copy.deepcopy(packet["proposed_truth"])
        row["review"] = isolated_review(expected_hash, row["ground_truth"])
        rows.append(row)
    yield rows
    after = sha256(packet_path.read_bytes())
    write_json(
        metadata_run[1] / "original-immutability.json",
        {
            "before": before,
            "after": after,
            "unchanged": before == after,
        },
    )
    assert before == after


class TestBenchmarkArchivedNativeMetadata:
    def test_complete_originals_contain_declared_output_and_both_full_signatures(
        self,
        metadata_run: tuple[Path, Path],
        real_packets: list[dict[str, Any]],
    ) -> None:
        date_dom = HTML.fromstring(real_packets[0]["raw_payload"])
        scripts = [s.text or "" for s in date_dom.xpath("//script")]
        declaration = [s for s in scripts if "var tm = '2026-04-13 10:48:34';" in s]
        assert len(declaration) == 1
        assert any(s.strip() == "document.write(tm.substring(0, 16));" for s in scripts)
        signer_dom = HTML.fromstring(real_packets[1]["raw_payload"])
        paragraphs = [p.text_content().strip() for p in signer_dom.xpath("//p")]
        assert f"采购人：{BUYER}" in paragraphs
        assert f"招标代理机构：{AGENT}" in paragraphs
        assert real_packets[0]["ground_truth"]["records"][0]["published_at"] == "2026-04-13"
        assert real_packets[1]["ground_truth"]["records"][0]["issuer"] == f"{BUYER}、{AGENT}"
        write_json(
            metadata_run[1] / "original-semantics.json",
            {
                "date_declaration": declaration[0],
                "date_writer": "document.write(tm.substring(0, 16));",
                "signatures": [f"采购人：{BUYER}", f"招标代理机构：{AGENT}"],
                "payload_sha256": dict(zip(REAL_IDS, REAL_HASHES, strict=True)),
            },
        )

    def test_real_receipts_preserve_complete_truth_and_accept_grounded_metadata(
        self,
        metadata_run: tuple[Path, Path],
        real_packets: list[dict[str, Any]],
    ) -> None:
        work, artifacts = metadata_run
        result = validate_receipts(
            real_packets, work / "real-receipts", artifacts / "real-receipts"
        )
        assert result["inputs_unchanged"] and result["raw_receipts_unchanged"]
        assert {row["id"] for row in result["accepted"]} == set(REAL_IDS), result["rejected"]
        assert not result["rejected"]
        expected = {row["id"]: row["ground_truth"] for row in real_packets}
        assert all(row["ground_truth"] == expected[row["id"]] for row in result["accepted"])

    @pytest.mark.parametrize("ident", REAL_IDS)
    def test_isolated_real_freeze_keeps_complete_raw_payload_and_all_truth_fields(
        self,
        metadata_run: tuple[Path, Path],
        real_packets: list[dict[str, Any]],
        ident: str,
    ) -> None:
        work, artifacts = metadata_run
        row = next(row for row in real_packets if row["id"] == ident)
        result = freeze_cases([row], work / ident, artifacts / ident)
        assert result["inputs_unchanged"]
        assert result["returncode"] == 0 and result["published"], result
        assert result["complete_payload_and_truth_preserved"]
