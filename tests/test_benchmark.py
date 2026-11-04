"""High-value black-box test suite for the quantitative benchmark system.

Follows Universal Harness guidelines:
- class Test<Component>:
- def test_<scenario_or_input_condition>_<expected_behavior_or_result>():
- Real fixtures and black-box verification without mocks.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from wlh.benchmark.evaluator import (
    evaluate_benchmark_dataset,
    evaluate_dataset,
    evaluate_sample,
    load_records,
)
from wlh.benchmark.metrics import (
    evaluate_accuracy,
    evaluate_cleanliness,
)
from wlh.benchmark.models import (
    AccuracyResult,
    AccuracySubScores,
    BenchmarkReport,
    BenchmarkSummaryReport,
    CleanlinessResult,
    CleanlinessSubScores,
    SampleEvaluationResult,
    SampleScoreReport,
)
from wlh.benchmark.reporter import (
    render_benchmark_json_report,
    render_benchmark_markdown_report,
    write_benchmark_reports,
)

BENCHMARK_JURISDICTIONS = ("CN", "US", "JP", "DE", "GB", "VN", "TW", "ZA")


def _write_benchmark_ground_truth_fixture(directory: Path) -> Path:
    """Write a self-contained multi-jurisdiction ground truth benchmark JSONL fixture."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "ground_truth_benchmark.jsonl"
    records: list[dict[str, Any]] = []
    for idx, jur in enumerate(BENCHMARK_JURISDICTIONS, start=1):
        records.append(
            {
                "id": f"cand_{jur.lower()}_{idx:02d}",
                "source_id": f"dd-{500 + idx}",
                "jurisdiction": jur,
                "detail_url": f"https://example.gov/{jur.lower()}/{idx}",
                "current_extracted": {
                    "title": f"{jur} Statutory Regulation {idx}",
                    "doc_number": f"No. 2026-{idx:02d}",
                    "published_at": "2026-01-15",
                    "effective_at": "2026-02-01" if idx > 1 else None,
                    "issuer": f"Ministry of Justice ({jur})",
                    "doc_type": "regulation",
                    "markdown": f"# {jur} Statutory Regulation {idx}\n\nArticle 1. Substantive legal text for {jur}.",
                },
                "ground_truth": {
                    "title": f"{jur} Statutory Regulation {idx}",
                    "doc_number": f"No. 2026-{idx:02d}",
                    "published_at": "2026-01-15",
                    "effective_at": "2026-02-01",
                    "issuer": f"Ministry of Justice ({jur})",
                    "doc_type": "regulation",
                    "markdown": f"# {jur} Statutory Regulation {idx}\n\nArticle 1. Substantive legal text for {jur}.",
                },
                "verification_status": "CONSENSUS",
            }
        )
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
        encoding="utf-8",
    )
    return path


# ==============================================================================
# 1. Cleanliness Metrics Black-Box Tests
# ==============================================================================


