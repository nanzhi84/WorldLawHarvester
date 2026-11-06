"""Offline business acceptance of evidence-grounded benchmark annotations."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from wlh.benchmark.arbitrator import evaluate_task_agreement

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = (
    "<article><h1>示例法规</h1>"
    "<p>第一条 纳税人应当<span>如实申报</span>收入。</p>"
    "<p>第二条 主管机关应当依法保护纳税人的商业秘密。</p>"
    '<img src="/figures/schedule.png" alt="税率附表"/></article>'
)
MARKDOWN = (
    "# 示例法规\n\n第一条 纳税人应当如实申报收入。\n\n"
    "第二条 主管机关应当依法保护纳税人的商业秘密。\n\n"
    "![税率附表](https://example.gov/figures/schedule.png)"
)


def _truth(markdown: str = MARKDOWN, **metadata: Any) -> dict[str, Any]:
    return {
        "records": [
            {
                "title": "示例法规",
                "doc_type": "法律法规",
                "doc_number": None,
                "published_at": None,
                "effective_at": None,
                "issuer": None,
                "attachment_urls": [],
                "markdown": markdown,
                **metadata,
            }
        ]
    }


@pytest.fixture(scope="session")
def wlh_env() -> dict[str, str]:
    """File-only benchmark contracts need no shared database or S3 bootstrap."""
    return {}


class TestBenchmarkGrounding:
    def test_natural_stop_omission_and_invented_facts_are_rejected_with_raw_evidence(
        self, tmp_path: Path
    ) -> None:
        truths = {
            "faithful-inline-image": _truth(),
            "active-omission": _truth(
                "# 示例法规\n\n第一条 纳税人应当如实申报收入。\n\n（其余条款省略）"
            ),
            "silent-omission": _truth(
                "# 示例法规\n\n第一条 纳税人应当如实申报收入。\n\n"
                "![税率附表](https://example.gov/figures/schedule.png)"
            ),
            "invented-effective-date": _truth(effective_at="2030-01-01"),
            "invented-obligation": _truth(MARKDOWN + "\n\n第三条 逾期申报一律罚款十万元。"),
        }
        attachment_title = "令和8年8月7日（政府調達　第146号）"
        attachment_url = (
            "https://www.kanpo.go.jp/20260807/20260807c00146/pdf/20260807c00146full00010048.pdf"
        )
        truths["faithful-attachment-page"] = _truth(
            f"# {attachment_title}\n\n[一括PDF]({attachment_url})",
            title=attachment_title,
            doc_type="其他",
            attachment_urls=[attachment_url],
        )
        payload_hash = hashlib.sha256(PAYLOAD.encode()).hexdigest()
        sources = [
            {
                "id": sample_id,
                "task_type": "content_page",
                "source_id": "dd-999",
                "jurisdiction": "CN",
                "request_url": "https://example.gov/legal/document",
                "mime_type": "text/html",
                "raw_payload": PAYLOAD,
                "payload_sha256": payload_hash,
            }
            for sample_id in truths
        ]
        attachment = next(row for row in sources if row["id"] == "faithful-attachment-page")
        attachment["raw_payload"] = (
            (ROOT / "tests/fixtures/benchmark/kanpo-20260807c00146.html")
            .read_bytes()
            .decode("utf-8")
        )
        attachment["payload_sha256"] = hashlib.sha256(
            attachment["raw_payload"].encode()
        ).hexdigest()
        attachment["request_url"] = (
            "https://www.kanpo.go.jp/20260807/20260807c00146/20260807c00146full00010048f.html"
        )
        payload_hashes = {row["id"]: row["payload_sha256"] for row in sources}
        receipts = [
            {
                "id": sample_id,
                "payload_sha256": payload_hashes[sample_id],
                "prompt_tokens": 300,
                "output_tokens": 200,
                "finish_reason": "stop",
                "text": json.dumps(truth, ensure_ascii=False, indent=2),
            }
            for sample_id, truth in truths.items()
        ]
        bundle, completions = tmp_path / "input.jsonl", tmp_path / "completions.jsonl"
        for path, rows in ((bundle, sources), (completions, receipts)):
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
        original_receipts = completions.read_bytes()
        output = tmp_path / "validated"
        command = [
            sys.executable,
            "-m",
            "wlh.benchmark.annotator",
            "--validate-completions",
            str(completions),
            "--input",
            str(bundle),
            "--output-dir",
            str(output),
        ]
        result = subprocess.run(
            command,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=40,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "HF_HUB_OFFLINE": "1"},
        )
        (tmp_path / "cli-result.json").write_text(
            json.dumps(
                {
                    "command": command,
                    "exit": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        assert result.returncode != 0, "Natural stop does not prove faithful complete extraction"
        accepted = [
            json.loads(line) for line in (output / "accepted.jsonl").read_text().splitlines()
        ]
        rejected = [
            json.loads(line) for line in (output / "rejected.jsonl").read_text().splitlines()
        ]
        faithful = {"faithful-inline-image", "faithful-attachment-page"}
        assert {row["id"] for row in accepted} == faithful
        assert {row["id"] for row in rejected} == set(truths) - faithful
        assert all(row["reasons"] for row in rejected)
        by_id = {row["id"]: row for row in receipts}
        for row in accepted + rejected:
            assert row["completion"]["text"] == by_id[row["id"]]["text"]
        assert accepted[0]["ground_truth"] == truths["faithful-inline-image"]
        assert completions.read_bytes() == original_receipts

    def test_equal_length_different_bodies_require_arbitration(self) -> None:
        first = "企业必须缴纳税款。" * 12
        second = "船舶禁止驶入港口。" * 12
        assert len(first) == len(second)
        agreement, reasons = evaluate_task_agreement("content_page", _truth(first), _truth(second))
        assert agreement is False, "Body length agreement is not content agreement"
        assert reasons

    def test_same_body_with_markdown_spacing_preserves_consensus(self) -> None:
        agreement, reasons = evaluate_task_agreement(
            "content_page", _truth(), _truth(MARKDOWN.replace("\n\n", "\n"))
        )
        assert agreement is True, reasons
