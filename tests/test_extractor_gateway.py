"""Stage 3b extractor gateway contracts: magic sniffing, engine registry routing, quirk stage hooks."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from tests.office_fixture_support import (
    build_biff8_xls_bytes,
    build_msdoc_bytes,
    build_ruled_table_scan_pdf,
    markdown_table_rows,
)
from wlh.core import WlhError

ACCEPTED_ROUTES = {"accept", "accept_ocr", "accept_with_warning"}
LOCAL_ENGLISH_OCR: dict[str, Any] = {
    "engine": "local",
    "languages": "eng",
    "model": "br-ocr-v1",
    "timeout_seconds": 60,
    "poll_seconds": 0.2,
    "max_pages": 10,
    "dpi": 150,
    "max_page_pixels": 20000000,
    "max_result_bytes": 16777216,
}
TCVN3_LINE = (
    "Lu\xcbt Doanh nghi\xd6p s\xe8 59/2020/QH14 \xa7i\xd2u 1 Ph\xb5m vi \xaei\xd2u ch\xd8nh"
)
TCVN3_RTF = (
    b"{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 .VnTime;}}\n"
    b"\\f0\\fs24 Lu\\'cbt Doanh nghi\\'d6p s\\'e8 59/2020/QH14 \\'a7i\\'d2u 1 Ph\\'b5m vi "
    b"\\'aei\\'d2u ch\\'d8nh\\par}"
)
PLAIN_RTF = (
    b"{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Times New Roman;}}\n"
    b"\\f0\\fs24 MINISTRY OF FINANCE CIRCULAR 2026 ON CORPORATE TAX\\par\n"
    b"Article 1. The standard corporate tax rate is twenty percent.\\par}"
)


def _docx_bytes(*paragraphs: str) -> bytes:
    from docx import Document  # type: ignore[import-untyped]

    word_doc: Any = Document()
    for text in paragraphs:
        word_doc.add_paragraph(text)
    buf = io.BytesIO()
    word_doc.save(buf)
    return buf.getvalue()


def _xlsx_bytes(rows: list[list[Any]]) -> bytes:
    import openpyxl  # type: ignore[import-untyped]

    workbook: Any = openpyxl.Workbook()
    sheet: Any = workbook.active
    sheet.title = "Schedule"
    for row in rows:
        sheet.append(row)
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


def _plain_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ruling.json", json.dumps({"title": "Ruling 791"}))
        archive.writestr("notes.txt", "Plain attachment text.")
    return buf.getvalue()


def _routing_registry(first_error_code: str, calls: list[str]) -> Any:
    """A fresh registry with two custom `rtf` engines; the first always raises ``first_error_code``."""
    from wlh.processor.extractors.base import EngineAdapter
    from wlh.processor.extractors.registry import ExtractorRegistry

    class FailingProbeEngine(EngineAdapter):
        kind = "rtf"
        engine_name = "failing_probe"
        parser_tag = "office"

        def extract(self, inp: Any, opts: Any, hooks: Any) -> tuple[str, dict[str, Any]]:
            calls.append(self.engine_name)
            raise WlhError(first_error_code)

    class WorkingProbeEngine(EngineAdapter):
        kind = "rtf"
        engine_name = "working_probe"
        parser_tag = "office"

        def extract(self, inp: Any, opts: Any, hooks: Any) -> tuple[str, dict[str, Any]]:
            calls.append(self.engine_name)
            return "Article 1. Working probe engine produced this statutory text.\n", {}

    registry: Any = ExtractorRegistry()
    registry.register(FailingProbeEngine())
    registry.register(WorkingProbeEngine())
    return registry


class TestRealFormatSniffing:
    def test_disguised_suffixes_resolve_to_real_container_format(self, tmp_path: Path) -> None:
        """Bytes are classified by magic/structure regardless of a misleading suffix hint."""
        from tests.conftest import make_native_pdf
        from wlh.processor.extractors.magic import detect_real_format

        native_pdf = make_native_pdf(tmp_path / "notice.pdf", ["Public notice body text."])
        cases: list[tuple[bytes, str, str]] = [
            (native_pdf.read_bytes(), ".doc", "pdf"),
            (_docx_bytes("Decree body text."), ".doc", "docx"),
            (_xlsx_bytes([["A", "B"], [1, 2]]), ".docx", "xlsx"),
            (_plain_zip_bytes(), ".docx", "zip"),
            (build_msdoc_bytes("Circular 2026 body text.\r"), ".xls", "doc"),
            (build_biff8_xls_bytes("Sheet1", {(0, 0): "A", (0, 1): 1.0}), ".doc", "xls"),
            (PLAIN_RTF, ".doc", "rtf"),
            (b"<!DOCTYPE html><html><body><p>Notice</p></body></html>", ".doc", "html"),
            (b'<?xml version="1.0"?><law><title>Act</title></law>', ".html", "xml"),
            (b'{"title": "Ruling", "body": "Text"}', ".doc", "json"),
            ("Plain statutory text in UTF-8: Điều 1.".encode(), ".pdf", "txt"),
            (b"\x00\x01\x02\x03binary-blob\x00\xff\xfe", ".doc", "unknown"),
        ]
        for data, suffix, expected in cases:
            assert detect_real_format(data, suffix) == expected, (suffix, expected)

    def test_kind_constants_and_package_reexport_match_gateway_contract(self) -> None:
        """`magic` exposes the container classification constants and is re-exported at package root."""
        from wlh.processor import extractors
        from wlh.processor.extractors import magic

        assert magic.TEXT_KINDS == {"html", "xml", "json", "txt"}
        assert magic.OFFICE_KINDS == {"doc", "docx", "rtf", "xls", "xlsx"}
        assert magic.BINARY_KINDS == magic.OFFICE_KINDS | {"pdf", "zip"}
        assert magic.ZIP_MEMBER_KINDS == magic.OFFICE_KINDS | {"pdf", "txt", "html", "json"}
        assert extractors.detect_real_format is magic.detect_real_format


class TestDefaultExtractorRegistry:
    def test_default_registry_is_singleton_with_contract_fallback_chains(self) -> None:
        """The process-wide default registry declares the per-kind engine fallback chains."""
        from wlh.processor.extractors.registry import default_registry

        registry: Any = default_registry()
        assert default_registry() is registry
        assert registry.chain("doc") == ["pure_python_ole2_msdoc", "msdoc_embedded_image_ocr"]
        assert registry.chain("docx") == ["python_docx", "docx_media_pdf_ocr"]
        assert registry.chain("rtf") == ["rtf_direct_text", "striprtf_direct"]
        assert registry.chain("xlsx") == ["openpyxl"]
        assert registry.chain("xls") == ["xlrd"]
        for kind in ("pdf", "html", "xml", "json", "txt", "zip"):
            assert len(registry.chain(kind)) == 1, kind
        assert {
            "doc",
            "docx",
            "rtf",
            "xlsx",
            "xls",
            "pdf",
            "html",
            "xml",
            "json",
            "txt",
            "zip",
        } <= set(registry.kinds())

    def test_text_doc_dispatch_uses_primary_engine_and_attaches_accepted_text_check(self) -> None:
        """A text `.doc` is served by the pure-Python primary engine with a `text_check` quality report."""
        from wlh.processor.extractors.base import ExtractionInput, ExtractionOptions
        from wlh.processor.extractors.registry import default_registry

        raw = build_msdoc_bytes(
            "MINISTRY OF FINANCE CIRCULAR 2026 ON CORPORATE TAX\r"
            "Article 1. The standard corporate tax rate is twenty percent.\r"
        )
        out: Any = default_registry().dispatch(
            ExtractionInput(data=raw, kind="doc"), ExtractionOptions()
        )
        assert "MINISTRY OF FINANCE CIRCULAR 2026" in out.markdown
        assert out.info["extractor"] == "pure_python_ole2_msdoc"
        assert out.info["detected_format"] == "doc"
        assert out.quality["recommended_route"] in ACCEPTED_ROUTES
        assert isinstance(out.parsers, list)

    def test_disguised_html_named_doc_dispatch_is_calibrated_to_html(self) -> None:
        """An HTML payload announced as `doc` is re-routed to the HTML engine by magic sniffing."""
        from wlh.processor.extractors.base import ExtractionInput, ExtractionOptions
        from wlh.processor.extractors.registry import default_registry

        registry: Any = default_registry()
        html = (
            b"<html><body><h1>Circular 01/2026/TT-BTC</h1>"
            b"<p>Tax regulation body text for all enterprises.</p></body></html>"
        )
        out: Any = registry.dispatch(ExtractionInput(data=html, kind="doc"), ExtractionOptions())
        assert "Circular 01/2026/TT-BTC" in out.markdown
        assert out.info["detected_format"] == "html"
        assert out.info["extractor"] == registry.chain("html")[0]

    def test_office_kinds_dispatch_report_engine_that_produced_markdown(self) -> None:
        """docx/xlsx/xls go to their primary engines; a non-TCVN3 RTF falls back to the striprtf engine."""
        from wlh.processor.extractors.base import ExtractionInput, ExtractionOptions
        from wlh.processor.extractors.registry import default_registry

        registry: Any = default_registry()
        cases: list[tuple[bytes, str, str, str]] = [
            (
                _docx_bytes(
                    "DECREE 15/2026 ON ADMINISTRATIVE PROCEDURES",
                    "Article 1. Agencies shall publish procedures online.",
                ),
                "docx",
                "python_docx",
                "DECREE 15/2026",
            ),
            (
                _xlsx_bytes([["Category", "Rate"], ["Standard", 20]]),
                "xlsx",
                "openpyxl",
                "| Category | Rate |",
            ),
            (
                build_biff8_xls_bytes(
                    "Schedule",
                    {(0, 0): "Category", (0, 1): "Rate", (1, 0): "Standard", (1, 1): 20.0},
                ),
                "xls",
                "xlrd",
                "| Category | Rate |",
            ),
            (PLAIN_RTF, "rtf", "striprtf_direct", "MINISTRY OF FINANCE CIRCULAR 2026"),
        ]
        for data, kind, engine, snippet in cases:
            out: Any = registry.dispatch(ExtractionInput(data=data, kind=kind), ExtractionOptions())
            assert snippet in out.markdown, kind
            assert out.info["extractor"] == engine, kind
            assert out.info["detected_format"] == kind
            assert out.quality["recommended_route"] in ACCEPTED_ROUTES, kind

    def test_preferred_engine_on_tcvn3_rtf_bypasses_primary_engine(self) -> None:
        """`preferred_engine` pins dispatch to exactly the named engine even when the primary would succeed."""
        from wlh.processor.extractors.base import ExtractionInput, ExtractionOptions
        from wlh.processor.extractors.registry import default_registry

        registry: Any = default_registry()
        inp = ExtractionInput(data=TCVN3_RTF, kind="rtf")
        primary: Any = registry.dispatch(inp, ExtractionOptions(quirks=("vi_tcvn3",)))
        assert primary.info["extractor"] == "rtf_direct_text"
        pinned: Any = registry.dispatch(
            inp, ExtractionOptions(quirks=("vi_tcvn3",), preferred_engine="striprtf_direct")
        )
        assert pinned.info["extractor"] == "striprtf_direct"

    @pytest.mark.parametrize("container", ["doc", "rtf"])
    def test_tcvn3_document_dispatch_recovers_vietnamese_and_records_legacy_encoding(
        self, container: str
    ) -> None:
        """A TCVN3 `.doc` / `.rtf` dispatched with `vi_tcvn3` is recovered after extraction and leaves a `tcvn3` trace."""
        from wlh.processor.extractors.base import ExtractionInput, ExtractionOptions
        from wlh.processor.extractors.registry import default_registry

        data = build_msdoc_bytes(TCVN3_LINE + "\r") if container == "doc" else TCVN3_RTF
        out: Any = default_registry().dispatch(
            ExtractionInput(data=data, kind=container),
            ExtractionOptions(profile="vi", quirks=("vi_tcvn3",)),
        )
        assert "Luật Doanh nghiệp" in out.markdown
        assert out.info["legacy_encoding"] == "tcvn3"
        assert "tcvn3" in out.parsers
        assert out.info["detected_format"] == container

    def test_scanned_pdf_with_ruled_table_dispatch_restores_markdown_table_with_accepted_route(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An image-only PDF holding a ruled table is OCR'd locally into a Markdown pipe table that passes the quality gate."""
        from wlh.processor.extractors.base import ExtractionInput, ExtractionOptions
        from wlh.processor.extractors.registry import default_registry

        monkeypatch.delenv("WLH_BR_OCR_URL", raising=False)
        pdf_path = build_ruled_table_scan_pdf(tmp_path / "scanned_ruled_table.pdf")
        registry: Any = default_registry()
        out: Any = registry.dispatch(
            ExtractionInput(data=pdf_path.read_bytes(), kind="pdf", path=pdf_path),
            ExtractionOptions(ocr=LOCAL_ENGLISH_OCR),
        )
        upper = out.markdown.upper()
        assert "TREASURY DECISION 9988" in upper
        assert "| --- |" in out.markdown
        assert any(
            any("FORM 1120" in cell.upper() for cell in row)
            for row in markdown_table_rows(out.markdown)
        )
        assert "15%" in out.markdown
        assert out.quality["recommended_route"] in ACCEPTED_ROUTES
        assert out.info["detected_format"] == "pdf"


