"""Benchmark evaluation engine for single samples and batch datasets."""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
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
    BenchmarkReport,
    BenchmarkSummaryReport,
    CleanlinessResult,
    ContentFidelityResult,
    SampleEvaluationResult,
)

_PROCESSOR_CACHE: dict[tuple[str, str], tuple[dict[str, Any], Any]] = {}


def clear_processor_cache() -> None:
    """Invalidate source configurations and parsers after configuration changes."""
    _PROCESSOR_CACHE.clear()


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


def extract_with_processor(
    task: Mapping[str, Any], *, sources_dir: Path = Path("sources")
) -> dict[str, Any]:
    """Run offline processor pipeline on a ProcessorBenchmarkTask."""
    source_id = str(task.get("source_id", ""))
    task_type = str(task.get("task_type", "content_page"))
    request_url = str(task.get("request_url", ""))
    raw_payload = str(task.get("raw_payload", ""))
    mime_type = str(task.get("mime_type", "text/html"))

    sources_dir = Path(sources_dir)
    yaml_path = sources_dir / f"{source_id}.yaml"
    if not yaml_path.is_file():
        if task_type == "list_page":
            return {"target_urls": []}
        return {
            "records": [{"title": None, "doc_number": None, "markdown": "", "attachment_urls": []}]
        }

    try:
        import yaml

        from wlh.processor.parsers import load, normalize_parser_config

        cache_key = (str(sources_dir.resolve()), source_id)
        if cache_key not in _PROCESSOR_CACHE:
            with yaml_path.open("r", encoding="utf-8") as f:
                cfg = yaml.load(f, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
            norm_cfg = normalize_parser_config(cfg)
            parser = load(norm_cfg["adapter"])(norm_cfg)
            _PROCESSOR_CACHE[cache_key] = (norm_cfg, parser)
        norm_cfg, parser = _PROCESSOR_CACHE[cache_key]
        # Per-sample discovery state must not leak through the cached parser.
        if hasattr(parser, "detail_url"):
            parser.detail_url = None
        if hasattr(parser, "truncated"):
            parser.truncated = None

        from wlh.core import digest

        payload_bytes = raw_payload.encode("utf-8")

        class _OfflineCtx:
            class _Runtime:
                mode = "collect"
                config = norm_cfg

            runtime = _Runtime()
            config = norm_cfg

            @staticmethod
            def read(_acq: Any) -> bytes:
                return payload_bytes

            @staticmethod
            def follow_links(_t: Any) -> bool:
                return True

            @staticmethod
            def text_quality(markdown: str) -> dict[str, Any]:
                from wlh.cleaners.extraction_quality import text_check

                lang = str(norm_cfg.get("metadata", {}).get("language") or "")
                return text_check(markdown, "vi" if lang.startswith("vi") else "generic")

            @staticmethod
            def find_archived_image(_url: str) -> bytes | None:
                return None

        ctx = _OfflineCtx()
        parse_task = {
            "depth": 0 if task_type == "list_page" else 1,
            "payload": {"url": request_url, "role": "page"},
        }
        acquisition = {
            "id": f"acq-{source_id}",
            "outcome": "ok",
            "actual_url": request_url,
            "request_url": request_url,
            "mime_type": mime_type,
            "charset": "utf-8",
            "archive_ref": digest(payload_bytes),
            "state": "archived",
        }

        parsed = parser.parse(ctx, parse_task, acquisition)

        parsed_dict: dict[str, Any] = (
            cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}
        )

        if task_type == "list_page":
            requests_list: list[dict[str, Any]] = (
                cast(list[dict[str, Any]], parsed_dict.get("requests"))
                if isinstance(parsed_dict.get("requests"), list)
                else []
            )
            discovered_urls = [str(r["url"]) for r in requests_list if "url" in r]
            return {"target_urls": discovered_urls}
        else:
            records: list[dict[str, Any]] = []
            targets: list[dict[str, Any]] = (
                cast(list[dict[str, Any]], parsed_dict.get("targets"))
                if isinstance(parsed_dict.get("targets"), list)
                else []
            )
            for target in targets:
                inputs_list: list[dict[str, Any]] = (
                    cast(list[dict[str, Any]], target.get("inputs"))
                    if isinstance(target.get("inputs"), list)
                    else []
                )
                for inp in inputs_list:
                    inp.setdefault("acquisition", acquisition)
                try:
                    built: Any = parser.build(ctx, target, inputs_list)
                    docs: list[object] = cast(
                        list[object], built if isinstance(built, list) else [built]
                    )
                    for d_raw in docs:
                        if not isinstance(d_raw, dict):
                            continue
                        d: dict[str, Any] = cast(dict[str, Any], d_raw)
                        meta_dict: dict[str, Any] = cast(dict[str, Any], d.get("metadata") or {})
                        dates_dict: dict[str, Any] = cast(dict[str, Any], d.get("dates") or {})
                        delivs: list[object] = cast(list[object], d.get("deliverables") or [])
                        md = ""
                        for dev_raw in delivs:
                            if isinstance(dev_raw, dict):
                                dev: dict[str, Any] = cast(dict[str, Any], dev_raw)
                                if dev.get("markdown"):
                                    md = str(dev["markdown"])
                                    break
                        if not md:
                            md_raw = d.get("markdown") or meta_dict.get("markdown") or ""
                            md = str(md_raw)

                        title: str | None = (
                            str(d["title"])
                            if d.get("title") is not None
                            else (
                                str(meta_dict["title"])
                                if meta_dict.get("title") is not None
                                else None
                            )
                        )
                        doc_num: str | None = (
                            str(meta_dict["doc_number"])
                            if meta_dict.get("doc_number") is not None
                            else (
                                str(meta_dict["document_number"])
                                if meta_dict.get("document_number") is not None
                                else None
                            )
                        )
                        pub: str | None = (
                            str(dates_dict["published_at"])
                            if dates_dict.get("published_at") is not None
                            else (
                                str(meta_dict["published_at"])
                                if meta_dict.get("published_at") is not None
                                else None
                            )
                        )
                        eff: str | None = (
                            str(dates_dict["effective_at"])
                            if dates_dict.get("effective_at") is not None
                            else (
                                str(meta_dict["effective_at"])
                                if meta_dict.get("effective_at") is not None
                                else None
                            )
                        )
                        iss: str | None = (
                            str(meta_dict["issuer"])
                            if meta_dict.get("issuer") is not None
                            else None
                        )
                        cfg_meta = cast(dict[str, Any], norm_cfg.get("metadata", {}))
                        doc_t: str = str(
                            meta_dict.get("doc_type") or cfg_meta.get("document_type", "其他")
                        )
                        inp_candidates: list[object] = [
                            *cast(list[object], d.get("inputs") or []),
                            *inputs_list,
                        ]
                        att_urls: list[str] = []
                        for item_raw in inp_candidates:
                            if isinstance(item_raw, dict):
                                item_dict: dict[str, Any] = cast(dict[str, Any], item_raw)
                                if item_dict.get("role") == "attachment":
                                    req_raw = item_dict.get("request") or item_dict.get("locator")
                                    if isinstance(req_raw, dict):
                                        req_dict: dict[str, Any] = cast(dict[str, Any], req_raw)
                                        url_val = req_dict.get("url")
                                        if url_val and str(url_val) not in att_urls:
                                            att_urls.append(str(url_val))
                        records.append(
                            {
                                "title": title,
                                "doc_number": doc_num,
                                "published_at": pub,
                                "effective_at": eff,
                                "issuer": iss,
                                "doc_type": doc_t,
                                "markdown": md,
                                "attachment_urls": att_urls,
                            }
                        )
                except Exception:
                    pass
            if not records:
                records = [
                    {
                        "title": None,
                        "doc_number": None,
                        "published_at": None,
                        "effective_at": None,
                        "issuer": None,
                        "doc_type": "其他",
                        "markdown": "",
                        "attachment_urls": [],
                    }
                ]
            return {"records": records}
    except Exception:
        if task_type == "list_page":
            return {"target_urls": []}
        return {
            "records": [{"title": None, "doc_number": None, "markdown": "", "attachment_urls": []}]
        }


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
            _extract_prediction_dict(cast(Mapping[str, Any], r))
            for r in cast(list[object], raw_pred_recs)
            if isinstance(r, Mapping)
        ]
    else:
        pred_records = [_extract_prediction_dict(prediction)]

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
                raw_data: Any = json.load(f)
                if isinstance(raw_data, list):
                    res_items: list[dict[str, Any]] = []
                    for it in cast(list[object], raw_data):
                        if isinstance(it, dict):
                            res_items.append(dict(cast(dict[str, Any], it)))
                    return res_items
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


