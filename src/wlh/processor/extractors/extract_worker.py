"""Isolated extraction process: every format through the gateway on a file materialized from CAS.

stdin carries a JSON job, stdout a single bounded JSON result. stderr is discarded by the
parent because third-party tools can echo document text or local paths.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, NoReturn, cast

from wlh.core import WlhError
from wlh.processor.extractors.base import ExtractionInput, ExtractionOptions


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(str(x) for x in cast(Sequence[Any], value) if x)
    return ()


def job_options(job: Mapping[str, Any]) -> ExtractionOptions:
    """The JSON job of the parent process as typed extraction options."""
    raw_parser = job.get("parser")
    parser_cfg: Mapping[str, Any] = (
        cast(Mapping[str, Any], raw_parser) if isinstance(raw_parser, Mapping) else {}
    )
    return ExtractionOptions(
        profile=str(job.get("profile") or "generic"),
        quirks=_strings(parser_cfg.get("quirks")),
        invalid_body_patterns=_strings(parser_cfg.get("invalid_body_patterns")),
        ocr=cast(Mapping[str, Any], job.get("ocr") or {}),
        max_bytes=int(job.get("max_bytes") or 104_857_600),
    )


def extract(
    path: Path, kind: str, job: dict[str, Any], depth: int = 0
) -> tuple[str, dict[str, Any], dict[str, Any], list[str], list[dict[str, Any]]]:
    """One materialized file through the gateway (magic calibration + registry dispatch)."""
    from wlh.processor.extractors.registry import default_registry

    output = default_registry().dispatch(
        ExtractionInput(
            data=Path(path).read_bytes(),
            kind=kind,
            path=Path(path),
            ocr_cache=job.get("ocr_cache"),
            depth=depth,
        ),
        job_options(job),
    )
    return output.markdown, output.info, output.quality, output.parsers, output.members


def watch_parent(parent_pid: int) -> None:
    while True:
        if os.getppid() != parent_pid:
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except OSError:
                pass
            return
        time.sleep(0.1)


def _run_job(job: dict[str, Any]) -> dict[str, Any]:
    try:
        with redirect_stdout(sys.stderr):
            markdown, info, report, parsers, members = extract(Path(job["path"]), job["kind"], job)
        if not markdown.strip():
            raise WlhError("extracted_text_empty")
        return {
            "markdown": markdown,
            "info": info,
            "quality": report,
            "parsers": sorted(set(parsers)),
            "members": members,
        }
    except WlhError as exc:
        return {"error": exc.code}
    except MemoryError:
        return {"error": "extraction_memory_exceeded"}
    except Exception as exc:
        return {"error": "extraction_failed", "category": type(exc).__name__}


def main() -> None:
    def terminate(*_: Any) -> NoReturn:
        return (_ for _ in ()).throw(SystemExit(143))

    signal.signal(signal.SIGTERM, terminate)
    threading.Thread(target=watch_parent, args=(os.getppid(),), daemon=True).start()
    if "--daemon" in sys.argv[1:]:
        from wlh.processor.extractors.registry import default_registry

        default_registry()  # warm every engine before the first job
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            req = json.loads(line)
            job = json.loads(Path(req["job_file"]).read_text(encoding="utf-8"))
            value = _run_job(job)
            Path(req["output_file"]).write_text(
                json.dumps(value, ensure_ascii=False), encoding="utf-8"
            )
            sys.stdout.write("__WLH_DONE__\n")
            sys.stdout.flush()
        return
    job = json.loads(sys.stdin.read())
    value = _run_job(job)
    sys.stdout.write(json.dumps(value, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