class TestCleanlinessMetrics:
    """Black-box evaluations of text cleanliness scoring across diverse synthetic fixtures."""

    def test_pristine_markdown_document_yields_near_perfect_cleanliness_score(self) -> None:
        """A well-formatted document without noise, HTML, or encoding errors scores >= 95.0."""
        pristine_markdown = (
            "# 第一条 立法目的\n\n"
            "为了规范气象灾害预警信号发布与传播，防御和减轻气象灾害，保护人民生命财产安全，制定本办法。\n\n"
            "## 第二条 适用范围\n\n"
            "在本省行政区域内发布与传播预警信号，适用本办法。\n\n"
            "### 第三条 机构职责\n\n"
            "各级气象主管机构负责本行政区域内预警信号发布、更新、传播、解除的管理工作。\n"
        )
        result = evaluate_cleanliness(pristine_markdown, title="河南省气象灾害预警信号发布与传播办法")

        assert isinstance(result, CleanlinessResult)
        assert isinstance(result.sub_scores, CleanlinessSubScores)
        assert result.score >= 95.0, f"Expected pristine text to score >= 95.0, got {result.score}"
        assert result.sub_scores.noise_purity >= 95.0
        assert result.sub_scores.encoding_purity == 100.0
        assert result.sub_scores.structural_integrity >= 90.0
        assert result.sub_scores.boilerplate_freedom == 100.0
        assert result.sub_scores.deduplication_purity == 100.0
        assert len(result.penalties) == 0

    def test_document_without_tables_yields_graceful_perfect_table_score(self) -> None:
        """Documents lacking tables must not be penalized for zero tables (M2 recommendation)."""
        tableless_markdown = (
            "# Notice of Public Rulemaking\n\n"
            "Notice is hereby given that the Environmental Protection Agency proposes "
            "new emission standards under Section 112 of the Clean Air Act.\n\n"
            "## Summary of Proposed Provisions\n\n"
            "All industrial facilities subject to this rule must monitor quarterly output.\n"
        )
        result = evaluate_cleanliness(tableless_markdown, title="EPA Proposed Rule")
        assert result.sub_scores.structural_integrity >= 95.0

    def test_raw_html_tags_and_entities_trigger_noise_purity_penalty(self) -> None:
        """Unescaped HTML tags (div, span, script) and raw entities trigger noise purity deductions."""
        polluted_markdown = (
            "<div class=\"content-wrapper\"><p><span>Notice content</span></p>"
            "<script type=\"text/javascript\">alert('tracking');</script>"
            "Some text with &nbsp;&nbsp;&nbsp; raw entities &#160; and <br/> breaks.</div>"
        )
        result = evaluate_cleanliness(polluted_markdown, title="Polluted Document")

        assert result.sub_scores.noise_purity < 80.0
        assert result.score < 95.0
        assert any(p["dimension"] == "noise_purity" for p in result.penalties)

    def test_unicode_replacement_characters_and_mojibake_trigger_encoding_penalty(self) -> None:
        """Unicode replacement characters (\\ufffd) and mojibake sequences trigger encoding penalties."""
        garbled_markdown = (
            "Điều 1. Quy định về cải tạo chung cư cũ. Căn cứ theo Luật Nhà ở.\n"
            "Nội dung bị lỗi phông chữ: §©®µ¶¸¹º»¿ va â€œtrÃch dáº«nâ€.\n"
        )
        result = evaluate_cleanliness(garbled_markdown, title="Quy định cải tạo")

        assert result.sub_scores.encoding_purity < 75.0
        assert any(p["dimension"] == "encoding_purity" for p in result.penalties)

    def test_broken_markdown_table_triggers_structural_integrity_penalty(self) -> None:
        """Tables with column mismatch or broken pipe structures trigger structural integrity penalties."""
        broken_table_markdown = (
            "# Financial Disclosure\n\n"
            "| Column 1 | Column 2 | Column 3 |\n"
            "| Value A | Value B |\n"
            "| Only One Value |\n"
            "| Row with too many values | B | C | D | E |\n"
        )
        result = evaluate_cleanliness(broken_table_markdown, title="Broken Table Report")

        assert result.sub_scores.structural_integrity < 85.0
        assert any(p["dimension"] == "structural_integrity" for p in result.penalties)

    def test_ui_navigation_and_boilerplate_elements_trigger_boilerplate_penalty(self) -> None:
        """Navigation residues, print buttons, UUID tokens, and disclaimer text trigger boilerplate penalties."""
        boilerplate_markdown = (
            "【打印本页】 【关闭窗口】 字体：[大 中 小] 分享到微信 来源：省政府办公厅\n\n"
            "# 某某省人民政府关于深化改革的通知\n\n"
            "正文内容关于深化推进政务公开工作的指导性意见。\n\n"
            "附件：notice-690779d6257c4649a72bd5ce76e64cb1.pdf\n"
            "Back to top | Site Map | Search this site\n"
        )
        result = evaluate_cleanliness(boilerplate_markdown, title="通知")

        assert result.sub_scores.boilerplate_freedom < 80.0
        assert any(p["dimension"] == "boilerplate_freedom" for p in result.penalties)
        assert result.score == round(
            0.25 * result.sub_scores.noise_purity
            + 0.20 * result.sub_scores.encoding_purity
            + 0.20 * result.sub_scores.structural_integrity
            + 0.25 * result.sub_scores.boilerplate_freedom
            + 0.10 * result.sub_scores.deduplication_purity,
            2,
        )

    def test_consecutive_duplicate_paragraphs_trigger_deduplication_penalty(self) -> None:
        """Dual-DOM responsive duplicate text triggers deduplication purity deductions."""
        repeated_paragraph = (
            "为深入贯彻落实党的二十大和二十届历次全会精神，提升制度建设水平，"
            "我委组织开展了规章和行政规范性文件清理工作。经过清理，决定对部分文件予以修改或废止。"
        )
        duplicate_markdown = (
            f"# 关于清理规章的决定\n\n"
            f"{repeated_paragraph}\n\n"
            f"{repeated_paragraph}\n\n"
            f"{repeated_paragraph}\n"
        )
        result = evaluate_cleanliness(duplicate_markdown, title="关于清理规章的决定")

        assert result.sub_scores.deduplication_purity < 70.0
        assert any(p["dimension"] == "deduplication_purity" for p in result.penalties)
        assert result.score == round(
            0.25 * result.sub_scores.noise_purity
            + 0.20 * result.sub_scores.encoding_purity
            + 0.20 * result.sub_scores.structural_integrity
            + 0.25 * result.sub_scores.boilerplate_freedom
            + 0.10 * result.sub_scores.deduplication_purity,
            2,
        )

    def test_multi_table_document_with_different_column_counts_incurs_zero_structural_penalty(self) -> None:
        """Documents containing multiple valid tables with distinct column counts receive zero penalty."""
        multi_table_markdown = (
            "# Multi-Table Administrative Document\n\n"
            "## Table 1: Summary Statistics (2 columns)\n\n"
            "| Metric | Value |\n"
            "| :--- | :--- |\n"
            "| Total Cases | 1,420 |\n"
            "| Resolved | 1,390 |\n\n"
            "Intervening statutory narrative describing departmental procedures...\n\n"
            "## Table 2: Budget Allocations (5 columns)\n\n"
            "| Department | Personnel | Equipment | Operations | Total |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| Judicial | $4.2M | $1.1M | $0.8M | $6.1M |\n"
            "| Enforcement | $3.5M | $2.0M | $1.2M | $6.7M |\n"
        )
        result = evaluate_cleanliness(multi_table_markdown, title="Multi-Table Administrative Document")

        assert result.sub_scores.structural_integrity == 100.0
        assert not any("Table column count mismatch" in p.get("reason", "") for p in result.penalties)
        assert len(result.penalties) == 0

    def test_reference_ground_truth_markdown_comparison_penalizes_extra_noise_and_missing_lines(self) -> None:
        """Comparing extracted markdown against ground_truth.markdown penalizes unstripped boilerplate and missing substantive lines."""
        ref_md = (
            "# 国家发展改革委令第45号\n\n"
            "第一条 为深入贯彻落实法治建设要求，决定对有关规章予以修改或废止。\n\n"
            "第二条 本决定自2026年10月1日起施行。"
        )
        extracted_md = (
            "- 點閱次數：5256\n"
            "CLOSE\n"
            "# 国家发展改革委令第45号\n\n"
            "第一条 为深入贯彻落实法治建设要求，决定对有关规章予以修改或废止。"
        )
        result = evaluate_cleanliness(
            extracted_md,
            title="国家发展改革委令第45号",
            metadata={
                "ground_truth": {
                    "markdown": ref_md,
                }
            },
        )
        assert result.sub_scores.boilerplate_freedom < 100.0
        assert result.sub_scores.structural_integrity < 100.0
        assert any("unstripped boilerplate/noise line(s)" in p.get("reason", "") for p in result.penalties)
        assert any("substantive line(s) missing" in p.get("reason", "") for p in result.penalties)

    def test_portal_metadata_headers_and_korean_chinese_sharing_controls_trigger_boilerplate_penalty(self) -> None:
        """Portal metadata lines (e.g. 字体：[ 大 中 小 ], 담당부서, 조회수, 微信 **X**, 点击量) are strictly penalized."""
        portal_polluted_markdown = (
            "字体：[ 大 中 小 ] 微信 **X**\n"
            "담당부서 : 법무심의관실 | 조회수 : 1,245 | 点击量：392 | 浏览次数：1024\n\n"
            "# 행정규칙 제정 고시\n\n"
            "제1조(목적) 이 고시는 행정절차의 투명성을 확보함을 목적으로 한다.\n"
        )
        result = evaluate_cleanliness(portal_polluted_markdown, title="행정규칙 제정 고시")

        assert result.sub_scores.boilerplate_freedom < 85.0
        assert any(p["dimension"] == "boilerplate_freedom" for p in result.penalties)

    def test_repeated_inline_statutory_repeal_clauses_and_duplicate_blocks_trigger_deduplication_penalty(
        self,
    ) -> None:
        """Repeated statutory repeal clauses or duplicated blocks trigger deduplication purity penalties."""
        repeated_repeal_markdown = (
            "# Export and Import Permits Act\n\n"
            "Section 1. Short title.\n"
            "[Repealed, 1997, c. 20, s. 45]\n"
            "[Repealed, 1997, c. 20, s. 45]\n"
            "[Repealed, 1997, c. 20, s. 45]\n"
            "[Repealed, 1997, c. 20, s. 45]\n"
            "[Repealed, 1997, c. 20, s. 45]\n"
        )
        result = evaluate_cleanliness(repeated_repeal_markdown, title="Export and Import Permits Act")

        assert result.sub_scores.deduplication_purity < 95.0
        assert any(p["dimension"] == "deduplication_purity" for p in result.penalties)

    def test_candidate_matching_clean_ground_truth_markdown_achieves_perfect_cleanliness_score(self) -> None:
        """When candidate markdown matches clean ground_truth.markdown, cleanliness score recovers to 100.0 without flag capping."""
        clean_gt_md = (
            "# 国家发展改革委关于修改、废止一批规章和行政规范性文件的决定\n\n"
            "第一条 为深入贯彻落实党的二十大精神，决定对有关规章予以修改或废止。\n\n"
            "第二条 本决定自2026年10月1日起施行。"
        )
        result = evaluate_cleanliness(
            clean_gt_md,
            title="国家发展改革委关于修改、废止一批规章和行政规范性文件的决定",
            metadata={
                "ground_truth": {"markdown": clean_gt_md},
                "cleanliness_ground_truth": {
                    "has_html_artifacts": True,
                    "has_navigation_boilerplate": True,
                    "has_garbled_encoding": True,
                    "has_duplicated_body": True,
                },
            },
        )
        assert result.score == 100.0
        assert result.sub_scores.noise_purity == 100.0
        assert result.sub_scores.encoding_purity == 100.0
        assert result.sub_scores.structural_integrity == 100.0
        assert result.sub_scores.boilerplate_freedom == 100.0
        assert result.sub_scores.deduplication_purity == 100.0
        assert len(result.penalties) == 0

    def test_empty_extracted_markdown_against_non_empty_reference_markdown_triggers_heavy_structural_penalty(
        self,
    ) -> None:
        """An empty extracted markdown against non-empty ground_truth.markdown must score 0.0 structural integrity."""
        ref_md = "# 国家发展改革委令第45号\n\n第一条 为深入贯彻落实法治建设要求，决定对有关规章予以修改或废止。"
        result = evaluate_cleanliness(
            "",
            title="国家发展改革委令第45号",
            metadata={"ground_truth": {"markdown": ref_md}},
        )
        assert result.sub_scores.structural_integrity == 0.0
        assert result.score < 90.0
        assert any("extracted markdown is empty" in p.get("reason", "").lower() for p in result.penalties)

    def test_severe_recall_and_precision_gaps_against_ground_truth_markdown_scale_without_artificial_caps(
        self,
    ) -> None:
        """Missing >60% of ground_truth.markdown or having >60% excess boilerplate scales without 30-pt caps."""
        ref_md = (
            "# 国家发展改革委令第45号\n\n"
            "第一条 为深入贯彻落实法治建设要求，决定对有关规章予以修改或废止。\n\n"
            "第二条 各级行政机关应当严格按照修改后的规章履行法定职责，确保行政执法公开公正。\n\n"
            "第三条 各地有关部门应当在规定期限内完成配套制度的清理与衔接工作，不得延误。\n\n"
            "第四条 对违反本决定的行为，依法追究相关责任人员的行政责任。\n\n"
            "第五条 本决定自2026年10月1日起施行，此前有关规定与本决定不一致的，以本决定为准。"
        )
        truncated_candidate = "# 国家发展改革委令第45号\n\n第一条 为深入贯彻落实法治建设要求。"
        recall_loss_res = evaluate_cleanliness(
            truncated_candidate,
            title="国家发展改革委令第45号",
            metadata={"ground_truth": {"markdown": ref_md}},
        )
        assert recall_loss_res.sub_scores.structural_integrity < 50.0

        short_ref_md = "# 通知\n\n第一条 本通知自发布之日起施行。"
        bloated_candidate = (
            short_ref_md
            + "\n\n版权所有：某某省人民政府办公厅 网站标识码：4100000001\n"
            + "地址：某某市行政中心大楼1号 邮政编码：450000\n"
            + "技术支持：政务服务大数据局 备案号：ICP备12345678号\n"
            + "政务微博 | 政务微信 | 移动客户端 | 网站地图 | 联系我们\n"
            + "热门搜索：营商环境 政务公开 重点民生 法治政府 政策解读\n"
            + "友情链接：国务院部门网站 各省区市政府网站 市县政府门户网站"
        )
        precision_loss_res = evaluate_cleanliness(
            bloated_candidate,
            title="通知",
            metadata={"ground_truth": {"markdown": short_ref_md}},
        )
        assert precision_loss_res.sub_scores.boilerplate_freedom < 40.0

    def test_line_wrapped_identical_markdown_incurs_zero_reference_comparison_penalty(self) -> None:
        """Identical substantive prose with different hard line breaks incurs zero false-positive penalty."""
        ref_md = (
            "# Notice of Rulemaking\n\n"
            "Article 1. Every person subject to this regulation shall comply with all reporting requirements."
        )
        wrapped_md = (
            "# Notice of Rulemaking\n\n"
            "Article 1. Every person subject to this\n"
            "regulation shall comply with all reporting\n"
            "requirements."
        )
        result = evaluate_cleanliness(
            wrapped_md,
            title="Notice of Rulemaking",
            metadata={"ground_truth": {"markdown": ref_md}},
        )
        assert result.sub_scores.boilerplate_freedom == 100.0
        assert result.sub_scores.structural_integrity == 100.0
        assert len(result.penalties) == 0

    def test_prose_angle_bracket_headings_incur_zero_html_tag_penalty(self) -> None:
        """English prose headings in angle brackets (e.g. <Domestic Documents>) are not misclassified as HTML tags."""
        prose_markdown = (
            "# Scope of Data Recorded in J-PlatPat\n\n"
            "<Domestic Documents (gazette publication schedule)>\n\n"
            "<Foreign Documents/Non-patent Documents>\n\n"
            "All patent gazettes are updated weekly according to the statutory schedule."
        )
        result = evaluate_cleanliness(prose_markdown, title="Scope of Data Recorded in J-PlatPat")
        assert result.sub_scores.noise_purity == 100.0
        assert not any("unescaped HTML tags" in p.get("reason", "") for p in result.penalties)

    def test_trailing_portal_footer_boilerplate_triggers_reference_precision_penalty(self) -> None:
        """Trailing portal footer boilerplate after a complete >=120-char reference markdown must be penalized."""
        ref_md = (
            "# 国家发展改革委令第45号\n\n"
            "第一条 为深入贯彻落实法治建设要求，决定对有关规章予以修改或废止。\n\n"
            "第二条 各级行政机关应当严格按照修改后的规章履行法定职责，确保行政执法公开公正。\n\n"
            "第三条 各地有关部门应当在规定期限内完成配套制度的清理与衔接工作。\n\n"
            "第四条 本决定自2026年10月1日起施行。"
        )
        extracted_md = (
            ref_md
            + "\n\n版权所有：国家发展和改革委员会 网站管理：办公厅\n"
            + "地址：北京市西城区月坛南街38号 邮编：100824\n"
            + "联系方式 | 网站地图 | 法律声明 | 隐私政策"
        )
        result = evaluate_cleanliness(
            extracted_md,
            title="国家发展改革委令第45号",
            metadata={"ground_truth": {"markdown": ref_md}},
        )
        assert result.sub_scores.boilerplate_freedom < 100.0
        assert any("unstripped boilerplate/noise line(s)" in p.get("reason", "") for p in result.penalties)


