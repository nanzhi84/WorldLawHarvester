"""Strict model-output contracts for independent benchmark annotation."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ListPageGroundTruth(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_urls: list[str]

    @field_validator("target_urls")
    @classmethod
    def http_urls(cls, value: list[str]) -> list[str]:
        if any(not u.startswith(("http://", "https://")) or "{" in u or "}" in u for u in value):
            raise ValueError("invalid_url")
        return value


class RecordGroundTruth(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    doc_type: Literal[
        "法律法规",
        "政策文件",
        "案例",
        "行业标准",
        "通知公告",
        "财务报告",
        "会计准则",
        "税务资料",
        "其他",
    ]
    markdown: str = Field(min_length=1)
    title: str | None = None
    doc_number: str | None = None
    published_at: str | None = None
    effective_at: str | None = None
    issuer: str | None = None
    attachment_urls: list[str] = Field(default_factory=list)

    @field_validator("attachment_urls")
    @classmethod
    def http_urls(cls, value: list[str]) -> list[str]:
        if any(not u.startswith(("http://", "https://")) or "{" in u or "}" in u for u in value):
            raise ValueError("invalid_url")
        return value

    @field_validator("markdown")
    @classmethod
    def nonempty_body(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("empty_body")
        return value

    @field_validator("published_at", "effective_at")
    @classmethod
    def iso_date(cls, value: str | None) -> str | None:
        if value is not None and (
            len(value) != 10 or date.fromisoformat(value).isoformat() != value
        ):
            raise ValueError("invalid_date")
        return value


class ContentPageGroundTruth(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    records: list[RecordGroundTruth] = Field(min_length=1)