class TestExtractorRegistryRouting:
    def test_registration_order_defines_chain_and_kinds(self) -> None:
        """Engines registered for a kind form its fallback chain in registration order."""
        registry = _routing_registry("rtf_tcvn3_not_matched", [])
        assert registry.chain("rtf") == ["failing_probe", "working_probe"]
        assert set(registry.kinds()) == {"rtf"}

    def test_recoverable_error_in_first_engine_falls_back_to_next_engine(self) -> None:
        """A recoverable code for the kind makes dispatch continue with the next engine in the chain."""
        from wlh.processor.extractors.base import ExtractionInput, ExtractionOptions

        calls: list[str] = []
        registry = _routing_registry("rtf_tcvn3_not_matched", calls)
        out: Any = registry.dispatch(
            ExtractionInput(data=PLAIN_RTF, kind="rtf"), ExtractionOptions()
        )
        assert calls == ["failing_probe", "working_probe"]
        assert "Working probe engine produced this statutory text." in out.markdown
        assert out.info["extractor"] == "working_probe"
        assert out.quality["recommended_route"] in ACCEPTED_ROUTES

    def test_non_recoverable_error_in_first_engine_propagates_without_fallback(self) -> None:
        """An error code outside the kind's recoverable set aborts dispatch with that code."""
        from wlh.processor.extractors.base import ExtractionInput, ExtractionOptions

        calls: list[str] = []
        registry = _routing_registry("probe_hard_failure", calls)
        with pytest.raises(WlhError) as excinfo:
            registry.dispatch(ExtractionInput(data=PLAIN_RTF, kind="rtf"), ExtractionOptions())
        assert excinfo.value.code == "probe_hard_failure"
        assert calls == ["failing_probe"]

    def test_preferred_engine_runs_only_that_engine(self) -> None:
        """`preferred_engine` executes exactly one engine: no fallback before or after it."""
        from wlh.processor.extractors.base import ExtractionInput, ExtractionOptions

        calls: list[str] = []
        registry = _routing_registry("rtf_tcvn3_not_matched", calls)
        inp = ExtractionInput(data=PLAIN_RTF, kind="rtf")

        out: Any = registry.dispatch(inp, ExtractionOptions(preferred_engine="working_probe"))
        assert calls == ["working_probe"]
        assert out.info["extractor"] == "working_probe"

        calls.clear()
        with pytest.raises(WlhError) as excinfo:
            registry.dispatch(inp, ExtractionOptions(preferred_engine="failing_probe"))
        assert excinfo.value.code == "rtf_tcvn3_not_matched"
        assert calls == ["failing_probe"]

    def test_unknown_preferred_engine_raises_engine_unknown_error(self) -> None:
        """Naming an engine that is not registered for the kind fails fast with `extractor_engine_unknown`."""
        from wlh.processor.extractors.base import ExtractionInput, ExtractionOptions
        from wlh.processor.extractors.registry import default_registry

        calls: list[str] = []
        registry = _routing_registry("rtf_tcvn3_not_matched", calls)
        with pytest.raises(WlhError) as excinfo:
            registry.dispatch(
                ExtractionInput(data=PLAIN_RTF, kind="rtf"),
                ExtractionOptions(preferred_engine="no_such_engine"),
            )
        assert excinfo.value.code == "extractor_engine_unknown"
        assert calls == []

        with pytest.raises(WlhError) as default_exc:
            default_registry().dispatch(
                ExtractionInput(data=PLAIN_RTF, kind="rtf"),
                ExtractionOptions(preferred_engine="openpyxl"),
            )
        assert default_exc.value.code == "extractor_engine_unknown"

    def test_unregistered_kind_raises_unsupported_document_type(self) -> None:
        """Dispatching a kind with no registered engine fails with `unsupported_document_type`."""
        from wlh.processor.extractors.base import ExtractionInput, ExtractionOptions
        from wlh.processor.extractors.registry import ExtractorRegistry

        registry: Any = ExtractorRegistry()
        with pytest.raises(WlhError) as excinfo:
            registry.dispatch(
                ExtractionInput(data=b"Plain statutory text body.", kind="txt"),
                ExtractionOptions(),
            )
        assert excinfo.value.code == "unsupported_document_type"


