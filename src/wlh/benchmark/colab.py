#!/usr/bin/env python3
"""Local driver for executing dual-blind batch annotation on Colab G4 with parallel dual-instance streaming."""

from __future__ import annotations

import argparse
import base64
import gzip
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def run_cmd(
    cmd: list[str], check: bool = True, max_retries: int = 3
) -> subprocess.CompletedProcess[str]:
    print(f"[Driver] Running: {' '.join(cmd)}", flush=True)
    res = subprocess.CompletedProcess(cmd, 1, "", "")
    for attempt in range(1, max_retries + 1):
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            return res
        err_msg = res.stderr.strip() or res.stdout.strip()
        print(
            f"[Driver] Command attempt {attempt}/{max_retries} failed: {err_msg[:200]}", flush=True
        )
        if attempt < max_retries:
            time.sleep(3)
    if check:
        raise subprocess.CalledProcessError(
            res.returncode, cmd, output=res.stdout, stderr=res.stderr
        )
    return res


def session_work_dir(session_name: str) -> Path:
    d = Path("work") / session_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_colab_session(session_name: str) -> None:
    res = run_cmd(["colab", "sessions"], check=False)
    if session_name not in res.stdout and session_name not in res.stderr:
        print(f"[{session_name}] Creating Colab session with G4 GPU (RTX PRO 6000)...", flush=True)
        run_cmd(["colab", "new", "-s", session_name, "--gpu", "G4"])
    else:
        print(f"[{session_name}] Colab session already active.", flush=True)
    print(f"[{session_name}] Ensuring vllm and pydantic are installed on Colab...", flush=True)
    wdir = session_work_dir(session_name)
    pip_py = """import subprocess, sys
try:
    import vllm, pydantic
    print("vllm and pydantic already installed.")
except ImportError:
    print("Installing vllm and pydantic via pip...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "vllm", "pydantic"], check=True)
    print("Installed vllm and pydantic successfully!")
"""
    ensure_file = wdir / "ensure_deps.py"
    ensure_file.write_text(pip_py, encoding="utf-8")
    run_cmd(["colab", "exec", "-s", session_name, "-f", str(ensure_file), "--timeout", "600"])


def upload_file_chunked(session_name: str, local_path: Path, remote_path: str) -> None:
    """Robust chunked upload of gzipped files to Colab using session-isolated temp files."""
    wdir = session_work_dir(session_name)
    with local_path.open("rb") as f:
        data = f.read()
    gz_data = gzip.compress(data)
    chunk_size = 512 * 1024
    total_chunks = (len(gz_data) + chunk_size - 1) // chunk_size

    clear_py = f"""import os
d = os.path.dirname('{remote_path}')
if d:
    os.makedirs(d, exist_ok=True)
with open('{remote_path}.gz', 'wb') as f: pass
"""
    clear_file = wdir / "clear_tmp.py"
    clear_file.write_text(clear_py, encoding="utf-8")
    run_cmd(["colab", "exec", "-s", session_name, "-f", str(clear_file)])

    print(
        f"[{session_name}] Uploading {local_path} ({len(data)} bytes -> {len(gz_data)} gz bytes in {total_chunks} chunks)...",
        flush=True,
    )
    chunk_file = wdir / "upload_tmp_chunk.py"
    for i in range(total_chunks):
        chunk = gz_data[i * chunk_size : (i + 1) * chunk_size]
        b64 = base64.b64encode(chunk).decode("ascii")
        py_code = f"""import base64
with open('{remote_path}.gz', 'ab') as f:
    f.write(base64.b64decode('''{b64}'''))
"""
        chunk_file.write_text(py_code, encoding="utf-8")
        run_cmd(["colab", "exec", "-s", session_name, "-f", str(chunk_file), "--timeout", "120"])
        time.sleep(0.5)

    decomp_py = f"""import gzip, shutil
with gzip.open('{remote_path}.gz', 'rb') as f_in:
    with open('{remote_path}', 'wb') as f_out:
        shutil.copyfileobj(f_in, f_out)
print('Deployed {remote_path} successfully!')
"""
    decomp_file = wdir / "decomp_tmp.py"
    decomp_file.write_text(decomp_py, encoding="utf-8")
    run_cmd(["colab", "exec", "-s", session_name, "-f", str(decomp_file), "--timeout", "60"])


def upload_bundle_and_script(session_name: str, annotator: str | None = None) -> None:
    bundle_src = Path("data/benchmark/processor_complete_bundle.jsonl")
    if not bundle_src.is_file():
        raise FileNotFoundError(f"Missing bundle: {bundle_src}")

    upload_file_chunked(session_name, bundle_src, "processor_batch_bundle.jsonl")

    # Deploy only the inference package; the host benchmark facade imports unrelated
    # evaluation dependencies. Empty package initializers keep the GPU runtime minimal.
    package_init = session_work_dir(session_name) / "__init__.py"
    package_init.write_text("", encoding="utf-8")
    for package in ("wlh", "wlh/benchmark"):
        upload_file_chunked(session_name, package_init, f"{package}/__init__.py")
    for name in ("annotator.py", "tokenization.py", "annotation_schema.py"):
        upload_file_chunked(session_name, Path(__file__).parent / name, f"wlh/benchmark/{name}")

    tags = [annotator.lower()] if annotator in ("A", "B") else ["a", "b"]
    for tag in tags:
        name = f"annotator_{tag}.jsonl"
        local = Path("data/benchmark/complete_annotations") / name
        if local.is_file() and local.stat().st_size > 0:
            upload_file_chunked(session_name, local, f"complete_annotations/{name}")

    print(f"[{session_name}] Bundle and scripts deployed successfully!", flush=True)


