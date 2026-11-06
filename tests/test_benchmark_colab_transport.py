"""Real subprocess and opt-in read-only Colab transport acceptance; no mocks."""

from __future__ import annotations

import hashlib
import json
import os
import select
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from tests.benchmark_colab_transport_support import partial_download, real_cli_launcher

ROOT = Path(__file__).resolve().parents[1]
DRIVER = """import json, subprocess, sys, traceback
from wlh.benchmark.colab import run_cmd
config = json.loads(open(sys.argv[1]).read())
secrets = config.get("secrets", [])
try:
    result = run_cmd(config["command"], timeout=config["timeout"], check=config["check"])
    surfaces = {"stdout": result.stdout, "stderr": result.stderr, "args": repr(result.args)}
    receipt = {"returned": True, "returncode": result.returncode}
except Exception as exc:
    surfaces = {"str": str(exc), "repr": repr(exc), "traceback": traceback.format_exc()}
    for field in ("stdout", "stderr", "output", "cmd", "args"):
        surfaces[field] = repr(getattr(exc, field, None))
    receipt = {"returned": False, "error_type": type(exc).__name__,
               "timeout": getattr(exc, "timeout", None)}
combined = "\\n".join(str(value) for value in surfaces.values())
receipt.update({"leaked_labels": [str(i) for i, value in enumerate(secrets) if value in combined],
    "diagnostic_retained": "DIAGNOSTIC_MARKER" in combined,
    "ssl_eof_retained": "SSLEOFError" in combined,
    "timeout_retained": "timeout" in combined.lower() or "timed out" in combined.lower(),
    "network_failure_retained": any(word in combined.lower() for word in
        ("ssl", "eof", "connection", "timeout", "timed out")),
    "partial_transfer_retained": any(word in combined.lower() for word in
        ("incompleteread", "chunkedencodingerror", "response ended prematurely")),
    "surfaces_checked": sorted(surfaces)})
print(json.dumps(receipt))
"""
EMITTER = """import json, sys, time
payload = json.loads(open(sys.argv[1]).read())
print(payload["stdout"], flush=True)
print(payload["stderr"], file=sys.stderr, flush=True)
if payload["mode"] == "timeout":
    time.sleep(20)
sys.exit(7 if payload["mode"] == "failure" else 0)
"""


def _receipt(name: str, result: dict[str, Any]) -> None:
    if destination := os.environ.get("WLH_COLAB_TRANSPORT_EVIDENCE"):
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{name}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def _run(
    directory: Path,
    command: list[str],
    *,
    check: bool = True,
    timeout: int = 30,
    secrets: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    driver = directory / "driver.py"
    driver.write_text(DRIVER, encoding="utf-8")
    config = directory / "config.json"
    config.write_text(
        json.dumps(
            {"command": command, "check": check, "timeout": timeout, "secrets": secrets or []}
        ),
        encoding="utf-8",
    )
    process = subprocess.run(
        [sys.executable, str(driver), str(config)],
        cwd=directory,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), **(env or {})},
        capture_output=True,
        text=True,
        timeout=timeout * 3 + 30,
        check=False,
    )
    assert process.returncode == 0, "acceptance driver failed; raw output deliberately withheld"
    result: dict[str, Any] = json.loads(process.stdout)
    return result


def _diagnostic_command(directory: Path, mode: str) -> tuple[list[str], list[str]]:
    secrets = [f"FAKE_TEST_TOKEN_{index}_NEVER_REAL" for index in range(5)]
    secrets.extend(["FAKE_TEST_TOKEN_WRAPPED_FIRST_HALF", "FAKE_TEST_TOKEN_WRAPPED_SECOND_HALF"])
    emitter = directory / "emit_diagnostics.py"
    emitter.write_text(EMITTER, encoding="utf-8")
    payload = directory / "diagnostics.json"
    payload.write_text(
        json.dumps(
            {
                "mode": mode,
                "stdout": (
                    "DIAGNOSTIC_MARKER normal progress\n"
                    f"/api/contents/data?colab-runtime-proxy-token={secrets[0]}&offset=2\n"
                    f"https://Runtime.GoogleColab.com/api?token={secrets[1]}\n"
                ),
                "stderr": (
                    "DIAGNOSTIC_MARKER actionable error\n"
                    f"HTTPSConnectionPool: url: /api/status?access_token={secrets[2]}&x=1\n"
                    f"https://example.invalid/colab?token={secrets[3]}&next=4\n"
                    f"/api/files?colab-runtime-proxy-token={secrets[4]}%2Bencoded\n"
                    "HTTPSConnectionPool(host='runtime.GoogleColab.com', port=443): "
                    "Max retries exceeded with url: /api/contents/data?"
                    f"colab-runtime-proxy-token={secrets[5]}\n{secrets[6]} "
                    "(Caused by SSLEOFError('EOF occurred in violation of protocol'))\n"
                ),
            }
        ),
        encoding="utf-8",
    )
    return [sys.executable, str(emitter), str(payload)], secrets


