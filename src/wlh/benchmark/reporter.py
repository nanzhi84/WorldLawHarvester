"""Report generator for benchmark evaluation results (JSON and Markdown)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wlh.benchmark.models import BenchmarkSummaryReport


def render_benchmark_json_report(report: BenchmarkSummaryReport) -> str:
    """Render structured JSON report representation."""
    return json.dumps(report.model_dump(), indent=2, ensure_ascii=False)


def render_benchmark_markdown_report(report: BenchmarkSummaryReport) -> str:
    """Render human-readable Markdown evaluation report with score cards, dimensions, and error diagnostics."""
    overall = report.average_overall_score
    cleanliness = (
        report.average_cleanliness_score if report.average_cleanliness_score is not None else 0.0
    )
    accuracy = report.average_accuracy_score if report.average_accuracy_score is not None else 0.0
    list_f1 = report.average_list_discovery_f1
    split_f1 = report.average_record_split_f1
    fid_f1 = report.average_content_fidelity_score

    clean_target = 95.0
    acc_target = 90.0
    overall_target = round(0.30 * clean_target + 0.70 * acc_target, 1)

    lines: list[str] = [
        "# WorldLawHarvester Quantitative Benchmark Evaluation Report",
        "",
        f"> **Generated At**: {report.evaluated_at or 'N/A'}  ",
        f"> **Evaluated Split**: `{report.evaluated_split}`  ",
        f"> **Total Evaluated Samples**: {report.total_samples}  ",
        f"> **Overall Composite Score**: **{overall:.2f} / 100.0**  ",
        f"> **Cleanliness Score**: **{cleanliness:.2f} / 100.0**  ",
        f"> **Accuracy Score**: **{accuracy:.2f} / 100.0**",
    ]
    if list_f1 is not None:
        lines.append(f"> **List Discovery F1**: **{list_f1:.2f} / 100.0**  ")
    if split_f1 is not None:
        lines.append(f"> **Record Split F1**: **{split_f1:.2f} / 100.0**  ")
    if fid_f1 is not None:
        lines.append(f"> **Content DOM Fidelity**: **{fid_f1:.2f} / 100.0**  ")

    lines.extend(
        [
            "",
            "---",
            "",
            "## 1. Executive Score Card",
            "",
            "| Evaluation Metric | Score | Target Standard | Assessment |",
            "| :--- | :---: | :---: | :---: |",
            f"| **Overall Quality Index** | **{overall:.2f}** | >= {overall_target:.1f} | {'✅ PASS' if overall >= overall_target else '⚠️ NEEDS REVIEW'} |",
        ]
    )

    if list_f1 is not None:
        lines.append(
            f"| **Pillar 1: List Discovery F1** | **{list_f1:.2f}** | >= 90.0 | {'✅ PASS' if list_f1 >= 90.0 else '⚠️ NEEDS REVIEW'} |"
        )
    if split_f1 is not None:
        lines.append(
            f"| **Pillar 2: Record Split F1** | **{split_f1:.2f}** | >= 90.0 | {'✅ PASS' if split_f1 >= 90.0 else '⚠️ NEEDS REVIEW'} |"
        )
    lines.append(
        f"| **Pillar 3: Metadata & Attachment Accuracy** | **{accuracy:.2f}** | >= {acc_target:.1f} | {'✅ PASS' if accuracy >= acc_target else '⚠️ NEEDS REVIEW'} |"
    )
    if fid_f1 is not None:
        lines.append(
            f"| **Pillar 4: Content DOM Boundary Fidelity** | **{fid_f1:.2f}** | >= 90.0 | {'✅ PASS' if fid_f1 >= 90.0 else '⚠️ NEEDS REVIEW'} |"
        )
    lines.append(
        f"| **Pillar 5: Text Cleanliness (干净度)** | **{cleanliness:.2f}** | >= {clean_target:.1f} | {'✅ PASS' if cleanliness >= clean_target else '⚠️ NEEDS REVIEW'} |"
    )

    if report.split_scores:
        lines.extend(
            [
                "",
                "### 1.1 Dataset Split Breakdown",
                "",
                "| Split | Sample Count | Overall Score | List Discovery F1 | Record Split F1 | Accuracy | Content Fidelity | Cleanliness |",
                "| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
            ]
        )
        for sp_key in ("dev", "test"):
            sp_data = report.split_scores.get(sp_key)
            if not sp_data:
                continue
            sp_cnt = int(sp_data.get("sample_count", 0))
            sp_ovr = float(sp_data.get("overall", 0.0))
            sp_disc = sp_data.get("list_discovery_f1")
            sp_rs = sp_data.get("record_split_f1")
            sp_acc = sp_data.get("accuracy")
            sp_fid = sp_data.get("content_fidelity")
            sp_cln = float(sp_data.get("cleanliness", 0.0))
            lines.append(
                f"| **`{sp_key}`** | {sp_cnt} | **{sp_ovr:.2f}** | "
                f"{f'{sp_disc:.2f}' if sp_disc is not None else 'N/A'} | "
                f"{f'{sp_rs:.2f}' if sp_rs is not None else 'N/A'} | "
                f"{f'{sp_acc:.2f}' if sp_acc is not None else 'N/A'} | "
                f"{f'{sp_fid:.2f}' if sp_fid is not None else 'N/A'} | "
                f"{sp_cln:.2f} |"
            )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 2. Dimension Radar Scores",
            "",
            "### 2.1 Text Cleanliness Dimensions (干净度五维)",
            "",
            "| Dimension Code | Metric Name | Score | Weight | Weighted Score |",
            "| :--- | :--- | :---: | :---: | :---: |",
        ]
    )

    clean_weights = {
        "noise_purity": (0.25, "HTML/Entity/Control Char Stripping (vs raw_payload)"),
        "encoding_purity": (0.20, "Unicode \\ufffd & Mojibake Freedom"),
        "structural_integrity": (0.20, "Table/Heading Formatting & Reference Markdown Recall"),
        "boilerplate_freedom": (0.25, "UI Navigation & Reference Markdown Boilerplate Removal"),
        "deduplication_purity": (0.10, "Paragraph & Block Deduplication"),
    }

    for dim, (w, label) in clean_weights.items():
        score = report.cleanliness_dimension_scores.get(dim, 0.0)
        weighted = score * w
        lines.append(f"| `{dim}` | {label} | {score:.2f} | {w:.0%} | {weighted:.2f} |")

    lines.extend(
        [
            "",
            "### 2.2 Field Accuracy Dimensions (准确度六维与附件发现)",
            "",
            "| Field Dimension | Target Field | Score | Weight | Weighted Score |",
            "| :--- | :--- | :---: | :---: | :---: |",
        ]
    )

    acc_weights = {
        "title_accuracy": (0.20, "Document Title (with CJK support)"),
        "doc_number_accuracy": (0.20, "Official Docket/Document Number"),
        "published_at_accuracy": (0.15, "Promulgation/Publication Date"),
        "effective_at_accuracy": (0.15, "Commencement/Effective Date"),
        "issuer_accuracy": (0.15, "Enacting Authority/Issuer"),
        "doc_type_accuracy": (0.15, "Document Taxonomy Classification"),
    }

    for dim, (w, label) in acc_weights.items():
        score = report.accuracy_dimension_scores.get(dim, 0.0)
        weighted = score * w
        lines.append(f"| `{dim}` | {label} | {score:.2f} | {w:.0%} | {weighted:.2f} |")

    att_score = report.accuracy_dimension_scores.get("attachment_url_f1")
    if att_score is not None:
        lines.append(
            f"| `attachment_url_f1` | Valid Attachment Download URLs | {att_score:.2f} | Diagnostic | N/A |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 3. Jurisdiction Breakdown",
            "",
            "| Jurisdiction | Sample Count | Cleanliness | Accuracy | Overall Score |",
            "| :---: | :---: | :---: | :---: | :---: |",
        ]
    )

    for jur, data in sorted(report.jurisdiction_scores.items()):
        cnt = int(data.get("sample_count", 0))
        j_clean = data.get("cleanliness", 0.0)
        j_acc = data.get("accuracy", 0.0)
        j_ovr = data.get("overall", 0.0)
        lines.append(f"| **{jur}** | {cnt} | {j_clean:.2f} | {j_acc:.2f} | **{j_ovr:.2f}** |")

    lines.extend(
        [
            "",
            "---",
            "",
            "## 4. Worst-Performing Error Cases & Defect Diagnostics",
            "",
        ]
    )

    if report.field_discrepancies:
        lines.extend(
            [
                "### 4.1 Field Discrepancies & Extraction Fill Rates (vs Ground Truth)",
                "",
                "| Target Field | Ground Truth Fill Rate | Prediction Fill Rate | Fill Rate Gap | Accuracy Score | Key Diagnostic Finding |",
                "| :--- | :---: | :---: | :---: | :---: | :--- |",
            ]
        )
        eff_stats = report.field_discrepancies.get("effective_at", {})
        eff_gt_rate = eff_stats.get("ground_truth_fill_rate", 0.0)
        eff_pred_rate = eff_stats.get("prediction_fill_rate", 0.0)
        diagnostic_notes = {
            "title": "Consistent title alignment with CJK support; residual suffix/prefix noise penalized",
            "doc_number": "Occasional document number omission, table-row confusion, or byte pollution",
            "published_at": "High extraction fidelity; portal repost dates or month/day mismatches penalized",
            "effective_at": (
                f"Major extraction deficit: {eff_gt_rate:.1f}% available in text "
                f"but only {eff_pred_rate:.1f}% captured"
            ),
            "issuer": "Portal operator / breadcrumb substitution and joint-issuer omission penalized",
            "doc_type": "Taxonomy mapping divergences against canonical statute/regulation/judgment/guidance",
        }
        for fld, stats in report.field_discrepancies.items():
            gt_str = f"{stats.get('ground_truth_fill_rate', 0.0):.1f}% ({stats.get('ground_truth_filled', 0)}/{report.total_samples})"
            pred_str = f"{stats.get('prediction_fill_rate', 0.0):.1f}% ({stats.get('prediction_filled', 0)}/{report.total_samples})"
            gap = stats.get("fill_rate_gap", 0.0)
            gap_str = f"{gap:+.1f}%" if gap != 0 else "0.0%"
            f_score = stats.get("accuracy_score", 0.0)
            note = diagnostic_notes.get(fld, "")
            lines.append(
                f"| `{fld}` | {gt_str} | {pred_str} | {gap_str} | {f_score:.2f} | {note} |"
            )
        lines.extend(
            [
                "",
                "### 4.2 Top Defect Diagnostics & Worst-Performing Samples",
                "",
            ]
        )

    lines.extend(
        [
            "| Sample ID | Source ID | Jurisdiction | Overall Score | Cleanliness | Accuracy | Primary Defects & Mismatches |",
            "| :--- | :--- | :---: | :---: | :---: | :---: | :--- |",
        ]
    )

    for s in report.worst_performing_samples:
        sid = s.get("sample_id")
        src = s.get("source_id")
        jur = s.get("jurisdiction")
        ovr = s.get("overall_score")
        ovr_val = float(ovr) if ovr is not None else 0.0
        cln = s.get("cleanliness_score")
        cln_str = f"{cln:.2f}" if cln is not None else "N/A"
        acc = s.get("accuracy_score")
        acc_str = f"{acc:.2f}" if acc is not None else "N/A"

        defects: list[str] = []
        penalties_list: list[Any] = s.get("top_penalties", [])
        for p in penalties_list:
            defects.append(f"[Penalty] {p}")
        mismatches_list: list[Any] = s.get("top_mismatches", [])
        for m in mismatches_list:
            defects.append(f"[Mismatch] {m}")
        defect_str = "<br>".join(defects[:2]) if defects else "No critical defects"

        lines.append(
            f"| `{sid}` | `{src}` | {jur} | **{ovr_val:.2f}** | {cln_str} | {acc_str} | {defect_str} |"
        )

    lines.append("")
    return "\n".join(lines)


def write_benchmark_reports(
    report: BenchmarkSummaryReport, output_dir: Path | str
) -> tuple[Path, Path]:
    """Write benchmark JSON and Markdown reports to specified output directory."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    json_path = out_path / "benchmark_report.json"
    md_path = out_path / "benchmark_report.md"

    json_path.write_text(render_benchmark_json_report(report), encoding="utf-8")
    md_path.write_text(render_benchmark_markdown_report(report), encoding="utf-8")

    return json_path, md_path
