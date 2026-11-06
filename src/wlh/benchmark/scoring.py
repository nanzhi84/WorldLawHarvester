"""Benchmark evaluation engine for single samples and batch datasets."""

from __future__ import annotations

from typing import Any, Literal, Mapping, cast

from wlh.benchmark.metrics import (
    evaluate_accuracy,
    evaluate_cleanliness,
    evaluate_content_fidelity,
    evaluate_list_discovery,
    evaluate_record_split,
)
from wlh.benchmark.models import (
    AccuracyResult,
    CleanlinessResult,
    ContentFidelityResult,
    SampleEvaluationResult,
)


def extract_prediction_dict(raw: Mapping[str, Any]) -> dict[str, Any]:
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
    task_type: Literal["list_page", "content_page"] | str = "",
) -> SampleEvaluationResult:
    """Evaluate a single document sample using the unified single-track scoring formula."""
    sid = sample_id or str(prediction.get("id") or "")
    src_id = source_id or str(prediction.get("source_id") or "")
    jur = jurisdiction or str(prediction.get("jurisdiction") or "")

    # Detect task_type
    t_type: Literal["list_page", "content_page"]
    if task_type in ["list_page", "content_page"]:
        t_type = cast(Literal["list_page", "content_page"], task_type)
    elif "target_urls" in prediction or (
        isinstance(ground_truth, Mapping) and "target_urls" in ground_truth
    ):
        t_type = "list_page"
    else:
        t_type = "content_page"

    # Branch 1: list_page evaluation (Pillar 1: Discovery)
    if t_type == "list_page":
        pred_u_raw = prediction.get("target_urls")
        pred_urls: list[str] = (
            [str(u) for u in cast(list[object], pred_u_raw)] if isinstance(pred_u_raw, list) else []
        )
        gt_u_raw = ground_truth.get("target_urls") if isinstance(ground_truth, Mapping) else None
        gt_urls: list[str] = (
            [str(u) for u in cast(list[object], gt_u_raw)] if isinstance(gt_u_raw, list) else []
        )
        discovery = evaluate_list_discovery(pred_urls, gt_urls)
        overall_score = round(discovery.target_url_f1, 2)

        return SampleEvaluationResult(
            sample_id=sid,
            task_type="list_page",
            source_id=src_id,
            jurisdiction=jur,
            discovery=discovery,
            overall_score=overall_score,
        )

    # Branch 2: content_page evaluation
    raw_pred_recs = prediction.get("records")
    if isinstance(raw_pred_recs, list) and raw_pred_recs:
        pred_records = [
            extract_prediction_dict(cast(Mapping[str, Any], r))
            for r in cast(list[object], raw_pred_recs)
            if isinstance(r, Mapping)
        ]
    else:
        pred_records = [extract_prediction_dict(prediction)]

    # If ground_truth is None: cleanliness-only evaluation
    if ground_truth is None:
        p_rec = pred_records[0]
        md = str(p_rec.get("markdown") or "")
        title = str(p_rec.get("title") or "")
        cleanliness = evaluate_cleanliness(md, title=title, metadata=p_rec)
        return SampleEvaluationResult(
            sample_id=sid,
            task_type="content_page",
            source_id=src_id,
            jurisdiction=jur,
            cleanliness=cleanliness,
            accuracy=None,
            overall_score=cleanliness.score,
        )

    # Extract ground truth records list
    gt_recs_raw = ground_truth.get("records")
    gt_records: list[dict[str, Any]] = []
    if isinstance(gt_recs_raw, list) and gt_recs_raw:
        for r in cast(list[object], gt_recs_raw):
            if isinstance(r, Mapping):
                gt_records.append(dict(cast(Mapping[str, Any], r)))
    if not gt_records:
        gt_records = [dict(ground_truth)]

    record_split, matched_pairs = evaluate_record_split(pred_records, gt_records)

    if not matched_pairs:
        p_rec = pred_records[0]
        cleanliness = evaluate_cleanliness(
            str(p_rec.get("markdown") or ""), title=str(p_rec.get("title") or "")
        )
        acc = evaluate_accuracy(p_rec, gt_records[0])
        fid = evaluate_content_fidelity(
            str(p_rec.get("markdown") or ""), str(gt_records[0].get("markdown") or "")
        )
        return SampleEvaluationResult(
            sample_id=sid,
            task_type="content_page",
            source_id=src_id,
            jurisdiction=jur,
            record_split=record_split,
            accuracy=acc,
            content_fidelity=fid,
            cleanliness=cleanliness,
            overall_score=0.0,
        )

    record_scores: list[float] = []
    acc_scores: list[AccuracyResult] = []
    fid_scores: list[ContentFidelityResult] = []
    clean_scores: list[CleanlinessResult] = []

    for p_idx, g_idx in matched_pairs:
        p_rec = pred_records[p_idx]
        g_rec: dict[str, Any] = gt_records[g_idx]

        clean_meta = dict(p_rec)
        clean_meta["ground_truth"] = g_rec

        acc = evaluate_accuracy(p_rec, g_rec)
        fid = evaluate_content_fidelity(
            str(p_rec.get("markdown") or ""), str(g_rec.get("markdown") or "")
        )
        clean = evaluate_cleanliness(
            str(p_rec.get("markdown") or ""),
            title=str(p_rec.get("title") or ""),
            metadata=clean_meta,
        )

        rec_score = 0.50 * acc.score + 0.25 * fid.content_f1 + 0.25 * clean.score
        record_scores.append(rec_score)
        acc_scores.append(acc)
        fid_scores.append(fid)
        clean_scores.append(clean)

    avg_rec_score = sum(record_scores) / len(record_scores)
    overall_score = round((record_split.record_split_f1 / 100.0) * avg_rec_score, 2)

    rep_acc = acc_scores[0]
    rep_fid = fid_scores[0]
    rep_clean = clean_scores[0]

    return SampleEvaluationResult(
        sample_id=sid,
        task_type="content_page",
        source_id=src_id,
        jurisdiction=jur,
        record_split=record_split,
        accuracy=rep_acc,
        content_fidelity=rep_fid,
        cleanliness=rep_clean,
        overall_score=overall_score,
    )