class _Tunnel:
    """A CONNECT transport fault, never a replacement Colab endpoint or TLS peer."""

    def __init__(self, launches: Path, fail_attempts: int) -> None:
        self.launches = launches
        self.fail_attempts = fail_attempts
        self.connections = 0
        self.failures = 0
        self.forwarded = 0
        self.lock = threading.Lock()
        self.upstream = urllib.request.getproxies().get("https", "")
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                owner.handle(self.request)

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> _Tunnel:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def handle(self, client: socket.socket) -> None:
        client.settimeout(20)
        header = b""
        while b"\r\n\r\n" not in header and len(header) < 65_536:
            part = client.recv(4096)
            if not part:
                return
            header += part
        first = header.split(b"\r\n", 1)[0].decode("ascii", errors="replace").split()
        if len(first) != 3 or first[0] != "CONNECT":
            return
        host, port_text = first[1].rsplit(":", 1)
        attempts = len(self.launches.read_text(encoding="utf-8").splitlines())
        with self.lock:
            self.connections += 1
            fail = attempts <= self.fail_attempts
            if fail:
                self.failures += 1
        if fail:
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            return  # EOF during the genuine client's TLS handshake.
        if not host.endswith((".google.com", ".googleapis.com", ".googleusercontent.com")):
            return
        upstream = urllib.parse.urlsplit(self.upstream)
        if self.upstream and upstream.scheme != "http":
            return
        address = (
            (upstream.hostname or host, upstream.port or 80)
            if self.upstream
            else (host, int(port_text))
        )
        try:
            with socket.create_connection(address, timeout=20) as remote:
                if self.upstream:
                    remote.sendall(
                        f"CONNECT {host}:{port_text} HTTP/1.1\r\nHost: {host}:{port_text}\r\n\r\n".encode()
                    )
                    response = b""
                    while b"\r\n\r\n" not in response and len(response) < 65_536:
                        part = remote.recv(4096)
                        if not part:
                            return
                        response += part
                    if b" 200 " not in response.split(b"\r\n", 1)[0]:
                        return
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                with self.lock:
                    self.forwarded += 1
                for peer in (client, remote):
                    peer.setblocking(False)
                while readable := select.select([client, remote], [], [], 20)[0]:
                    for source in readable:
                        data = source.recv(65_536)
                        if not data:
                            return
                        (remote if source is client else client).sendall(data)
        except (OSError, ValueError):
            return  # No raw remote errors, requests, or credentials enter diagnostics.