def _summarize_split_group(group: list[SampleEvaluationResult]) -> dict[str, Any]:
    """Compute aggregate 5-pillar and composite metrics for a split subset (dev or test)."""
    cnt = len(group)
    if cnt == 0:
        return {
            "sample_count": 0,
            "overall": 0.0,
            "list_discovery_f1": None,
            "record_split_f1": None,
            "accuracy": None,
            "content_fidelity": None,
            "cleanliness": 0.0,
        }
    ovr = round(sum(s.overall_score for s in group) / cnt, 2)
    disc = [s.discovery.target_url_f1 for s in group if s.discovery is not None]
    rs = [s.record_split.record_split_f1 for s in group if s.record_split is not None]
    acc = [s.accuracy.score for s in group if s.accuracy is not None]
    fid = [s.content_fidelity.content_f1 for s in group if s.content_fidelity is not None]
    cln = [s.cleanliness.score for s in group if s.cleanliness is not None]
    return {
        "sample_count": cnt,
        "overall": ovr,
        "list_discovery_f1": round(sum(disc) / len(disc), 2) if disc else None,
        "record_split_f1": round(sum(rs) / len(rs), 2) if rs else None,
        "accuracy": round(sum(acc) / len(acc), 2) if acc else None,
        "content_fidelity": round(sum(fid) / len(fid), 2) if fid else None,
        "cleanliness": round(sum(cln) / len(cln), 2) if cln else 0.0,
    }


