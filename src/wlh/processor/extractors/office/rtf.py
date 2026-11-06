"""Rich Text Format (``.rtf``): pure-Python TCVN3 state machine, striprtf fallback, table recovery."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from wlh.cleaners.text_cleaner import compact
from wlh.core import WlhError
from wlh.processor.extractors.base import (
    EngineAdapter,
    ExtractionInput,
    ExtractionOptions,
    QuirkHookRunner,
)
from wlh.processor.extractors.office.tables import table_markdown
from wlh.processor.extractors.quirks.vi_tcvn3 import (
    legal_markdown,
    rtf_bytes_to_tcvn3_raw,
    rtf_uses_tcvn3,
)

MIN_RTF_CHARS = 8


def pipe_rows_markdown(text: str) -> str:
    """striprtf renders ``\\cell`` as ``|`` and ``\\row`` as a line break: rebuild tables."""
    out: list[str] = []
    run: list[list[str]] = []

    def flush() -> None:
        if len(run) >= 2:
            out.extend(["", table_markdown([[c.replace("|", "\\|") for c in r] for r in run]), ""])
        else:
            out.extend(" ".join(r) for r in run)
        run.clear()

    for line in text.splitlines():
        cells = [compact(c) for c in line.split("|")]
        if line.rstrip().endswith("|") and len(cells) >= 3:
            run.append(cells[:-1])
            continue
        flush()
        out.append(line)
    flush()
    return "\n".join(out)


class Tcvn3RtfEngine(EngineAdapter):
    kind = "rtf"
    engine_name = "rtf_direct_text"
    parser_tag = "office"

    def extract(
        self, inp: ExtractionInput, opts: ExtractionOptions, hooks: QuirkHookRunner
    ) -> tuple[str, dict[str, Any]]:
        uses, fonts = rtf_uses_tcvn3(inp.data)
        if not uses:
            raise WlhError("rtf_tcvn3_not_matched")
        return rtf_bytes_to_tcvn3_raw(inp.data), {"legacy_fonts": fonts}

    def finalize(self, markdown: str) -> str:
        return legal_markdown(markdown)


class StripRtfEngine(EngineAdapter):
    kind = "rtf"
    engine_name = "striprtf_direct"
    parser_tag = "office"

    def extract(
        self, inp: ExtractionInput, opts: ExtractionOptions, hooks: QuirkHookRunner
    ) -> tuple[str, dict[str, Any]]:
        from striprtf.striprtf import rtf_to_text  # type: ignore[import-untyped]

        text = pipe_rows_markdown(cast(str, rtf_to_text(inp.data.decode("latin-1"))))
        if len(compact(text)) < MIN_RTF_CHARS:
            raise WlhError("rtf_direct_text_unusable")
        _, fonts = rtf_uses_tcvn3(inp.data)
        return text, {"legacy_fonts": fonts}

    def finalize(self, markdown: str) -> str:
        return legal_markdown(markdown)


def extract_rtf(source: bytes | str | Path) -> tuple[str, dict[str, Any]]:
    from wlh.processor.extractors.office import extract_office

    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    return extract_office(raw, "rtf")


__all__ = ["StripRtfEngine", "Tcvn3RtfEngine", "extract_rtf", "pipe_rows_markdown"]
