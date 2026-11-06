"""Real local Jupyter and HTTP framing faults for Colab download acceptance."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def real_cli_launcher(directory: Path, config: Path) -> tuple[Path, Path, dict[str, str]]:
    real = shutil.which("colab")
    assert real is not None, "real Google Colab CLI must be installed"
    launches = directory / "cli-launches.txt"
    launches.touch()
    executable = directory / "colab"
    executable.write_text(
        f"#!{sys.executable}\nimport os, sys\n"
        f"with open({str(launches)!r}, 'a') as log: log.write('launch\\n')\n"
        f"os.execv({real!r}, [{real!r}, '--config', {str(config)!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return (
        executable,
        launches,
        {
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "http_proxy": "http://127.0.0.1:9",
            "https_proxy": "http://127.0.0.1:9",
            "ALL_PROXY": "",
            "all_proxy": "",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        },
    )


@contextmanager
def _jupyter(directory: Path) -> Generator[int]:
    python = os.environ["WLH_COLAB_JUPYTER_PYTHON"]
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    command = [
        python,
        "-m",
        "jupyter_server",
        "--no-browser",
        "--ServerApp.ip=127.0.0.1",
        f"--ServerApp.port={port}",
        "--ServerApp.port_retries=0",
        f"--ServerApp.root_dir={directory / 'served'}",
        "--IdentityProvider.token=",
    ]
    with (directory / "jupyter.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={
                **os.environ,
                "JUPYTER_RUNTIME_DIR": str(directory / "runtime"),
                "JUPYTER_CONFIG_DIR": str(directory / "config"),
                "JUPYTER_DATA_DIR": str(directory / "jupyter-data"),
            },
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                try:
                    connection.request("GET", "/api")
                    if connection.getresponse().status == 200:
                        break
                except OSError:
                    pass
                finally:
                    connection.close()
                assert process.poll() is None, "real Jupyter Server exited before readiness"
                assert time.monotonic() < deadline, "real Jupyter Server readiness timed out"
                time.sleep(0.1)
            yield port
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@contextmanager
def partial_download(directory: Path, *, failures: int, framing: str) -> Generator[dict[str, Any]]:
    served = directory / "served"
    served.mkdir()
    source = served / "annotations.jsonl"
    rows = [
        {"id": f"native-{index}", "text": "完整原生正文，末条必须保留。" * 200}
        for index in range(80)
    ]
    body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()
    source.write_bytes(body)
    evidence: dict[str, Any] = {
        "framing": framing,
        "requests": 0,
        "truncated": 0,
        "complete_responses": 0,
        "expected_sha256": hashlib.sha256(body).hexdigest(),
        "expected_bytes": len(body),
        "expected_rows": len(rows),
    }
    lock = threading.Lock()
    with _jupyter(directory) as upstream_port:

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                connection = http.client.HTTPConnection("127.0.0.1", upstream_port, timeout=10)
                try:
                    connection.request("GET", self.path)
                    response = connection.getresponse()
                    data = response.read()
                    status = response.status
                finally:
                    connection.close()
                with lock:
                    evidence["requests"] += 1
                    truncate = evidence["requests"] <= failures
                    evidence["truncated" if truncate else "complete_responses"] += 1
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Connection", "close")
                payload = data[: len(data) // 2] if truncate else data
                if framing == "chunked":
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
                    if not truncate:
                        self.wfile.write(b"0\r\n\r\n")
                else:
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(payload)
                self.wfile.flush()
                self.close_connection = True

            def log_message(self, format: str, *args: object) -> None:
                pass  # Never record access-bearing URLs, even synthetic test tokens.

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config = directory / "sessions.json"
                session = str(directory / "cli-history")
                config.write_text(
                    json.dumps(
                        {
                            session: {
                                "name": session,
                                "token": "FAKE_TEST_TOKEN_LOCAL_DOWNLOAD_NEVER_REAL",
                                "url": f"http://127.0.0.1:{server.server_port}",
                                "endpoint": "local-jupyter-acceptance",
                                "token_expires_at": "2099-01-01T00:00:00+00:00",
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                executable, launches, env = real_cli_launcher(directory, config)
                destination = directory / "downloaded.jsonl"
                yield {
                    "command": [
                        str(executable),
                        "download",
                        "-s",
                        session,
                        source.name,
                        str(destination),
                    ],
                    "launches": launches,
                    "env": env,
                    "destination": destination,
                    "evidence": evidence,
                }
            finally:
                server.shutdown()
                thread.join(timeout=3)
