"""Real-file CLI acceptance of archived inputs and frozen benchmark membership."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_KEYS = {
    "id",
    "task_type",
    "source_id",
    "jurisdiction",
    "request_url",
    "mime_type",
    "payload_sha256",
    "raw_payload",
    "ground_truth",
}


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _cli(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    directory.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "wlh", "benchmark", *args]
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=45,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "HF_HUB_OFFLINE": "1",
            "CUDA_VISIBLE_DEVICES": "",
        },
    )
    (directory / "cli-result.json").write_text(
        json.dumps(
            {
                "command": command,
                "exit": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return result


def _archived(
    directory: Path, sample_id: str, payload: str | bytes, mime_type: str = "text/html"
) -> dict[str, Any]:
    body = payload.encode("utf-8") if isinstance(payload, str) else payload
    archive = directory / "archive" / f"{sample_id}.body"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(body)
    return {
        "id": sample_id,
        "task_type": "content_page",
        "source_id": "dd-999",
        "jurisdiction": "CN",
        "request_url": f"https://example.gov/legal/{sample_id}",
        "mime_type": mime_type,
        "provenance": {
            "kind": "archived_response",
            "acquisition_id": f"acquisition-{sample_id}",
            "archive_path": str(archive.relative_to(directory)),
            "archive_sha256": _sha(body),
            "charset": "utf-8",
            "representation": "http_body",
        },
    }


def _reviewed(
    directory: Path, sample_id: str, title: str, body: str, *, xml: bool = False
) -> dict[str, Any]:
    raw = f"<article><h1>{title}</h1><p>{body}</p></article>"
    if xml:
        raw = f'<?xml version="1.0" encoding="UTF-8"?><law><title>{title}</title><text>{body}</text></law>'
    row = _archived(directory, sample_id, raw, "application/xml" if xml else "text/html")
    truth: dict[str, Any] = {
        "records": [
            {
                "title": title,
                "doc_type": "法律法规",
                "doc_number": None,
                "published_at": None,
                "effective_at": None,
                "issuer": None,
                "attachment_urls": [],
                "markdown": f"# {title}\n\n{body}",
            }
        ]
    }
    row.update(raw_payload=raw, payload_sha256=_sha(raw.encode()), ground_truth=truth)
    row["review"] = {
        "status": "approved",
        "reviewer": "local-acceptance-review",
        "payload_sha256": row["payload_sha256"],
        "ground_truth_sha256": _sha(json.dumps(truth, ensure_ascii=False, sort_keys=True).encode()),
        "notes": "逐字核对完整标题及唯一正文段落；原文未载明的日期和文号均保留为空。",
    }
    return row


class TestBenchmarkProvenance:
    def test_archive_rebuild_preserves_original_bytes_and_excludes_fallback_and_pollution(
        self, tmp_path: Path
    ) -> None:
        valid = {
            "html-original": (
                "<article>\r\n<p>第一条 原始完整正文。</p>\r\n</article>",
                "text/html",
            ),
            "json-original": (
                '{"title":"法规","body":"第一条 原始完整正文。"}',
                "application/json",
            ),
            "xml-original": (
                '<?xml version="1.0"?><law>第一条 原始完整正文。</law>',
                "application/xml",
            ),
            "valid-4000": (
                "<article>" + "法" * (4000 - len("<article></article>")) + "</article>",
                "text/html",
            ),
        }
        candidates = [_archived(tmp_path, key, raw, mime) for key, (raw, mime) in valid.items()]
        candidates[0]["raw_payload"] = "旧的截断文本，必须忽略而读取归档原件"
        candidates.extend(
            [
                _archived(tmp_path, "markdown-fallback", "# 法规\n\n" + "正文" * 1996),
                _archived(
                    tmp_path,
                    "host-placeholder",
                    '<article><a href="https://{host}/law">法规</a></article>',
                ),
                _archived(
                    tmp_path,
                    "unselected-link",
                    '<article><a href="#unselected/42">法规</a></article>',
                ),
                _archived(tmp_path, "injected-pages", '<article data-wlh-pages="2">法规</article>'),
                _archived(tmp_path, "wrong-bytes", "<article>原始正文</article>"),
                _archived(tmp_path, "invalid-utf8", b"<article>\xff</article>"),
            ]
        )
        candidates[-2]["provenance"]["archive_sha256"] = "0" * 64
        catalog = tmp_path / "catalog.jsonl"
        _write_rows(catalog, candidates)
        original_catalog = catalog.read_bytes()
        originals = {p: p.read_bytes() for p in (tmp_path / "archive").iterdir()}
        output = tmp_path / "rebuilt"
        result = _cli(tmp_path, "rebuild", "--input", str(catalog), "--output-dir", str(output))
        assert result.returncode == 0, result.stdout + result.stderr
        selected, rejected = _rows(output / "selected.jsonl"), _rows(output / "rejected.jsonl")
        assert {row["id"] for row in selected} == set(valid)
        assert {row["id"] for row in rejected} == {row["id"] for row in candidates} - set(valid)
        assert all(row["reasons"] for row in rejected)
        for row in selected:
            expected = valid[row["id"]][0].encode()
            assert row["raw_payload"].encode() == expected
            assert row["payload_sha256"] == _sha(expected)
            assert row["provenance"]["archive_sha256"] == _sha(expected)
        assert (output / "report.json").is_file()
        assert catalog.read_bytes() == original_catalog
        assert all(path.read_bytes() == body for path, body in originals.items())

    def test_duplicate_candidate_identity_fails_without_a_publishable_selected_package(
        self, tmp_path: Path
    ) -> None:
        row = _archived(tmp_path, "duplicate-id", "<article>真实原件</article>")
        catalog, output = tmp_path / "catalog.jsonl", tmp_path / "rebuilt"
        _write_rows(catalog, [row, row])
        result = _cli(tmp_path, "rebuild", "--input", str(catalog), "--output-dir", str(output))
        assert result.returncode != 0
        assert not (output / "selected.jsonl").exists() or not _rows(output / "selected.jsonl")


class TestBenchmarkFrozenMembership:
    def test_candidate_ledger_preserves_excluded_sources_and_exact_denominator(
        self, tmp_path: Path
    ) -> None:
        approved = _reviewed(tmp_path, "approved-law", "税收规定", "纳税人应当依法申报收入。")
        candidates: list[dict[str, Any]] = [
            {
                "id": "approved-law",
                "source_id": "dd-999",
                "jurisdiction": "CN",
                "task_type": "content_page",
                "status": "approved",
                "reasons": [],
            },
            {
                "id": "missing-original",
                "source_id": "dd-999",
                "jurisdiction": "CN",
                "task_type": "content_page",
                "status": "excluded",
                "reasons": ["missing_original"],
            },
            {
                "id": "binary-only",
                "source_id": "dd-998",
                "jurisdiction": "CN",
                "task_type": "content_page",
                "status": "excluded",
                "reasons": ["unsupported_binary"],
            },
        ]
        input_file, candidate_file, output = (
            tmp_path / "reviewed.jsonl",
            tmp_path / "candidates.json",
            tmp_path / "frozen",
        )
        _write_rows(input_file, [approved])
        candidate_file.write_text(json.dumps({"items": candidates}), encoding="utf-8")
        result = _cli(
            tmp_path,
            "freeze",
            "--input",
            str(input_file),
            "--candidate-ledger",
            str(candidate_file),
            "--output-dir",
            str(output),
            "--version",
            "candidate-coverage-v2",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        ledger = json.loads((output / "annotation_pipeline.json").read_text(encoding="utf-8"))
        assert len(_rows(output / "ground_truth_benchmark.jsonl")) == 1
        assert {row["id"] for row in ledger["candidates"]} == {row["id"] for row in candidates}
        assert ledger["summary"]["total_candidates"] == 3
        assert ledger["summary"]["golden_set_exported"] == 1
        assert ledger["summary"]["excluded_count"] == 2
        coverage = {row["source_id"]: row for row in ledger["coverage"]}
        for source_id, expected in {"dd-999": (2, 1, 1), "dd-998": (1, 0, 1)}.items():
            row = coverage[source_id]
            assert (row["candidate_count"], row["golden_count"], row["excluded_count"]) == expected
            assert row["reasons"]
        excluded = {row["id"]: row for row in ledger["candidates"] if row["id"] != "approved-law"}
        assert excluded["missing-original"]["reasons"] == ["missing_original"]
        assert excluded["binary-only"]["reasons"] == ["unsupported_binary"]

    def test_test_split_without_a_ledger_cannot_invent_membership(self, tmp_path: Path) -> None:
        samples = [
            _reviewed(tmp_path, "tax-law", "税收规定", "纳税人应当依法申报收入。"),
            _reviewed(tmp_path, "port-law", "港口规定", "船舶进入港口前应当报告路线。"),
        ]
        for row in samples:
            row["current_extracted"] = row["ground_truth"]["records"][0]
        input_file, output = tmp_path / "samples.jsonl", tmp_path / "report"
        _write_rows(input_file, samples)
        result = _cli(
            tmp_path,
            "run",
            "--input",
            str(input_file),
            "--ground-truth",
            str(input_file),
            "--split",
            "test",
            "--output-dir",
            str(output),
        )
        assert result.returncode != 0, "A test split must consume a frozen ledger"
        assert not (output / "benchmark_report.json").exists()

    def test_duplicate_representations_stay_together_and_subset_evaluation_keeps_frozen_split(
        self, tmp_path: Path
    ) -> None:
        same_body = "第一条 纳税人应当如实申报收入。第二条 税务机关应当依法保护申报信息。"
        related = {"same-html", "same-xml", "same-mirror"}
        reviewed = [
            _reviewed(tmp_path, "same-html", "申报规则", same_body),
            _reviewed(tmp_path, "same-xml", "申报规则", same_body, xml=True),
            _reviewed(tmp_path, "same-mirror", "申报规则", same_body),
        ]
        reviewed[2]["source_id"] = "dd-998"
        other_bodies = [
            ("航运规则", "船舶进入港口前应当报告航行路线，并遵守码头安全规定。"),
            ("环境规则", "企业排放污染物前应当取得排污许可，并按月公开监测报告。"),
            ("学校规则", "学校应当为学生提供饮用水，并定期检查教学楼消防设施。"),
            ("食品规则", "食品生产者应当记录原料来源，并在包装上标明保质日期。"),
            ("道路规则", "施工单位占用道路前应当设置警示标志，并保障行人安全通行。"),
        ]
        reviewed.extend(
            _reviewed(tmp_path, f"distinct-{i}", title, body)
            for i, (title, body) in enumerate(other_bodies)
        )
        input_file, output = tmp_path / "reviewed.jsonl", tmp_path / "frozen"
        _write_rows(input_file, reviewed)
        result = _cli(
            tmp_path / "freeze",
            "freeze",
            "--input",
            str(input_file),
            "--output-dir",
            str(output),
            "--version",
            "acceptance-v2",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        golden_file, ledger_file = (
            output / "ground_truth_benchmark.jsonl",
            output / "annotation_pipeline.json",
        )
        golden = _rows(golden_file)
        ledger = json.loads(ledger_file.read_text(encoding="utf-8"))
        assert ledger["dataset_version"] == "acceptance-v2"
        assert ledger["dataset_sha256"] == _sha(golden_file.read_bytes())
        assert {row["id"] for row in golden} == {row["id"] for row in reviewed}
        assert all(set(row) == GOLDEN_KEYS for row in golden)
        members = {row["id"]: row for row in ledger["items"]}
        assert set(members) == {row["id"] for row in reviewed}
        assert len({members[sample_id]["document_group_id"] for sample_id in related}) == 1
        assert len({members[sample_id]["split"] for sample_id in related}) == 1
        assert len({row["document_group_id"] for row in members.values()}) == 6
        assert {row["split"] for row in members.values()} == {"dev", "test"}
        assert all(members[row["id"]]["review"] == row["review"] for row in reviewed)
        split_manifest = [
            {
                "id": row["id"],
                "document_group_id": members[row["id"]]["document_group_id"],
                "split": members[row["id"]]["split"],
                "payload_sha256": row["payload_sha256"],
                "ground_truth_sha256": row["review"]["ground_truth_sha256"],
            }
            for row in sorted(reviewed, key=lambda item: item["id"])
        ]
        assert ledger["split_manifest_sha256"] == _sha(
            json.dumps(split_manifest, ensure_ascii=False, sort_keys=True).encode()
        )
        frozen_bytes = {
            golden_file: golden_file.read_bytes(),
            ledger_file: ledger_file.read_bytes(),
        }
        for split in ("dev", "test"):
            sample = next(row for row in golden if members[row["id"]]["split"] == split)
            predictions = tmp_path / f"{split}-predictions.jsonl"
            _write_rows(
                predictions, [{**sample, "current_extracted": sample["ground_truth"]["records"][0]}]
            )
            reports = tmp_path / f"report-{split}"
            evaluated = _cli(
                reports,
                "run",
                "--input",
                str(predictions),
                "--ground-truth",
                str(golden_file),
                "--ledger",
                str(ledger_file),
                "--split",
                split,
                "--output-dir",
                str(reports),
            )
            assert evaluated.returncode == 0, evaluated.stdout + evaluated.stderr
            report = json.loads((reports / "benchmark_report.json").read_text(encoding="utf-8"))
            assert report["total_samples"] == 1
            assert report["split_scores"][split]["sample_count"] == 1
        unknown = {**golden[0], "id": "absent-from-freeze"}
        unknown_file = tmp_path / "unknown.jsonl"
        _write_rows(unknown_file, [unknown])
        result = _cli(
            tmp_path / "unknown",
            "run",
            "--input",
            str(unknown_file),
            "--ledger",
            str(ledger_file),
            "--split",
            "test",
            "--output-dir",
            str(tmp_path / "unknown-report"),
        )
        assert result.returncode != 0, "An unknown ID cannot silently become dev or disappear"
        reviewed.append(_reviewed(tmp_path, "new-law", "新增法规", "申请人应当提交书面申请。"))
        _write_rows(input_file, reviewed)
        result = _cli(
            tmp_path / "refreeze",
            "freeze",
            "--input",
            str(input_file),
            "--output-dir",
            str(output),
            "--version",
            "acceptance-v2",
        )
        assert result.returncode != 0, "A published version cannot change its contents"
        assert all(path.read_bytes() == body for path, body in frozen_bytes.items())

    @pytest.mark.parametrize("defect", ["missing-review", "changed-truth", "changed-payload"])
    def test_missing_or_stale_review_cannot_create_a_frozen_dataset(
        self, tmp_path: Path, defect: str
    ) -> None:
        row = _reviewed(tmp_path, "reviewed-law", "食品规则", "食品生产者应当记录原料来源。")
        if defect == "missing-review":
            row.pop("review")
        elif defect == "changed-truth":
            row["ground_truth"]["records"][0]["effective_at"] = "2030-01-01"
        else:
            row["raw_payload"] += "<p>新增条款</p>"
            row["payload_sha256"] = _sha(row["raw_payload"].encode())
        input_file, output = tmp_path / "reviewed.jsonl", tmp_path / "frozen"
        _write_rows(input_file, [row])
        result = _cli(
            tmp_path,
            "freeze",
            "--input",
            str(input_file),
            "--output-dir",
            str(output),
            "--version",
            "rejected-v2",
        )
        assert result.returncode != 0
        assert not (output / "ground_truth_benchmark.jsonl").exists()
        assert not (output / "annotation_pipeline.json").exists()
