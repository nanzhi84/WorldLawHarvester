"""File-only public benchmark execution and independent Markdown observation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from lxml import html as html_library  # type: ignore[import-untyped]
from markdown_it import MarkdownIt

from wlh.benchmark.arbitrator import validate_ground_truth_integrity

ROOT = Path(__file__).resolve().parents[1]

HTML: Any = html_library

RENDERER = MarkdownIt("commonmark", {"html": True}).enable("table")

TITLE = "完整计算规则"

PARAGRAPH = "第一条 主管机关必须逐项核对全部登记资料，依法保护每一名申请人的权益。"

TAIL = "最后一条 本文全部表格与附注具有相同效力，任何单位不得任意省略或者更改。"

FORGED = "第三条 所有申请人必须额外缴纳十万元保证金，否则永久取消其申请资格。"

TABLE = (
    '<table><tr><th rowspan="2">类别</th><th colspan="2">核算标准</th></tr>\n'
    "<tr><th>公式</th><th>金额</th></tr>\n"
    '<tr><td rowspan="2">甲类</td><td>面积 m<sup>2</sup></td><td>100元</td></tr>\n'
    "<tr><td>系数 H<sub>2</sub>O</td><td>200元</td></tr></table>"
)

INLINE = "第二条 <strong>核算结果</strong>按面积 m<sup>2</sup>与系数 H<sub>2</sub>O登记。"

MARKDOWN = f"# {TITLE}\n\n{PARAGRAPH}\n\n{INLINE}\n\n{TABLE}\n\n{TAIL}"

SOURCE = f"<article><h1>{TITLE}</h1><p>{PARAGRAPH}</p><p>{INLINE}</p>{TABLE}<p>{TAIL}</p></article>"

REAL_IDS = ("content_dd-496_cand_349", "content_dd-513_cand_435")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().split("\n") if line]


def compact_text(text: str) -> str:
    return "".join(text.split())


def rendered_dom(markdown: str) -> Any:
    return HTML.fromstring("<div>" + RENDERER.render(markdown) + "</div>")


def table_signature(table: Any) -> list[list[tuple[str, int, int]]]:
    return [
        [
            (
                compact_text(cell.text_content()),
                int(cell.get("rowspan", "1")),
                int(cell.get("colspan", "1")),
            )
            for cell in row.xpath("./th | ./td")
        ]
        for row in table.xpath("./tr | ./thead/tr | ./tbody/tr | ./tfoot/tr")
    ]


def _truth(markdown: str) -> dict[str, Any]:
    return {
        "records": [
            {
                "title": TITLE,
                "doc_type": "法律法规",
                "doc_number": None,
                "published_at": None,
                "effective_at": None,
                "issuer": None,
                "attachment_urls": [],
                "markdown": markdown,
            }
        ]
    }


def reviewed_case(work: Path, ident: str, raw: str, mime: str, markdown: str) -> dict[str, Any]:
    archive = work / f"{ident}.body"
    archive.write_text(raw, encoding="utf-8")
    digest, truth = sha256(raw.encode()), _truth(markdown)
    return {
        "id": ident,
        "task_type": "content_page",
        "source_id": "dd-999",
        "jurisdiction": "CN",
        "request_url": "https://example.gov/legal/rules",
        "mime_type": mime,
        "raw_payload": raw,
        "payload_sha256": digest,
        "ground_truth": truth,
        "provenance": {
            "kind": "archived_response",
            "acquisition_id": f"markdown-evidence-{ident}",
            "archive_path": str(archive),
            "archive_sha256": digest,
            "charset": "utf-8",
            "representation": "http_body",
        },
        "review": isolated_review(digest, truth),
    }


def isolated_review(digest: str, truth: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "approved",
        "reviewer": "isolated-markdown-evidence-acceptance",
        "payload_sha256": digest,
        "ground_truth_sha256": sha256(
            json.dumps(truth, ensure_ascii=False, sort_keys=True).encode()
        ),
        "notes": "Isolated test-only freeze receipt; no real draft or dataset approval.",
    }


def _run(args: list[str], artifact: Path) -> dict[str, Any]:
    command = [sys.executable, *args]
    process = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=45,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
        },
    )
    result = {
        "command": command,
        "returncode": process.returncode,
        "stdout": process.stdout,
        "stderr": process.stderr,
    }
    write_json(artifact, result)
    return result


def public_integrity(row: dict[str, Any]) -> dict[str, Any]:
    valid, reason = validate_ground_truth_integrity(
        row["ground_truth"],
        row["task_type"],
        row["raw_payload"],
        row["request_url"],
        mime_type=row["mime_type"],
        jurisdiction=row["jurisdiction"],
    )
    return {"id": row["id"], "valid": valid, "reason": reason}


def validate_receipts(rows: list[dict[str, Any]], work: Path, artifacts: Path) -> dict[str, Any]:
    work.mkdir(parents=True, exist_ok=True)
    bundle, completions = work / "inputs.jsonl", work / "completions.jsonl"
    receipts = [
        {
            "id": row["id"],
            "payload_sha256": row["payload_sha256"],
            "prompt_tokens": 2000,
            "output_tokens": 2000,
            "finish_reason": "stop",
            "text": json.dumps(row["ground_truth"], ensure_ascii=False),
        }
        for row in rows
    ]
    _rows(bundle, rows)
    _rows(completions, receipts)
    before = {str(p): sha256(p.read_bytes()) for p in (bundle, completions)}
    result = _run(
        [
            "-m",
            "wlh.benchmark.annotator",
            "--validate-completions",
            str(completions),
            "--input",
            str(bundle),
            "--output-dir",
            str(work / "validated"),
        ],
        artifacts / "receipt-cli.json",
    )
    accepted = read_rows(work / "validated/accepted.jsonl")
    rejected = read_rows(work / "validated/rejected.jsonl")
    observed = {row["id"]: row for row in accepted + rejected}
    preserved = all(sha256(Path(p).read_bytes()) == h for p, h in before.items())
    exact_receipts = all(observed[r["id"]]["completion"]["text"] == r["text"] for r in receipts)
    result.update(
        accepted=accepted,
        rejected=rejected,
        inputs_unchanged=preserved,
        raw_receipts_unchanged=exact_receipts,
    )
    write_json(artifacts / "receipt-assertions.json", result)
    return result


def freeze_cases(rows: list[dict[str, Any]], work: Path, artifacts: Path) -> dict[str, Any]:
    work.mkdir(parents=True, exist_ok=True)
    bundle, output = work / "reviewed.jsonl", work / "frozen"
    _rows(bundle, rows)
    before = sha256(bundle.read_bytes())
    result = _run(
        [
            "-m",
            "wlh",
            "benchmark",
            "freeze",
            "--input",
            str(bundle),
            "--output-dir",
            str(output),
            "--version",
            "markdown-evidence-acceptance-v1",
        ],
        artifacts / "freeze-cli.json",
    )
    golden = output / "ground_truth_benchmark.jsonl"
    exported = read_rows(golden) if golden.exists() else []
    result.update(
        exported=exported,
        inputs_unchanged=sha256(bundle.read_bytes()) == before,
        complete_payload_and_truth_preserved=(
            {r["id"]: (r["raw_payload"], r["ground_truth"]) for r in exported}
            == {r["id"]: (r["raw_payload"], r["ground_truth"]) for r in rows}
        ),
        published=(output / "annotation_pipeline.json").exists(),
    )
    write_json(artifacts / "freeze-assertions.json", result)
    return result
