"""Generic archive container layer: bounded ZIP unpacking whose members re-enter the gateway."""

from __future__ import annotations

import io
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wlh.core import WlhError
from wlh.processor.extractors.base import (
    EngineAdapter,
    ExtractionInput,
    ExtractionOptions,
    QuirkHookRunner,
)
from wlh.processor.extractors.magic import ZIP_MEMBER_KINDS, detect_real_format
from wlh.processor.extractors.quirks import QUIRK_HOOKS
from wlh.processor.extractors.quirks.zip_salvage import safe_member

if TYPE_CHECKING:
    from wlh.processor.extractors.registry import ExtractorRegistry

MAX_ZIP_MEMBERS = 50
MAX_ZIP_DEPTH = 2
ROUTE_ORDER = (
    "accept",
    "accept_ocr",
    "accept_with_warning",
    "vision_reparse_pages",
    "legacy_font_encoding_recovery",
)


def worst(routes: Sequence[str]) -> str:
    return max(routes, key=ROUTE_ORDER.index)


def _open_archive(data: bytes, hooks: QuirkHookRunner) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        repairs = hooks.active("pre_container")
        if not repairs:
            raise
    for hook in repairs:  # truncated stream: rebuild the central directory
        data = hook.handler(data)
    return zipfile.ZipFile(io.BytesIO(data))


def unpack_zip(
    source: str | Path | bytes,
    folder: str | Path,
    max_bytes: int,
    *,
    quirks: Sequence[str] = ("zip_salvage",),
) -> list[dict[str, Any]]:
    """Inner real documents with lineage paths, size/count bounded and path-traversal safe."""
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    hooks = QuirkHookRunner(QUIRK_HOOKS, quirks, "zip")
    members: list[dict[str, Any]] = []
    try:
        with _open_archive(data, hooks) as archive:
            infos = [i for i in archive.infolist() if not i.is_dir()]
            for hook in hooks.active("archive_members"):
                infos = hook.handler(infos)
            if len(infos) > MAX_ZIP_MEMBERS:
                raise WlhError("zip_member_limit_exceeded")
            if sum(i.file_size for i in infos) > max_bytes:
                raise WlhError("zip_expanded_size_exceeded")
            total = 0
            for index, info in enumerate(sorted(infos, key=lambda i: i.filename)):
                name = safe_member(info.filename)
                if name is None:
                    raise WlhError("zip_member_path_invalid")
                if name.startswith("__MACOSX/") or Path(name).name.startswith("._"):
                    continue
                with archive.open(info) as stream:
                    member = stream.read(max_bytes - total + 1)
                total += len(member)
                if total > max_bytes:
                    raise WlhError("zip_expanded_size_exceeded")
                kind = detect_real_format(member)
                if kind not in ZIP_MEMBER_KINDS and kind != "zip":
                    continue
                target = Path(folder) / f"member-{index}"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(member)
                members.append({"path": name, "file": target, "kind": kind})
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
        raise WlhError("zip_invalid") from None
    if not members:
        raise WlhError("zip_without_documents")
    return members


class ZipContainerEngine(EngineAdapter):
    """Unpacks a ZIP and dispatches every member back through the registry (bounded depth)."""

    kind = "zip"
    engine_name = "zip_container"
    parser_tag = "zip"
    native_stages = frozenset({"pre_container", "archive_members"})

    def __init__(self, registry: ExtractorRegistry) -> None:
        self.registry = registry

    def extract(
        self, inp: ExtractionInput, opts: ExtractionOptions, hooks: QuirkHookRunner
    ) -> tuple[str, dict[str, Any]]:
        if inp.depth >= MAX_ZIP_DEPTH:
            raise WlhError("zip_nesting_limit_exceeded")
        # A source without declared quirks keeps the historical zip_salvage default.
        quirks = tuple(hooks.enabled_names()) or ("zip_salvage",)
        if inp.path is not None:
            folder = inp.path.with_name(inp.path.name + "-members")
            folder.mkdir()
            return self._extract_members(inp, opts, quirks, folder)
        with tempfile.TemporaryDirectory(prefix="wlh-zip-") as temp:
            markdown, info = self._extract_members(inp, opts, quirks, Path(temp))
        info["_members"] = []  # in-memory dispatch: member files do not outlive the call
        return markdown, info

    def _extract_members(
        self, inp: ExtractionInput, opts: ExtractionOptions, quirks: Sequence[str], folder: Path
    ) -> tuple[str, dict[str, Any]]:
        members = unpack_zip(inp.data, folder, opts.max_bytes, quirks=quirks)
        parts: list[str] = []
        infos: list[dict[str, Any]] = []
        reports: list[dict[str, Any]] = []
        parsers = {"zip"}
        lineage: list[dict[str, Any]] = []
        for member in members:
            out = self.registry.dispatch(
                ExtractionInput(
                    data=member["file"].read_bytes(),
                    kind=member["kind"],
                    path=member["file"],
                    base_url=inp.base_url,
                    ocr_cache=inp.ocr_cache,
                    depth=inp.depth + 1,
                ),
                opts,
            )
            parts.append("## " + Path(member["path"]).stem + "\n\n" + out.markdown.strip())
            infos.append({"path": member["path"], **out.info})
            reports.append({"path": member["path"], **out.quality})
            parsers.update(out.parsers)
            lineage.append(
                {"path": member["path"], "file": str(member["file"]), "kind": member["kind"]}
            )
            lineage.extend(
                dict(item, path=member["path"] + "!/" + item["path"]) for item in out.members
            )
        report = {
            "contract": reports[0]["contract"],
            "profile": opts.profile,
            "recommended_route": worst([r["recommended_route"] for r in reports]),
            "members": reports,
            "pages": [],
            "document_reasons": sorted({x for r in reports for x in r["document_reasons"]}),
            "reason_count": sum(r["reason_count"] for r in reports),
        }
        info: dict[str, Any] = {
            "kind": "zip",
            "members": infos,
            "_quality": report,
            "_parsers": sorted(parsers),
            "_members": lineage,
        }
        return "\n\n".join(parts).strip() + "\n", info


__all__ = [
    "MAX_ZIP_DEPTH",
    "MAX_ZIP_MEMBERS",
    "ROUTE_ORDER",
    "ZIP_MEMBER_KINDS",
    "ZipContainerEngine",
    "unpack_zip",
    "worst",
]
