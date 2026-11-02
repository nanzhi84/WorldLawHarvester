"""Benchmark evaluation engine for single samples and batch datasets."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, cast

from wlh.benchmark.metrics import (
    evaluate_accuracy,
    evaluate_cleanliness,
)
from wlh.benchmark.models import (
    BenchmarkReport,
    BenchmarkSummaryReport,
    SampleEvaluationResult,
)


def _extract_prediction_dict(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Extract standard prediction fields from a raw candidate or extraction dictionary."""
    base: dict[str, Any]
    curr = raw.get("current_extracted")
    ext = raw.get("extracted")
    res = raw.get("result")
    if isinstance(curr, dict):
        base = dict(cast(dict[str, Any], curr))
    elif isinstance(ext, dict):
        base = dict(cast(dict[str, Any], ext))
    elif isinstance(res, dict):
        base = dict(cast(dict[str, Any], res))
    else:
        base = dict(cast(dict[str, Any], raw))

    # Normalize markdown field naming
    if "markdown" not in base:
        md_val = base.get("body_markdown") or base.get("body") or base.get("content") or ""
        base["markdown"] = str(md_val)
    return base


def evaluate_sample(
    prediction: Mapping[str, Any],
    ground_truth: Mapping[str, Any] | None = None,
    sample_id: str = "",
    source_id: str = "",
    jurisdiction: str = "",
) -> SampleEvaluationResult:
    """Evaluate a single document sample for cleanliness and optional ground truth accuracy."""
    pred_dict = _extract_prediction_dict(prediction)
    sid = sample_id or str(prediction.get("id") or "")
    src_id = source_id or str(prediction.get("source_id") or "")
    jur = jurisdiction or str(prediction.get("jurisdiction") or "")

    markdown = str(pred_dict.get("markdown") or "")
    title = str(pred_dict.get("title") or "")

    clean_meta = dict(pred_dict)
    if ground_truth is not None:
        clean_meta["ground_truth"] = dict(ground_truth)

    cleanliness = evaluate_cleanliness(markdown, title=title, metadata=clean_meta)

    if ground_truth is not None:
        accuracy = evaluate_accuracy(pred_dict, ground_truth)
        overall_score = round(0.30 * cleanliness.score + 0.70 * accuracy.score, 2)
    else:
        accuracy = None
        overall_score = cleanliness.score

    return SampleEvaluationResult(
        sample_id=sid,
        source_id=src_id,
        jurisdiction=jur,
        cleanliness=cleanliness,
        accuracy=accuracy,
        overall_score=overall_score,
    )


def load_records(path_or_records: Any) -> list[dict[str, Any]]:
    """Load records from a file path (JSON/JSONL) or existing collection."""
    if isinstance(path_or_records, (str, Path)):
        path = Path(path_or_records)
        if not path.is_file():
            raise FileNotFoundError(f"Dataset file not found: {path}")

        records: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as f:
            head = f.read(4096).lstrip()
            f.seek(0)
            if head.startswith("["):
                # JSON array
                raw_data: Any = json.load(f)
                if isinstance(raw_data, list):
                    res_items: list[dict[str, Any]] = []
                    for it in cast(list[object], raw_data):
                        if isinstance(it, dict):
                            res_items.append(dict(cast(dict[str, Any], it)))
                    return res_items
            # JSONL lines
            for line in f:
                stripped = line.strip()
                if stripped:
                    item: Any = json.loads(stripped)
                    if isinstance(item, dict):
                        records.append(dict(cast(dict[str, Any], item)))
        return records
    if isinstance(path_or_records, list):
        res_records: list[dict[str, Any]] = []
        for r_item in cast(list[object], path_or_records):
            if isinstance(r_item, dict):
                res_records.append(dict(cast(dict[str, Any], r_item)))
        return res_records
    raise TypeError(f"Unsupported dataset input type: {type(path_or_records)}")


_load_records = load_records


