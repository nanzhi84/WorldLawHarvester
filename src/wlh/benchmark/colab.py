"""Run isolated, whole-input A/B annotation on Colab; recover receipts and release GPUs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from wlh.benchmark.annotator import write_json
from wlh.benchmark.budget import DEFAULT_CONTEXT, DEFAULT_OUTPUT

_CREDENTIAL_QUERY = re.compile(
    r"([?&](?:colab-runtime-proxy-token|access_token|refresh_token|id_token|token|"
    r"X-Goog-Signature|X-Amz-Signature|X-Amz-Security-Token)=)[^\s&<>\"')]+",
    re.IGNORECASE,
)
_TRANSIENT_TRANSPORT = re.compile(
    r"SSLEOFError|SSLError|UNEXPECTED_EOF|ConnectionError|ConnectionResetError|"
    r"RemoteDisconnected|ConnectTimeout|ReadTimeout|TimeoutError|"
    r"IncompleteRead|ChunkedEncodingError|Response ended prematurely|"
    r"Connection (?:aborted|reset|refused)|Temporary failure in name resolution",
    re.IGNORECASE,
)
_RETRYABLE_COMMANDS = {"sessions", "status", "log", "ls", "upload", "download", "stop"}


def _safe_diagnostic(value: str | bytes | None) -> str:
    text = value.decode(errors="replace") if isinstance(value, bytes) else value or ""
    # Requests may wrap an access-bearing relative URL across lines. Remove the
    # entire URL block before individual values, retaining the transport cause.
    text = re.sub(
        r"(\burl:\s*)(.*?)(?=\s*\(Caused by|\n\n|\Z)",
        lambda match: (
            match[1] + "[ACCESS_URL_REDACTED]" if _CREDENTIAL_QUERY.search(match[2]) else match[0]
        ),
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"https?://\S+",
        lambda match: (
            "[ACCESS_URL_REDACTED]"
            if "colab" in match[0].lower() or _CREDENTIAL_QUERY.search(match[0])
            else match[0]
        ),
        text,
        flags=re.IGNORECASE,
    )
    return _CREDENTIAL_QUERY.sub(r"\1[REDACTED]", text)


def run_cmd(
    command: list[str], *, timeout: int = 300, check: bool = True
) -> subprocess.CompletedProcess[str]:
    # Repeating allocation or execution can start duplicate paid work. Only
    # idempotent transport operations receive bounded retries (1 s, then 2 s).
    attempts = (
        3
        if len(command) > 1
        and Path(command[0]).name == "colab"
        and command[1] in _RETRYABLE_COMMANDS
        else 1
    )
    safe_command = [_safe_diagnostic(argument) for argument in command]
    result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(attempts):
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired as exc:
            if attempt + 1 < attempts:
                time.sleep(attempt + 1)
                continue
            raise subprocess.TimeoutExpired(
                safe_command,
                exc.timeout,
                output=_safe_diagnostic(exc.output),
                stderr=_safe_diagnostic(exc.stderr),
            ) from None
        result.args = safe_command
        result.stdout = _safe_diagnostic(result.stdout)
        result.stderr = _safe_diagnostic(result.stderr)
        if (
            result.returncode
            and attempt + 1 < attempts
            and _TRANSIENT_TRANSPORT.search(result.stdout + result.stderr)
        ):
            time.sleep(attempt + 1)
            continue
        break
    assert result is not None
    if check and result.returncode:
        raise RuntimeError(
            f"{safe_command[:2]} failed ({result.returncode}): "
            f"{result.stderr[-1500:] or result.stdout[-1500:]}"
        )
    return result


def remote(session: str, directory: Path, name: str, code: str, timeout: int = 300) -> str:
    path = directory / f"{name}.py"
    path.write_text(code, encoding="utf-8")
    result = run_cmd(
        ["colab", "exec", "-s", session, "-f", str(path), "--timeout", str(timeout)],
        timeout=timeout + 90,
    )
    (directory / f"{name}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    return result.stdout


def upload(session: str, local: Path, destination: str) -> None:
    run_cmd(["colab", "upload", "-s", session, str(local), "content/" + destination], timeout=600)


def run_session(
    tag: str, session: str, bundle: Path, output: Path, options: list[str]
) -> dict[str, object]:
    work = output / f"runtime_{tag.lower()}"
    work.mkdir(parents=True, exist_ok=True)
    recovered: dict[str, str] = {}
    try:
        print(f"[{tag}] Allocating {session}", flush=True)
        run_cmd(["colab", "new", "-s", session, "--gpu", "G4"], timeout=300)
        remote(
            session,
            work,
            "setup",
            """import subprocess, sys, pathlib, importlib.metadata, json
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'vllm', 'pydantic', 'markdown-it-py>=4.2,<5'], check=True)
for directory in ['wlh/benchmark', 'annotations']:
    pathlib.Path(directory).mkdir(parents=True, exist_ok=True)
