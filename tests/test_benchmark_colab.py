"""Offline preparation of a Colab annotation run from an explicit input bundle."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


class TestBenchmarkColabPreparation:
    def test_explicit_complete_bundle_is_prepared_without_touching_published_data(
        self, tmp_path: Path
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        canonical = root / "data" / "benchmark"
        published_before = {
            path.name: path.read_bytes() for path in canonical.iterdir() if path.is_file()
        }
        rows: list[dict[str, Any]] = []
        for sample_id, body in (
            (
                "complete-cjk",
                "<article><h1>示例法规</h1><p>最后一条 原件必须完整保留。</p></article>",
            ),
            ("oversized-cjk", "<article>" + "完整中文条文。" * 8_000 + "</article>"),
        ):
            rows.append(
                {
                    "id": sample_id,
                    "task_type": "content_page",
                    "source_id": "dd-999",
                    "jurisdiction": "CN",
                    "request_url": f"https://example.gov/{sample_id}",
                    "mime_type": "text/html",
                    "raw_payload": body,
                    "payload_sha256": hashlib.sha256(body.encode()).hexdigest(),
                }
            )
        bundle = tmp_path / "explicit-input.jsonl"
        bundle.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
        )
        original_bundle = bundle.read_bytes()
        output, report_file = tmp_path / "prepared", tmp_path / "selection.json"
        command = [
            sys.executable,
            "-m",
            "wlh.benchmark.colab",
            "--prepare-only",
            "--input",
            str(bundle),
            "--output-dir",
            str(output),
            "--selection-report",
            str(report_file),
        ]
        result = subprocess.run(
            command,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=40,
            env={
                **os.environ,
                "PYTHONPATH": str(root / "src"),
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "CUDA_VISIBLE_DEVICES": "",
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
            },
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
        assert result.returncode == 0, result.stdout + result.stderr
        selected = [
            json.loads(line)
            for line in (output / "selected.jsonl").read_text(encoding="utf-8").split("\n")
            if line
        ]
        assert selected == [rows[0]]
        report = json.loads(report_file.read_text(encoding="utf-8"))
        assert report["input_sha256"] == hashlib.sha256(original_bundle).hexdigest()
        assert (report["total_tasks"], report["selected_tasks"], report["excluded_tasks"]) == (
            2,
            1,
            1,
        )
        assert report["tokenizers"]["A"]["revision"] == "1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0"
        assert report["tokenizers"]["B"]["revision"] == "842da3794eaa0b77d5f08bae87a17459d91ff475"
        assert bundle.read_bytes() == original_bundle
        assert {
            path.name: path.read_bytes() for path in canonical.iterdir() if path.is_file()
        } == published_before