# ==============================================================================
# 2. Accuracy Metrics Black-Box Tests
# ==============================================================================


class TestAccuracyMetrics:
    """Black-box evaluations of ground-truth accuracy scoring against gold standards."""

    def test_exact_ground_truth_alignment_yields_perfect_accuracy_score(self) -> None:
        """Identical predictions and ground truth values achieve a 100.0 accuracy score."""
        sample = {
            "title": "City Ombudsman By-law, 2025",
            "doc_number": "Western Cape Provincial Gazette 9196",
            "published_at": "2026-02-18",
            "effective_at": "2026-02-18",
            "issuer": "City of Cape Town",
            "doc_type": "by-law",
        }
        result = evaluate_accuracy(sample, sample)

        assert isinstance(result, AccuracyResult)
        assert isinstance(result.sub_scores, AccuracySubScores)
        assert result.score == 100.0
        assert result.sub_scores.title_accuracy == 100.0
        assert result.sub_scores.doc_number_accuracy == 100.0
        assert result.sub_scores.published_at_accuracy == 100.0
        assert result.sub_scores.effective_at_accuracy == 100.0
        assert result.sub_scores.issuer_accuracy == 100.0
        assert result.sub_scores.doc_type_accuracy == 100.0
        assert len(result.mismatches) == 0

    def test_cjk_short_titles_support_containment_and_character_overlap(self) -> None:
        """CJK short titles match accurately even when containing minor spacing or surrounding decree affixes."""
        prediction = {
            "title": "中华人民共和国国家发展和改革委员会令（第45号）　　国家发展改革委关于修改、废止一批规章和行政规范性文件的决定",
        }
        ground_truth = {
            "title": "国家发展改革委关于修改、废止一批规章和行政规范性文件的决定",
        }
        result = evaluate_accuracy(prediction, ground_truth)
        assert result.sub_scores.title_accuracy >= 80.0

        short_cjk_pred = {"title": "株券引渡請求事件"}
        short_cjk_gt = {"title": "株券引渡請求事件"}
        assert evaluate_accuracy(short_cjk_pred, short_cjk_gt).sub_scores.title_accuracy == 100.0

    def test_doc_number_both_null_evaluates_to_full_score(self) -> None:
        """When doc_number is absent in both prediction and ground truth, accuracy is 100.0 (null==null)."""
        prediction = {"doc_number": None}
        ground_truth = {"doc_number": None}
        result = evaluate_accuracy(prediction, ground_truth)
        assert result.sub_scores.doc_number_accuracy == 100.0

    def test_doc_number_missing_in_prediction_triggers_zero_score(self) -> None:
        """When prediction is None but ground truth has a valid doc number, accuracy is 0.0."""
        prediction = {"doc_number": None}
        ground_truth = {"doc_number": "管理運営課情報第1号"}
        result = evaluate_accuracy(prediction, ground_truth)
        assert result.sub_scores.doc_number_accuracy == 0.0
        assert any(m["field"] == "doc_number" for m in result.mismatches)

    def test_doc_number_polluted_with_file_size_receives_partial_credit(self) -> None:
        """A doc_number that contains the ground truth but has extraneous bytes (e.g. 372.7 KB) receives partial score."""
        prediction = {"doc_number": "Western Cape Provincial Gazette 9196 on 18 February 2026 (372.7 KB)"}
        ground_truth = {"doc_number": "Western Cape Provincial Gazette 9196"}
        result = evaluate_accuracy(prediction, ground_truth)
        assert 50.0 <= result.sub_scores.doc_number_accuracy < 90.0

    def test_effective_at_both_null_evaluates_to_full_score(self) -> None:
        """When effective_at is null in both prediction and ground truth, accuracy is 100.0."""
        prediction = {"effective_at": None}
        ground_truth = {"effective_at": None}
        result = evaluate_accuracy(prediction, ground_truth)
        assert result.sub_scores.effective_at_accuracy == 100.0

    def test_effective_at_missing_when_ground_truth_exists_triggers_penalty(self) -> None:
        """When effective_at is null in prediction but exists in ground truth, accuracy is 0.0."""
        prediction = {"effective_at": None}
        ground_truth = {"effective_at": "2026-10-01"}
        result = evaluate_accuracy(prediction, ground_truth)
        assert result.sub_scores.effective_at_accuracy == 0.0
        assert any(m["field"] == "effective_at" for m in result.mismatches)

    def test_issuer_administrative_division_variation_receives_high_partial_score(self) -> None:
        """Issuer variations such as division suffix (民事部) or prefecture naming receive partial matching score."""
        prediction = {"issuer": "甲府地方裁判所 民事部"}
        ground_truth = {"issuer": "甲府地方裁判所民事部"}
        result = evaluate_accuracy(prediction, ground_truth)
        assert result.sub_scores.issuer_accuracy >= 95.0

        short_issuer_pred = {"issuer": "河南省"}
        full_issuer_gt = {"issuer": "河南省人民政府"}
        result_short = evaluate_accuracy(short_issuer_pred, full_issuer_gt)
        assert 50.0 <= result_short.sub_scores.issuer_accuracy <= 80.0

    def test_doc_type_canonical_taxonomy_mapping_awards_high_credit(self) -> None:
        """A doc_type matching canonical taxonomy (e.g. by-law vs regulation, or 通知公告 vs notice) is credited."""
        prediction = {"doc_type": "by-law"}
        ground_truth = {"doc_type": "regulation"}
        result = evaluate_accuracy(prediction, ground_truth)
        assert result.sub_scores.doc_type_accuracy >= 80.0

        composite_notice = evaluate_accuracy({"doc_type": "通知公告"}, {"doc_type": "notice"})
        assert composite_notice.sub_scores.doc_type_accuracy == 85.0

    def test_empty_or_whitespace_title_prediction_yields_zero_accuracy_and_records_mismatch(self) -> None:
        """Empty or whitespace-only predicted titles must score 0.0 accuracy and record a mismatch."""
        ground_truth = {"title": "Constitution of the Republic of South Africa"}

        # Empty string prediction
        result_empty = evaluate_accuracy({"title": ""}, ground_truth)
        assert result_empty.sub_scores.title_accuracy == 0.0
        assert any(m["field"] == "title" and "Missing or blank title" in m["reason"] for m in result_empty.mismatches)

        # Whitespace-only prediction
        result_ws = evaluate_accuracy({"title": "   \n\t  "}, ground_truth)
        assert result_ws.sub_scores.title_accuracy == 0.0
        assert any(m["field"] == "title" and "Missing or blank title" in m["reason"] for m in result_ws.mismatches)

        # None prediction
        result_none = evaluate_accuracy({"title": None}, ground_truth)
        assert result_none.sub_scores.title_accuracy == 0.0
        assert any(m["field"] == "title" for m in result_none.mismatches)

    def test_date_mismatch_on_day_or_month_enforces_strict_zero_exact_match(self) -> None:
        """Dates must enforce Strict Exact Match (EM): day or month mismatches score strictly 0.0."""
        ground_truth = {
            "published_at": "2026-10-01",
            "effective_at": "2026-10-01",
        }
        same_year_month_diff_day = {
            "published_at": "2026-10-15",
            "effective_at": "2026-10-02",
        }
        res_day = evaluate_accuracy(same_year_month_diff_day, ground_truth)
        assert res_day.sub_scores.published_at_accuracy == 0.0
        assert res_day.sub_scores.effective_at_accuracy == 0.0
        assert any(m["field"] == "published_at" for m in res_day.mismatches)
        assert any(m["field"] == "effective_at" for m in res_day.mismatches)

        same_year_diff_month = {
            "published_at": "2026-09-01",
            "effective_at": "2026-01-01",
        }
        res_month = evaluate_accuracy(same_year_diff_month, ground_truth)
        assert res_month.sub_scores.published_at_accuracy == 0.0
        assert res_month.sub_scores.effective_at_accuracy == 0.0

    def test_anls_threshold_clips_low_similarity_title_issuer_and_doc_number_to_zero(self) -> None:
        """When normalized Levenshtein similarity < 0.50 (without containment), field accuracy is clipped to 0.0."""
        ground_truth = {
            "title": "Environmental Protection and Pollution Control Act",
            "doc_number": "Regulation 2026-8841-A",
            "issuer": "Ministry of Ecological Transition",
        }
        unrelated_prediction = {
            "title": "Notice of Municipal Highway Closure",
            "doc_number": "Decree 1998-0012-Z",
            "issuer": "Department of Fisheries",
        }
        result = evaluate_accuracy(unrelated_prediction, ground_truth)
        assert result.sub_scores.title_accuracy == 0.0
        assert result.sub_scores.doc_number_accuracy == 0.0
        assert result.sub_scores.issuer_accuracy == 0.0


