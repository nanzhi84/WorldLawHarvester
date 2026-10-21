"""Adapter Runtime: persistent request tasks and document targets with attempt fencing.

Three completion points stay distinct: an input is archived (request task ``archived``),
a document target is registered (target row) and every target has a success or failure
delivery (target ``done``/``failed``). Follow-up requests and targets are registered in
the same transaction that completes their parent request, before any watermark moves.
"""

import asyncio

from wlh.adapters import CasSessionProxy, RawRecord, load
from wlh.core import WlhError, uid
from wlh.db import connect, fenced, j, locked_run, one, record_error
from wlh.domain.document import build
from wlh.domain.resources import commit, fail, save_checkpoint
from wlh.engine.adapter_context import Context, adapter_error
from wlh.engine.objects import Objects
from wlh.engine.runtime_registration import register
from wlh.engine.runtime_report import build_report
from wlh.engine.transport import Budget, Recorder, Transport, request_key

CHECKPOINT_SCHEMA = "wlh.checkpoint.v1"
# A document target cap skips extra targets without starving accepted targets of inputs.
STOPPING = {"request_budget_exceeded", "crawl_time_budget_exceeded"}


class Runtime:
    def __init__(self, context):
        self.run = context
        self.run_id = context["id"]
        self.epoch = context["attempt_epoch"]
        self.config = context["config"]
        self.mode = context["mode"]
        self.checkpoint = context["checkpoint"] or {}
        with connect() as c:
            used = one(
                c,
                "SELECT count(*) AS n FROM wlh.acquisition WHERE run_id=%s AND acquisition_kind='http'",
                (self.run_id,),
            )["n"]
            capped = c.execute(
                "SELECT 1 FROM wlh.task WHERE run_id=%s AND error='document_budget_exceeded' LIMIT 1",
                (self.run_id,),
            ).fetchone()
        self.budget = Budget(self.config, used, context["deadline"])
        self.recorder = Recorder(self.run_id, self.epoch, context["source_id"])
        self.transport = Transport(self.config, self.budget, self.recorder)
        self.session = CasSessionProxy(self.transport, self.mode)
        self.browser = None
        if self.config["fetch"]["mode"] == "rendered_dom":
            from wlh.engine.browser import Browser

            self.browser = Browser(self.config, self.budget, self.recorder, self.transport)
        self.adapter = load(self.config["adapter"])(self.config)
        self.ocr_cache = {}
        self.derived_ocr = {}
        self.ctx = Context(self)
        plan = context.get("recheck_plan") or context.get("reparse_plan") or {}
        self.truncated = self.checkpoint.get("truncated") or (
            "document_budget_exceeded" if capped or plan.get("truncated") else None
        )

    # ---- persistence -------------------------------------------------------------------

    def write(self, function):
        with connect() as c:
            source, run = locked_run(c, self.run_id)
            fenced(source, run, self.epoch)
            return function(c)

    def insert_request(self, c, request, depth):
        key = request_key(request)
        payload = {
            k: request[k]
            for k in ("url", "method", "form", "role", "idempotent", "meta")
            if k in request
        }
        row = c.execute(
            """INSERT INTO wlh.task(id,run_id,kind,key,payload,depth) VALUES(%s,%s,'request',
            %s,%s,%s) ON CONFLICT(run_id,kind,key) DO NOTHING RETURNING id""",
            (uid(), self.run_id, key, j(payload), depth),
        ).fetchone()
        if row:
            return row["id"]
        return c.execute(
            "SELECT id FROM wlh.task WHERE run_id=%s AND kind='request' AND key=%s",
            (self.run_id, key),
        ).fetchone()["id"]

    def mark_request(self, task_id, state, acquisition_id=None, error=None):
        self.write(
            lambda c: c.execute(
                """UPDATE wlh.task SET state=%s,acquisition_id=%s,error=%s,attempt_epoch=%s,
                updated_at=clock_timestamp() WHERE id=%s""",
                (state, acquisition_id, error, self.epoch, task_id),
            )
        )

    def save(self, **values):
        self.checkpoint.update(values)
        self.checkpoint["schema"] = CHECKPOINT_SCHEMA
        save_checkpoint(self.run_id, self.epoch, self.checkpoint)

    def rows(self, query, params=()):
        with connect() as c:
            return c.execute(query, params).fetchall()

    def acquisition(self, identity):
        with connect() as c:
            return c.execute("SELECT * FROM wlh.acquisition WHERE id=%s", (identity,)).fetchone()

    def reuse(self, key):
        """An archived final response from an earlier attempt is re-read, never refetched."""
        with connect() as c:
            return c.execute(
                """SELECT * FROM wlh.acquisition WHERE run_id=%s AND request_key=%s
                AND role='content' AND state='archived' AND outcome='ok'
                ORDER BY acquired_at DESC LIMIT 1""",
                (self.run_id, key),
            ).fetchone()

    # ---- phases ------------------------------------------------------------------------

    def plan(self):
        if self.checkpoint.get("planned"):
            return
        state = (self.checkpoint.get("adapter_state") or {}).copy()
        state.pop("schema", None)
        if self.mode == "recheck":
            resources = (self.run.get("recheck_plan") or {}).get("resources", [])
            result = self.adapter.plan_recheck(resources, state)
        elif self.mode == "reparse":
            result = {"requests": [], "targets": self.frozen_targets(), "state": state}
        else:
            result = self.adapter.plan(state)
        register(self, result)
        self.save(planned=True)

    def frozen_targets(self):
        """Offline reparse: each current Version's archived adapter inputs, verified in CAS."""
        objects, targets = Objects(), []
        for resource in (self.run.get("reparse_plan") or {}).get("resources", []):
            manifest = objects.json(resource["inputs_ref"])
            for entry in manifest.get("inputs", []):
                cache_key = (entry.get("context") or {}).get("ocr_cache_key")
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

    async def fetch(self, task):
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

    async def fetch_batch(self):
        tasks = self.rows(
            """SELECT * FROM wlh.task WHERE run_id=%s AND kind='request' AND state='pending'
            ORDER BY seq LIMIT %s""",
            (self.run_id, self.config["concurrency"]),
        )
        if not tasks:
            return False
        results = await asyncio.gather(*(self.fetch(t) for t in tasks), return_exceptions=True)
        for task, result in zip(tasks, results, strict=True):
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

    def parse_archived(self):
        tasks = self.rows(
            """SELECT * FROM wlh.task WHERE run_id=%s AND kind='request' AND state='archived'
            ORDER BY seq""",
            (self.run_id,),
        )
        for task in tasks:
            self.budget.active()
            acquisition = self.acquisition(task["acquisition_id"])
            try:
                result = self.adapter.parse(self.ctx, task, acquisition)
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

    def ready_targets(self):
        return self.rows(
            """SELECT t.* FROM wlh.task t WHERE t.run_id=%s AND t.kind='target'
            AND t.state='pending' AND NOT EXISTS (
              SELECT 1 FROM wlh.task_input i JOIN wlh.task r ON r.id=i.request_id
              WHERE i.target_id=t.id AND r.state NOT IN ('done','failed','skipped'))
            ORDER BY t.seq""",
            (self.run_id,),
        )

    def target_inputs(self, target):
        return self.rows(
            """SELECT r.*, i.position FROM wlh.task_input i JOIN wlh.task r ON r.id=i.request_id
            WHERE i.target_id=%s ORDER BY i.position""",
            (target["id"],),
        )

    def deliver(self, target):
        identity = target["payload"].get("identity")
        inputs, acquisitions = [], {}
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
        for meta in [] if frozen else target["payload"]["inputs"]:
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
                original = next(
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
            built = build(
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
        result = commit(
            self.run_id, self.epoch, built, target_id=target["id"], check_active=self.budget.active
        )
        return result

    def deliver_ready(self):
        targets = self.ready_targets()
        for target in targets:
            self.budget.active()
            self.deliver(target)
        return bool(targets)

    def close_unfinished(self):
        """Targets that can no longer complete are reported, not invented as failures."""
        reason = self.truncated if self.truncated in STOPPING else "inputs_unavailable"
        self.write(
            lambda c: c.execute(
                """UPDATE wlh.task SET state='skipped',error=%s,attempt_epoch=%s,
                updated_at=clock_timestamp() WHERE run_id=%s AND kind='target' AND state='pending'""",
                (reason, self.epoch, self.run_id),
            )
        )

    async def execute(self):
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
            await self.transport.close()
            if self.browser is not None:
                await self.browser.close()
        report = build_report(
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

    def watermark(self):
        with connect() as c:
            row = one(
                c,
                """SELECT max(seq) AS seq FROM wlh.task WHERE run_id=%s
                AND state IN ('done','failed','skipped')""",
                (self.run_id,),
            )
        return row["seq"] or 0