def _real_cli(directory: Path, *, fail_attempts: int, invalid: bool = False) -> dict[str, Any]:
    real = shutil.which("colab")
    assert real is not None, "real Colab CLI must be installed and authenticated"
    launches = directory / "launches.txt"
    launches.touch()
    executable = directory / "colab"
    executable.write_text(
        f"#!{sys.executable}\nimport os, sys\n"
        f"with open({str(launches)!r}, 'a') as log: log.write('launch\\n')\n"
        f"os.execv({real!r}, [{real!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    with _Tunnel(launches, fail_attempts) as tunnel:
        environment = {
            "PATH": str(directory) + os.pathsep + os.environ.get("PATH", ""),
            "HTTP_PROXY": tunnel.url,
            "HTTPS_PROXY": tunnel.url,
            "http_proxy": tunnel.url,
            "https_proxy": tunnel.url,
            "ALL_PROXY": "",
            "all_proxy": "",
            "NO_PROXY": "",
            "no_proxy": "",
        }
        command = ["colab", "sessions"] + (["--definitely-invalid-option"] if invalid else [])
        result = _run(directory, command, timeout=30, env=environment, check=False)
        result.update(
            launches=len(launches.read_text(encoding="utf-8").splitlines()),
            connections=tunnel.connections,
            injected_eof=tunnel.failures,
            forwarded=tunnel.forwarded,
        )
    return result


class TestBenchmarkColabDiagnostics:
    @pytest.mark.parametrize(
        "mode,check", [("success", True), ("failure", False), ("failure", True)]
    )
    def test_real_process_outputs_do_not_expose_access_bearing_urls(
        self, tmp_path: Path, mode: str, check: bool
    ) -> None:
        command, secrets = _diagnostic_command(tmp_path, mode)
        result = _run(tmp_path, command, check=check, secrets=secrets)
        _receipt(f"diagnostics-{mode}-{check}", result)
        assert result["leaked_labels"] == [], (
            "access-bearing query leaked through public diagnostics"
        )
        assert result["diagnostic_retained"], (
            "ordinary actionable diagnostics must survive redaction"
        )
        assert result["ssl_eof_retained"], "wrapped URL redaction must preserve the transport error"
        if mode == "failure" and check:
            assert not result["returned"], "failed child must remain an error"
        else:
            assert result["returncode"] == (7 if mode == "failure" else 0)

    def test_real_process_timeout_redacts_partial_output_and_command_arguments(
        self, tmp_path: Path
    ) -> None:
        command, secrets = _diagnostic_command(tmp_path, "timeout")
        argument_secret = "FAKE_TEST_TOKEN_TIMEOUT_ARGUMENT_NEVER_REAL"
        command.append(f"/api/contents?colab-runtime-proxy-token={argument_secret}")
        result = _run(tmp_path, command, timeout=1, secrets=[*secrets, argument_secret])
        _receipt("diagnostics-timeout", result)
        assert result["leaked_labels"] == [], (
            "timeout partial output or command leaked a credential"
        )
        assert not result["returned"] and result["timeout_retained"], "timeout must remain explicit"
        assert result["timeout"] == 1, "deadline context must survive sanitization"
        assert result["diagnostic_retained"], "partial diagnostic context must remain available"


@pytest.mark.skipif(
    not os.environ.get("WLH_COLAB_JUPYTER_PYTHON"),
    reason="requires opt-in isolated real Jupyter Server and real installed Colab CLI",
)
class TestBenchmarkColabDownloads:
    @pytest.mark.parametrize("framing", ["length", "chunked"])
    @pytest.mark.parametrize("failures", [2, 100])
    def test_incomplete_download_recovers_or_stops_within_three_attempts(
        self, tmp_path: Path, framing: str, failures: int
    ) -> None:
        with partial_download(tmp_path, framing=framing, failures=failures) as case:
            result = _run(
                tmp_path,
                case["command"],
                env=case["env"],
                check=False,
                secrets=["FAKE_TEST_TOKEN_LOCAL_DOWNLOAD_NEVER_REAL"],
            )
            destination: Path = case["destination"]
            downloaded = destination.read_bytes() if destination.exists() else b""
            result.update(
                case["evidence"],
                launches=len(case["launches"].read_text(encoding="utf-8").splitlines()),
                downloaded_bytes=len(downloaded),
                downloaded_sha256=hashlib.sha256(downloaded).hexdigest(),
                downloaded_rows=len(downloaded.splitlines()),
            )
        _receipt(f"partial-{framing}-{failures}", result)
        assert result["leaked_labels"] == []
        assert result["launches"] == 3 and result["requests"] == 3
        if failures == 2:
            assert result["returned"] and result["returncode"] == 0
            assert result["truncated"] == 2 and result["complete_responses"] == 1
            assert result["downloaded_sha256"] == result["expected_sha256"]
            assert result["downloaded_bytes"] == result["expected_bytes"]
            assert result["downloaded_rows"] == result["expected_rows"]
        else:
            assert result["returned"] and result["returncode"] != 0
            assert result["truncated"] == 3 and result["complete_responses"] == 0
            assert result["partial_transfer_retained"], "real HTTP failure cause must survive"
            assert not destination.exists(), "permanently incomplete download cannot become a file"

    @pytest.mark.parametrize("action", ["new", "exec"])
    def test_nonidempotent_command_does_not_repeat_when_error_mentions_transfer(
        self, tmp_path: Path, action: str
    ) -> None:
        config = tmp_path / "sessions.json"
        config.write_text("{}", encoding="utf-8")
        executable, launches, env = real_cli_launcher(tmp_path, config)
        result = _run(
            tmp_path,
            [str(executable), action, "--invalid-IncompleteRead-option"],
            env=env,
            check=False,
        )
        result["launches"] = len(launches.read_text(encoding="utf-8").splitlines())
        _receipt(f"nonidempotent-{action}", result)
        assert result["returned"] and result["returncode"] != 0
        assert result["partial_transfer_retained"], "real CLI argument diagnostic must carry marker"
        assert result["launches"] == 1, "new and exec must never be retried automatically"


@pytest.mark.skipif(
    os.environ.get("WLH_COLAB_TRANSPORT_REAL") != "1",
    reason="requires opt-in real authenticated read-only Colab sessions; no GPU mutations",
)
class TestBenchmarkColabTransport:
    def test_readonly_inventory_recovers_after_two_real_tls_eofs(self, tmp_path: Path) -> None:
        result = _real_cli(tmp_path, fail_attempts=2)
        _receipt("network-recovery", result)
        assert result["returncode"] == 0, "real readonly sessions must recover on the third attempt"
        assert result["launches"] == 3 and result["injected_eof"] >= 2
        assert result["forwarded"] >= 1, "successful inventory must come from the real service"

    def test_persistent_tls_eof_stops_after_three_cli_attempts(self, tmp_path: Path) -> None:
        result = _real_cli(tmp_path, fail_attempts=100)
        _receipt("network-exhausted", result)
        assert result["launches"] == 3, "transient retries must stop after exactly three attempts"
        assert result["returned"] and result["returncode"] != 0
        assert result["network_failure_retained"], (
            "exhausted retries must retain useful diagnostics"
        )
        assert result["forwarded"] == 0

    def test_invalid_inventory_argument_fails_once_without_network(self, tmp_path: Path) -> None:
        result = _real_cli(tmp_path, fail_attempts=100, invalid=True)
        _receipt("semantic-single-attempt", result)
        assert result["returned"] and result["returncode"] != 0
        assert result["launches"] == 1 and result["connections"] == 0
