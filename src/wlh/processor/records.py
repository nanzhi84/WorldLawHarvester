"""The single parser boundary: legal metadata and one or more deliverable units."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RequestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    method: Literal["GET", "POST"] = "GET"
    role: str = "page"
    form: dict[str, str] | None = None
    idempotent: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)


class RawUnitSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["primary", "volume", "independent_attachment"] = "primary"
    title: str | None = None
    volume_order: int | None = Field(default=None, gt=0)
    markdown: str
    input: int = Field(ge=0)
    original: dict[str, Any] | None = None
    quality: dict[str, Any] = Field(default_factory=dict)
    extraction: dict[str, Any] = Field(default_factory=dict)
    parsers: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RawRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: str = Field(min_length=1)
    external_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    doc_type: str
    document_number: str | None = None
    issuer: str | None = None
    published_at: str | None = None
    effective_at: str | None = None
    expired_at: str | None = None
    status: str | None = None
    source_name: str | None = None
    detail_url: str | None = None
    units: list[RawUnitSource] = Field(min_length=1)
    recheck_locator: dict[str, Any] = Field(default_factory=dict)
    inputs: list[dict[str, Any]] = Field(min_length=1)
    parsers: list[str] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_candidate(cls, value: Mapping[str, Any]) -> RawRecord:
        meta = value["metadata"]
        dates = value.get("dates", {})
        return cls(
            namespace=value["namespace"],
            external_key=value["external_key"],
            title=meta["title"],
            doc_type=meta["document_type"],
            **{
                key: meta.get(key) for key in ("document_number", "issuer", "status", "source_name")
            },
            **dates,
            detail_url=value.get("public_url"),
            recheck_locator=value.get("recheck_locator") or {},
            inputs=value["inputs"],
            units=value["deliverables"],
            parsers=value.get("parsers", []),
            extensions=value.get("extensions", {}),
        )

    def candidate(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "external_key": self.external_key,
            "public_url": self.detail_url,
            "recheck_locator": self.recheck_locator,
            "metadata": {
                "title": self.title,
                "document_type": self.doc_type,
                **{
                    key: getattr(self, key)
                    for key in ("document_number", "issuer", "status", "source_name")
                },
            },
            "dates": {
                key: getattr(self, key) for key in ("published_at", "effective_at", "expired_at")
            },
            "deliverables": [u.model_dump() for u in self.units],
            "inputs": self.inputs,
            "parsers": self.parsers,
            "extensions": self.extensions,
        }
