"""Black-box contract tests for controlled HTTP acquisition, redirect chains, and offline session replay."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import psycopg


class _RedirectAndPolicyHandler(BaseHTTPRequestHandler):
    request_count = 0

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        _RedirectAndPolicyHandler.request_count += 1
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /laws/\nDisallow: /private/\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/laws/old-entry":
            self.send_response(302)
            self.send_header("Location", "/laws/final-act")
            self.end_headers()
        elif self.path == "/laws/final-act":
            body = (
                "<html><head><title>Law 2026-01</title></head>"
                "<body><h1>Law 2026-01 on Digital Archives</h1>"
                "<p>Article 1. All public legal documents shall be archived in S3 CAS.</p>"
                "</body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


class TestHttpAcquisitionAndOfflineReplay:
    def test_redirected_entry_records_acquisition_chain_and_reparses_offline_without_network(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """A 302 redirect chain is recorded in `wlh.acquisition` and replayed offline with zero external requests."""
        _RedirectAndPolicyHandler.request_count = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectAndPolicyHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            source_yaml = tmp_path / "dd-901.yaml"
            source_yaml.write_text(
                f"""
id: dd-901
scheduler:
  scope: "Redirect and offline reparse test"
  entry_urls:
    - "http://127.0.0.1:{port}/laws/old-entry"
  allowed_hosts:
    - "127.0.0.1"
  allowed_path_prefixes:
    - "/laws/"
fetcher:
  mode: http
  robots: true
processor:
  parser:
    type: html
    documents:
      detail_url_patterns:
        - "^http://127\\\\.0\\\\.0\\\\.1:{port}/laws/final-act$"
      detail_body: "always"
cleaners: {{}}
storage:
  archive_approved: true
  metadata:
    source_name: "Test Legal Portal"
    document_type: "法律法规"
    language: "en"
    jurisdiction: "US"
""".strip(),
                encoding="utf-8",
            )

            from wlh.scheduler.runs import execute_run, prepare_run

            collect_run_id = prepare_run(source_yaml, "collect")
            collect_res = execute_run(collect_run_id)
            assert collect_res["status"] == "complete", f"collect failed: {collect_res}"
            assert _RedirectAndPolicyHandler.request_count >= 2

            with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as conn:
                acqs = conn.execute(
                    """
                    SELECT id, role, request_url, actual_url, http_status,
                           parent_acquisition_id, parent_relation, archive_ref, state
                    FROM wlh.acquisition
                    WHERE run_id = %s
                    ORDER BY started_at
                    """,
                    (collect_run_id,),
                ).fetchall()
                assert any(r[1] == "policy" for r in acqs), "Expected robots.txt policy acquisition"
                redirect_child = [r for r in acqs if r[6] == "redirect"]
                assert redirect_child, f"Expected redirect child acquisition in {acqs}"
                assert redirect_child[0][5] is not None
                assert redirect_child[0][8] == "archived"

            server.shutdown()
            requests_before_reparse = _RedirectAndPolicyHandler.request_count

            reparse_run_id = prepare_run(source_yaml, "reparse")
            reparse_res = execute_run(reparse_run_id)
            assert reparse_res["status"] == "complete", f"reparse failed: {reparse_res}"
            assert _RedirectAndPolicyHandler.request_count == requests_before_reparse
            report_obj: dict[str, Any] = reparse_res.get("report") or {}
            report_counts: dict[str, Any] = report_obj.get("counts") or {}
            assert report_counts.get("external_requests", 0) == 0
        finally:
            server.shutdown()
            server.server_close()
