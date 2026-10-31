"""Run-owned, cancellable extraction processes with bounded output and group cleanup."""

from __future__ import annotations

import atexit
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from wlh.core import WlhError
from wlh.processor.extractors.slots import PDFSlots
from wlh.storage.cas import work_directory

MAX_OUTPUT_BYTES = 80 * 1024 * 1024
MAX_WARM_WORKERS = 12
_POOL_LOCK = threading.Lock()
_IDLE_WORKERS: list[subprocess.Popen[bytes]] = []


def _worker_env() -> dict[str, str]:
    env: dict[str, str] = {
        k: os.environ[k]
        for k in (
            "PATH",
            "LANG",
            "LC_ALL",
            "TMPDIR",
            "SSL_CERT_FILE",
            "TESSDATA_PREFIX",
            "WLH_TESSERACT",
            "WLH_SOFFICE",
            "WLH_BR_OCR_URL",
            "HOME",
        )
        if k in os.environ
    }
    env["OMP_THREAD_LIMIT"] = "1"
    # Select this checkout without forwarding ambient PYTHONPATH or storage credentials.
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
    return env


def _checkout_worker() -> subprocess.Popen[bytes]:
    with _POOL_LOCK:
        while _IDLE_WORKERS:
            proc = _IDLE_WORKERS.pop()
            if proc.poll() is None and proc.stdin is not None and proc.stdout is not None:
                return proc
            try:
                stop_group(proc)
            except Exception:
                pass
    return subprocess.Popen(
        [sys.executable, "-m", "wlh.processor.extractors.extract_worker", "--daemon"],
        cwd=work_directory(),
        env=_worker_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _checkin_worker(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    with _POOL_LOCK:
        if len(_IDLE_WORKERS) < MAX_WARM_WORKERS:
            _IDLE_WORKERS.append(proc)
            return
    try:
        stop_group(proc)
    except Exception:
        pass


def prewarm_workers(count: int = 4) -> None:
    """Spawn up to `count` warm daemon extraction workers in the background."""
    target = max(0, min(count, MAX_WARM_WORKERS))

    def _spawn_needed() -> None:
        while True:
            with _POOL_LOCK:
                alive = [p for p in _IDLE_WORKERS if p.poll() is None]
                _IDLE_WORKERS[:] = alive
                if len(_IDLE_WORKERS) >= target:
                    return
            try:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "wlh.processor.extractors.extract_worker", "--daemon"],
                    cwd=work_directory(),
                    env=_worker_env(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception:
                return
            with _POOL_LOCK:
                if len(_IDLE_WORKERS) < MAX_WARM_WORKERS and proc.poll() is None:
                    _IDLE_WORKERS.append(proc)
                else:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait(timeout=0.2)
                    except Exception:
                        pass
                    return

    threading.Thread(target=_spawn_needed, daemon=True).start()


@atexit.register
def _shutdown_warm_workers() -> None:
    with _POOL_LOCK:
        workers = list(_IDLE_WORKERS)
        _IDLE_WORKERS.clear()
    for proc in workers:
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    for proc in workers:
        try:
            proc.wait(timeout=0.5)
        except Exception:
            pass


def stop_group(process: subprocess.Popen[bytes]) -> None:
    for sig, seconds in ((signal.SIGTERM, 0.5), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(process.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                try:
                    os.killpg(process.pid, 0)
                except (ProcessLookupError, PermissionError):
                    return
            time.sleep(0.02)
    raise WlhError("extraction_cleanup_failed")


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(cast(Mapping[str, Any], value))
    return {}


def _extract_job_params(
    config: Mapping[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any], int]:
    storage_cfg = _as_dict(config.get("storage"))
    clean_cfg = _as_dict(config.get("cleaners"))
    metadata = (
        _as_dict(config.get("metadata"))
        or _as_dict(storage_cfg.get("metadata"))
        or _as_dict(clean_cfg.get("metadata"))
    )
    profile = "vi" if str(metadata.get("language") or "").startswith("vi") else "generic"
    proc_cfg = _as_dict(config.get("processor"))
    ext_cfg = _as_dict(proc_cfg.get("extractor"))
    if "parser" in config and not isinstance(config.get("processor"), Mapping):
        parser_cfg: dict[str, Any] = _as_dict(config["parser"])
        parser_cfg.setdefault(
            "invalid_body_patterns",
            list(
                cast(
                    Sequence[Any],
                    clean_cfg.get("invalid_body_patterns")
                    or config.get("invalid_body_patterns")
                    or [],
                )
            ),
        )
    else:
        parser_cfg = {
            "office_text_policy": ext_cfg.get("office_text_policy", "prefer"),
            "libreoffice_timeout_seconds": ext_cfg.get("libreoffice_timeout_seconds", 120),
            "quality_accept_routes": clean_cfg.get(
                "quality_accept_routes", ["accept", "accept_ocr"]
            ),
            "quirks": list(cast(Sequence[Any], ext_cfg.get("quirks") or [])),
            "invalid_body_patterns": list(
                cast(
                    Sequence[Any],
                    clean_cfg.get("invalid_body_patterns")
                    or config.get("invalid_body_patterns")
                    or [],
                )
            ),
        }
    ocr_cfg: dict[str, Any] = (
        _as_dict(config.get("ocr"))
        or _as_dict(ext_cfg.get("ocr"))
        or _as_dict(proc_cfg.get("ocr"))
    )
    sched_cfg = _as_dict(config.get("scheduler"))
    fetch_cfg = _as_dict(config.get("fetcher"))
    budget_cfg = (
        _as_dict(config.get("budget"))
        or _as_dict(sched_cfg.get("budget"))
        or _as_dict(fetch_cfg.get("budget"))
    )
    max_bytes = int(budget_cfg.get("response_bytes", 104_857_600))
    return profile, parser_cfg, ocr_cfg, max_bytes


def run_extraction(
    data: bytes,
    kind: str,
    config: Mapping[str, Any],
    *,
    active: Callable[[], None] | None = None,
    cached_ocr: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialized CAS bytes in, (result, member files) out. ``active`` raises to cancel."""
    profile, parser_cfg, ocr_cfg, max_bytes = _extract_job_params(config)
    job: dict[str, Any] = {
        "kind": kind,
        "ocr_cache": cached_ocr,
        "parser": parser_cfg,
        "ocr": ocr_cfg,
        "profile": profile,
        "max_bytes": max_bytes,
    }
    timeout: float = (
        ocr_cfg["timeout_seconds"] + parser_cfg["libreoffice_timeout_seconds"] * 2
    )
    slots = PDFSlots()
    slot = slots.acquire_blocking(active)
    folder = Path(tempfile.mkdtemp(prefix="wlh-extract-", dir=work_directory()))
    process: subprocess.Popen[bytes] | None = None
    reusable = False
    try:
        job["path"] = str(folder / "input")
        (folder / "input").write_bytes(data)
        job_path = folder / "job.json"
        output_path = folder / "result.json"
        job_path.write_text(json.dumps(job), encoding="utf-8")
        process = _checkout_worker()
        assert process.stdin is not None and process.stdout is not None
        req_line = (
            json.dumps({"job_file": str(job_path), "output_file": str(output_path)}) + "\n"
        ).encode("utf-8")
        process.stdin.write(req_line)
        process.stdin.flush()
        deadline = time.monotonic() + timeout
        stdout_fd = process.stdout.fileno()
        buf = b""
        while True:
            if active is not None:
                active()
            if time.monotonic() >= deadline:
                raise WlhError("extraction_timeout")
            if output_path.exists() and output_path.stat().st_size > MAX_OUTPUT_BYTES:
                raise WlhError("extraction_output_too_large")
            wait_sec = min(0.05, max(0.001, deadline - time.monotonic()))
            ready, _, _ = select.select([stdout_fd], [], [], wait_sec)
            if ready:
                chunk = os.read(stdout_fd, 4096)
                if not chunk:
                    break
                buf += chunk
                if b"__WLH_DONE__\n" in buf:
                    reusable = True
                    break
            elif process.poll() is not None:
                break
        if not output_path.exists():
            raise WlhError("extraction_output_invalid")
        if output_path.stat().st_size > MAX_OUTPUT_BYTES:
            raise WlhError("extraction_output_too_large")
        try:
            value: dict[str, Any] | list[Any] | str | int | float | None = json.loads(
                output_path.read_bytes()
            )
        except ValueError:
            raise WlhError("extraction_output_invalid") from None
        if not isinstance(value, dict):
            raise WlhError("extraction_output_invalid")
        if "error" in value:
            code = value["error"]
            raise WlhError(
                code if isinstance(code, str) and len(code) < 80 else "extraction_failed"
            )
        if not isinstance(value.get("markdown"), str) or not isinstance(value.get("members"), list):
            raise WlhError("extraction_output_invalid")
        members: list[dict[str, Any]] = []
        for member in value["members"]:
            path = Path(member["file"]).resolve()
            if folder.resolve() not in path.parents:
                raise WlhError("extraction_output_invalid")
            members.append(
                {"path": member["path"], "kind": member["kind"], "data": path.read_bytes()}
            )
        value["members"] = members
        return value
    finally:
        try:
            if process is not None:
                if reusable and process.poll() is None:
                    _checkin_worker(process)
                else:
                    stop_group(process)
        except WlhError:
            slots.block()
            raise
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            os.close(slot)
