"""Real HTTP fixture servers shared by source-adapter acceptance tests."""

from __future__ import annotations

import io
import socket
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from wlh.fixtures import FixtureRouter

from .conftest import make_native_pdf
from .source_replay_support import recorded_redirect

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _make_rii_zip_bytes(
    doknr: str = "JURE100055033",
    aktenzeichen: str = "VIII ZR 12/25",
    ecli: str = "ECLI:DE:BGH:2026:150426UVIIIZR12.25.0",
    titelzeile: str = "Kaufrechtliche Gewährleistung bei digitalen Produkten",
) -> bytes:
    buf = io.BytesIO()
    xml_payload = f"""<?xml version="1.0" encoding="UTF-8"?>
<dokument>
  <doknr>{doknr}</doknr>
  <ecli>{ecli}</ecli>
  <gertyp>BGH</gertyp>
  <entsch-datum>20260415</entsch-datum>
  <aktenzeichen>{aktenzeichen}</aktenzeichen>
  <titelzeile>{titelzeile}</titelzeile>
  <tenor><p>Die Revision der Beklagten gegen das Urteil des Oberlandesgerichts wird zurückgewiesen.</p></tenor>
  <entscheidungsgruende><p>Die zulässige Revision hat in der Sache keinen Erfolg. Der Kläger hat Anspruch auf Schadensersatz.</p></entscheidungsgruende>
</dokument>""".encode("utf-8")
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{doknr}.xml", xml_payload)
    return buf.getvalue()


def _make_fixture_zip_bytes(arcname: str, fixture_rel: str) -> bytes:
    fixture_path = FIXTURES_DIR / fixture_rel
    if not fixture_path.exists():
        return b""
    buf = io.BytesIO()
    xml_bytes = fixture_path.read_bytes()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(arcname, xml_bytes)
    return buf.getvalue()


class _IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


class BridgeFixtureHandler(BaseHTTPRequestHandler):
    sample_pdf: bytes = b""
    scotus_pdf_1: bytes = b""
    scotus_pdf_2: bytes = b""
    primary_netloc: str = ""
    secondary_netloc: str = ""
    active_source_id: str = ""
    rii_zip: bytes = _make_rii_zip_bytes(
        "JURE100055033", "VIII ZR 12/25", "ECLI:DE:BGH:2026:150426UVIIIZR12.25.0"
    )
    rii_zip_2: bytes = _make_rii_zip_bytes(
        "JURE100055034",
        "VIII ZR 13/25",
        "ECLI:DE:BGH:2026:150426UVIIIZR13.25.0",
        titelzeile="Mietrechtliche Kündigung wegen Zahlungsverzugs",
    )
    gg_zip: bytes = _make_fixture_zip_bytes("BJNR000010949.xml", "dd-550/BJNR000010949.xml")
    bgb_zip: bytes = _make_fixture_zip_bytes("BJNR001950896.xml", "dd-550/BJNR001950896.xml")
    usc01_zip: bytes = _make_fixture_zip_bytes("usc01.xml", "dd-558/usc01.xml")
    usc02_zip: bytes = _make_fixture_zip_bytes("usc02.xml", "dd-558/usc02.xml")
    router: FixtureRouter | None = None

    def log_message(self, format: str, *args: object) -> None:
        return

    @classmethod
    def _get_router(cls) -> FixtureRouter:
        if cls.router is None:
            cls.router = FixtureRouter(
                FIXTURES_DIR,
                binary_providers={
                    "sample_pdf": cls.sample_pdf,
                    "scotus_pdf_1": cls.scotus_pdf_1,
                    "scotus_pdf_2": cls.scotus_pdf_2,
                    "rii_zip": cls.rii_zip,
                    "rii_zip_2": cls.rii_zip_2,
                    "gg_zip": cls.gg_zip,
                    "bgb_zip": cls.bgb_zip,
                    "usc01_zip": cls.usc01_zip,
                    "usc02_zip": cls.usc02_zip,
                },
            )
        return cls.router

    def do_GET(self) -> None:
        redirect = recorded_redirect(self.active_source_id, self.path, self.primary_netloc)
        if redirect is not None:
            status, location = redirect
            self.send_response(status)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        router = self._get_router()
        rendered = router.resolve_and_render(
            self.active_source_id,
            self.path,
            headers=dict(self.headers.items()),
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
def bridge_fixture_servers(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[int, int]]:
    pdf_dir = tmp_path_factory.mktemp("bridge_pdfs")
    BridgeFixtureHandler.sample_pdf = make_native_pdf(
        pdf_dir / "sample.pdf",
        ["Official Legal Document Full Text Body Content for Verification."],
    ).read_bytes()
    BridgeFixtureHandler.scotus_pdf_1 = make_native_pdf(
        pdf_dir / "scotus1.pdf",
        [
            "SUPREME COURT OF THE UNITED STATES No. 26A388 PEOPLE NOT POLITICIANS, ET AL. v. ROBERT ONDER, ET AL. ON APPLICATION FOR STAY [September 25, 2026] PER CURIAM. The application for stay presented to the Court is denied."
        ],
    ).read_bytes()
    BridgeFixtureHandler.scotus_pdf_2 = make_native_pdf(
        pdf_dir / "scotus2.pdf",
        [
            "SUPREME COURT OF THE UNITED STATES Syllabus WEST VIRGINIA ET AL. v. B. P. J., BY HER NEXT FRIEND AND MOTHER, HEATHER JACKSON CERTIORARI TO THE UNITED STATES COURT OF APPEALS FOR THE FOURTH CIRCUIT No. 24–43. Argued January 13, 2026—Decided June 30, 2026 Held: The judgment of the Court of Appeals is reversed."
        ],
    ).read_bytes()
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
        BridgeFixtureHandler.active_source_id = ""
        BridgeFixtureHandler.router = None
        server.shutdown()
        server_v6.shutdown()
        thread.join(timeout=2.0)
        thread_v6.join(timeout=2.0)
        server.server_close()
        server_v6.server_close()