def _prepare_eval_work_item(
    item: dict[str, Any],
    *,
    gt_map: dict[str, dict[str, Any]],
    split_map: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    sid = str(item.get("id") or item.get("sample_id") or "")
    src_id = str(item.get("source_id") or "")
    item_split = split_map.get(sid) or str(item.get("split") or "dev")
    lookup_id = sid if sid in gt_map else f"content_{src_id}_{sid}"
    matched_gt = gt_map.get(lookup_id)
    return (item, matched_gt, item_split)


def _evaluate_work_item(
    work_item: tuple[dict[str, Any], dict[str, Any] | None, str],
) -> tuple[SampleEvaluationResult, tuple[dict[str, Any], dict[str, Any]] | None]:
    item, matched_gt, item_split = work_item
    sid = str(item.get("id") or item.get("sample_id") or "")
    src_id = str(item.get("source_id") or "")
    jur = str(item.get("jurisdiction") or "")
    t_type = str(item.get("task_type") or "")

    # Determine prediction and ground truth pair
    if matched_gt is not None:
        prediction = item.get("current_extracted") or item
        ground_truth: Any = matched_gt
    elif "current_extracted" in item and "ground_truth" in item:
        prediction = item["current_extracted"]
        ground_truth = item["ground_truth"]
    elif "ground_truth" in item:
        prediction = item.get("current_extracted") or item
        ground_truth = item["ground_truth"]
    else:
        prediction = item.get("current_extracted") or item
        ground_truth = None

    pred_map: Mapping[str, Any] = (
        cast(Mapping[str, Any], prediction) if isinstance(prediction, Mapping) else {}
    )
    if "raw_payload" in pred_map and "markdown" not in pred_map and "target_urls" not in pred_map:
        pred_map = extract_with_processor(pred_map)

    gt_map_val: Mapping[str, Any] | None = (
        cast(Mapping[str, Any], ground_truth) if isinstance(ground_truth, Mapping) else None
    )

    res = evaluate_sample(
        prediction=pred_map,
        ground_truth=gt_map_val,
        sample_id=sid,
        source_id=src_id,
        jurisdiction=jur,
        task_type=t_type,
    )
    res.split = item_split
    pair: tuple[dict[str, Any], dict[str, Any]] | None = None
    if res.task_type == "content_page" and gt_map_val is not None:
        raw_p_recs = pred_map.get("records")
        if isinstance(raw_p_recs, list) and raw_p_recs and isinstance(raw_p_recs[0], Mapping):
            p_rec_dict = _extract_prediction_dict(cast(Mapping[str, Any], raw_p_recs[0]))
        else:
            p_rec_dict = _extract_prediction_dict(pred_map)
        raw_g_recs = gt_map_val.get("records")
        if isinstance(raw_g_recs, list) and raw_g_recs and isinstance(raw_g_recs[0], Mapping):
            g_rec_dict = dict(cast(Mapping[str, Any], raw_g_recs[0]))
        else:
            g_rec_dict = dict(gt_map_val)
        pair = (p_rec_dict, g_rec_dict)

    return res, pair


def evaluate_benchmark_dataset(
    dataset_path_or_records: Any,
    ground_truth_path_or_records: Any | None = None,
    *,
    split: str = "all",
    workers: int = 1,
) -> BenchmarkSummaryReport:
    """Evaluate an entire dataset (or a stratified dev/test split) and compute aggregate metrics."""
    from wlh.benchmark.sampler import assign_stratified_splits

    if workers < 1:
        raise ValueError("workers must be >= 1")
    normalized_split = (split or "all").strip().lower()
    if normalized_split not in ("all", "dev", "test"):
        raise ValueError(
            f"Unsupported benchmark split '{split}'; expected 'all', 'dev', or 'test'."
        )

    records = _load_records(dataset_path_or_records)

    gt_map: dict[str, dict[str, Any]] = {}
    gt_outer_map: dict[str, dict[str, Any]] = {}
    if ground_truth_path_or_records is not None:
        gt_records = _load_records(ground_truth_path_or_records)
        for item in gt_records:
            gid = str(item.get("id") or item.get("sample_id") or "")
            if gid:
                gt_map[gid] = item.get("ground_truth") or item
                gt_outer_map[gid] = item

    if gt_outer_map:
        enriched_records: list[dict[str, Any]] = []
        for item in records:
            sid_candidate = str(item.get("id") or item.get("sample_id") or "")
            src_candidate = str(item.get("source_id") or "")
            lookup_key = (
                sid_candidate
                if sid_candidate in gt_outer_map
                else f"content_{src_candidate}_{sid_candidate}"
            )
            gt_outer = gt_outer_map.get(lookup_key)
            if gt_outer is not None:
                merged_item = dict(item)
                for meta_key in ("source_id", "jurisdiction", "task_type", "split"):
                    if not merged_item.get(meta_key) and gt_outer.get(meta_key):
                        merged_item[meta_key] = gt_outer[meta_key]
                enriched_records.append(merged_item)
            else:
                enriched_records.append(item)
        records = enriched_records

    split_map = assign_stratified_splits(records)

    if normalized_split in ("dev", "test"):
        records = [
            item
            for item in records
            if split_map.get(str(item.get("id") or item.get("sample_id") or "")) == normalized_split
        ]

    sample_results: list[SampleEvaluationResult] = []
    evaluated_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []

    work_items = [
        _prepare_eval_work_item(item, gt_map=gt_map, split_map=split_map) for item in records
    ]
    if workers > 1 and len(work_items) > 1:
        # Processes isolate mutable parser state and parallelize CPU-bound extraction.
        worker_count = min(workers, len(work_items))
        chunksize = max(1, len(work_items) // (worker_count * 4))
        with ProcessPoolExecutor(max_workers=worker_count) as pool:
            evaluated = list(pool.map(_evaluate_work_item, work_items, chunksize=chunksize))
    else:
        evaluated = [_evaluate_work_item(w) for w in work_items]
    for res, pair in evaluated:
        sample_results.append(res)
        if pair is not None:
            evaluated_pairs.append(pair)

    total_samples = len(sample_results)
    if total_samples == 0:
        return BenchmarkSummaryReport(
            total_samples=0,
            evaluated_at=datetime.now(timezone.utc).isoformat(),
            evaluated_split=normalized_split,
            average_overall_score=0.0,
            average_cleanliness_score=0.0,
        )

    avg_overall = round(sum(s.overall_score for s in sample_results) / total_samples, 2)

    # 5 Pillars Averages
    disc_scores = [s.discovery.target_url_f1 for s in sample_results if s.discovery is not None]
    avg_list_discovery = round(sum(disc_scores) / len(disc_scores), 2) if disc_scores else None

    split_f1_scores = [
        s.record_split.record_split_f1 for s in sample_results if s.record_split is not None
    ]
    avg_record_split = (
        round(sum(split_f1_scores) / len(split_f1_scores), 2) if split_f1_scores else None
    )

    valid_clean = [s.cleanliness for s in sample_results if s.cleanliness is not None]
    if valid_clean:
        c_len = len(valid_clean)
        avg_cleanliness = round(sum(c.score for c in valid_clean) / c_len, 2)
        clean_dims = {
            "noise_purity": round(sum(c.sub_scores.noise_purity for c in valid_clean) / c_len, 2),
            "encoding_purity": round(
                sum(c.sub_scores.encoding_purity for c in valid_clean) / c_len, 2
            ),
            "structural_integrity": round(
                sum(c.sub_scores.structural_integrity for c in valid_clean) / c_len, 2
            ),
            "boilerplate_freedom": round(
                sum(c.sub_scores.boilerplate_freedom for c in valid_clean) / c_len, 2
            ),
            "deduplication_purity": round(
                sum(c.sub_scores.deduplication_purity for c in valid_clean) / c_len, 2
            ),
        }
    else:
        avg_cleanliness = 0.0
        clean_dims = {}

    valid_acc = [s.accuracy for s in sample_results if s.accuracy is not None]
    if valid_acc:
        acc_count = len(valid_acc)
        avg_accuracy = round(sum(a.score for a in valid_acc) / acc_count, 2)
        acc_dims = {
            "title_accuracy": round(
                sum(a.sub_scores.title_accuracy for a in valid_acc) / acc_count, 2
            ),
            "doc_number_accuracy": round(
                sum(a.sub_scores.doc_number_accuracy for a in valid_acc) / acc_count, 2
            ),
            "published_at_accuracy": round(
                sum(a.sub_scores.published_at_accuracy for a in valid_acc) / acc_count, 2
            ),
            "effective_at_accuracy": round(
                sum(a.sub_scores.effective_at_accuracy for a in valid_acc) / acc_count, 2
            ),
            "issuer_accuracy": round(
                sum(a.sub_scores.issuer_accuracy for a in valid_acc) / acc_count, 2
            ),
            "doc_type_accuracy": round(
                sum(a.sub_scores.doc_type_accuracy for a in valid_acc) / acc_count, 2
            ),
            "attachment_url_f1": round(
                sum(a.sub_scores.attachment_url_f1 for a in valid_acc) / acc_count, 2
            ),
        }
    else:
        avg_accuracy = None
        acc_dims = {}

    fid_scores = [
        s.content_fidelity.content_f1 for s in sample_results if s.content_fidelity is not None
    ]
    avg_fidelity = round(sum(fid_scores) / len(fid_scores), 2) if fid_scores else None

    # Jurisdiction breakdown
    jur_groups: dict[str, list[SampleEvaluationResult]] = {}
    for s in sample_results:
        key = s.jurisdiction or "UNKNOWN"
        jur_groups.setdefault(key, []).append(s)

    jur_scores: dict[str, dict[str, float]] = {}
    for jur, group in sorted(jur_groups.items()):
        cnt = len(group)
        j_overall = round(sum(item.overall_score for item in group) / cnt, 2)
        clean_items = [item.cleanliness.score for item in group if item.cleanliness is not None]
        j_clean = round(sum(clean_items) / len(clean_items), 2) if clean_items else 0.0
        acc_items = [item.accuracy.score for item in group if item.accuracy is not None]
        j_acc = round(sum(acc_items) / len(acc_items), 2) if acc_items else 0.0
        jur_scores[jur] = {
            "overall": j_overall,
            "cleanliness": j_clean,
            "accuracy": j_acc,
            "sample_count": float(cnt),
        }

    # Source breakdown
    source_groups: dict[str, list[SampleEvaluationResult]] = {}
    for s in sample_results:
        key = s.source_id or "UNKNOWN"
        source_groups.setdefault(key, []).append(s)

    source_scores: dict[str, dict[str, float]] = {}
    for src, group in sorted(source_groups.items()):
        cnt = len(group)
        s_overall = round(sum(item.overall_score for item in group) / cnt, 2)
        source_scores[src] = {
            "overall": s_overall,
            "sample_count": float(cnt),
        }

    # Stratified split breakdown (dev vs test)
    split_groups: dict[str, list[SampleEvaluationResult]] = {"dev": [], "test": []}
    for s in sample_results:
        sp_key = s.split if s.split in ("dev", "test") else "dev"
        split_groups[sp_key].append(s)
    split_breakdown: dict[str, dict[str, Any]] = {
        sp_name: _summarize_split_group(sp_group)
        for sp_name, sp_group in split_groups.items()
        if sp_group
    }

    # Worst-performing samples diagnostics
    sorted_samples = sorted(sample_results, key=lambda x: x.overall_score)
    worst_samples: list[dict[str, Any]] = []
    for s in sorted_samples[:10]:
        penalties_summary = [
            p["reason"] for p in (s.cleanliness.penalties if s.cleanliness is not None else [])[:3]
        ]
        mismatches_summary = [
            m["reason"] for m in (s.accuracy.mismatches if s.accuracy is not None else [])[:3]
        ]
        worst_samples.append(
            {
                "sample_id": s.sample_id,
                "source_id": s.source_id,
                "jurisdiction": s.jurisdiction,
                "overall_score": s.overall_score,
                "cleanliness_score": s.cleanliness.score if s.cleanliness is not None else None,
                "accuracy_score": s.accuracy.score if s.accuracy is not None else None,
                "top_penalties": penalties_summary,
                "top_mismatches": mismatches_summary,
            }
        )

    # Field discrepancy and fill rate analysis vs ground truth
    field_discrepancies: dict[str, dict[str, Any]] = {}
    if valid_acc:
        acc_fields = [
            "title",
            "doc_number",
            "published_at",
            "effective_at",
            "issuer",
            "doc_type",
            "attachment_urls",
        ]
        field_score_keys = {
            "title": "title_accuracy",
            "doc_number": "doc_number_accuracy",
            "published_at": "published_at_accuracy",
            "effective_at": "effective_at_accuracy",
            "issuer": "issuer_accuracy",
            "doc_type": "doc_type_accuracy",
            "attachment_urls": "attachment_url_f1",
        }
        acc_total = len(evaluated_pairs) if evaluated_pairs else len(valid_acc)
        for field in acc_fields:
            gt_filled_count = 0
            pred_filled_count = 0
            missing_in_pred = 0
            for pred_dict, gt_dict in evaluated_pairs:
                gt_val = gt_dict.get(field)
                pred_val = pred_dict.get(field)
                gt_list: list[object] | None = (
                    cast(list[object], gt_val) if isinstance(gt_val, list) else None
                )
                pred_list: list[object] | None = (
                    cast(list[object], pred_val) if isinstance(pred_val, list) else None
                )
                gt_obj = cast(object, gt_val)
                pred_obj = cast(object, pred_val)
                gt_str = str(gt_obj).strip() if gt_obj is not None else ""
                pred_str = str(pred_obj).strip() if pred_obj is not None else ""
                gt_has = len(gt_list) > 0 if gt_list is not None else bool(gt_str)
                pred_has = len(pred_list) > 0 if pred_list is not None else bool(pred_str)
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
        evaluated_split=normalized_split,
        average_overall_score=avg_overall,
        average_list_discovery_f1=avg_list_discovery,
        average_record_split_f1=avg_record_split,
        average_accuracy_score=avg_accuracy,
        average_content_fidelity_score=avg_fidelity,
        average_cleanliness_score=avg_cleanliness,
        cleanliness_dimension_scores=clean_dims,
        accuracy_dimension_scores=acc_dims,
        jurisdiction_scores=jur_scores,
        source_scores=source_scores,
        split_scores=split_breakdown,
        worst_performing_samples=worst_samples,
        field_discrepancies=field_discrepancies,
        sample_results=sample_results,
    )


def evaluate_dataset(
    dataset: list[dict[str, Any]], *, split: str = "all", workers: int = 1
) -> BenchmarkReport:
    """Evaluate dataset alias matching PROJECT.md interface contract."""
    return evaluate_benchmark_dataset(dataset, split=split, workers=workers)
