"""Operator commands and a read-only result server."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any, NoReturn

from wlh.core import WlhError


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise WlhError("invalid_arguments")


def parser() -> Parser:
    root = Parser(prog="wlh")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Explicitly initialize or upgrade storage")
    serve = commands.add_parser("serve", help="Serve existing successful content and attachments")
    serve.add_argument("--config", type=Path, default=os.environ.get("WLH_API_CONFIG"))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    snapshot = commands.add_parser("snapshot", help="Capture, inspect and manually publish content")
    operations = snapshot.add_subparsers(dest="operation", required=True)
    create = operations.add_parser(
        "create", help="Freeze global successful data without publishing"
    )
    create.add_argument("--version-overrides", type=Path)
    listing = operations.add_parser("list", help="List immutable captures")
    inspect = operations.add_parser("inspect", help="Inspect frozen sources and version mappings")
    inspect.add_argument("snapshot_id")
    inspect.add_argument("--source-after", default="")
    for paged in (listing, inspect):
        paged.add_argument("--limit", type=int, default=20)
        paged.add_argument("--after", default="")
    operations.add_parser("publish", help="Manually publish or restore a capture").add_argument(
        "snapshot_id"
    )
    for mode in ("collect", "recheck", "reparse"):
        command = commands.add_parser(
            mode,
            help="Offline reparse of frozen archived inputs (no source network)"
            if mode == "reparse"
            else "Online " + mode + " from a Source YAML",
        )
        command.add_argument("source", type=Path)
        command.add_argument("--enqueue", action="store_true")
        command.add_argument("--no-progress", action="store_true")
    commands.add_parser("worker", help="Consume persistent jobs").add_argument(
        "--once", action="store_true"
    )
    return root


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "serve":
        from wlh.api.server import serve

        return serve(args.config, args.host, args.port)
    from wlh.db import initialize, require_current

    if args.command == "init":
        return initialize()
    if args.command == "snapshot":
        from wlh.domain import snapshots

        require_current()
        if args.operation == "create":
            from wlh.domain.snapshot_overrides import load

            return snapshots.create(load(args.version_overrides) if args.version_overrides else [])
        if args.operation == "list":
            return snapshots.listing(args.limit, args.after)
        if args.operation == "inspect":
            return snapshots.inspect(args.snapshot_id, args.limit, args.after, args.source_after)
        return snapshots.publish(args.snapshot_id)
    from wlh.engine.objects import Objects

    Objects().check()
    require_current()
    if args.command == "worker":
        from wlh.engine.queue import worker

        return worker(args.once)
    from wlh.engine.progress import ProgressPanel
    from wlh.engine.runs import execute_run, prepare_run, receipt

    run_id = prepare_run(args.source, args.command, enqueue=args.enqueue)
    if args.enqueue:
        return receipt(run_id)
    cancelled = threading.Event()
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def _on_signal(_signum: int, _frame: Any) -> None:
        cancelled.set()

    for sig in handlers:
        signal.signal(sig, _on_signal)
    with ProgressPanel(not args.no_progress) as panel:
        try:
            result = execute_run(run_id, progress=panel, cancel_event=cancelled)
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
        if cancelled.is_set():
            result = dict(result, error="interrupted", cancelled=True)
        panel.update(run_id, final=True)
        return result


def main() -> int:
    try:
        args = parser().parse_args()
        result = dispatch(args)
        print(json.dumps(result, ensure_ascii=False, default=str))
        if result.get("error") == "interrupted":
            return 130
        if result.get("error"):
            return 2
        return 1 if result.get("status") in {"failed", "retryable"} else 0
    except KeyboardInterrupt:
        print(json.dumps({"error": "interrupted"}))
        return 130
    except WlhError as exc:
        print(json.dumps({"error": exc.code}))
        return 2
    except Exception as exc:
        print(json.dumps({"error": "operation_failed", "category": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