# ==============================================================================
# 3. Benchmark Evaluator Black-Box Tests
# ==============================================================================


class TestBenchmarkEvaluator:
    """Evaluates single samples and entire multi-jurisdiction benchmark datasets."""

    def test_evaluate_single_sample_with_ground_truth_produces_composite_score(self) -> None:
        """Evaluating a sample with ground truth calculates cleanliness, accuracy, and overall score."""
        prediction = {
            "title": "City Ombudsman By-law, 2025",
            "doc_number": "Western Cape Provincial Gazette 9196",
            "published_at": "2026-02-18",
            "effective_at": "2026-02-18",
            "issuer": "City of Cape Town",
            "doc_type": "by-law",
            "markdown": "# City Ombudsman By-law, 2025\n\nTo provide for the appointment of the Ombudsman.",
        }
        ground_truth = {
            "title": "City Ombudsman By-law, 2025",
            "doc_number": "Western Cape Provincial Gazette 9196",
            "published_at": "2026-02-18",
            "effective_at": "2026-02-18",
            "issuer": "City of Cape Town",
            "doc_type": "by-law",
            "markdown": "# City Ombudsman By-law, 2025\n\nTo provide for the appointment of the Ombudsman.",
        }
        res = evaluate_sample(
            prediction,
            ground_truth=ground_truth,
            sample_id="cand_test_01",
            source_id="dd-572",
            jurisdiction="ZA",
        )

        assert isinstance(res, SampleEvaluationResult)
        assert res.sample_id == "cand_test_01"
        assert res.source_id == "dd-572"
        assert res.jurisdiction == "ZA"
        assert res.accuracy is not None
        assert res.accuracy.score == 100.0
        assert res.cleanliness is not None
        assert res.cleanliness.score == 100.0
        assert res.overall_score == 100.0

        # Verify 30% Cleanliness + 70% Accuracy composite weighting when scores differ
        pred_missing_effective_date = {
            **prediction,
            "effective_at": None,
        }
        res_weighted = evaluate_sample(
            pred_missing_effective_date,
            ground_truth=ground_truth,
            sample_id="cand_test_02",
            source_id="dd-572",
            jurisdiction="ZA",
        )
        assert res_weighted.cleanliness is not None
        assert res_weighted.cleanliness.score == 100.0
        assert res_weighted.accuracy is not None
        assert res_weighted.accuracy.score == 85.0
        assert res_weighted.content_fidelity is not None
        assert res_weighted.content_fidelity.content_f1 == 100.0
        assert res_weighted.overall_score == round(
            0.50 * res_weighted.accuracy.score
            + 0.25 * res_weighted.content_fidelity.content_f1
            + 0.25 * res_weighted.cleanliness.score,
            2,
        )
        assert res_weighted.overall_score == 92.5

    def test_evaluate_single_sample_without_ground_truth_produces_cleanliness_only(self) -> None:
        """Evaluating a sample without ground truth assigns accuracy=None and overall_score=cleanliness.score."""
        prediction = {
            "title": "Standalone Rule",
            "markdown": "# Standalone Rule\n\nValid text without ground truth annotation.",
        }
        res = evaluate_sample(prediction, ground_truth=None, sample_id="cand_unsupervised")
        assert res.accuracy is None
        assert res.cleanliness is not None
        assert res.overall_score == res.cleanliness.score

    def test_evaluate_authoritative_benchmark_dataset_produces_summary_report(self, tmp_path: Path) -> None:
        """Evaluating a multi-jurisdiction ground_truth_benchmark.jsonl produces a fully populated BenchmarkSummaryReport."""
        gt_path = _write_benchmark_ground_truth_fixture(tmp_path)
        assert gt_path.is_file()

        report = evaluate_benchmark_dataset(gt_path)

        assert isinstance(report, BenchmarkSummaryReport)
        assert report.total_samples == len(BENCHMARK_JURISDICTIONS)
        assert 0.0 <= report.average_overall_score <= 100.0
        assert report.average_cleanliness_score is not None
        assert 0.0 <= report.average_cleanliness_score <= 100.0
        assert report.average_accuracy_score is not None
        assert 0.0 <= report.average_accuracy_score <= 100.0

        # Dimension scores
        for dim in ("noise_purity", "encoding_purity", "structural_integrity", "boilerplate_freedom", "deduplication_purity"):
            assert dim in report.cleanliness_dimension_scores
            assert 0.0 <= report.cleanliness_dimension_scores[dim] <= 100.0

        for field in ("title_accuracy", "doc_number_accuracy", "published_at_accuracy", "effective_at_accuracy", "issuer_accuracy", "doc_type_accuracy"):
            assert field in report.accuracy_dimension_scores
            assert 0.0 <= report.accuracy_dimension_scores[field] <= 100.0

        # Jurisdictions coverage
        assert len(report.jurisdiction_scores) >= 8
        for _jur, scores in report.jurisdiction_scores.items():
            assert "overall" in scores
            assert "cleanliness" in scores
            assert "accuracy" in scores

        # Worst performing samples
        assert len(report.worst_performing_samples) > 0
        assert len(report.worst_performing_samples) <= report.total_samples

    def test_evaluate_dataset_alias_matches_contract(self, tmp_path: Path) -> None:
        """Function alias evaluate_dataset and return type alias BenchmarkReport match contract."""
        gt_path = _write_benchmark_ground_truth_fixture(tmp_path)
        with gt_path.open(encoding="utf-8") as f:
            records = [json.loads(line) for line in f][:3]

        report = evaluate_dataset(records)
        assert isinstance(report, (BenchmarkSummaryReport, BenchmarkReport))
        assert SampleScoreReport is SampleEvaluationResult
        assert report.total_samples == 3

    def test_json_array_with_leading_whitespace_or_newlines_loads_successfully(self, tmp_path: Path) -> None:
        """JSON array files with leading whitespace or newlines must load cleanly without JSONDecodeError."""
        json_content = (
            "   \n\n"
            "[\n"
            '  {"id": "cand_ws_01", "current_extracted": {"title": "Rule 1", "markdown": "# Rule 1"}},\n'
            '  {"id": "cand_ws_02", "current_extracted": {"title": "Rule 2", "markdown": "# Rule 2"}}\n'
            "]\n"
        )
        json_file = tmp_path / "indented_records.json"
        json_file.write_text(json_content, encoding="utf-8")

        loaded_records = load_records(json_file)
        assert len(loaded_records) == 2
        assert loaded_records[0]["id"] == "cand_ws_01"
        assert loaded_records[1]["id"] == "cand_ws_02"

        report = evaluate_benchmark_dataset(json_file)
        assert report.total_samples == 2
        assert report.average_cleanliness_score is not None
        assert report.average_cleanliness_score >= 90.0

    def test_external_ground_truth_takes_precedence_over_embedded_ground_truth(self) -> None:
        """Explicit external ground truth must take precedence over embedded ground truth in candidate items."""
        candidate_records = [{
            "id": "cand_override_01",
            "current_extracted": {"title": "Candidate Prediction Title", "markdown": "# Candidate Prediction Title"},
            "ground_truth": {"title": "Embedded Ground Truth Title"},
        }]
        external_gt_records = [{
            "id": "cand_override_01",
            "ground_truth": {"title": "External Override Ground Truth Title"},
        }]

        report = evaluate_benchmark_dataset(candidate_records, ground_truth_path_or_records=external_gt_records)
        assert report.total_samples == 1
        sample_res = report.sample_results[0]
        assert sample_res.accuracy is not None
        # Mismatches must reflect the external ground truth title, not the embedded one
        assert any(
            m.get("ground_truth") == "External Override Ground Truth Title"
            for m in sample_res.accuracy.mismatches
        )


