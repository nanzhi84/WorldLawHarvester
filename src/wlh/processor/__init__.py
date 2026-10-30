"""Stage 3: Response parsing (Stage 3a) and full-text extraction (Stage 3b)."""

from __future__ import annotations

from wlh.processor.manifest import effective, fingerprint, manifest, require_runtime
from wlh.processor.records import RawRecord, RawUnitSource, RequestSpec

__all__ = [
    "RawRecord",
    "RawUnitSource",
    "RequestSpec",
    "effective",
    "fingerprint",
    "manifest",
    "require_runtime",
]