def evaluate_benchmark_dataset(
    dataset_path_or_records: Any,
    ground_truth_path_or_records: Any | None = None,
) -> BenchmarkSummaryReport:
    """Evaluate an entire dataset and compute aggregate quality metrics across all dimensions."""
    records = _load_records(dataset_path_or_records)

    gt_map: dict[str, dict[str, Any]] = {}
    if ground_truth_path_or_records is not None:
        gt_records = _load_records(ground_truth_path_or_records)
        for item in gt_records:
            if "id" in item:
                gt_map[str(item["id"])] = item.get("ground_truth") or item

    sample_results: list[SampleEvaluationResult] = []

    for item in records:
        sid = str(item.get("id") or "")
        src_id = str(item.get("source_id") or "")
        jur = str(item.get("jurisdiction") or "")

        # Determine prediction and ground truth pair
        if sid in gt_map:
            prediction = item.get("current_extracted") or item
            ground_truth = gt_map[sid]
        elif "current_extracted" in item and "ground_truth" in item:
            prediction = item["current_extracted"]
            ground_truth = item["ground_truth"]
        elif "ground_truth" in item:
            prediction = item.get("current_extracted") or item
            ground_truth = item["ground_truth"]
        else:
            prediction = item.get("current_extracted") or item
            ground_truth = None

        res = evaluate_sample(
            prediction=prediction,
            ground_truth=ground_truth,
            sample_id=sid,
            source_id=src_id,
            jurisdiction=jur,
        )
        sample_results.append(res)

    total_samples = len(sample_results)
    if total_samples == 0:
        return BenchmarkSummaryReport(
            total_samples=0,
            evaluated_at=datetime.now(timezone.utc).isoformat(),
            average_overall_score=0.0,
            average_cleanliness_score=0.0,
        )

    avg_overall = round(sum(s.overall_score for s in sample_results) / total_samples, 2)
    avg_cleanliness = round(sum(s.cleanliness.score for s in sample_results) / total_samples, 2)

    # Cleanliness dimension breakdown
    clean_dims = {
        "noise_purity": round(sum(s.cleanliness.sub_scores.noise_purity for s in sample_results) / total_samples, 2),
        "encoding_purity": round(sum(s.cleanliness.sub_scores.encoding_purity for s in sample_results) / total_samples, 2),
        "structural_integrity": round(sum(s.cleanliness.sub_scores.structural_integrity for s in sample_results) / total_samples, 2),
        "boilerplate_freedom": round(sum(s.cleanliness.sub_scores.boilerplate_freedom for s in sample_results) / total_samples, 2),
        "deduplication_purity": round(sum(s.cleanliness.sub_scores.deduplication_purity for s in sample_results) / total_samples, 2),
    }

    # Accuracy dimension breakdown
    samples_with_acc = [s for s in sample_results if s.accuracy is not None]
    if samples_with_acc:
        acc_count = len(samples_with_acc)
        avg_accuracy = round(sum(s.accuracy.score for s in samples_with_acc) / acc_count, 2)  # type: ignore[union-attr]
        acc_dims = {
            "title_accuracy": round(sum(s.accuracy.sub_scores.title_accuracy for s in samples_with_acc) / acc_count, 2),  # type: ignore[union-attr]
            "doc_number_accuracy": round(sum(s.accuracy.sub_scores.doc_number_accuracy for s in samples_with_acc) / acc_count, 2),  # type: ignore[union-attr]
            "published_at_accuracy": round(sum(s.accuracy.sub_scores.published_at_accuracy for s in samples_with_acc) / acc_count, 2),  # type: ignore[union-attr]
            "effective_at_accuracy": round(sum(s.accuracy.sub_scores.effective_at_accuracy for s in samples_with_acc) / acc_count, 2),  # type: ignore[union-attr]
            "issuer_accuracy": round(sum(s.accuracy.sub_scores.issuer_accuracy for s in samples_with_acc) / acc_count, 2),  # type: ignore[union-attr]
            "doc_type_accuracy": round(sum(s.accuracy.sub_scores.doc_type_accuracy for s in samples_with_acc) / acc_count, 2),  # type: ignore[union-attr]
        }
    else:
        avg_accuracy = None
        acc_dims = {}

    # Jurisdiction breakdown
    jur_groups: dict[str, list[SampleEvaluationResult]] = {}
    for s in sample_results:
        key = s.jurisdiction or "UNKNOWN"
        jur_groups.setdefault(key, []).append(s)

    jur_scores: dict[str, dict[str, float]] = {}
    for jur, group in sorted(jur_groups.items()):
        cnt = len(group)
        j_overall = round(sum(item.overall_score for item in group) / cnt, 2)
        j_clean = round(sum(item.cleanliness.score for item in group) / cnt, 2)
        acc_items = [item for item in group if item.accuracy is not None]
        j_acc = round(sum(item.accuracy.score for item in acc_items) / len(acc_items), 2) if acc_items else 0.0  # type: ignore[union-attr]
        jur_scores[jur] = {
            "overall": j_overall,
            "cleanliness": j_clean,
            "accuracy": j_acc,
            "sample_count": float(cnt),
        }

    # Worst-performing samples diagnostics
    sorted_samples = sorted(sample_results, key=lambda x: x.overall_score)
    worst_samples: list[dict[str, Any]] = []
    for s in sorted_samples[:10]:
        penalties_summary = [p["reason"] for p in s.cleanliness.penalties[:3]]
        mismatches_summary = [m["reason"] for m in (s.accuracy.mismatches if s.accuracy else [])[:3]]
        worst_samples.append({
            "sample_id": s.sample_id,
            "source_id": s.source_id,
            "jurisdiction": s.jurisdiction,
            "overall_score": s.overall_score,
            "cleanliness_score": s.cleanliness.score,
            "accuracy_score": s.accuracy.score if s.accuracy else None,
            "top_penalties": penalties_summary,
            "top_mismatches": mismatches_summary,
        })

    # Field discrepancy and fill rate analysis vs ground truth
    field_discrepancies: dict[str, dict[str, Any]] = {}
    if samples_with_acc:
        acc_fields = ["title", "doc_number", "published_at", "effective_at", "issuer", "doc_type"]
        field_score_keys = {
            "title": "title_accuracy",
            "doc_number": "doc_number_accuracy",
            "published_at": "published_at_accuracy",
            "effective_at": "effective_at_accuracy",
            "issuer": "issuer_accuracy",
            "doc_type": "doc_type_accuracy",
        }
        evaluated_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for item in records:
            sid = str(item.get("id") or "")
            if sid in gt_map:
                p = item.get("current_extracted") or item
                g = gt_map[sid]
            elif "current_extracted" in item and "ground_truth" in item:
                p = item["current_extracted"]
                g = item["ground_truth"]
            elif "ground_truth" in item:
                p = item.get("current_extracted") or item
                g = item["ground_truth"]
            else:
                continue
            if isinstance(p, dict) and isinstance(g, dict):
                p_dict = cast(dict[str, Any], p)
                g_dict = cast(dict[str, Any], g)
                evaluated_pairs.append((p_dict, g_dict))

        acc_total = len(evaluated_pairs) if evaluated_pairs else len(samples_with_acc)
        for field in acc_fields:
            gt_filled_count = 0
            pred_filled_count = 0
            missing_in_pred = 0
            for pred_dict_raw, gt_dict in evaluated_pairs:
                pred_dict = _extract_prediction_dict(pred_dict_raw)
                gt_val = gt_dict.get(field)
                pred_val = pred_dict.get(field)
                gt_has = gt_val is not None and str(gt_val).strip() != ""
                pred_has = pred_val is not None and str(pred_val).strip() != ""
                if gt_has:
                    gt_filled_count += 1
                    if not pred_has:
                        missing_in_pred += 1
                if pred_has:
                    pred_filled_count += 1
            gt_rate = round(gt_filled_count / acc_total * 100, 2) if acc_total > 0 else 0.0
            pred_rate = round(pred_filled_count / acc_total * 100, 2) if acc_total > 0 else 0.0
            gap = round(pred_rate - gt_rate, 2)
            field_discrepancies[field] = {
                "ground_truth_filled": gt_filled_count,
                "ground_truth_fill_rate": gt_rate,
                "prediction_filled": pred_filled_count,
                "prediction_fill_rate": pred_rate,
                "fill_rate_gap": gap,
                "missing_in_prediction_count": missing_in_pred,
                "accuracy_score": acc_dims.get(field_score_keys[field], 0.0),
            }

    return BenchmarkSummaryReport(
        total_samples=total_samples,
        evaluated_at=datetime.now(timezone.utc).isoformat(),
        average_overall_score=avg_overall,
        average_cleanliness_score=avg_cleanliness,
        average_accuracy_score=avg_accuracy,
        cleanliness_dimension_scores=clean_dims,
        accuracy_dimension_scores=acc_dims,
        jurisdiction_scores=jur_scores,
        worst_performing_samples=worst_samples,
        field_discrepancies=field_discrepancies,
        sample_results=sample_results,
    )


# Alias per PROJECT.md
def evaluate_dataset(dataset: list[dict[str, Any]]) -> BenchmarkReport:
    """Evaluate dataset alias matching PROJECT.md interface contract."""
    return evaluate_benchmark_dataset(dataset)
