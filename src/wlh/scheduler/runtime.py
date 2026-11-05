"""Stage 1 Runtime: persistent request tasks and document targets with attempt fencing.

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

from wlh.core import WlhError, request_key, uid
from wlh.fetcher.acquisition import Budget, Recorder
from wlh.fetcher.session import CasSessionProxy
from wlh.fetcher.transport import Transport
from wlh.processor.extractors.router import prewarm_workers
from wlh.processor.parsers import Base, load, normalize_parser_config
from wlh.scheduler.context import Context, adapter_error
from wlh.scheduler.run_state import STOPPING
from wlh.scheduler.runtime_delivery import deliver_ready
from wlh.scheduler.runtime_plan import frozen_targets
from wlh.scheduler.runtime_registration import register
from wlh.scheduler.runtime_report import build_report
from wlh.storage.db import DbConn, connect, fenced, j, locked_run, one, record_error
from wlh.storage.document_store import save_checkpoint

if TYPE_CHECKING:
    from wlh.fetcher.browser import Browser

CHECKPOINT_SCHEMA = "wlh.checkpoint.v1"


class Runtime:
    def __init__(self, context: Mapping[str, Any]) -> None:
        self.run: Mapping[str, Any] = context
        self.run_id: str = context["id"]
        self.epoch: int = context["attempt_epoch"]
        self.config: dict[str, Any] = normalize_parser_config(context["config"])
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
            from wlh.fetcher.browser import Browser

            self.browser = Browser(self.config, self.budget, self.recorder, self.transport)
        self.adapter: Base = load(self.config["adapter"])(self.config)
        self.parser: Base = self.adapter
        self.ocr_cache: dict[str, str] = {}
        self.recovered_any = False
        self.derived_ocr: dict[str, dict[str, str]] = {}
        self.acquisition_cache: dict[str, dict[str, Any]] = {}
        self.extract_cache: dict[tuple[str, str], dict[str, Any] | WlhError] = {}
        self.inflight: dict[str, tuple[dict[str, Any], asyncio.Task[Mapping[str, Any]]]] = {}
        self.extract_pool = concurrent.futures.ThreadPoolExecutor(max_workers=12)
        prewarm_workers(6)
        self.extract_futures: dict[
            tuple[str, str],
            asyncio.Future[tuple[tuple[str, str], dict[str, Any] | WlhError]],
        ] = {}
        self.extract_targets: dict[str, tuple[dict[str, Any], list[tuple[str, str]]]] = {}
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
        return cast(
            dict[str, Any],
            c.execute(
                "SELECT id FROM wlh.task WHERE run_id=%s AND kind='request' AND key=%s",
                (self.run_id, key),
            ).fetchone(),
        )["id"]

    def mark_request(
        self,
        task_id: str,
        state: str,
        acquisition_id: str | None = None,
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
            result = {"requests": [], "targets": frozen_targets(self), "state": state}
        else:
            result = self.adapter.plan(state)
        register(self, result)
        self.save(planned=True)

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

    def fill_inflight(self) -> None:
        batch_limit = max(int(self.config["concurrency"]) * 8, int(self.config["concurrency"]))
        active_inflight = sum(1 for _, fut in self.inflight.values() if not fut.done())
        slots_avail = max(0, batch_limit - active_inflight)
        tasks: list[dict[str, Any]] = []
        if slots_avail > 0:
            inflight_ids = list(self.inflight.keys())
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
            self.inflight[t["id"]] = (t, asyncio.create_task(self.fetch(t)))

    async def fetch_batch(self) -> bool:
        self.fill_inflight()
        if not self.inflight:
            return False
        futs = [fut for _, fut in self.inflight.values()]
        await asyncio.wait(futs, return_when=asyncio.FIRST_COMPLETED)
        pending_futs = [fut for fut in futs if not fut.done()]
        if pending_futs:
            await asyncio.wait(pending_futs, timeout=0.15, return_when=asyncio.ALL_COMPLETED)
        completed_entries = sorted(
            (entry for entry in self.inflight.values() if entry[1].done()),
            key=lambda item: item[0]["seq"],
        )
        for task, fut in completed_entries:
            self.inflight.pop(task["id"], None)
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
                self.acquisition_cache[str(result["id"])] = dict(result)
                self.mark_request(task["id"], "archived", result["id"])
            elif result["state"] == "cancelled" or result["error"] == "interrupted":
                raise WlhError("interrupted")
            elif result["error"] in STOPPING:
                self.truncated = result["error"]
            else:
                self.mark_request(task["id"], "failed", result["id"], result["error"])
                record_error(self.run_id, self.epoch, result["error"] or "request_failed")
        return True

    def parse_archived(self, *, limit: int | None = None) -> bool:
        limit_sql = f"LIMIT {limit}" if limit is not None else ""
        tasks: list[dict[str, Any]] = self.rows(
            f"""SELECT * FROM wlh.task WHERE run_id=%s AND kind='request' AND state='archived'
            ORDER BY seq {limit_sql}""",
            (self.run_id,),
        )
        missing_aids = [
            str(t["acquisition_id"])
            for t in tasks
            if t.get("acquisition_id") and str(t["acquisition_id"]) not in self.acquisition_cache
        ]
        if missing_aids:
            for acq in self.rows(
                "SELECT * FROM wlh.acquisition WHERE id = ANY(%s)",
                (list(set(missing_aids)),),
            ):
                if acq.get("state") == "archived":
                    self.acquisition_cache[str(acq["id"])] = acq
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
                if self.mode != "reparse" and self.truncated not in STOPPING:
                    self.fill_inflight()
                    if self.inflight:
                        await asyncio.sleep(0)
                progressed = self.parse_archived(limit=64)
                if self.mode != "reparse" and self.truncated not in STOPPING:
                    self.fill_inflight()
                    if self.inflight:
                        await asyncio.sleep(0)
                progressed |= await deliver_ready(self, max_chunks=1)
                if self.truncated in STOPPING:
                    break
                fetched = await self.fetch_batch()
                if not (progressed or fetched):
                    break
            self.parse_archived()
            await deliver_ready(self)
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
            if self.inflight:
                for _, fut in self.inflight.values():
                    fut.cancel()
                await asyncio.gather(
                    *(fut for _, fut in self.inflight.values()), return_exceptions=True
                )
                self.inflight.clear()
            self.extract_pool.shutdown(wait=False, cancel_futures=True)
            self.extract_futures.clear()
            self.extract_targets.clear()
            self.extract_cache.clear()
            await self.transport.close()
            if self.browser is not None:
                await self.browser.close()
        report: dict[str, Any] = build_report(
            self.run_id, self.config["adapter"], self.checkpoint, self.truncated, finish_reason
        )
        report["cas_io"] = self.transport.cas_io.report()
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
