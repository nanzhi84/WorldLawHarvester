"""Adapter capabilities and stable task identities; no transport access is exposed."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from wlh.core import WlhError, digest
from wlh.engine.objects import Objects
from wlh.engine.transport import request_key
from wlh.extraction.process import run_extraction
from wlh.extraction.quality import text_check

if TYPE_CHECKING:
    from wlh.engine.runtime import Runtime


def adapter_error(exc: Exception, prefix: str) -> str:
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
    """The only capabilities an Adapter receives: archived reads and bounded extraction."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime: Runtime = runtime
        self.config: dict[str, Any] = runtime.config
        self.objects: Objects = Objects()

    def read(self, acquisition: Mapping[str, Any]) -> bytes:
        if acquisition.get("state") != "archived":
            raise WlhError("input_not_archived")
        return self.runtime.session.read(acquisition).content

    def store(self, data: bytes) -> str:
        ref = self.objects.put(data)
        self.objects.get(ref)
        return ref

    def extract(self, data: bytes, kind: str) -> dict[str, Any]:
        key: str = digest(
            {
                "original": digest(data),
                "model": self.config["ocr"]["model"],
                "contract": "wlh.ocr-cache.v1",
            }
        )
        cached_ref = self.runtime.ocr_cache.get(key)
        cached = self.objects.json(cached_ref) if cached_ref else None
        result: dict[str, Any] = run_extraction(
            data, kind, self.config, active=self.runtime.budget.active, cached_ocr=cached
        )
        info: dict[str, Any] = result.get("info") or {}
        engine: dict[str, Any] = info.get("ocr") or {}
        raw: Any = engine.pop("result", None)
        if raw is not None and engine.get("engine") == "br-ocr-v1":
            ref = self.objects.put_json(raw)
            self.runtime.ocr_cache[key] = ref
            self.runtime.derived_ocr[digest(data)] = {"ref": ref, "key": key}
            engine["result_ref"] = ref
        return result

    def text_quality(self, markdown: str) -> dict[str, Any]:
        language = str(self.config["metadata"].get("language") or "")
        return text_check(markdown, "vi" if language.startswith("vi") else "generic")

    def follow_links(self, task: Mapping[str, Any]) -> bool:
        return self.runtime.mode == "collect" and task["depth"] < self.config["fetch"]["max_depth"]