for name in ['wlh/__init__.py', 'wlh/benchmark/__init__.py']:
    pathlib.Path(name).write_text('')
print(json.dumps({name: importlib.metadata.version(name) for name in ['vllm', 'pydantic', 'torch', 'transformers', 'tokenizers']}))
subprocess.run(['nvidia-smi'], check=True)
""",
            900,
        )
        files = {"selected.jsonl": bundle}
        for name in (
            "annotator.py",
            "inference.py",
            "budget.py",
            "tokenization.py",
            "annotation_schema.py",
            "quality.py",
            "date_evidence.py",
            "markdown_evidence.py",
            "source_evidence.py",
        ):
            files[f"wlh/benchmark/{name}"] = Path(__file__).parent / name
        # Resume only this run's results; annotator validates original receipts before reuse.
        previous = output / f"annotator_{tag.lower()}.jsonl"
        if previous.is_file():
            files[f"annotations/{previous.name}"] = previous
        for destination, local in files.items():
            upload(session, local, destination)
        hashes = {
            name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()
        }
        remote(
            session,
            work,
            "verify_upload",
            f"""import hashlib, pathlib
expected = {hashes!r}
for name, expected_hash in expected.items():
    assert hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest() == expected_hash, name
print('All upload hashes verified')
""",
        )
        runner = work / "run.py"
        runner.write_text(
            f"""import subprocess, sys, pathlib, json, hashlib
for name in ['annotator_{tag.lower()}.jsonl', 'rejected_{tag}.jsonl']:
    pathlib.Path('annotations', name).touch(exist_ok=True)
command = [sys.executable, '-m', 'wlh.benchmark.annotator', '--annotator', {tag!r}, '--input', 'selected.jsonl', '--output-dir', 'annotations', ] + {options!r}
with open('annotations/inference_{tag}.log', 'w') as log:
    result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
