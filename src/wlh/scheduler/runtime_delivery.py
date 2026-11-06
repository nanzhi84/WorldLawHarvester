"""Ready targets, extraction scheduling and bounded verified document delivery."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from wlh.core import WlhError, digest
from wlh.processor.extractors.magic import detect_real_format
from wlh.processor.extractors.router import run_extraction
from wlh.scheduler.context import adapter_error
from wlh.scheduler.run_state import STOPPING
from wlh.storage.cas import Objects
from wlh.storage.document_store import build, commit, fail

if TYPE_CHECKING:
    from wlh.scheduler.runtime import Runtime


def ready_targets(runtime: Runtime) -> list[dict[str, Any]]:
    return runtime.rows(
        """SELECT t.* FROM wlh.task t WHERE t.run_id=%s AND t.kind='target'
        AND t.state='pending' AND NOT EXISTS (
          SELECT 1 FROM wlh.task_input i JOIN wlh.task r ON r.id=i.request_id
          WHERE i.target_id=t.id AND r.state NOT IN ('done','failed','skipped'))
        ORDER BY t.seq""",
        (runtime.run_id,),
    )


def _preload_targets(runtime: Runtime, targets: Sequence[Mapping[str, Any]]) -> None:
    missing_tids = [str(t["id"]) for t in targets if "_cached_inputs" not in t]
    missing_aids: list[str] = []
    if missing_tids:
        rows = runtime.rows(
            """SELECT r.*, i.position, i.target_id FROM wlh.task_input i
            JOIN wlh.task r ON r.id=i.request_id
            WHERE i.target_id = ANY(%s) ORDER BY i.target_id, i.position""",
            (missing_tids,),
        )
        by_target: dict[str, list[dict[str, Any]]] = {tid: [] for tid in missing_tids}
        for row in rows:
            tid = str(row["target_id"])
            by_target.setdefault(tid, []).append(row)
            aid = row.get("acquisition_id")
            if aid and str(aid) not in runtime.acquisition_cache:
                missing_aids.append(str(aid))
        for t in targets:
            if isinstance(t, dict) and str(t["id"]) in by_target:
                t["_cached_inputs"] = by_target[str(t["id"])]
    for t in targets:
        frozen = t["payload"].get("frozen")
        if frozen:
            for item in frozen.get("inputs", []):
                aid = item.get("acquisition_id")
                if aid and str(aid) not in runtime.acquisition_cache:
                    missing_aids.append(str(aid))
    if missing_aids:
        acq_rows = runtime.rows(
            "SELECT * FROM wlh.acquisition WHERE id = ANY(%s)",
            (list(set(missing_aids)),),
        )
        for acq in acq_rows:
            if acq.get("state") == "archived":
                runtime.acquisition_cache[str(acq["id"])] = acq


def target_inputs(runtime: Runtime, target: Mapping[str, Any]) -> list[dict[str, Any]]:
    cached = target.get("_cached_inputs")
    if isinstance(cached, list):
        return cast(list[dict[str, Any]], cached)
    return runtime.rows(
        """SELECT r.*, i.position FROM wlh.task_input i JOIN wlh.task r ON r.id=i.request_id
        WHERE i.target_id=%s ORDER BY i.position""",
        (target["id"],),
    )


async def deliver(runtime: Runtime, target: Mapping[str, Any]) -> dict[str, Any] | str | None:
    identity = target["payload"].get("identity")
    inputs: list[dict[str, Any]] = []
    acquisitions: dict[str, dict[str, Any]] = {}
    frozen = target["payload"].get("frozen")
    if frozen:
        for item in frozen["inputs"]:
            acquisition = runtime.acquisition(item.get("acquisition_id"))
            if (
                acquisition is None
                or acquisition["source_id"] != runtime.run["source_id"]
                or (acquisition["state"] != "archived" or acquisition["outcome"] != "ok")
            ):
                return fail(
                    runtime.run_id,
                    runtime.epoch,
                    identity,
                    "reparse_input_unavailable",
                    target_id=target["id"],
                )
            acquisitions[acquisition["id"]] = acquisition
            meta = {k: v for k, v in item.items() if k != "acquisition_id"}
            inputs.append({**meta, "task": None, "acquisition": acquisition})
    rows = {row["key"]: row for row in target_inputs(runtime, target)}
    for meta in cast(list[dict[str, Any]], [] if frozen else target["payload"]["inputs"]):
        row = rows[meta["request_key"]]
        acquisition = runtime.acquisition(row["acquisition_id"]) if row["acquisition_id"] else None
        if (
            acquisition is None
            or acquisition["state"] != "archived"
            or acquisition["outcome"] != "ok"
        ):
            code = (acquisition or {}).get("error") or row["error"] or "input_unavailable"
            return fail(
                runtime.run_id,
                runtime.epoch,
                identity,
                "required_input_failed:" + code,
                acquisition_ids=[a["id"] for a in acquisitions.values()]
                + ([acquisition["id"]] if acquisition else []),
                target_id=target["id"],
            )
        acquisitions[acquisition["id"]] = acquisition
        inputs.append({**meta, "task": row, "acquisition": acquisition})
    try:
        runtime.derived_ocr = {}
        records = runtime.adapter.build(runtime.ctx, target["payload"], inputs)
        if len(records) != 1:
            raise WlhError("target_must_build_one_document")
        candidates = [r.candidate() for r in records]
        for original_ref, entry in runtime.derived_ocr.items():
            original: dict[str, Any] | None = next(
                (a for a in acquisitions.values() if a["archive_ref"] == original_ref), None
            )
            if original is None:
                raise WlhError("ocr_lineage_missing")
            candidates[0]["inputs"].append(
                {
                    "acquisition_id": original["id"],
                    "archive_ref": entry["ref"],
                    "role": "derived",
                    "locator": {"kind": "ocr_result", "original_ref": original_ref},
                    "context": {"ocr_cache_key": entry["key"]},
                }
            )
        candidates[0]["adapter_inputs"] = [
            {
                **{k: v for k, v in item.items() if k not in ("task", "acquisition")},
                "acquisition_id": item["acquisition"]["id"],
            }
            for item in inputs
        ]
        if frozen and frozen.get("inputs_ref") and frozen.get("version_id"):
            candidates[0]["input_reuse"] = {
                "mode": "reparse",
                "frozen_inputs_ref": frozen["inputs_ref"],
                "previous_version_id": frozen["version_id"],
            }
        built: dict[str, Any] = await runtime.transport.cas_io.run(
            build,
            Objects(),
            source_id=runtime.run["source_id"],
            run_id=runtime.run_id,
            manifest=runtime.run["parser_manifest"],
            candidate=candidates[0],
            acquisitions=acquisitions,
        )
    except (WlhError, Exception) as exc:
        code = adapter_error(exc, "adapter_build_failed")
        if code in {
            "interrupted",
            "crawl_time_budget_exceeded",
            "deadline_exceeded",
            "stale_attempt",
            "inactive_attempt",
            "run_not_started",
        }:
            raise
        plan_obj: Mapping[str, Any] = runtime.run.get("reparse_plan") or {}
        if (
            frozen
            and frozen.get("recovery")
            and (int(plan_obj.get("resource_count") or 0) > 0 or runtime.recovered_any)
        ):
            runtime.write(
                lambda c: c.execute(
                    """UPDATE wlh.task SET state='skipped',attempt_epoch=%s,
                    updated_at=clock_timestamp() WHERE id=%s AND kind='target'""",
                    (runtime.epoch, target["id"]),
                )
            )
            return None
        return fail(
            runtime.run_id,
            runtime.epoch,
            identity,
            code,
            acquisition_ids=list(acquisitions),
            target_id=target["id"],
        )
    runtime.budget.active()
    result: dict[str, Any] = commit(
        runtime.run_id,
        runtime.epoch,
        built,
        target_id=target["id"],
        check_active=runtime.budget.active,
    )
    runtime.recovered_any = True
    return result


def _target_binary_jobs(
    runtime: Runtime, target: Mapping[str, Any]
) -> tuple[
    list[tuple[str, str]],
    dict[tuple[str, str], tuple[bytes, str, dict[str, Any] | None]],
]:
    binary_kinds = {"pdf", "doc", "docx", "rtf", "xls", "xlsx", "zip"}
    cks: list[tuple[str, str]] = []
    jobs: dict[tuple[str, str], tuple[bytes, str, dict[str, Any] | None]] = {}
    frozen = target["payload"].get("frozen")
    if frozen:
        acq_ids = [
            str(item["acquisition_id"])
            for item in frozen.get("inputs", [])
            if item.get("acquisition_id")
            and item.get("role") != "inline_image"
            and cast(Mapping[str, Any], item.get("unit") or {}).get("role")
            != "inline_attachment_zip"
        ]
    else:
        inline_rkeys = {
            str(meta.get("request_key"))
            for meta in cast(list[dict[str, Any]], target["payload"].get("inputs") or [])
            if meta.get("role") == "inline_image"
            or cast(Mapping[str, Any], meta.get("unit") or {}).get("role")
            == "inline_attachment_zip"
        }
        acq_ids = [
            str(row["acquisition_id"])
            for row in target_inputs(runtime, target)
            if row.get("acquisition_id") and str(row.get("key")) not in inline_rkeys
        ]
    for aid in acq_ids:
        acq = runtime.acquisition(aid)
        if not acq or acq.get("state") != "archived" or acq.get("outcome") != "ok":
            continue
        try:
            data = runtime.ctx.read(acq)
        except WlhError:
            continue
        kind = detect_real_format(data, "html" if acq.get("mime_type") == "text/html" else "")
        if kind not in binary_kinds:
            continue
        d_hash = digest(data)
        ck = (d_hash, kind)
        if ck not in cks:
            cks.append(ck)
        if ck in jobs or ck in runtime.extract_cache or ck in runtime.extract_futures:
            continue
        ocr_key = digest(
            {
                "original": d_hash,
                "model": runtime.config["ocr"]["model"],
                "contract": "wlh.ocr-cache.v1",
            }
        )
        cached_ref = runtime.ocr_cache.get(ocr_key)
        cached_ocr = runtime.ctx.objects.json(cached_ref) if cached_ref else None
        jobs[ck] = (data, kind, cached_ocr)
    return cks, jobs


def _extract_one_job(
    runtime: Runtime, item: tuple[tuple[str, str], tuple[bytes, str, dict[str, Any] | None]]
) -> tuple[tuple[str, str], dict[str, Any] | WlhError]:
    ck, (payload, knd, cached) = item
    try:
        res = run_extraction(
            payload, knd, runtime.config, active=runtime.budget.active, cached_ocr=cached
        )
        return ck, res
    except WlhError as exc:
        if exc.code in {
            "interrupted",
            "crawl_time_budget_exceeded",
            "deadline_exceeded",
            "stale_attempt",
            "inactive_attempt",
            "run_not_started",
            "extraction_cleanup_failed",
        }:
            raise
        return ck, exc


def _harvest_extract_futures(runtime: Runtime) -> None:
    for ck, fut in list(runtime.extract_futures.items()):
        if fut.done():
            runtime.extract_futures.pop(ck, None)
            _, res = fut.result()
            runtime.extract_cache[ck] = res


async def deliver_ready(runtime: Runtime, *, max_chunks: int | None = None) -> bool:
    _harvest_extract_futures(runtime)
    targets = ready_targets(runtime)
    loop = asyncio.get_running_loop()
    max_queued = 96 if max_chunks is not None else len(targets) + len(runtime.extract_targets)
    new_targets: list[dict[str, Any]] = []
    for target in targets:
        if len(runtime.extract_targets) + len(new_targets) >= max_queued:
            break
        tid = str(target["id"])
        if tid in runtime.extract_targets:
            continue
        new_targets.append(target)
    if new_targets:
        _preload_targets(runtime, new_targets)
    for target in new_targets:
        tid = str(target["id"])
        cks, new_jobs = _target_binary_jobs(runtime, target)
        for ck, job_data in new_jobs.items():
            runtime.extract_futures[ck] = loop.run_in_executor(
                runtime.extract_pool, _extract_one_job, runtime, (ck, job_data)
            )
        runtime.extract_targets[tid] = (target, cks)

    if runtime.mode != "reparse" and runtime.truncated not in STOPPING:
        runtime.fill_inflight()
        if runtime.inflight:
            await asyncio.sleep(0)

    if max_chunks is None or not runtime.inflight:
        while runtime.extract_futures:
            await asyncio.wait(
                list(runtime.extract_futures.values()),
                return_when=asyncio.ALL_COMPLETED,
            )
            _harvest_extract_futures(runtime)
    elif runtime.extract_futures:
        has_ready_target = any(
            all(ck in runtime.extract_cache for ck in cks)
            for _, cks in runtime.extract_targets.values()
        )
        if not has_ready_target:
            await asyncio.wait(
                list(runtime.extract_futures.values()),
                timeout=0.15,
                return_when=asyncio.FIRST_COMPLETED,
            )
            rem_futs = [f for f in runtime.extract_futures.values() if not f.done()]
            if rem_futs:
                await asyncio.wait(
                    rem_futs,
                    timeout=0.05,
                    return_when=asyncio.ALL_COMPLETED,
                )
            _harvest_extract_futures(runtime)

    ready = [
        (tid, target)
        for tid, (target, keys) in runtime.extract_targets.items()
        if all(key in runtime.extract_cache for key in keys)
    ]
    width = runtime.transport.cas_io.workers
    for start in range(0, len(ready), width):
        chunk = ready[start : start + width]
        runtime.budget.active()
        outcomes = await asyncio.gather(
            *(deliver(runtime, target) for _, target in chunk), return_exceptions=True
        )
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
        for tid, _ in chunk:
            runtime.extract_targets.pop(tid, None)
    delivered_any = bool(ready)

    still_needed = {ck for _, cks in runtime.extract_targets.values() for ck in cks}
    for ck in list(runtime.extract_cache.keys()):
        if ck not in still_needed:
            runtime.extract_cache.pop(ck, None)
    return delivered_any or bool(runtime.extract_targets)
