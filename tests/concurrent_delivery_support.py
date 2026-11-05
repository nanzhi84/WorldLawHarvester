"""Real HTTP/S3 transport and CLI observations for bounded document delivery."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import boto3  # type: ignore[import-untyped]
import psycopg
import yaml
from psycopg.rows import dict_row

from .conftest import ROOT, make_native_pdf

SOURCE = "dd-993901"
Route = tuple[bytes, str]


def receipt(directory: Path, name: str, value: Any) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps(value, indent=2, default=str))


def fixture_documents(
    directory: Path, count: int = 16
) -> tuple[dict[str, Route], dict[str, list[str]]]:
    routes: dict[str, Route] = {}
    expected: dict[str, list[str]] = {}
    links: list[str] = []
    for index in range(count):
        identity = 0 if index == 1 else index
        clauses = [
            f"Article {number}. The authority shall preserve the complete public legal record, "
            "provide an annual report, and retain every statutory instrument for public access. "
            f"CLAUSE{identity:03d}PART{number:02d}END"
            for number in range(1, 9)
        ]
        title = f"Public Records Regulation {identity:03d}"
        if index >= 12:
            path = f"/laws/record-{index:02d}.pdf"
            target = directory / f"original-{index:02d}.pdf"
            make_native_pdf(target, ["\n\n".join(clauses[:4]), "\n\n".join(clauses[4:])])
            routes[path] = (target.read_bytes(), "application/pdf")
        else:
            path = f"/laws/record-{index:02d}.html"
            body = "".join(f"<p>{clause}</p>" for clause in clauses)
            payload = (
                '<!doctype html><html><head><meta charset="utf-8"></head><body>'
                f"<h1>{title}</h1><article>{body}</article></body></html>"
            ).encode()
            routes[path] = (payload, "text/html; charset=utf-8")
        expected[path] = clauses
        links.append(f'<a href="{path}">{title}</a>')
    routes["/laws/index.html"] = (
        ("<html><body>" + "".join(links) + "</body></html>").encode(),
        "text/html; charset=utf-8",
    )
    receipt(
        directory,
        "originals",
        {
            path: {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
            for path, (payload, _) in routes.items()
        },
    )
    return routes, expected


@contextmanager
def legal_server(routes: dict[str, Route]) -> Generator[tuple[str, list[str]]]:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            requests.append(self.path)
            route = routes.get(self.path)
            payload, media = route or (b"Not found", "text/plain")
            self.send_response(200 if route else 404)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def source_config(directory: Path, origin: str) -> Path:
    config = {
        "id": SOURCE,
        "scheduler": {
            "scope": "Complete finite concurrent CAS delivery acceptance fixture",
            "entry_urls": [origin + "/laws/index.html"],
            "allowed_hosts": ["127.0.0.1"],
            "allowed_path_prefixes": ["/laws/"],
            "concurrency": 16,
            "budget": {"requests": 40, "documents": 20, "seconds": 100},
        },
        "fetcher": {"delay": 0, "retries": 0, "robots": False},
        "processor": {
            "parser": {
                "type": "html",
                "documents": {
                    "detail_url_patterns": [r"/record-\d+\.(html|pdf)$"],
                    "detail_body": "always",
                    "content_xpath": "//article",
                    "binary_title_from_link": True,
                    "fields": {"title": {"xpath": "//h1"}},
                },
            }
        },
        "cleaners": {"require_complete_text": True},
        "storage": {
            "archive_approved": True,
            "metadata": {
                "source_name": "Concurrent CAS delivery acceptance",
                "document_type": "法律法规",
                "language": "en",
                "jurisdiction": "INTL",
            },
        },
    }
    path = directory / f"{SOURCE}.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    return path


def cli_environment(
    env: dict[str, str], endpoint: str, workers: str | None = None
) -> dict[str, str]:
    result = {**os.environ, **env, "WLH_S3_ENDPOINT": endpoint, "PYTHONPATH": str(ROOT / "src")}
    result.pop("WLH_CAS_WORKERS", None)
    if workers is not None:
        result["WLH_CAS_WORKERS"] = workers
    return result


def start_cli(command: str, config: Path, env: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "wlh", command, str(config), "--no-progress"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def finish_cli(
    process: subprocess.Popen[str], directory: Path, name: str, timeout: float = 120
) -> dict[str, Any]:
    stdout, stderr = process.communicate(timeout=timeout)
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        result = {}
    observed: dict[str, Any] = {
        "exit_code": process.returncode,
        "result": result,
        "stdout": stdout,
        "stderr": stderr,
    }
    receipt(directory, name, observed)
    return observed


def delivered(env: dict[str, str]) -> list[dict[str, Any]]:
    with psycopg.connect(env["WLH_DATABASE_URL"]) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            return cursor.execute(
                "SELECT r.id AS resource_id, d.* FROM wlh.document_row d JOIN wlh.resource r "
                "ON r.current_version_id=d.version_id JOIN wlh.frontier f ON f.id=r.frontier_id "
                "WHERE f.source_id=%s ORDER BY d.detail_url, d.row_index",
                (SOURCE,),
            ).fetchall()


def stored_objects(env: dict[str, str]) -> dict[str, bytes]:
    boto3_module: Any = boto3
    client: Any = boto3_module.client(
        "s3",
        endpoint_url=env["WLH_S3_ENDPOINT"],
        aws_access_key_id=env["WLH_S3_ACCESS_KEY"],
        aws_secret_access_key=env["WLH_S3_SECRET_KEY"],
        region_name=env["WLH_S3_REGION"],
    )
    result: dict[str, bytes] = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=env["WLH_S3_BUCKET"]):
        for item in page.get("Contents", []):
            body = client.get_object(Bucket=env["WLH_S3_BUCKET"], Key=item["Key"])["Body"]
            try:
                payload = body.read()
            finally:
                body.close()
            result[hashlib.sha256(payload).hexdigest()] = payload
    return result


class S3Observer:
    """Forward signed requests to real MinIO; record no headers or credentials."""

    def __init__(self, endpoint: str, originals: set[str], mode: str = "delay") -> None:
        self.upstream = urlsplit(endpoint)
        self.originals = originals
        self.mode = mode
        self.lock = threading.Lock()
        self.release = threading.Event()
        self.active = 0
        self.max_active = 0
        self.by_kind: Counter[str] = Counter()
        self.max_by_kind: Counter[str] = Counter()
        self.events: list[dict[str, Any]] = []
        self.injected = 0
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "active": self.active,
                "max_active": self.max_active,
                "by_kind": dict(self.by_kind),
                "max_by_kind": dict(self.max_by_kind),
                "injected": self.injected,
                "events": list(self.events),
            }

    def wait_for_documents(self, minimum: int, timeout: float = 12) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.snapshot()["by_kind"].get("document:PUT", 0) >= minimum:
                return True
            time.sleep(0.025)
        return False

    def __enter__(self) -> S3Observer:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:
                pass

            def transfer(self) -> None:
                match = re.search(r"([0-9a-f]{64})(?:\?|$)", self.path)
                digest = match.group(1) if match else ""
                category = "original" if digest in owner.originals else "document"
                kind = f"{category}:{self.command}" if digest else "control"
                payload = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                with owner.lock:
                    owner.active += 1
                    owner.max_active = max(owner.max_active, owner.active)
                    owner.by_kind[kind] += 1
                    owner.max_by_kind[kind] = max(owner.max_by_kind[kind], owner.by_kind[kind])
                status = 0
                connection: http.client.HTTPConnection | None = None
                try:
                    if digest:
                        time.sleep(0.12)
                    is_document = bool(digest) and category == "document"
                    if is_document and self.command == "PUT" and owner.mode == "hold":
                        owner.release.wait(timeout=18)
                    if is_document and self.command == "PUT" and owner.mode == "fail_put":
                        with owner.lock:
                            owner.injected += 1
                        status = 403
                        body = b"<Error><Code>AccessDenied</Code><Message>acceptance failure</Message></Error>"
                        headers = [("Content-Type", "application/xml")]
                    else:
                        connection = http.client.HTTPConnection(
                            str(owner.upstream.hostname), owner.upstream.port, timeout=25
                        )
                        headers_out = {
                            key: value
                            for key, value in self.headers.items()
                            if key.lower() != "connection"
                        }
                        connection.request(
                            self.command, self.path, body=payload, headers=headers_out
                        )
                        response = connection.getresponse()
                        status, body = response.status, response.read()
                        headers = response.getheaders()
                        if (
                            is_document
                            and self.command == "GET"
                            and owner.mode == "corrupt_get"
                            and status == 200
                        ):
                            body = bytes([body[0] ^ 1]) + body[1:] if body else b"corrupt"
                            with owner.lock:
                                owner.injected += 1
                    self.send_response(status)
                    for key, value in headers:
                        if key.lower() not in {"content-length", "transfer-encoding", "connection"}:
                            self.send_header(key, value)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    if connection is not None:
                        connection.close()
                    with owner.lock:
                        owner.active -= 1
                        owner.by_kind[kind] -= 1
                        owner.events.append(
                            {
                                "method": self.command,
                                "kind": kind,
                                "sha256": digest,
                                "status": status,
                            }
                        )

            do_GET = do_PUT = do_HEAD = do_POST = do_DELETE = transfer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    @property
    def endpoint(self) -> str:
        assert self.server is not None
        return f"http://127.0.0.1:{self.server.server_port}"

    def __exit__(self, *args: object) -> None:
        self.release.set()
        assert self.server is not None and self.thread is not None
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
