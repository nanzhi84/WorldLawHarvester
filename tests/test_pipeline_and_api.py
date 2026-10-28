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
adapter: web
archive_approved: true
scope: "Multi-volume and independent attachment portal test"
entry_urls:
  - "http://127.0.0.1:{port}/portal/index.html"
allowed_hosts:
  - "127.0.0.1"
allowed_path_prefixes:
  - "/portal/"
metadata:
  source_name: "Trade Law Portal"
  document_type: "法律法规"
  language: "en"
  jurisdiction: "VN"
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
""".strip(),
                encoding="utf-8",
            )

            from wlh.domain import snapshots
            from wlh.engine.runs import execute_run, prepare_run

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

                from wlh.projections.canonical import COLUMNS

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