# ==============================================================================
# 4. Benchmark Reporter Black-Box Tests
# ==============================================================================


class TestBenchmarkReporter:
    """Generates structured JSON and human-readable Markdown score reports."""

    def test_json_and_markdown_reports_contain_required_score_cards_and_tables(self, tmp_path: Path) -> None:
        """Reporter generates complete, parseable JSON and informative Markdown artifacts."""
        gt_path = _write_benchmark_ground_truth_fixture(tmp_path / "fixture")
        report = evaluate_benchmark_dataset(gt_path)

        json_str = render_benchmark_json_report(report)
        parsed_json = json.loads(json_str)
        assert parsed_json["total_samples"] == len(BENCHMARK_JURISDICTIONS)
        assert "average_overall_score" in parsed_json
        assert "cleanliness_dimension_scores" in parsed_json
        assert "accuracy_dimension_scores" in parsed_json

        md_str = render_benchmark_markdown_report(report)
        assert "# WorldLawHarvester Quantitative Benchmark Evaluation Report" in md_str
        assert "## 1. Executive Score Card" in md_str
        assert "## 2. Dimension Radar Scores" in md_str
        assert "## 3. Jurisdiction Breakdown" in md_str
        assert "## 4. Worst-Performing Error Cases & Defect Diagnostics" in md_str
        assert "noise_purity" in md_str
        assert "effective_at_accuracy" in md_str
        assert "| >= 91.5 |" in md_str
        assert "| >= 95.0 |" in md_str
        assert "| >= 90.0 |" in md_str
        assert any("| `boilerplate_freedom` |" in line and "| 25% |" in line for line in md_str.splitlines())
        assert any("| `deduplication_purity` |" in line and "| 10% |" in line for line in md_str.splitlines())

        json_out, md_out = write_benchmark_reports(report, tmp_path)
        assert json_out.is_file()
        assert md_out.is_file()
        assert json_out.stat().st_size > 0
        assert md_out.stat().st_size > 0