manifest = {{'exit_code': result.returncode, 'files': {{p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path('annotations').iterdir() if p.is_file()}}}}
pathlib.Path('annotations/manifest_{tag}.json').write_text(json.dumps(manifest))
print(json.dumps(manifest))
""",
            encoding="utf-8",
        )
        print(f"[{tag}] Running full-input inference", flush=True)
        result = run_cmd(
            ["colab", "exec", "-s", session, "-f", str(runner), "--timeout", "7200"],
            timeout=7290,
            check=False,
        )
        (work / "execution.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        # Download before reporting a model failure; rejection receipts are valuable evidence.
        manifest_path = output / f"manifest_{tag}.json"
        run_cmd(
            [
                "colab",
                "download",
                "-s",
                session,
                f"content/annotations/manifest_{tag}.json",
                str(manifest_path),
            ],
            timeout=300,
        )
        manifest = json.loads(manifest_path.read_text())
        for name, expected_hash in manifest["files"].items():
            if Path(name).name != name:
                raise ValueError("unsafe_remote_filename")
            destination = output / name
            run_cmd(
                [
                    "colab",
                    "download",
                    "-s",
                    session,
                    f"content/annotations/{name}",
                    str(destination),
                ],
                timeout=600,
            )
            actual = hashlib.sha256(destination.read_bytes()).hexdigest()
            if actual != expected_hash:
                raise ValueError(f"download_hash_mismatch:{name}")
            recovered[name] = actual
        if result.returncode or manifest["exit_code"] not in (0, 1):
            raise RuntimeError(f"{tag}: inference failed; inspect {work} and recovered logs")
        return {"annotator": tag, "exit_code": manifest["exit_code"], "files": recovered}
    finally:
        print(f"[{tag}] Releasing {session}", flush=True)
        release = run_cmd(["colab", "stop", "-s", session], check=False, timeout=180)
        (work / "release.log").write_text(release.stdout + release.stderr, encoding="utf-8")
        if release.returncode:
            print(f"[{tag}] RELEASE FAILED: {session}; manual cleanup required", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--selection-report", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--annotator", choices=("A", "B", "AB"), default="AB")
    parser.add_argument("--session", default="wlh-benchmark-v3")
    parser.add_argument("--context-tokens", type=int, default=DEFAULT_CONTEXT)
    parser.add_argument("--max-output-tokens", type=int, default=DEFAULT_OUTPUT)
    parser.add_argument("--safety-tokens", type=int, default=1024)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--kv-cache-dtype", choices=("auto", "fp8"), default="auto")
    parser.add_argument("--cpu-offload-gb", type=float, default=0.0)
    args = parser.parse_args()
    if not 0 <= args.cpu_offload_gb <= 32:
        parser.error("invalid_cpu_offload_gb:expected_finite_0_to_32")
    options: list[str] = []
    for key in (
        "context_tokens",
        "max_output_tokens",
        "safety_tokens",
        "batch_size",
        "kv_cache_dtype",
        "cpu_offload_gb",
    ):
        options.extend(["--" + key.replace("_", "-"), str(getattr(args, key))])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    selected = args.output_dir / "selected.jsonl"
    selection = args.selection_report or args.output_dir / "selection.json"
    if len({p.resolve() for p in (args.input, selected, selection)}) != 3:
        parser.error("input_and_output_paths_must_differ")
    command = [
        sys.executable,
        "-m",
        "wlh.benchmark.annotator",
        "--select-only",
        "--local-files-only",
        "--input",
        str(args.input),
        "--selected-bundle",
        str(selected),
        "--selection-report",
        str(selection),
    ]
    result = run_cmd(command + options, timeout=900, check=False)
    if not selection.exists():
        raise RuntimeError(result.stderr or result.stdout)
    report = json.loads(selection.read_text())
    report["input_sha256"] = hashlib.sha256(args.input.read_bytes()).hexdigest()
    write_json(selection, report)
    print(
        json.dumps({k: report[k] for k in ("total_tasks", "selected_tasks", "excluded_tasks")}),
        flush=True,
    )
    if result.returncode or args.prepare_only:
        return result.returncode
    tags = list("AB") if args.annotator == "AB" else [args.annotator]
    with ThreadPoolExecutor(max_workers=len(tags)) as pool:
        futures = [
            pool.submit(
                run_session,
                tag,
                f"{args.session}-{tag.lower()}",
                selected,
                args.output_dir,
                options,
            )
            for tag in tags
        ]
        receipts = [future.result() for future in futures]
    write_json(
        args.output_dir / "run.json",
        {
            "input_sha256": report["input_sha256"],
            "budget": report["budget"],
            "runtime": report["runtime"],
            "options": options,
            "receipts": receipts,
        },
    )
    if len(tags) == 2:
        paired = run_cmd(
            [
                sys.executable,
                "-m",
                "wlh.benchmark.annotator",
                "--pair-only",
                "--input",
                str(selected),
                "--output-dir",
                str(args.output_dir),
            ]
            + options,
            timeout=300,
            check=False,
        )
        (args.output_dir / "pair.log").write_text(paired.stdout + paired.stderr, encoding="utf-8")
        return paired.returncode
    return int(any(row["exit_code"] != 0 for row in receipts))


if __name__ == "__main__":
    raise SystemExit(main())
