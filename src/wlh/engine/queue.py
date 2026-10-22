"""Procrastinate alone owns persistent job scheduling, locking and recovery."""

from __future__ import annotations

import asyncio
import signal
import threading
from typing import Any, cast

import procrastinate

from wlh.db import DbConn, connect, dsn, locked_run, one
from wlh.engine.run_state import TERMINAL_STATES, exhaustion_error

app = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=dsn()))
producer = procrastinate.App(connector=procrastinate.SyncPsycopgConnector(conninfo=dsn()))


@app.task(
    pass_context=True,
    name="wlh.crawl",
    queue="wlh",
    retry=procrastinate.RetryStrategy(max_attempts=3, wait=5),
)
def crawl_job(context: procrastinate.JobContext, run_id: str) -> None:
    from wlh.engine.runs import execute_run

    result = execute_run(run_id, cancel_event=cast(Any, context).additional_context.get("cancel_event"))
    if result["status"] == "retryable":
        raise RuntimeError("retryable_crawl_failure")


def enqueue(c: DbConn, run_id: str) -> int | None:
    # Worker recovery runs alongside an async consumer. Its external Source/Run
    # transaction is synchronous; never pass it to the consumer's async cursor.
    job = crawl_job.configure(queueing_lock="wlh:run:" + run_id).make_new_job(run_id=run_id)
    job = producer.job_manager.defer_job(job=job, connection=c)
    c.execute("UPDATE wlh.run SET job_id=%s WHERE id=%s", (job.id, run_id))
    return job.id


@app.periodic(cron="* * * * *")
@app.task(name="wlh.recover", queue="wlh-control", lock="wlh:recover", queueing_lock="wlh:recover")
async def recover_stalled(timestamp: int = 0) -> dict[str, int]:
    await asyncio.to_thread(recover_unqueued)
    jobs = await app.job_manager.get_stalled_jobs(task_name="wlh.crawl", seconds_since_heartbeat=30)
    count = 0
    for job in jobs:
        run_id = str(job.task_kwargs["run_id"])

        def inspect_limit(run_id: str = run_id) -> bool:
            with connect() as c:
                source, run = locked_run(c, run_id)
                if run["status"] in TERMINAL_STATES:
                    return True  # one final task invocation reports the stored outcome
                limit = exhaustion_error(run)
                if limit:
                    from wlh.engine.runs import end_run

                    end_run(c, source, run, "failed", limit)
                return True

        if await asyncio.to_thread(inspect_limit):
            await app.job_manager.retry_job(job)
            count += 1
    return {"recovered": count}


def recover_unqueued() -> None:
    with connect() as c:
        rows: list[dict[str, Any]] = c.execute(
            "SELECT id FROM wlh.run WHERE job_id IS NULL AND status IN ('pending','running','finishing','retryable') ORDER BY created_at"
        ).fetchall()
        for row in rows:
            key = row["id"]
            if one(
                c, "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS acquired", (key,)
            )["acquired"]:
                _, run = locked_run(c, key)
                if run["job_id"] is None and run["status"] not in TERMINAL_STATES:
                    enqueue(c, key)


def worker(once: bool = False) -> dict[str, str]:
    from procrastinate.worker import Worker

    cancelled = threading.Event()

    async def consume() -> None:
        async with app.open_async():
            await recover_stalled()
            consumer: Worker = Worker(
                app=app,
                queues=["wlh", "wlh-control"],
                concurrency=2,
                wait=not once,
                install_signal_handlers=False,
                update_heartbeat_interval=10,
                stalled_worker_timeout=30,
                additional_context={"cancel_event": cancelled},
            )
            loop = asyncio.get_running_loop()

            def stop() -> None:
                cancelled.set()
                consumer.stop()

            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop)
            try:
                await consumer.run()
            finally:
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.remove_signal_handler(sig)

    asyncio.run(consume())
    return {"worker": "stopped", **({"error": "interrupted"} if cancelled.is_set() else {})}
