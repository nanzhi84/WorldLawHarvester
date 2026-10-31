"""Real HTTP fixture servers shared by source-adapter acceptance tests."""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from wlh.fixtures import FixtureRouter

from .source_replay_support import recorded_redirect

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class _IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


class BridgeFixtureHandler(BaseHTTPRequestHandler):
    primary_netloc: str = ""
    secondary_netloc: str = ""
    router: FixtureRouter | None = None

    def log_message(self, format: str, *args: object) -> None:
        return

    @classmethod
    def _get_router(cls) -> FixtureRouter:
        if cls.router is None:
            cls.router = FixtureRouter(FIXTURES_DIR)
        return cls.router

    def do_GET(self) -> None:
        header_map = dict(self.headers.items())
        req_headers_low = {k.lower(): v for k, v in header_map.items()}
        effective_source_id = ""
        effective_path = self.path
        if effective_path.startswith("/__source__/"):
            rest = effective_path[len("/__source__/") :]
            slash_idx = rest.find("/")
            if slash_idx > 0:
                effective_source_id = rest[:slash_idx]
                effective_path = rest[slash_idx:]
            elif rest:
                effective_source_id = rest
                effective_path = "/"
        header_source_id = req_headers_low.get("x-wlh-source-id", "").strip()
        if header_source_id:
            effective_source_id = header_source_id
        if not effective_source_id:
            self.send_response(400)
            self.end_headers()
            return

        redirect = recorded_redirect(effective_source_id, effective_path, self.primary_netloc)
        if redirect is not None:
            status, location = redirect
            self.send_response(status)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        router = self._get_router()
        rendered = router.resolve_and_render(
            effective_source_id,
            effective_path,
            headers=header_map,
            secondary_netloc=self.secondary_netloc,
        )
        if rendered is None:
            self.send_response(404)
            self.end_headers()
            return
        status, content_type, body = rendered
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def bridge_fixture_servers() -> Iterator[tuple[int, int]]:
    BridgeFixtureHandler.router = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), BridgeFixtureHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    server_v6 = _IPv6ThreadingHTTPServer(("::1", 0), BridgeFixtureHandler)
    port_v6 = server_v6.server_address[1]
    thread_v6 = threading.Thread(target=server_v6.serve_forever, daemon=True)
    thread_v6.start()
    BridgeFixtureHandler.primary_netloc = f"127.0.0.1:{port}"
    BridgeFixtureHandler.secondary_netloc = f"[::1]:{port_v6}"

    try:
        yield port, port_v6
    finally:
        BridgeFixtureHandler.primary_netloc = ""
        BridgeFixtureHandler.secondary_netloc = ""
        BridgeFixtureHandler.router = None
        server.shutdown()
        server_v6.shutdown()
        thread.join(timeout=2.0)
        thread_v6.join(timeout=2.0)
        server.server_close()
        server_v6.server_close()
