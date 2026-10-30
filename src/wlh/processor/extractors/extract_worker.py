"""Isolated extraction process: Office/ZIP/PDF/OCR on a file materialized from CAS.

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
from collections.abc import Sequence
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, NoReturn, cast

from wlh.cleaners.extraction_quality import text_check
from wlh.core import WlhError
from wlh.processor.extractors.html.html_to_md import (
    html_to_markdown,
    json_to_markdown,
    text_to_markdown,
)
from wlh.processor.extractors.pdf.scan_detector import extract_pdf
from wlh.processor.extractors.word.office import OFFICE_KINDS, extract_office, unpack_zip

ROUTE_ORDER = (
    "accept",
    "accept_ocr",
    "accept_with_warning",
    "vision_reparse_pages",
    "legacy_font_encoding_recovery",
)


def worst(routes: Sequence[str]) -> str:
    return max(routes, key=ROUTE_ORDER.index)


def extract(
    path: Path, kind: str, job: dict[str, Any], depth: int = 0
) -> tuple[str, dict[str, Any], dict[str, Any], list[str], list[dict[str, Any]]]:
    parser_cfg = cast(dict[str, Any], job.get("parser")) if isinstance(job.get("parser"), dict) else {}
    raw_pats = cast(list[Any], parser_cfg.get("invalid_body_patterns")) if isinstance(parser_cfg.get("invalid_body_patterns"), list) else []
    invalid_pats: list[str] = [str(x) for x in raw_pats if x]
    if kind in {"txt", "html", "json"}:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeError:
            raise WlhError("zip_text_encoding_invalid") from None
        markdown = (
            html_to_markdown(text, "")
            if kind == "html"
            else json_to_markdown(text)
            if kind == "json"
            else text_to_markdown(text)
        )
        return (
            markdown,
            {"kind": kind},
            text_check(markdown, job["profile"], invalid_body_patterns=invalid_pats),
            ["markdown", "quality"],
            [],
        )
    if kind == "pdf":
        markdown, info, report, parsers = extract_pdf(
            path,
            job["ocr"],
            job["profile"],
            job.get("ocr_cache"),
            invalid_body_patterns=invalid_pats,
        )
        return markdown, info, report, parsers, []
    if kind in OFFICE_KINDS:
        markdown, info = extract_office(
            path,
            kind,
            job["parser"],
            ocr=job.get("ocr"),
            script_hint=job.get("profile"),
        )
        parsers = ["office", "quality"]
        if info.get("legacy_encoding"):
            parsers.append("tcvn3")
        return (
            markdown,
            info,
            text_check(markdown, job["profile"], invalid_body_patterns=invalid_pats),
            parsers,
            [],
        )
    if kind == "zip":
        if depth >= 2:
            raise WlhError("zip_nesting_limit_exceeded")
        folder = Path(path).with_name(Path(path).name + "-members")
        folder.mkdir()
        raw_quirks = parser_cfg.get("quirks")
        quirks: Sequence[str] = (
            [str(x) for x in cast(Sequence[Any], raw_quirks) if x]
            if isinstance(raw_quirks, Sequence) and not isinstance(raw_quirks, str) and raw_quirks
            else ("zip_salvage",)
        )
        members = unpack_zip(path, folder, job["max_bytes"], quirks=quirks)
        parts: list[str] = []
        infos: list[dict[str, Any]] = []
        reports: list[dict[str, Any]] = []
        parsers = {"zip"}
        lineage: list[dict[str, Any]] = []
        for member in members:
            inner = extract(member["file"], member["kind"], job, depth + 1)
            markdown, info, report, used, nested = inner
            title = Path(member["path"]).stem
            parts.append("## " + title + "\n\n" + markdown.strip())
            infos.append({"path": member["path"], **info})
            reports.append({"path": member["path"], **report})
            parsers.update(used)
            lineage.append(
                {"path": member["path"], "file": str(member["file"]), "kind": member["kind"]}
            )
            lineage.extend(dict(item, path=member["path"] + "!/" + item["path"]) for item in nested)
        report = {
            "contract": reports[0]["contract"],
            "profile": job["profile"],
            "recommended_route": worst([r["recommended_route"] for r in reports]),
            "members": reports,
            "pages": [],
            "document_reasons": sorted({x for r in reports for x in r["document_reasons"]}),
            "reason_count": sum(r["reason_count"] for r in reports),
        }
        info = {"kind": "zip", "members": infos}
        return "\n\n".join(parts).strip() + "\n", info, report, sorted(parsers), lineage
    raise WlhError("unsupported_document_type")


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