# ==============================================================================
# 5. Standalone CLI & Subcommand Black-Box Tests
# ==============================================================================


class TestBenchmarkCommandLineInterface:
    """CLI execution via python -m wlh.benchmark and wlh benchmark."""

    def test_standalone_module_execution_with_valid_arguments_exits_zero(self, tmp_path: Path) -> None:
        """Executing python -m wlh.benchmark against benchmark dataset generates outputs and exits 0."""
        gt_path = _write_benchmark_ground_truth_fixture(tmp_path / "fixture")
        cmd = [
            sys.executable,
            "-m",
            "wlh.benchmark",
            "--input",
            str(gt_path),
            "--ground-truth",
            str(gt_path),
            "--output-dir",
            str(tmp_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert proc.returncode == 0, f"CLI failed with stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"

        json_file = tmp_path / "benchmark_report.json"
        md_file = tmp_path / "benchmark_report.md"
        assert json_file.is_file(), "benchmark_report.json not written"
        assert md_file.is_file(), "benchmark_report.md not written"

        with json_file.open(encoding="utf-8") as f:
            data = json.load(f)
            assert data["total_samples"] == len(BENCHMARK_JURISDICTIONS)

    def test_wlh_cli_benchmark_subcommand_execution_exits_zero(self, tmp_path: Path) -> None:
        """Executing python -m wlh benchmark against benchmark dataset generates outputs and exits 0."""
        gt_path = _write_benchmark_ground_truth_fixture(tmp_path / "fixture")
        cmd = [
            sys.executable,
            "-m",
            "wlh",
            "benchmark",
            "--input",
            str(gt_path),
            "--ground-truth",
            str(gt_path),
            "--output-dir",
            str(tmp_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert proc.returncode == 0, f"wlh benchmark failed:\n{proc.stderr}\nstdout:\n{proc.stdout}"

        json_file = tmp_path / "benchmark_report.json"
        md_file = tmp_path / "benchmark_report.md"
        assert json_file.is_file()
        assert md_file.is_file()

    def test_cli_missing_input_file_returns_non_zero_exit_code(self) -> None:
        """Providing a non-existent input file exits with code 2."""
        cmd = [
            sys.executable,
            "-m",
            "wlh.benchmark",
            "--input",
            "data/benchmark/non_existent_file.jsonl",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert proc.returncode != 0
