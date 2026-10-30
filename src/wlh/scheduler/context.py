"""Stage 3a Parser capabilities and stable task identities; no transport or SQL access is exposed to parsers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import TYPE_CHECKING, Any, cast

from wlh.cleaners.extraction_quality import text_check
from wlh.core import WlhError, digest, public_url, request_key
from wlh.processor.extractors.router import run_extraction
from wlh.storage.cas import Objects

if TYPE_CHECKING:
    from wlh.scheduler.runtime import Runtime


def adapter_error(exc: Exception, prefix: str = "adapter_exception") -> str:
    """Stable error code; source-specific or unexpected failures never crash the Run."""
    if isinstance(exc, WlhError):
        if exc.code in {
            "interrupted",
            "crawl_time_budget_exceeded",
            "stale_attempt",
            "inactive_attempt",
            "run_not_started",
            "deadline_exceeded",
        }:
            raise exc
        return exc.code
    return prefix + ":" + type(exc).__name__


def target_key(target: Mapping[str, Any]) -> str:
    identity: Mapping[str, Any] = target.get("identity") or {}
    if identity.get("namespace") and identity.get("external_key"):
        return digest(
            {"namespace": identity["namespace"], "external_key": identity["external_key"]}
        )
    return digest({"inputs": [request_key(item["request"]) for item in target["inputs"]]})


class Context:
    """The only capabilities a Parser receives: archived reads and bounded extraction."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime: Runtime = runtime
        self.config: dict[str, Any] = runtime.config
        self.objects: Objects = Objects()
        self._archived_image_refs: dict[str, tuple[str, str]] | None = None

    def read(self, acquisition: Mapping[str, Any]) -> bytes:
        if acquisition.get("state") != "archived":
            raise WlhError("input_not_archived")
        return self.runtime.session.read(acquisition).content

    def find_archived_image(self, img_url: str) -> bytes | None:
        """Look up archived image bytes for the current run's source without exposing SQL to parsers."""
        if not img_url:
            return None
        try:
            norm_url = public_url(img_url)
        except WlhError:
            return None
        if self._archived_image_refs is None:
            ref_cache: dict[str, tuple[str, str]] = {}
            for row in self.runtime.rows(
                "SELECT id, archive_ref, actual_url, request_url FROM wlh.acquisition "
                "WHERE source_id=%s AND state='archived' AND outcome='ok'",
                (self.config["id"],),
            ):
                aref = str(row.get("archive_ref") or "")
                aid = str(row.get("id") or "")
                if not aref or not aid:
                    continue
                for u_col in ("actual_url", "request_url"):
                    u_val = str(row.get(u_col) or "")
                    if u_val:
                        try:
                            ref_cache[public_url(u_val)] = (aid, aref)
                        except WlhError:
                            pass
            self._archived_image_refs = ref_cache
        if norm_url in self._archived_image_refs:
            aid, aref = self._archived_image_refs[norm_url]
            return self.read({"id": aid, "archive_ref": aref, "state": "archived"})
        return None

    def adapter_state(self) -> dict[str, Any]:
        """Detached run checkpoint; changes become durable only through parse's result."""
        state: dict[str, Any] = self.runtime.checkpoint.get("adapter_state") or {}
        return deepcopy(state)

    def store(self, data: bytes) -> str:
        ref = self.objects.put(data)
        self.objects.get(ref)
        return ref

    def extract(self, data: bytes, kind: str) -> dict[str, Any]:
        data_hash = digest(data)
        key: str = digest(
            {
                "original": data_hash,
                "model": self.config["ocr"]["model"],
                "contract": "wlh.ocr-cache.v1",
            }
        )
        pre = self.runtime.extract_cache.get((data_hash, kind))
        if isinstance(pre, WlhError):
            raise pre
        if isinstance(pre, dict):
            result: dict[str, Any] = deepcopy(pre)
        else:
            cached_ref = self.runtime.ocr_cache.get(key)
            cached = self.objects.json(cached_ref) if cached_ref else None
            result = run_extraction(
                data, kind, self.config, active=self.runtime.budget.active, cached_ocr=cached
            )
        info: dict[str, Any] = result.get("info") or {}
        engine: dict[str, Any] = info.get("ocr") or {}
        raw: Any = engine.pop("result", None)
        if raw is not None and engine.get("engine") == "br-ocr-v1":
            ref = self.objects.put_json(raw)
            self.runtime.ocr_cache[key] = ref
            self.runtime.derived_ocr[data_hash] = {"ref": ref, "key": key}
            engine["result_ref"] = ref
        return result

    def text_quality(self, markdown: str) -> dict[str, Any]:
        language = str(self.config["metadata"].get("language") or "")
        cleaners = self.config.get("cleaners")
        cleaners_map: Mapping[str, Any] = (
            cast(Mapping[str, Any], cleaners) if isinstance(cleaners, Mapping) else {}
        )
        raw_pats = cleaners_map.get("invalid_body_patterns") or self.config.get(
            "invalid_body_patterns"
        )
        pats_seq: Sequence[Any] = (
            cast(Sequence[Any], raw_pats) if isinstance(raw_pats, (list, tuple)) else ()
        )
        invalid_pats: list[str] = [str(x) for x in pats_seq if x]
        return text_check(
            markdown,
            "vi" if language.startswith("vi") else "generic",
            invalid_body_patterns=invalid_pats,
        )

    def follow_links(self, task: Mapping[str, Any]) -> bool:
        return self.runtime.mode == "collect" and task["depth"] < self.config["scheduler"]["max_depth"]


__all__ = ["Context", "adapter_error", "target_key"]

