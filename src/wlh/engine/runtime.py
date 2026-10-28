"""Adapter Runtime: persistent request tasks and document targets with attempt fencing.

Three completion points stay distinct: an input is archived (request task ``archived``),
a document target is registered (target row) and every target has a success or failure
delivery (target ``done``/``failed``). Follow-up requests and targets are registered in
the same transaction that completes their parent request, before any watermark moves.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from wlh.adapters import Base, CasSessionProxy, RawRecord, load
from wlh.core import WlhError, digest, uid
from wlh.db import DbConn, connect, fenced, j, locked_run, one, record_error
from wlh.domain.document import build
from wlh.domain.resources import commit, fail, save_checkpoint
from wlh.engine.adapter_context import Context, adapter_error
from wlh.engine.objects import Objects
from wlh.engine.runtime_registration import register
from wlh.engine.runtime_report import build_report
from wlh.engine.transport import Budget, Recorder, Transport, request_key
from wlh.extraction.office import detect_real_format
from wlh.extraction.process import run_extraction

if TYPE_CHECKING:
    from wlh.engine.browser import Browser

CHECKPOINT_SCHEMA = "wlh.checkpoint.v1"
# A document target cap skips extra targets without starving accepted targets of inputs.
STOPPING = {"request_budget_exceeded", "crawl_time_budget_exceeded"}


class Runtime:
    def __init__(self, context: Mapping[str, Any]) -> None:
        self.run: Mapping[str, Any] = context
        self.run_id: str = context["id"]
        self.epoch: int = context["attempt_epoch"]
        self.config: dict[str, Any] = context["config"]
        self.mode: str = context["mode"]
        self.checkpoint: dict[str, Any] = context["checkpoint"] or {}
        with connect() as c:
            used: int = one(
                c,
                "SELECT count(*) AS n FROM wlh.acquisition WHERE run_id=%s AND acquisition_kind='http'",
                (self.run_id,),
            )["n"]
            capped: dict[str, Any] | None = c.execute(
                "SELECT 1 FROM wlh.task WHERE run_id=%s AND error='document_budget_exceeded' LIMIT 1",
                (self.run_id,),
            ).fetchone()
        self.budget: Budget = Budget(self.config, used, context["deadline"])
        self.recorder: Recorder = Recorder(self.run_id, self.epoch, context["source_id"])
        self.transport: Transport = Transport(self.config, self.budget, self.recorder)
        self.session: CasSessionProxy = CasSessionProxy(self.transport, self.mode)
        self.browser: Browser | None = None
        if self.config["fetch"]["mode"] == "rendered_dom":
            from wlh.engine.browser import Browser

            self.browser = Browser(self.config, self.budget, self.recorder, self.transport)
        self.adapter: Base = load(self.config["adapter"])(self.config)
        self.ocr_cache: dict[str, str] = {}
        self.derived_ocr: dict[str, dict[str, str]] = {}
        self.acquisition_cache: dict[str, dict[str, Any]] = {}
        self.extract_cache: dict[tuple[str, str], dict[str, Any] | WlhError] = {}
        self._inflight: dict[str, tuple[dict[str, Any], asyncio.Task[Mapping[str, Any]]]] = {}
        self.ctx: Context = Context(self)
        plan: Mapping[str, Any] = context.get("recheck_plan") or context.get("reparse_plan") or {}
        self.truncated: str | None = self.checkpoint.get("truncated") or (
            "document_budget_exceeded" if capped or plan.get("truncated") else None
        )

    # ---- persistence -------------------------------------------------------------------

    def write[T](self, function: Callable[[DbConn], T]) -> T:
        with connect() as c:
            source, run = locked_run(c, self.run_id)
            fenced(source, run, self.epoch)
            return function(c)

    def insert_request(self, c: DbConn, request: Mapping[str, Any], depth: int) -> str:
        key = request_key(request)
        payload: dict[str, Any] = {
            k: request[k]
            for k in ("url", "method", "form", "role", "idempotent", "meta")
            if k in request
        }
        row: dict[str, Any] | None = c.execute(
            """INSERT INTO wlh.task(id,run_id,kind,key,payload,depth) VALUES(%s,%s,'request',
            %s,%s,%s) ON CONFLICT(run_id,kind,key) DO NOTHING RETURNING id""",
            (uid(), self.run_id, key, j(payload), depth),
        ).fetchone()
        if row:
            return row["id"]
        return cast(dict[str, Any], c.execute(
            "SELECT id FROM wlh.task WHERE run_id=%s AND kind='request' AND key=%s",
            (self.run_id, key),
        ).fetchone())["id"]

    def mark_request(
        self, task_id: str, state: str, acquisition_id: str | None = None,
        error: str | None = None,
    ) -> None:
        self.write(
            lambda c: c.execute(
                """UPDATE wlh.task SET state=%s,acquisition_id=%s,error=%s,attempt_epoch=%s,
                updated_at=clock_timestamp() WHERE id=%s""",
                (state, acquisition_id, error, self.epoch, task_id),
            )
        )

    def save(self, **values: Any) -> None:
        self.checkpoint.update(values)
        self.checkpoint["schema"] = CHECKPOINT_SCHEMA
        save_checkpoint(self.run_id, self.epoch, self.checkpoint)

    def rows(
        self, query: Any, params: Sequence[Any] | Mapping[str, Any] = ()
    ) -> list[dict[str, Any]]:
        with connect() as c:
            return c.execute(query, params).fetchall()

    def acquisition(self, identity: str | None) -> dict[str, Any] | None:
        if not identity:
            return None
        cached = self.acquisition_cache.get(identity)
        if cached is not None:
            return cached
        with connect() as c:
            row = c.execute("SELECT * FROM wlh.acquisition WHERE id=%s", (identity,)).fetchone()
        if row is not None and row.get("state") == "archived":
            self.acquisition_cache[identity] = row
        return row

    def reuse(self, key: str) -> dict[str, Any] | None:
        """An archived final response from an earlier attempt is re-read, never refetched."""
        with connect() as c:
            return c.execute(
                """SELECT * FROM wlh.acquisition WHERE run_id=%s AND request_key=%s
                AND role='content' AND state='archived' AND outcome='ok'
                ORDER BY acquired_at DESC LIMIT 1""",
                (self.run_id, key),
            ).fetchone()

    # ---- phases ------------------------------------------------------------------------

    def plan(self) -> None:
        if self.checkpoint.get("planned"):
            return
        state = cast(dict[str, Any], self.checkpoint.get("adapter_state") or {}).copy()
        state.pop("schema", None)
        if self.mode == "recheck":
            resources: list[dict[str, Any]] = cast(
                dict[str, Any], self.run.get("recheck_plan") or {}
            ).get("resources", [])
            result = self.adapter.plan_recheck(resources, state)
        elif self.mode == "reparse":
            result = {"requests": [], "targets": self.frozen_targets(), "state": state}
        else:
            result = self.adapter.plan(state)
        register(self, result)
        self.save(planned=True)

    def frozen_targets(self) -> list[dict[str, Any]]:
        """Offline reparse: each current Version's archived adapter inputs, verified in CAS."""
        objects = Objects()
        targets: list[dict[str, Any]] = []
        plan: Mapping[str, Any] = self.run.get("reparse_plan") or {}
        resources: list[dict[str, Any]] = plan.get("resources", [])
        for resource in resources:
            manifest = objects.json(resource["inputs_ref"])
            for entry in manifest.get("inputs", []):
                cache_key: str | None = cast(
                    dict[str, Any], entry.get("context") or {}
                ).get("ocr_cache_key")
                if entry.get("role") == "derived" and cache_key:
                    self.ocr_cache[cache_key] = entry["archive_ref"]
            frozen = manifest.get("adapter_inputs")
            if (
                manifest.get("schema") != "wlh.inputs.v1"
                or not isinstance(frozen, list)
                or not frozen
            ):
                raise WlhError("reparse_inputs_unavailable")
            targets.append(
                {
                    "identity": {
                        "namespace": resource["namespace"],
                        "external_key": resource["external_key"],
                        "public_url": resource["public_url"],
                        "recheck_locator": resource["recheck_locator"],
                    },
                    "inputs": [],
                    "frozen": {
                        "inputs": frozen,
                        "inputs_ref": resource["inputs_ref"],
                        "version_id": resource["version_id"],
                    },
                }
            )
        return targets

    async def fetch(self, task: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.mode == "reparse":
            raise WlhError("network_disabled_in_reparse")
        request = task["payload"]
        previous = self.reuse(task["key"])
        if previous:
            return previous
        if (
            self.browser is not None
            and request.get("role", "page") == "page"
            and request.get("method", "GET") == "GET"
        ):
            return await self.browser.acquire(request)
        return await self.session.acquire(request)

    async def fetch_batch(self) -> bool:
        batch_limit = max(int(self.config["concurrency"]) * 8, int(self.config["concurrency"]))
        slots_avail = max(0, batch_limit - len(self._inflight))
        tasks: list[dict[str, Any]] = []
        if slots_avail > 0:
            inflight_ids = list(self._inflight.keys())
            excl_sql = "AND id != ALL(%s)" if inflight_ids else ""
            base_params: tuple[Any, ...] = (
                (self.run_id, inflight_ids) if inflight_ids else (self.run_id,)
            )
            page_tasks: list[dict[str, Any]] = self.rows(
                f"""SELECT * FROM wlh.task WHERE run_id=%s AND kind='request' AND state='pending'
                {excl_sql} AND COALESCE(payload->>'role', 'page') != 'attachment'
                ORDER BY seq LIMIT %s""",
                (*base_params, slots_avail),
            )
            attachment_backlog = (
                self.rows(
                    f"""SELECT count(*) AS n FROM wlh.task WHERE run_id=%s AND kind='request'
                    AND state='pending' {excl_sql} AND payload->>'role' = 'attachment'""",
                    base_params,
                )[0]["n"]
                if page_tasks
                else 0
            )
            if page_tasks and attachment_backlog < 240:
                tasks = page_tasks
            else:
                tasks = self.rows(
                    f"""SELECT * FROM wlh.task WHERE run_id=%s AND kind='request' AND state='pending'
                    {excl_sql}
                    ORDER BY (COALESCE(payload->>'role', 'page') != 'attachment'), seq LIMIT %s""",
                    (*base_params, slots_avail),
                )
        for t in tasks:
            self._inflight[t["id"]] = (t, asyncio.create_task(self.fetch(t)))
        if not self._inflight:
            return False
        futs = [fut for _, fut in self._inflight.values()]
        await asyncio.wait(futs, return_when=asyncio.FIRST_COMPLETED)
        pending_futs = [fut for fut in futs if not fut.done()]
        if pending_futs:
            await asyncio.wait(pending_futs, timeout=2.5, return_when=asyncio.ALL_COMPLETED)
        completed_entries = sorted(
            (entry for entry in self._inflight.values() if entry[1].done()),
            key=lambda item: item[0]["seq"],
        )
        for task, fut in completed_entries:
            self._inflight.pop(task["id"], None)
            result: Mapping[str, Any] | BaseException
            try:
                result = fut.result()
            except BaseException as exc:
                result = exc
            if isinstance(result, WlhError):
                if result.code in {"request_budget_exceeded", "crawl_time_budget_exceeded"}:
                    self.truncated = result.code
                    continue
                if result.code in {
                    "interrupted",
                    "stale_attempt",
                    "inactive_attempt",
                    "run_not_started",
                    "deadline_exceeded",
                }:
                    raise result
                self.mark_request(task["id"], "failed", error=result.code)
                record_error(self.run_id, self.epoch, result.code)
            elif isinstance(result, BaseException):
                raise result
            elif result["state"] == "archived" and result["outcome"] == "ok":
                self.mark_request(task["id"], "archived", result["id"])
            elif result["state"] == "cancelled" or result["error"] == "interrupted":
                raise WlhError("interrupted")
            elif result["error"] in STOPPING:
                self.truncated = result["error"]
            else:
                self.mark_request(task["id"], "failed", result["id"], result["error"])
                record_error(self.run_id, self.epoch, result["error"] or "request_failed")
        return True

    def parse_archived(self) -> bool:
        tasks: list[dict[str, Any]] = self.rows(
            """SELECT * FROM wlh.task WHERE run_id=%s AND kind='request' AND state='archived'
            ORDER BY seq""",
            (self.run_id,),
        )
        for task in tasks:
            self.budget.active()
            acquisition = self.acquisition(task["acquisition_id"])
            try:
                result = self.adapter.parse(self.ctx, task, cast(Mapping[str, Any], acquisition))
            except (WlhError, Exception) as exc:
                code = adapter_error(exc, "adapter_parse_failed")
                self.write(
                    lambda c, t=task, e=code: c.execute(
                        """UPDATE wlh.task SET state='failed',error=%s,attempt_epoch=%s,
                        updated_at=clock_timestamp() WHERE id=%s""",
                        (e, self.epoch, t["id"]),
                    )
                )
                record_error(self.run_id, self.epoch, code)
                continue
            register(self, result, parent=task["id"], depth=task["depth"] + 1)
        return bool(tasks)

    def ready_targets(self) -> list[dict[str, Any]]:
        return self.rows(
            """SELECT t.* FROM wlh.task t WHERE t.run_id=%s AND t.kind='target'
            AND t.state='pending' AND NOT EXISTS (
              SELECT 1 FROM wlh.task_input i JOIN wlh.task r ON r.id=i.request_id
              WHERE i.target_id=t.id AND r.state NOT IN ('done','failed','skipped'))
            ORDER BY t.seq""",
            (self.run_id,),
        )

    def target_inputs(self, target: Mapping[str, Any]) -> list[dict[str, Any]]:
        return self.rows(
            """SELECT r.*, i.position FROM wlh.task_input i JOIN wlh.task r ON r.id=i.request_id
            WHERE i.target_id=%s ORDER BY i.position""",
            (target["id"],),
        )

    def deliver(self, target: Mapping[str, Any]) -> dict[str, Any] | str | None:
        identity = target["payload"].get("identity")
        inputs: list[dict[str, Any]] = []
        acquisitions: dict[str, dict[str, Any]] = {}
        frozen = target["payload"].get("frozen")
        if frozen:
            for item in frozen["inputs"]:
                acquisition = self.acquisition(item.get("acquisition_id"))
                if (
                    acquisition is None
                    or acquisition["source_id"] != self.run["source_id"]
                    or (acquisition["state"] != "archived" or acquisition["outcome"] != "ok")
                ):
                    return fail(
                        self.run_id,
                        self.epoch,
                        identity,
                        "reparse_input_unavailable",
                        target_id=target["id"],
                    )
                acquisitions[acquisition["id"]] = acquisition
                meta = {k: v for k, v in item.items() if k != "acquisition_id"}
                inputs.append({**meta, "task": None, "acquisition": acquisition})
        rows = {row["key"]: row for row in self.target_inputs(target)}
        for meta in cast(list[dict[str, Any]], [] if frozen else target["payload"]["inputs"]):
            row = rows[meta["request_key"]]
            acquisition = self.acquisition(row["acquisition_id"]) if row["acquisition_id"] else None
            if (
                acquisition is None
                or acquisition["state"] != "archived"
                or acquisition["outcome"] != "ok"
            ):
                code = (acquisition or {}).get("error") or row["error"] or "input_unavailable"
                return fail(
                    self.run_id,
                    self.epoch,
                    identity,
                    "required_input_failed:" + code,
                    acquisition_ids=[a["id"] for a in acquisitions.values()]
                    + ([acquisition["id"]] if acquisition else []),
                    target_id=target["id"],
                )
            acquisitions[acquisition["id"]] = acquisition
            inputs.append({**meta, "task": row, "acquisition": acquisition})
        try:
            self.derived_ocr = {}
            candidates = self.adapter.build(self.ctx, target["payload"], inputs)
            if len(candidates) != 1:
                raise WlhError("target_must_build_one_document")
            candidates = [
                (c if isinstance(c, RawRecord) else RawRecord.from_candidate(c)).candidate()
                for c in candidates
            ]
            for original_ref, entry in self.derived_ocr.items():
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
            # Freeze adapter selection so a later offline Run can replay verified inputs.
            candidates[0]["adapter_inputs"] = [
                {
                    **{k: v for k, v in item.items() if k not in ("task", "acquisition")},
                    "acquisition_id": item["acquisition"]["id"],
                }
                for item in inputs
            ]
            if frozen:
                candidates[0]["input_reuse"] = {
                    "mode": "reparse",
                    "frozen_inputs_ref": frozen["inputs_ref"],
                    "previous_version_id": frozen["version_id"],
                }
            built: dict[str, Any] = build(
                Objects(),
                source_id=self.run["source_id"],
                run_id=self.run_id,
                manifest=self.run["parser_manifest"],
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
            return fail(
                self.run_id,
                self.epoch,
                identity,
                code,
                acquisition_ids=list(acquisitions),
                target_id=target["id"],
            )
        self.budget.active()
        result: dict[str, Any] = commit(
            self.run_id, self.epoch, built, target_id=target["id"], check_active=self.budget.active
        )
        return result

    def _prewarm_chunk(self, chunk: Sequence[Mapping[str, Any]]) -> None:
        if len(chunk) <= 1:
            return
        jobs: dict[tuple[str, str], tuple[bytes, str, dict[str, Any] | None]] = {}
        binary_kinds = {"pdf", "doc", "docx", "rtf", "xls", "xlsx", "zip"}
        for target in chunk:
            frozen = target["payload"].get("frozen")
            acq_ids: list[str] = []
            if frozen:
                acq_ids = [
                    str(item["acquisition_id"])
                    for item in frozen.get("inputs", [])
                    if item.get("acquisition_id")
                ]
            else:
                acq_ids = [
                    str(row["acquisition_id"])
                    for row in self.target_inputs(target)
                    if row.get("acquisition_id")
                ]
            for aid in acq_ids:
                acq = self.acquisition(aid)
                if not acq or acq.get("state") != "archived" or acq.get("outcome") != "ok":
                    continue
                try:
                    data = self.ctx.read(acq)
                except WlhError:
                    continue
                kind = detect_real_format(
                    data, "html" if acq.get("mime_type") == "text/html" else ""
                )
                if kind not in binary_kinds:
                    continue
                d_hash = digest(data)
                ck = (d_hash, kind)
                if ck in jobs or ck in self.extract_cache:
                    continue
                ocr_key = digest(
                    {
                        "original": d_hash,
                        "model": self.config["ocr"]["model"],
                        "contract": "wlh.ocr-cache.v1",
                    }
                )
                cached_ref = self.ocr_cache.get(ocr_key)
                cached_ocr = self.ctx.objects.json(cached_ref) if cached_ref else None
                jobs[ck] = (data, kind, cached_ocr)
        if len(jobs) <= 1:
            return

        def _extract_one(
            item: tuple[tuple[str, str], tuple[bytes, str, dict[str, Any] | None]],
        ) -> tuple[tuple[str, str], dict[str, Any] | WlhError]:
            ck, (payload, knd, cached) = item
            try:
                res = run_extraction(
                    payload, knd, self.config, active=self.budget.active, cached_ocr=cached
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

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for ck, outcome in pool.map(_extract_one, jobs.items()):
                self.extract_cache[ck] = outcome

    def deliver_ready(self) -> bool:
        targets = self.ready_targets()
        chunk_size = 32
        for start in range(0, len(targets), chunk_size):
            chunk = targets[start : start + chunk_size]
            try:
                self._prewarm_chunk(chunk)
                for target in chunk:
                    self.budget.active()
                    self.deliver(target)
            finally:
                self.extract_cache.clear()
        return bool(targets)

    def close_unfinished(self) -> None:
        """Targets that can no longer complete are reported, not invented as failures."""
        reason = self.truncated if self.truncated in STOPPING else "inputs_unavailable"
        self.write(
            lambda c: c.execute(
                """UPDATE wlh.task SET state='skipped',error=%s,attempt_epoch=%s,
                updated_at=clock_timestamp() WHERE run_id=%s AND kind='target' AND state='pending'""",
                (reason, self.epoch, self.run_id),
            )
        )

    async def execute(self) -> None:
        finish_reason = "finished"
        try:
            self.plan()
            while True:
                self.budget.active()
                progressed = self.parse_archived()
                progressed |= self.deliver_ready()
                if self.truncated in STOPPING:
                    break
                fetched = await self.fetch_batch()
                if not (progressed or fetched):
                    break
            self.parse_archived()
            self.deliver_ready()
            self.close_unfinished()
            self.save(watermark=self.watermark())
        except asyncio.CancelledError:
            finish_reason = "interrupted"
            record_error(self.run_id, self.epoch, "interrupted")
        except WlhError as exc:
            if exc.code in {
                "stale_attempt",
                "inactive_attempt",
                "run_not_started",
                "deadline_exceeded",
                "source_paused",
                "source_retired",
                "source_superseded",
            }:
                raise
            finish_reason = "interrupted" if exc.code == "interrupted" else "stopped"
            if exc.code == "crawl_time_budget_exceeded":
                self.truncated = exc.code
                finish_reason = "finished"
                try:
                    self.close_unfinished()
                except WlhError:
                    pass
            if exc.code != "interrupted":
                record_error(self.run_id, self.epoch, exc.code)
        finally:
            if self._inflight:
                for _, fut in self._inflight.values():
                    fut.cancel()
                await asyncio.gather(
                    *(fut for _, fut in self._inflight.values()), return_exceptions=True
                )
                self._inflight.clear()
            await self.transport.close()
            if self.browser is not None:
                await self.browser.close()
        report: dict[str, Any] = build_report(
            self.run_id, self.config["adapter"], self.checkpoint, self.truncated, finish_reason
        )
        if self.truncated and self.truncated != "document_budget_exceeded":
            record_error(self.run_id, self.epoch, self.truncated)
        if "zero_targets_discovered" in report["coverage"]["known_gaps"]:
            record_error(self.run_id, self.epoch, "zero_targets_discovered")
        with connect() as c:
            c.execute(
                """UPDATE wlh.run SET status='finishing',report=report || %s
                WHERE id=%s AND attempt_epoch=%s AND status='running'""",
                (j(report), self.run_id, self.epoch),
            )

    def watermark(self) -> int:
        with connect() as c:
            row: dict[str, Any] = one(
                c,
                """SELECT max(seq) AS seq FROM wlh.task WHERE run_id=%s
                AND state IN ('done','failed','skipped')""",
                (self.run_id,),
            )
        return row["seq"] or 0
