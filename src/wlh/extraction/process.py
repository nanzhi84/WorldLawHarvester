"""Run-owned, cancellable extraction processes with bounded output and group cleanup."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from wlh.core import WlhError
from wlh.engine.objects import work_directory
from wlh.extraction.slots import PDFSlots

MAX_OUTPUT_BYTES = 80 * 1024 * 1024


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


def run_extraction(
    data: bytes,
    kind: str,
    config: Mapping[str, Any],
    *,
    active: Callable[[], None] | None = None,
    cached_ocr: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialized CAS bytes in, (result, member files) out. ``active`` raises to cancel."""
    profile: str = (
        "vi" if str(config["metadata"].get("language") or "").startswith("vi") else "generic"
    )
    job: dict[str, Any] = {
        "kind": kind,
        "ocr_cache": cached_ocr,
        "parser": config["parser"],
        "ocr": config["ocr"],
        "profile": profile,
        "max_bytes": config["budget"]["response_bytes"],
    }
    timeout: float = (
        config["ocr"]["timeout_seconds"] + config["parser"]["libreoffice_timeout_seconds"] * 2
    )
    slots = PDFSlots()
    slot = slots.acquire_blocking(active)
    folder = Path(tempfile.mkdtemp(prefix="wlh-extract-", dir=work_directory()))
    process = None
    try:
        job["path"] = str(folder / "input")
        (folder / "input").write_bytes(data)
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
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        output_path = folder / "result.json"
        with (folder / "job.json").open("w") as handle:
            json.dump(job, handle)
        with (folder / "job.json").open("rb") as stdin, output_path.open("wb") as stdout:
            process = subprocess.Popen(
                [sys.executable, "-m", "wlh.extraction.extract_worker"],
                cwd=folder,
                env=env,
                stdin=stdin,
                stdout=stdout,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if active is not None:
                    active()
                if time.monotonic() >= deadline:
                    raise WlhError("extraction_timeout")
                if output_path.stat().st_size > MAX_OUTPUT_BYTES:
                    raise WlhError("extraction_output_too_large")
                time.sleep(0.05)
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
                stop_group(process)
        except WlhError:
            slots.block()
            raise
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            os.close(slot)
