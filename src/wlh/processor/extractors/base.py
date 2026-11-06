"""Stage 3b strategy/adapter contracts and the declarative quirk hook runner (DSH)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

HookStage = Literal[
    "pre_container",  # 1: raw container bytes repair before unpacking (bytes -> bytes)
    "archive_members",  # 2: archive member list filtering (Sequence[ZipInfo] -> list[ZipInfo])
    "ast_node",  # 3: DOM / XML node inline replacement (Element -> str | None)
    "text_span",  # 4: per page / span charset decoding (str -> str)
    "post_markdown",  # 5: legacy encoding recovery on engine Markdown (str -> (str, bool))
]
HOOK_STAGES: tuple[HookStage, ...] = (
    "pre_container",
    "archive_members",
    "ast_node",
    "text_span",
    "post_markdown",
)


@dataclass(frozen=True, slots=True)
class ExtractionInput:
    data: bytes
    kind: str  # real format, calibrated by magic.detect_real_format()
    path: Path | None = None  # materialized file for engines that need a path (PyMuPDF)
    base_url: str = ""
    ocr_cache: dict[str, Any] | None = None
    depth: int = 0  # container nesting level (ZIP members re-enter the gateway at depth + 1)


@dataclass(frozen=True, slots=True)
class ExtractionOptions:
    profile: str = "generic"  # "vi" | "generic"
    quirks: tuple[str, ...] = ()
    invalid_body_patterns: tuple[str, ...] = ()
    ocr: Mapping[str, Any] = field(default_factory=lambda: {})
    max_bytes: int = 104_857_600
    preferred_engine: str | None = None  # route one engine only; None walks the fallback chain


@dataclass(slots=True)
class ExtractionOutput:
    markdown: str
    info: dict[str, Any]
    quality: dict[str, Any]
    parsers: list[str]
    members: list[dict[str, Any]] = field(default_factory=lambda: [])


@dataclass(frozen=True, slots=True)
class QuirkHook:
    """One quirk handler mounted on one lifecycle stage for a set of real formats.

    ``always`` marks evidence-gated handlers (e.g. TCVN3 glyph/font detection) that run even
    when the source does not list the quirk, because they only act on positive evidence.
    """

    name: str
    stage: HookStage
    target_kinds: frozenset[str]
    handler: Callable[..., Any]
    always: bool = False


class QuirkHookRunner:
    """Resolves which declared quirk hooks are active for one extraction (enabled x stage x kind)."""

    def __init__(self, hooks: Sequence[QuirkHook], enabled: Iterable[str], kind: str) -> None:
        self._hooks = tuple(hooks)
        self._enabled = frozenset(enabled)
        self.kind = kind

    def enabled_names(self) -> frozenset[str]:
        return self._enabled

    def active(self, stage: HookStage) -> list[QuirkHook]:
        return [
            hook
            for hook in self._hooks
            if hook.stage == stage
            and self.kind in hook.target_kinds
            and (hook.always or hook.name in self._enabled)
        ]

    def for_kind(self, kind: str) -> QuirkHookRunner:
        return QuirkHookRunner(self._hooks, self._enabled, kind)


class EngineAdapter(ABC):
    """Strategy + adapter: wraps one concrete engine behind ``(input, options) -> (md, info)``.

    Engines owning a native quality gate (PDF OCR, ZIP members) return it under the reserved
    ``info["_quality"]`` key, plus optional ``info["_parsers"]`` / ``info["_members"]``; the
    registry lifts them into :class:`ExtractionOutput`.
    """

    kind: str  # real format handled, e.g. "doc", "pdf", "html"
    engine_name: str  # unique id recorded in info["extractor"], e.g. "pure_python_ole2_msdoc"
    parser_tag: str  # manifest component tag, e.g. "office", "pdf", "markdown"
    # Stages the wrapped engine applies natively (page/DOM level) instead of the registry.
    native_stages: frozenset[HookStage] = frozenset()

    @abstractmethod
    def extract(
        self, inp: ExtractionInput, opts: ExtractionOptions, hooks: QuirkHookRunner
    ) -> tuple[str, dict[str, Any]]:
        """Run the engine; raise a recoverable ``WlhError`` to fall back to the next engine."""

    def finalize(self, markdown: str) -> str:
        """Format template applied after ``post_markdown`` hooks (e.g. legal heading levels)."""
        return markdown


__all__ = [
    "HOOK_STAGES",
    "EngineAdapter",
    "ExtractionInput",
    "ExtractionOptions",
    "ExtractionOutput",
    "HookStage",
    "QuirkHook",
    "QuirkHookRunner",
]
