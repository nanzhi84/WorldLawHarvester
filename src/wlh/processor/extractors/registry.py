"""Two-dimensional ``(kind, engine)`` registry with fallback chains and the DSH hook pipeline."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any

from wlh.cleaners.extraction_quality import text_check
from wlh.cleaners.text_cleaner import apply_text_cleaners
from wlh.core import WlhError
from wlh.processor.extractors.base import (
    EngineAdapter,
    ExtractionInput,
    ExtractionOptions,
    ExtractionOutput,
    QuirkHookRunner,
)
from wlh.processor.extractors.magic import OFFICE_KINDS, detect_real_format
from wlh.processor.extractors.quirks import QUIRK_HOOKS

# Error codes that hand a document to the next engine of the same kind.
RECOVERABLE_CODES: dict[str, frozenset[str]] = {
    "doc": frozenset({"doc_text_too_short", "doc_stream_missing"}),
    "docx": frozenset({"quality_too_short"}),
    "rtf": frozenset({"rtf_tcvn3_not_matched", "rtf_direct_text_unusable"}),
}


class ExtractorRegistry:
    """Routes a calibrated real format to one engine or walks its ordered fallback chain."""

    def __init__(self) -> None:
        self._chains: dict[str, list[EngineAdapter]] = {}

    def register(self, adapter: EngineAdapter) -> None:
        chain = self._chains.setdefault(adapter.kind, [])
        if any(engine.engine_name == adapter.engine_name for engine in chain):
            raise ValueError(f"duplicate extractor engine {adapter.kind}/{adapter.engine_name}")
        chain.append(adapter)

    def kinds(self) -> set[str]:
        return set(self._chains)

    def chain(self, kind: str) -> list[str]:
        return [engine.engine_name for engine in self._chains.get(kind, [])]

    def _engines(self, kind: str, preferred: str | None) -> list[EngineAdapter]:
        chain = self._chains.get(kind)
        if not chain:
            raise WlhError("unsupported_document_type")
        if preferred is None:
            return chain
        selected = [engine for engine in chain if engine.engine_name == preferred]
        if not selected:
            raise WlhError("extractor_engine_unknown")
        return selected

    def dispatch(self, inp: ExtractionInput, opts: ExtractionOptions) -> ExtractionOutput:
        detected = detect_real_format(inp.data, inp.kind)
        kind = detected if detected in self._chains else inp.kind
        engines = self._engines(kind, opts.preferred_engine)
        if kind != inp.kind:
            inp = ExtractionInput(
                data=inp.data,
                kind=kind,
                path=inp.path,
                base_url=inp.base_url,
                ocr_cache=inp.ocr_cache,
                depth=inp.depth,
            )
        hooks = QuirkHookRunner(QUIRK_HOOKS, opts.quirks, kind)
        recoverable = RECOVERABLE_CODES.get(kind, frozenset())
        attempts: list[str] = []
        for position, engine in enumerate(engines, 1):
            try:
                markdown, info = engine.extract(inp, opts, hooks)
            except WlhError as exc:
                if position == len(engines) or exc.code not in recoverable:
                    raise
                attempts.append(exc.code)
                continue
            return self._finish(kind, engine, hooks, opts, markdown, info, attempts)
        raise WlhError("unsupported_document_type")  # pragma: no cover - chains are non-empty

    @staticmethod
    def _finish(
        kind: str,
        engine: EngineAdapter,
        hooks: QuirkHookRunner,
        opts: ExtractionOptions,
        markdown: str,
        engine_info: dict[str, Any],
        attempts: list[str],
    ) -> ExtractionOutput:
        quality: dict[str, Any] | None = engine_info.pop("_quality", None)
        parsers: list[str] = list(engine_info.pop("_parsers", None) or [engine.parser_tag])
        members: list[dict[str, Any]] = list(engine_info.pop("_members", None) or [])
        info: dict[str, Any] = {"kind": kind, "detected_format": kind}
        if kind in OFFICE_KINDS:
            info.update(legacy_encoding=None, legacy_fonts=[], attempts=attempts)
        info.update(engine_info)
        info["extractor"] = engine.engine_name
        if "post_markdown" not in engine.native_stages:
            for hook in hooks.active("post_markdown"):
                markdown, changed = hook.handler(
                    markdown, font_evidence=bool(info.get("legacy_fonts"))
                )
                if changed:
                    info["legacy_encoding"] = "tcvn3"
        markdown = engine.finalize(markdown)
        if info.get("legacy_encoding") and "tcvn3" not in parsers:
            parsers.append("tcvn3")
        if quality is None:
            quality = text_check(
                markdown, opts.profile, invalid_body_patterns=list(opts.invalid_body_patterns)
            )
            parsers.append("quality")
        elif "quality" not in parsers:
            parsers.append("quality")
        return ExtractionOutput(markdown, info, quality, parsers, members)


def inline_markdown(
    source: Any,
    kind: str,
    *,
    base_url: str = "",
    quirks: Sequence[str] | None = None,
    strip_text_regexes: Sequence[str] | None = None,
    **html_options: Any,
) -> str:
    """In-process Markdown for archived text/markup kinds (``html``/``xml``/``json``/``txt``).

    Stage 3a calls this instead of individual converters: the kind's active quirk hooks run
    here, and ``strip_text_regexes`` (when given, even empty) applies the text cleaners.
    ``html_options`` (``content_xpath``, ``title``, ``already_cleaned``) reach the HTML engine.
    """
    from wlh.processor.extractors.html.html_to_md import (
        html_to_markdown,
        json_to_markdown,
        text_to_markdown,
    )
    from wlh.processor.extractors.xml.markdown import xml_to_markdown

    if kind == "html":
        markdown = html_to_markdown(source, base_url, quirks=quirks, **html_options)
    elif kind == "xml":
        markdown = xml_to_markdown(source.encode("utf-8") if isinstance(source, str) else source)
    elif kind == "json":
        markdown = json_to_markdown(source)
    elif kind == "txt":
        markdown = text_to_markdown(source)
    else:
        raise WlhError("unsupported_document_type")
    for hook in QuirkHookRunner(QUIRK_HOOKS, quirks or (), kind).active("post_markdown"):
        markdown, _ = hook.handler(markdown, font_evidence=False)
    if strip_text_regexes is not None:
        markdown = apply_text_cleaners(markdown, strip_text_regexes)
    return markdown


_default: ExtractorRegistry | None = None
_DEFAULT_LOCK = threading.Lock()


def build_default_registry() -> ExtractorRegistry:
    from wlh.processor.extractors.archive.zip_container import ZipContainerEngine
    from wlh.processor.extractors.html.html_to_md import (
        HtmlDomEngine,
        JsonOutlineEngine,
        PlainTextEngine,
    )
    from wlh.processor.extractors.office.doc import Ole2BlipOcrEngine, Ole2PieceTableEngine
    from wlh.processor.extractors.office.docx import DocxMediaOcrEngine, PythonDocxEngine
    from wlh.processor.extractors.office.rtf import StripRtfEngine, Tcvn3RtfEngine
    from wlh.processor.extractors.office.xls import XlrdEngine
    from wlh.processor.extractors.office.xlsx import OpenpyxlEngine
    from wlh.processor.extractors.pdf.scan_detector import PyMuPdfHybridEngine
    from wlh.processor.extractors.xml.markdown import XmlDialectEngine

    registry = ExtractorRegistry()
    for engine in (
        Ole2PieceTableEngine(),
        Ole2BlipOcrEngine(),
        PythonDocxEngine(),
        DocxMediaOcrEngine(),
        Tcvn3RtfEngine(),
        StripRtfEngine(),
        OpenpyxlEngine(),
        XlrdEngine(),
        PyMuPdfHybridEngine(),
        HtmlDomEngine(),
        XmlDialectEngine(),
        JsonOutlineEngine(),
        PlainTextEngine(),
        ZipContainerEngine(registry),
    ):
        registry.register(engine)
    return registry


def default_registry() -> ExtractorRegistry:
    """Process-wide registry holding every built-in engine."""
    global _default
    if _default is None:
        with _DEFAULT_LOCK:
            if _default is None:
                _default = build_default_registry()
    return _default


__all__ = [
    "RECOVERABLE_CODES",
    "ExtractorRegistry",
    "build_default_registry",
    "default_registry",
    "inline_markdown",
]
