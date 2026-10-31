"""Black-box contract tests for end-to-end collection, snapshot publication, and read-only HTTP API projections."""

from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from .conftest import make_native_pdf


class _PortalHandler(BaseHTTPRequestHandler):
    pdf_vol1: bytes = b""
    pdf_vol2: bytes = b""
    pdf_annex: bytes = b""

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/portal/index.html":
            body = (
                "<html><body>"
                "<a href='/portal/detail/decree-100.html'>Decree 100/2026</a>"
                "</body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/portal/detail/decree-100.html":
            body = (
                "<html><head><title>Decree 100/2026/ND-CP</title></head><body>"
                "<h1 class='doc-title'>Decree 100/2026/ND-CP on Cross-Border Trade</h1>"
                "<div class='meta'>"
                "<span class='doc-num'>100/2026/ND-CP</span>"
                "<span class='issuer'>Government</span>"
                "<span class='pub-date'>2026-05-01</span>"
                "<span class='eff-date'>2026-07-01</span>"
                "<span class='status'>Effective</span>"
                "</div>"
                "<div class='attachments'>"
                "<a href='/portal/files/decree100_vol1.pdf'>Decree 100 Part 1</a>"
                "<a href='/portal/files/decree100_vol2.pdf'>Decree 100 Part 2</a>"
                "<a href='/portal/files/annex_tariff.pdf'>Tariff Schedule Annex</a>"
                "</div>"
                "</body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/portal/files/decree100_vol1.pdf":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(_PortalHandler.pdf_vol1)))
            self.end_headers()
            self.wfile.write(_PortalHandler.pdf_vol1)
        elif self.path == "/portal/files/decree100_vol2.pdf":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(_PortalHandler.pdf_vol2)))
            self.end_headers()
            self.wfile.write(_PortalHandler.pdf_vol2)
        elif self.path == "/portal/files/annex_tariff.pdf":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(_PortalHandler.pdf_annex)))
            self.end_headers()
            self.wfile.write(_PortalHandler.pdf_annex)
        else:
            self.send_response(404)
            self.end_headers()


