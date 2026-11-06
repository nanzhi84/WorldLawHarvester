"""Black-box acceptance of benchmark arbitration and its published reports."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from wlh.benchmark.arbitrator import merge_resolutions, validate_ground_truth_integrity
from wlh.benchmark.models import BenchmarkSummaryReport
from wlh.benchmark.reporter import render_benchmark_markdown_report


class TestBenchmarkArbitrationAndReporting:
    """Black-box verification of arbitration resolution merging and report rendering."""

    def test_merge_resolutions_produces_valid_adjudication_metadata(self, tmp_path: Path) -> None:
        """merge_resolutions builds records containing valid SHA-256 adjudication_metadata."""
        annotator_a_path = tmp_path / "annotator_a.jsonl"
        annotator_b_path = tmp_path / "annotator_b.jsonl"
        res_path = tmp_path / "resolutions.jsonl"
        out_path = tmp_path / "ground_truth.jsonl"

        raw_payload = "<article><h1>测试案例</h1><p>正文</p></article>"
        payload_sha256 = hashlib.sha256(raw_payload.encode()).hexdigest()
        task_a: dict[str, Any] = {
            "id": "content_test_01",
            "task_type": "content_page",
            "source_id": "dd-017",
            "jurisdiction": "CN",
            "request_url": "https://example.gov/doc/1",
            "mime_type": "text/html",
            "raw_payload": raw_payload,
            "payload_sha256": payload_sha256,
            "ground_truth": {
                "records": [
                    {
                        "doc_type": "案例",
                        "title": "测试案例",
                        "markdown": "# 测试案例\n\n正文",
                    }
                ]
            },
        }
        task_b: dict[str, Any] = {
            "id": "content_test_01",
            "task_type": "content_page",
            "source_id": "dd-017",
            "jurisdiction": "CN",
            "request_url": "https://example.gov/doc/1",
            "mime_type": "text/html",
            "raw_payload": raw_payload,
            "payload_sha256": payload_sha256,
            "ground_truth": {
                "records": [
                    {
                        "doc_type": "案例",
                        "title": "测试案例（不同标题）",
                        "markdown": "# 测试案例\n\n正文",
                    }
                ]
            },
        }
        resolution: dict[str, Any] = {
            "id": "content_test_01",
            "task_type": "content_page",
            "source_id": "dd-017",
            "jurisdiction": "CN",
            "request_url": "https://example.gov/doc/1",
            "ground_truth": {
                "records": [
                    {
                        "doc_type": "案例",
                        "title": "测试案例",
                        "markdown": "# 测试案例\n\n正文",
                    }
                ]
            },
            "arbitration_notes": "Local acceptance reviewer checked the archived title and paragraph.",
        }

        annotator_a_path.write_text(json.dumps(task_a) + "\n", encoding="utf-8")
        annotator_b_path.write_text(json.dumps(task_b) + "\n", encoding="utf-8")
        res_path.write_text(json.dumps(resolution) + "\n", encoding="utf-8")

        merged = merge_resolutions(
            annotator_a_path=annotator_a_path,
            annotator_b_path=annotator_b_path,
            resolutions_path=res_path,
            output_path=out_path,
        )

        assert len(merged) == 1
        rec = merged[0]
        assert rec["id"] == "content_test_01"
        assert rec["verification_status"] == "ARBITRATED"
        assert "adjudication_metadata" in rec
        meta = rec["adjudication_metadata"]
        assert "content_hash" in meta
        assert (
            meta["content_hash"]
            == hashlib.sha256(
                json.dumps(resolution["ground_truth"], ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
        )
        assert meta["payload_sha256"] == payload_sha256

        for row in (task_a, task_b):
            row.pop("raw_payload")
            row.pop("payload_sha256")
        annotator_a_path.write_text(json.dumps(task_a) + "\n", encoding="utf-8")
        annotator_b_path.write_text(json.dumps(task_b) + "\n", encoding="utf-8")
        try:
            missing_source = merge_resolutions(
                annotator_a_path=annotator_a_path,
                annotator_b_path=annotator_b_path,
                resolutions_path=res_path,
                output_path=tmp_path / "missing-source.jsonl",
            )
        except ValueError:
            missing_source = []
        assert all(not row["adjudication_metadata"].get("payload_sha256") for row in missing_source)

    def test_unresolved_disagreement_and_single_annotation_cannot_export_authoritative_truth(
        self, tmp_path: Path
    ) -> None:
        raw = "<article><h1>申报规定</h1><p>纳税人应当依法申报收入。</p></article>"
        row: dict[str, Any] = {
            "id": "disagreement",
            "task_type": "content_page",
            "source_id": "dd-999",
            "jurisdiction": "CN",
            "request_url": "https://example.gov/law",
            "mime_type": "text/html",
            "raw_payload": raw,
            "payload_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "ground_truth": {
                "records": [
                    {
                        "doc_type": "法律法规",
                        "title": "申报规定",
                        "markdown": "# 申报规定\n\n纳税人应当依法申报收入。",
                    }
                ]
            },
        }
        other = {
            **row,
            "ground_truth": {
                "records": [
                    {
                        "doc_type": "法律法规",
                        "title": "港口规定",
                        "markdown": "# 港口规定\n\n船舶应当报告航线。",
                    }
                ]
            },
        }
        first, second, resolutions = (
            tmp_path / "a.jsonl",
            tmp_path / "b.jsonl",
            tmp_path / "resolutions.jsonl",
        )
        first.write_text(
            "".join(json.dumps(item) + "\n" for item in [row, {**row, "id": "single-a"}]),
            encoding="utf-8",
        )
        second.write_text(json.dumps(other) + "\n", encoding="utf-8")
        resolutions.write_text("", encoding="utf-8")
        output = tmp_path / "resolved.jsonl"
        try:
            merged = merge_resolutions(
                annotator_a_path=first,
                annotator_b_path=second,
                resolutions_path=resolutions,
                output_path=output,
            )
        except ValueError:
            merged = []
        assert merged == [], (
            "No explicit resolution exists for either disputed or single annotation"
        )
        assert not output.exists() or not output.read_text(encoding="utf-8").strip()

    def test_render_benchmark_markdown_report_renders_all_five_pillars(self) -> None:
        """render_benchmark_markdown_report renders all 5 evaluation pillars in executive score card."""
        rep = BenchmarkSummaryReport(
            total_samples=10,
            evaluated_at="2026-11-03T00:00:00Z",
            average_overall_score=94.5,
            average_list_discovery_f1=98.0,
            average_record_split_f1=96.0,
            average_accuracy_score=95.0,
            average_content_fidelity_score=92.0,
            average_cleanliness_score=97.5,
        )
        md = render_benchmark_markdown_report(rep)
        assert "## 1. Executive Score Card" in md
        assert "Pillar 1: List Discovery F1" in md
        assert "Pillar 2: Record Split F1" in md
        assert "Pillar 3: Metadata & Attachment Accuracy" in md
        assert "Pillar 4: Content DOM Boundary Fidelity" in md
        assert "Pillar 5: Text Cleanliness (干净度)" in md

    def test_validate_ground_truth_integrity_rejects_ungrounded_commentary_and_hallucinated_content(
        self,
    ) -> None:
        """validate_ground_truth_integrity enforces strict raw_payload grounding and rejects LLM commentary or fabricated text."""
        raw_html = (
            "<html><body>"
            "<h1>国家税务总局关于增值税留抵退税政策的通知</h1>"
            "<div class='meta'>发文字号：税总发〔2026〕12号 发布日期：2026-03-15</div>"
            "<div class='content'>"
            "<p>为进一步支持小微企业和制造业发展，现将增值税期末留抵退税有关政策通知如下：符合条件的纳税人可以向主管税务机关申请退还增量留抵税额。</p>"
            "<a href='/files/annex1.pdf'>附件1：退税申请表.pdf</a>"
            "</div>"
            "</body></html>"
        )
        request_url = "https://fgk.chinatax.gov.cn/zcfgk/c100012/c52001/content.html"

        # 1. Faithful grounded extraction (including relative URL resolved against request_url) -> (True, ...)
        faithful_gt = {
            "records": [
                {
                    "title": "国家税务总局关于增值税留抵退税政策的通知",
                    "doc_number": "税总发〔2026〕12号",
                    "published_at": "2026-03-15",
                    "effective_at": None,
                    "issuer": "国家税务总局",
                    "doc_type": "通知公告",
                    "attachment_urls": ["https://fgk.chinatax.gov.cn/files/annex1.pdf"],
                    "markdown": (
                        "# 国家税务总局关于增值税留抵退税政策的通知\n\n"
                        "发文字号：税总发〔2026〕12号 发布日期：2026-03-15\n\n"
                        "为进一步支持小微企业和制造业发展，现将增值税期末留抵退税有关政策通知如下："
                        "符合条件的纳税人可以向主管税务机关申请退还增量留抵税额。\n\n"
                        "[附件1：退税申请表.pdf](https://fgk.chinatax.gov.cn/files/annex1.pdf)"
                    ),
                }
            ]
        }
        ok_faithful, reason_faithful = validate_ground_truth_integrity(
            faithful_gt,
            "content_page",
            raw_payload=raw_html,
            request_url=request_url,
        )
        assert ok_faithful is True, (
            f"Expected faithful ground truth to pass, got: {reason_faithful}"
        )

        # 2. Unattested LLM commentary not present in raw_payload -> (False, ...)
        for commentary in (
            "（正文内容通过 OFD 阅读器加载，此处无直接文本正文）",
            "（此处为元数据摘要）",
            "（正文内容见附件预览）",
        ):
            commentary_gt = {
                "records": [
                    {
                        "title": "国家税务总局关于增值税留抵退税政策的通知",
                        "doc_type": "通知公告",
                        "markdown": (
                            "# 国家税务总局关于增值税留抵退税政策的通知\n\n"
                            "发文字号：税总发〔2026〕12号 发布日期：2026-03-15\n\n"
                            f"{commentary}"
                        ),
                    }
                ]
            }
            ok_comm, _ = validate_ground_truth_integrity(
                commentary_gt,
                "content_page",
                raw_payload=raw_html,
                request_url=request_url,
            )
            assert ok_comm is False

        # 3. Unattested Chinese bold metadata label ('**状态**：有效' when '状态' is absent in raw_payload) -> (False, ...)
        unattested_label_gt = {
            "records": [
                {
                    "title": "国家税务总局关于增值税留抵退税政策的通知",
                    "doc_type": "通知公告",
                    "markdown": (
                        "# 国家税务总局关于增值税留抵退税政策的通知\n\n"
                        "- **发文字号**：税总发〔2026〕12号\n"
                        "- **状态**：有效\n\n"
                        "为进一步支持小微企业和制造业发展，现将增值税期末留抵退税有关政策通知如下："
                        "符合条件的纳税人可以向主管税务机关申请退还增量留抵税额。"
                    ),
                }
            ]
        }
        ok_label, _ = validate_ground_truth_integrity(
            unattested_label_gt,
            "content_page",
            raw_payload=raw_html,
            request_url=request_url,
        )
        assert ok_label is False

        # 4. Hallucinated paragraphs / duplicated numbers not in raw_payload -> (False, ...)
        hallucinated_gt = {
            "records": [
                {
                    "title": "国家税务总局关于增值税留抵退税政策的通知",
                    "doc_type": "通知公告",
                    "markdown": (
                        "# 国家税务总局关于增值税留抵退税政策的通知\n\n"
                        "为进一步支持小微企业和制造业发展，现将增值税期末留抵退税有关政策通知如下："
                        "符合条件的纳税人可以向主管税务机关申请退还增量留抵税额。\n\n"
                        "第二条 纳税人信用等级为A级或B级的，退税比例提高至百分之百（100%），"
                        "并于2026年4月1日起施行，逾期未申报的视为自动放弃退税资格999888777。"
                    ),
                }
            ]
        }
        ok_hallucinated, _ = validate_ground_truth_integrity(
            hallucinated_gt,
            "content_page",
            raw_payload=raw_html,
            request_url=request_url,
        )
        assert ok_hallucinated is False