def run_single_annotator_on_session(
    annotator: str,
    session_name: str,
    out_path: Path | None = None,
) -> int:
    """Run a single annotator ('A' or 'B') on its dedicated Colab G4 session."""
    wdir = session_work_dir(session_name)
    local_ann_dir = Path("data/benchmark/complete_annotations")
    local_ann_dir.mkdir(parents=True, exist_ok=True)
    target_out = out_path or (local_ann_dir / f"annotator_{annotator.lower()}.jsonl")

    try:
        ensure_colab_session(session_name)
        upload_bundle_and_script(session_name, annotator=annotator)

        runner_py = f"""import subprocess, sys
cmd = [
    sys.executable,
    '-m', 'wlh.benchmark.annotator',
    '--annotator', '{annotator}',
    '--input', 'processor_batch_bundle.jsonl',
    '--output-dir', 'complete_annotations',
    '--batch-size', '32',
]
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
if proc.stdout:
    for line in iter(proc.stdout.readline, ''):
        print(line, end='', flush=True)
proc.wait()
sys.exit(proc.returncode)
"""
        stream_file = wdir / "run_stream.py"
        stream_file.write_text(runner_py, encoding="utf-8")

        print(
            f"[{session_name}] Launching Annotator {annotator} batch inference stream...",
            flush=True,
        )
        cmd = ["colab", "exec", "-s", session_name, "-f", str(stream_file), "--timeout", "7200"]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )

        prefix = f"STREAM_JSONL:Annotator_{annotator}:"
        count = 0
        with target_out.open("a", encoding="utf-8") as fp:
            try:
                if proc.stdout:
                    for line in iter(proc.stdout.readline, ""):
                        if line.startswith(prefix):
                            raw_json = line[len(prefix) :].strip()
                            fp.write(raw_json + "\n")
                            fp.flush()
                            os.fsync(fp.fileno())
                            count += 1
                        elif len(line) < 500:
                            print(f"[{session_name}] {line}", end="", flush=True)
                proc.wait()
            finally:
                for name in (f"rejected_{annotator}.jsonl", f"annotator_{annotator.lower()}.jsonl"):
                    dl_py = f"""import base64, gzip, os
p = '/content/complete_annotations/{name}'
if os.path.isfile(p):
    with open(p, 'rb') as f:
        gz = gzip.compress(f.read())
    print('B64_GZ_START:' + base64.b64encode(gz).decode('ascii') + ':B64_GZ_END')
"""
                    dl_file = wdir / "dl_tmp.py"
                    dl_file.write_text(dl_py, encoding="utf-8")
                    dl_res = run_cmd(
                        [
                            "colab",
                            "exec",
                            "-s",
                            session_name,
                            "-f",
                            str(dl_file),
                            "--timeout",
                            "180",
                        ],
                        check=False,
                        max_retries=2,
                    )
                    if "B64_GZ_START:" in dl_res.stdout and ":B64_GZ_END" in dl_res.stdout:
                        b64_part = (
                            dl_res.stdout.split("B64_GZ_START:", 1)[1]
                            .split(":B64_GZ_END", 1)[0]
                            .strip()
                        )
                        raw_bytes = gzip.decompress(base64.b64decode(b64_part))
                        dest = (
                            target_out if name.startswith("annotator_") else (local_ann_dir / name)
                        )
                        dest.write_bytes(raw_bytes)
                        print(
                            f"[{session_name}] Downloaded {dest.name} ({len(raw_bytes)} bytes)",
                            flush=True,
                        )

        print(
            f"[{session_name}] Annotator {annotator} stream finished. Streamed {count} items.",
            flush=True,
        )
        if proc.returncode not in (0, 1):
            raise RuntimeError(
                f"[{session_name}] Colab execution failed with returncode {proc.returncode}"
            )
        return count
    finally:
        print(f"[{session_name}] Releasing Colab session to conserve compute units...", flush=True)
        run_cmd(["colab", "stop", "-s", session_name], check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotator", choices=("A", "B", "AB"), default="AB")
    parser.add_argument("--session", type=str, default=None)
    parser.add_argument("--out-file", type=Path, default=None)
    parser.add_argument("--skip-preselect", action="store_true")
    args = parser.parse_args()

    Path("work").mkdir(exist_ok=True)
    if not args.skip_preselect:
        input_bundle = (
            "data/benchmark/processor_batch_bundle.jsonl"
            if Path("data/benchmark/processor_batch_bundle.jsonl").is_file()
            else "data/benchmark/annotation_pipeline.json"
        )
        # CPU-only admission must succeed before allocating any paid GPU resources.
        run_cmd(
            [
                sys.executable,
                "-m",
                "wlh.benchmark.annotator",
                "--select-only",
                "--local-files-only",
                "--input",
                input_bundle,
                "--selected-bundle",
                "data/benchmark/processor_complete_bundle.jsonl",
                "--selection-report",
                "artifacts/benchmark-completion/selection.json",
            ],
            max_retries=1,
        )

    if args.annotator in ("A", "B"):
        sess = args.session or f"wlh-bench-{args.annotator.lower()}"
        run_single_annotator_on_session(args.annotator, sess, out_path=args.out_file)
        return 0

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_a = pool.submit(run_single_annotator_on_session, "A", "wlh-bench-a")
        fut_b = pool.submit(run_single_annotator_on_session, "B", "wlh-bench-b")
        fut_a.result()
        fut_b.result()

    run_cmd(
        [
            sys.executable,
            "-m",
            "wlh.benchmark.annotator",
            "--pair-only",
            "--input",
            "data/benchmark/processor_complete_bundle.jsonl",
            "--output-dir",
            "data/benchmark/complete_annotations",
        ],
        check=False,
        max_retries=1,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