class TestQuirkHookRunner:
    def test_active_hooks_filtered_by_enabled_names_stage_and_kind(self) -> None:
        """Only enabled hooks mounted on the requested stage and targeting the document kind are active."""
        from wlh.processor.extractors.base import QuirkHookRunner
        from wlh.processor.extractors.quirks import (
            QUIRK_HOOKS,
            filter_redundant_zip_members,
            inline_glyph_text,
            salvage_zip_prefix,
        )

        zip_runner: Any = QuirkHookRunner(QUIRK_HOOKS, ["zip_salvage"], "zip")
        assert [h.handler for h in zip_runner.active("pre_container")] == [salvage_zip_prefix]
        assert [h.handler for h in zip_runner.active("archive_members")] == [
            filter_redundant_zip_members
        ]
        assert zip_runner.active("ast_node") == []
        assert zip_runner.enabled_names() == frozenset({"zip_salvage"})

        disabled_zip: Any = QuirkHookRunner(QUIRK_HOOKS, [], "zip")
        assert disabled_zip.active("pre_container") == []

        html_runner: Any = QuirkHookRunner(QUIRK_HOOKS, ["ja_inline_glyphs", "zip_salvage"], "html")
        assert [h.handler for h in html_runner.active("ast_node")] == [inline_glyph_text]
        assert html_runner.active("pre_container") == []
        assert html_runner.active("text_span") == []

        pdf_runner: Any = QuirkHookRunner(QUIRK_HOOKS, ["ja_inline_glyphs"], "pdf")
        assert pdf_runner.active("ast_node") == []

    def test_always_hooks_bypass_enablement_but_not_stage_or_kind(self) -> None:
        """A hook declared `always` is active without enablement, yet still honours stage and kind."""
        from wlh.processor.extractors.base import QuirkHook, QuirkHookRunner

        def upper(value: str) -> str:
            return value.upper()

        def lower(value: str) -> str:
            return value.lower()

        hooks = (
            QuirkHook(
                name="probe_always",
                stage="post_markdown",
                target_kinds=frozenset({"doc"}),
                handler=upper,
                always=True,
            ),
            QuirkHook(
                name="probe_opt_in",
                stage="post_markdown",
                target_kinds=frozenset({"doc", "pdf"}),
                handler=lower,
            ),
        )
        doc_default: Any = QuirkHookRunner(hooks, [], "doc")
        assert [h.name for h in doc_default.active("post_markdown")] == ["probe_always"]
        assert doc_default.active("text_span") == []

        doc_opted: Any = QuirkHookRunner(hooks, ["probe_opt_in"], "doc")
        assert [h.name for h in doc_opted.active("post_markdown")] == [
            "probe_always",
            "probe_opt_in",
        ]

        pdf_default: Any = QuirkHookRunner(hooks, [], "pdf")
        assert pdf_default.active("post_markdown") == []