class TestCollectionSnapshotAndDeliveryApi:
    @pytest.mark.asyncio
    async def test_multi_volume_and_attachment_source_projects_sealed_snapshot_via_http_api(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """Collecting a multi-volume document with an annex seals a snapshot and serves 16-column and canonical rows."""
        from aiohttp.test_utils import TestClient, TestServer

        _PortalHandler.pdf_vol1 = make_native_pdf(
            tmp_path / "v1.pdf", ["Decree 100/2026 Volume 1: General Provisions and Definitions."]
        ).read_bytes()
        _PortalHandler.pdf_vol2 = make_native_pdf(
            tmp_path / "v2.pdf", ["Decree 100/2026 Volume 2: Customs Procedures and Penalties."]
        ).read_bytes()
        _PortalHandler.pdf_annex = make_native_pdf(
            tmp_path / "annex.pdf", ["Tariff Schedule Annex: Harmonized System Codes 2026."]
        ).read_bytes()

        server = ThreadingHTTPServer(("127.0.0.1", 0), _PortalHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            source_yaml = tmp_path / "dd-902.yaml"
            source_yaml.write_text(
                f"""
id: dd-902
scheduler:
  scope: "Multi-volume and independent attachment portal test"
  entry_urls:
    - "http://127.0.0.1:{port}/portal/index.html"
  allowed_hosts:
    - "127.0.0.1"
  allowed_path_prefixes:
    - "/portal/"
fetcher:
  mode: http
processor:
  parser:
    type: html
    documents:
      detail_url_patterns:
        - "^http://127\\\\.0\\\\.0\\\\.1:{port}/portal/detail/.*\\\\.html$"
      detail_body: "auto"
      fields:
        title:
          xpath: "//h1[@class='doc-title']/text()"
        document_number:
          xpath: "//span[@class='doc-num']/text()"
        issuer:
          xpath: "//span[@class='issuer']/text()"
        published_at:
          xpath: "//span[@class='pub-date']/text()"
        effective_at:
          xpath: "//span[@class='eff-date']/text()"
        status:
          xpath: "//span[@class='status']/text()"
cleaners: {{}}
storage:
  archive_approved: true
  metadata:
    source_name: "Trade Law Portal"
    document_type: "法律法规"
    language: "en"
    jurisdiction: "VN"
""".strip(),
                encoding="utf-8",
            )

            from wlh.scheduler.runs import execute_run, prepare_run
            from wlh.storage import snapshots

            c_run = prepare_run(source_yaml, "collect")
            c_res = execute_run(c_run)
            assert c_res["status"] == "complete", f"collect failed: {c_res}"

            rc_run = prepare_run(source_yaml, "recheck")
            rc_res = execute_run(rc_run)
            assert rc_res["status"] == "complete", f"recheck failed: {rc_res}"

            server.shutdown()
            rp_run = prepare_run(source_yaml, "reparse")
            rp_res = execute_run(rp_run)
            assert rp_res["status"] == "complete", f"reparse failed: {rp_res}"

            snap = snapshots.create()
            snapshot_id = snap["snapshot_id"]
            assert snap["sealed"] is True
            assert snap["resource_count"] == 1
            assert snap["row_count"] == 3

            pub = snapshots.publish(snapshot_id)
            assert pub["published_snapshot_id"] == snapshot_id

            from wlh.api.security import Settings
            from wlh.api.server import application

            token_secret = "test-bearer-token-secret-1234567890"
            token_sha256 = hashlib.sha256(token_secret.encode("utf-8")).hexdigest()
            api_cfg_path = tmp_path / "api.yaml"
            api_cfg_path.write_text(
                f"""
public_base_url: "http://127.0.0.1:8080"
cursor_secret: "cursor-secret-key-for-testing-1234567890"
callers:
  - id: "auditor"
    token_sha256: "{token_sha256}"
    sources: ["dd-902"]
""".strip(),
                encoding="utf-8",
            )
            settings = Settings.load(api_cfg_path)
            app = application(settings, api_cfg_path)

            async with TestClient(TestServer(app)) as client:
                resp_h = await client.get("/healthz")
                assert resp_h.status == 200
                assert await resp_h.json() == {"status": "ok"}

                headers = {"Authorization": f"Bearer {token_secret}"}

                resp_default = await client.get("/results?source_id=dd-902", headers=headers)
                assert resp_default.status == 200
                data_default = await resp_default.json()
                assert data_default["snapshot_id"] == snapshot_id
                items_default = data_default["items"]
                assert len(items_default) == 3

                from wlh.storage.snapshots import COLUMNS

                assert "source_id" in COLUMNS
                assert "jurisdiction" in COLUMNS
                for item in items_default:
                    assert tuple(item.keys()) == COLUMNS
                    assert item["source_id"] == "dd-902"
                    assert item["jurisdiction"] == "VN"
                    for hidden_key in (
                        "resource_id",
                        "version_id",
                        "row_index",
                        "role",
                        "original_media_type",
                        "locator",
                        "extensions",
                    ):
                        assert hidden_key not in item

                vol1, vol2, annex = items_default[0], items_default[1], items_default[2]
                rec8 = vol1["source_record_hash"][:8]
                assert vol1["volume_order"] == 1
                assert vol1["markdown_file"].endswith(
                    f"_{rec8}_{vol1['markdown_hash'][:8]}_01.md"
                )
                assert vol1["original_file"].endswith(
                    f"_{rec8}_{vol1['original_hash'][:8]}_01.pdf"
                )
                assert vol1["doc_number"] == "100/2026/ND-CP"
                assert vol1["issuer"] == "Government"
                assert vol1["published_at"] == "2026-05-01"
                assert vol1["effective_at"] == "2026-07-01"
                assert vol1["status"] == "Effective"
                assert (
                    vol1["source_record_hash"]
                    == vol2["source_record_hash"]
                    == annex["source_record_hash"]
                )

                assert vol2["volume_order"] == 2
                assert vol2["markdown_file"].endswith(
                    f"_{rec8}_{vol2['markdown_hash'][:8]}_02.md"
                )

                assert annex["volume_order"] is None
                assert annex["title"] == "Tariff Schedule Annex"
                assert annex["markdown_file"].endswith(
                    f"_{rec8}_{annex['markdown_hash'][:8]}.md"
                )
                assert annex["doc_number"] is None
                assert annex["issuer"] is None

                resp_can = await client.get(
                    "/results?source_id=dd-902&projection=canonical", headers=headers
                )
                assert resp_can.status == 400
                data_can = await resp_can.json()
                assert data_can == {"error": "invalid_parameters"}

                # Verify keyset cursor pagination still works with streamlined payload
                paged_items: list[dict[str, object]] = []
                cursor = None
                for _ in range(3):
                    url = "/results?source_id=dd-902&limit=1"
                    if cursor:
                        url += f"&cursor={cursor}"
                    resp_p = await client.get(url, headers=headers)
                    assert resp_p.status == 200
                    page_data = await resp_p.json()
                    assert len(page_data["items"]) == 1
                    paged_items.extend(page_data["items"])
                    cursor = page_data["next_cursor"]
                    if cursor is not None:
                        link_header = resp_p.headers.get("Link", "")
                        assert 'rel="next"' in link_header
                        assert "projection=" not in link_header
                assert paged_items == items_default
                assert cursor is None

                for sha_key in ("markdown_hash", "original_hash"):
                    file_sha = vol1[sha_key]
                    resp_f = await client.get(f"/files/{file_sha}", headers=headers)
                    assert resp_f.status == 200
                    body_bytes = await resp_f.read()
                    assert hashlib.sha256(body_bytes).hexdigest() == file_sha
        finally:
            server.shutdown()
            server.server_close()

    def test_snapshot_version_overrides_listing_and_inspection_contracts(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """`snapshots.create`, `snapshots.load`, `snapshots.listing`, and `snapshots.inspect` freeze overridden historical versions, paginate sealed captures, and reject malformed override payloads."""
        import json
        from typing import Any
        from uuid import uuid4

        import psycopg

        from wlh.core import WlhError
        from wlh.scheduler.runs import execute_run, prepare_run
        from wlh.storage import snapshots

        from .conftest import run_wlh_cli

        state = {"edition": "v1"}
        port_holder: list[int] = []

        class _VersionedHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                port = port_holder[0]
                if self.path == "/laws/index.html":
                    body = (
                        f'<html><body><a href="http://127.0.0.1:{port}/laws/statute-1.html">'
                        f"Statute 1 ({state['edition']})</a></body></html>"
                    ).encode("utf-8")
                elif self.path == "/laws/statute-1.html":
                    if state["edition"] == "v1":
                        body = (
                            "<html><body>"
                            '<h1 class="doc-title">Statute 1 — Original 2025 Edition</h1>'
                            '<span class="doc-no">Act 2025/01</span>'
                            '<div class="law-body"><p>Article 1. Initial statutory provisions enacted in 2025 governing digital archives and public records.</p></div>'
                            "</body></html>"
                        ).encode("utf-8")
                    else:
                        body = (
                            "<html><body>"
                            '<h1 class="doc-title">Statute 1 — Amended 2026 Edition</h1>'
                            '<span class="doc-no">Act 2026/01</span>'
                            '<div class="law-body"><p>Article 1. Amended statutory provisions enacted in 2026 expanding cross-border electronic evidence admissibility.</p></div>'
                            "</body></html>"
                        ).encode("utf-8")
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _VersionedHandler)
        port = srv.server_address[1]
        port_holder.append(port)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()

        try:
            source_yaml = tmp_path / "dd-907.yaml"
            source_yaml.write_text(
                f"""
id: dd-907
scheduler:
  scope: "Snapshot version overrides contract test"
  entry_urls:
    - "http://127.0.0.1:{port}/laws/index.html"
  allowed_hosts:
    - "127.0.0.1"
  allowed_path_prefixes:
    - "/laws/"
fetcher:
  mode: http
  delay: 0.0
  robots: false
processor:
  parser:
    type: html
    documents:
      detail_url_patterns:
        - "^http://127\\\\.0\\\\.0\\\\.1:{port}/laws/statute-\\\\d+\\\\.html$"
      detail_body: always
      content_xpath: '//div[@class="law-body"]'
      fields:
        title:
          xpath: '//h1[@class="doc-title"]'
        document_number:
          xpath: '//span[@class="doc-no"]'
        issuer:
          value: "Legislature"
storage:
  archive_approved: true
  metadata:
    source_name: "Versioned Statute Portal"
    document_type: "法律法规"
    language: "en"
    jurisdiction: "INTL"
""".strip(),
                encoding="utf-8",
            )

            r1 = prepare_run(source_yaml, "collect")
            assert execute_run(r1)["status"] == "complete"

            with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as conn:
                row_v1 = conn.execute(
                    "SELECT id, current_version_id FROM wlh.resource"
                ).fetchone()
                assert row_v1 is not None
                resource_id = str(row_v1[0])
                version_v1 = str(row_v1[1])

            state["edition"] = "v2"
            r2 = prepare_run(source_yaml, "collect")
            assert execute_run(r2)["status"] == "complete"

            with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as conn:
                row_v2 = conn.execute(
                    "SELECT current_version_id FROM wlh.resource WHERE id = %s",
                    (resource_id,),
                ).fetchone()
                assert row_v2 is not None
                version_v2 = str(row_v2[0])
            assert version_v2 != version_v1

            snap_default = snapshots.create()
            insp_default = snapshots.inspect(snap_default["snapshot_id"])
            assert insp_default["items"][0]["version_id"] == version_v2

            overrides_file = tmp_path / "overrides.json"
            overrides_file.write_text(
                json.dumps(
                    {
                        "overrides": [
                            {"resource_id": resource_id, "version_id": version_v1}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            rc, out, err = run_wlh_cli(
                "snapshot",
                "create",
                "--version-overrides",
                str(overrides_file),
                env=initialized_db,
            )
            assert rc == 0, f"snapshot create --version-overrides failed: {out} {err}"
            snap_override: dict[str, Any] = json.loads(out)
            override_snap_id = str(snap_override["snapshot_id"])

            rc_i, out_i, err_i = run_wlh_cli(
                "snapshot",
                "inspect",
                override_snap_id,
                "--limit",
                "10",
                env=initialized_db,
            )
            assert rc_i == 0, f"snapshot inspect failed: {out_i} {err_i}"
            insp_override: dict[str, Any] = json.loads(out_i)
            assert insp_override["items"][0]["resource_id"] == resource_id
            assert insp_override["items"][0]["version_id"] == version_v1

            rc_l, out_l, err_l = run_wlh_cli(
                "snapshot", "list", "--limit", "1", env=initialized_db
            )
            assert rc_l == 0, f"snapshot list failed: {out_l} {err_l}"
            list_page1: dict[str, Any] = json.loads(out_l)
            assert len(list_page1["snapshots"]) == 1
            assert list_page1["next_after"] is not None
            list_page2 = snapshots.listing(limit=1, after=str(list_page1["next_after"]))
            assert len(list_page2["snapshots"]) == 1
            assert (
                list_page2["snapshots"][0]["id"]
                != list_page1["snapshots"][0]["id"]
            )

            dup_file = tmp_path / "dup-overrides.json"
            dup_file.write_text(
                json.dumps(
                    {
                        "overrides": [
                            {"resource_id": resource_id, "version_id": version_v1},
                            {"resource_id": resource_id, "version_id": version_v2},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with pytest.raises(WlhError) as exc_dup:
                snapshots.load(dup_file)
            assert exc_dup.value.code == "duplicate_override_resource"

            with pytest.raises(WlhError) as exc_ver:
                snapshots.create(
                    [{"resource_id": resource_id, "version_id": str(uuid4())}]
                )
            assert exc_ver.value.code == "invalid_override_version"
        finally:
            srv.shutdown()
            thread.join(timeout=2.0)
            srv.server_close()

    @pytest.mark.asyncio
    async def test_procrastinate_queue_enqueues_recovers_unqueued_runs_and_executes_worker_once(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """`prepare_run(..., enqueue=True)` defers a Procrastinate job, `recover_unqueued` / `recover_stalled` restores missing `job_id`s, and `worker(once=True)` executes the queued crawl job to completion."""
        import psycopg

        from wlh.scheduler.queue import app, recover_stalled, recover_unqueued
        from wlh.scheduler.runs import prepare_run, receipt

        from .conftest import run_wlh_cli

        port_holder: list[int] = []

        class _QueueOriginHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                port = port_holder[0]
                if self.path == "/laws/index.html":
                    body = (
                        f'<html><body><a href="http://127.0.0.1:{port}/laws/item-1.html">'
                        "Queued Statute 2026</a></body></html>"
                    ).encode("utf-8")
                elif self.path == "/laws/item-1.html":
                    body = (
                        "<html><body>"
                        '<h1 class="doc-title">Queued Statute 2026</h1>'
                        '<span class="doc-no">Q-2026-01</span>'
                        '<div class="law-body"><p>Section 1. Enqueued worker execution contract verification across persistent PostgreSQL job queue.</p></div>'
                        "</body></html>"
                    ).encode("utf-8")
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _QueueOriginHandler)
        port = srv.server_address[1]
        port_holder.append(port)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()

        try:
            source_yaml = tmp_path / "dd-908.yaml"
            source_yaml.write_text(
                f"""
id: dd-908
scheduler:
  scope: "Procrastinate queue and worker contract test"
  entry_urls:
    - "http://127.0.0.1:{port}/laws/index.html"
  allowed_hosts:
    - "127.0.0.1"
  allowed_path_prefixes:
    - "/laws/"
fetcher:
  mode: http
  delay: 0.0
  robots: false
processor:
  parser:
    type: html
    documents:
      detail_url_patterns:
        - "^http://127\\\\.0\\\\.0\\\\.1:{port}/laws/item-\\\\d+\\\\.html$"
      detail_body: always
      content_xpath: '//div[@class="law-body"]'
      fields:
        title:
          xpath: '//h1[@class="doc-title"]'
        document_number:
          xpath: '//span[@class="doc-no"]'
        issuer:
          value: "Queue Commission"
storage:
  archive_approved: true
  metadata:
    source_name: "Queued Portal"
    document_type: "法律法规"
    language: "en"
    jurisdiction: "INTL"
""".strip(),
                encoding="utf-8",
            )

            run_id = prepare_run(source_yaml, "collect", enqueue=False)
            with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as conn:
                r_row = conn.execute(
                    "SELECT job_id, status FROM wlh.run WHERE id = %s", (run_id,)
                ).fetchone()
                assert r_row is not None
                assert r_row[0] is None
                assert r_row[1] == "pending"

            recover_unqueued()
            with psycopg.connect(initialized_db["WLH_DATABASE_URL"], autocommit=True) as conn:
                r_row2 = conn.execute(
                    "SELECT job_id FROM wlh.run WHERE id = %s", (run_id,)
                ).fetchone()
                assert r_row2 is not None
                job_id = r_row2[0]
                assert job_id is not None
                stalled_worker_row = conn.execute(
                    "INSERT INTO public.procrastinate_workers (last_heartbeat) "
                    "VALUES (now() - interval '120 seconds') RETURNING id"
                ).fetchone()
                assert stalled_worker_row is not None
                conn.execute(
                    "UPDATE public.procrastinate_jobs SET status = 'doing', worker_id = %s WHERE id = %s",
                    (stalled_worker_row[0], job_id),
                )

            async with app.open_async():
                stalled_res = await recover_stalled(0)
                assert stalled_res == {"recovered": 1}

            with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as conn:
                j_row = conn.execute(
                    "SELECT status::text FROM public.procrastinate_jobs WHERE id = %s", (job_id,)
                ).fetchone()
                assert j_row is not None
                assert j_row[0] == "todo"

            rc, out, err = run_wlh_cli("worker", "--once", env=initialized_db)
            assert rc == 0, f"wlh worker --once failed: {out} {err}"

            final_receipt = receipt(run_id)
            assert final_receipt["status"] == "complete", f"Worker run did not complete: {final_receipt}"
            assert final_receipt["summary"]["counts"]["resources"] == 1
        finally:
            srv.shutdown()
            thread.join(timeout=2.0)
            srv.server_close()