class TestExtractionRuntimeWithoutOfficeSuite:
    def test_worker_environment_never_forwards_office_suite_binary_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The extraction worker environment drops `WLH_SOFFICE` even when it is set in the parent."""
        from wlh.processor.extractors.router import _worker_env  # pyright: ignore[reportPrivateUsage]

        monkeypatch.setenv("WLH_SOFFICE", "/opt/office/program/soffice")
        monkeypatch.setenv("WLH_TESSERACT", "/usr/local/bin/tesseract")
        env = _worker_env()
        assert "WLH_SOFFICE" not in env
        assert env.get("WLH_TESSERACT") == "/usr/local/bin/tesseract"

    def test_processor_manifest_office_section_has_no_office_suite_identity(self) -> None:
        """The processor manifest fingerprints Office libraries only, never an Office suite binary."""
        from wlh.processor.manifest import manifest

        config: dict[str, Any] = {
            "id": "dd-998",
            "scheduler": {
                "scope": "Manifest office section contract test",
                "entry_urls": ["https://example.gov/laws/1.html"],
                "allowed_hosts": ["example.gov"],
            },
            "processor": {
                "parser": {
                    "type": "html",
                    "documents": {
                        "content_xpath": "//div[@id='body']",
                        "fields": {"title": {"xpath": "//h1/text()"}},
                    },
                }
            },
            "storage": {"archive_approved": True, "metadata": {"document_type": "法律法规"}},
        }
        office = manifest(config)["office"]
        assert "libreoffice" not in office
        serialized = json.dumps(office, ensure_ascii=False, default=str).lower()
        assert "libreoffice" not in serialized
        assert "soffice" not in serialized
        assert {"contract", "python-docx", "openpyxl", "xlrd"} <= set(office)
