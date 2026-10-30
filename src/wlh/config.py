"""Closed Source configuration; historical Recipe YAML requires explicit migration."""

from __future__ import annotations

import os
import re
from typing import Any, overload
from urllib.parse import urlsplit

import lxml.etree as etree  # type: ignore[import-untyped]
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from wlh.core import MAX_ORIGINAL_BYTES, WlhError, check_address, public_url
from wlh.engine.scope import paths_for, valid_prefix

DOCUMENT_TYPES = (
    "法律法规",
    "政策文件",
    "案例",
    "行业标准",
    "通知公告",
    "财务报告",
    "会计准则",
    "税务资料",
    "其他",
)
FIELD_NAMES = (
    "title",
    "document_number",
    "issuer",
    "status",
    "published_at",
    "effective_at",
    "expired_at",
)
QUALITY_ROUTES = (
    "accept",
    "accept_ocr",
    "accept_with_warning",
    "vision_reparse_pages",
    "legacy_font_encoding_recovery",
)
ADAPTERS = (
    "web",
    "federal_register",
    "structured_xml",
    "japan_courts",
    "worldwidelaw_bridge",
    "juriscraper_bridge",
    "oldp_bridge",
    "openstates_bridge",
    "opencaselaw_bridge",
)


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    data: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)  # type: ignore[no-untyped-call]
        if key in data:
            raise WlhError("duplicate_config_key")
        data[key] = loader.construct_object(value_node, deep=deep)  # type: ignore[no-untyped-call]
    return data


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def load_file(path: str | os.PathLike[str]) -> SourceConfig:
    try:
        with open(path) as file:
            value = yaml.load(file, Loader=UniqueLoader)
        if not isinstance(value, dict):
            raise WlhError("invalid_source_config")
        if "recipe" in value:
            raise WlhError("legacy_recipe_config_requires_migration")
        return SourceConfig.model_validate(value)
    except (yaml.YAMLError, ValidationError, OSError):
        raise WlhError("invalid_source_config") from None


@overload
def regex(value: str) -> str: ...


@overload
def regex(value: None) -> None: ...


def regex(value: str | None) -> str | None:
    if value is None:
        return value
    try:
        re.compile(value)
    except re.error:
        raise ValueError("invalid_regex") from None
    return value


@overload
def xpath(value: str) -> str: ...


@overload
def xpath(value: None) -> None: ...


def xpath(value: str | None) -> str | None:
    if value is None:
        return value
    try:
        etree.XPath(value)  # type: ignore[attr-defined]
    except etree.XPathError:  # type: ignore[attr-defined]
        raise ValueError("invalid_xpath") from None
    return value


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Budget(Strict):
    requests: int = Field(default=100, ge=1, le=200000)
    seconds: int = Field(default=120, ge=1, le=86400)
    response_bytes: int = Field(default=10485760, ge=1024, le=MAX_ORIGINAL_BYTES)
    documents: int = Field(default=10, ge=1, le=1000000)


class FetchConfig(Strict):
    mode: str = Field(default="http", pattern="^(http|rendered_dom)$")
    max_depth: int = Field(default=3, ge=0, le=100)
    page_timeout_ms: int = Field(default=30000, ge=100, le=1800000)
    wait_until: str = Field(default="domcontentloaded", pattern="^(commit|domcontentloaded|load)$")
    retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.5, ge=0.0, le=60.0)


class FieldRule(Strict):
    """Declarative field mapping on the archived detail HTML; never inferred."""

    xpath: str | None = Field(default=None, max_length=1000)
    regex: str | None = Field(default=None, max_length=1000)
    date_format: str | None = Field(default=None, max_length=64)
    value: str | None = Field(default=None, max_length=2000)

    @field_validator("xpath")
    @classmethod
    def valid_xpath(cls, value: str | None) -> str | None:
        return xpath(value)

    @field_validator("regex")
    @classmethod
    def valid_regex(cls, value: str | None) -> str | None:
        return regex(value)

    @model_validator(mode="after")
    def one_source(self) -> FieldRule:
        if self.value is not None and (self.xpath or self.regex):
            raise ValueError("constant_field_rule_is_exclusive")
        if self.value is None and not (self.xpath or self.regex):
            raise ValueError("empty_field_rule")
        return self


class DocumentRules(Strict):
    detail_url_patterns: list[str] = Field(default_factory=list, max_length=50)
    detail_xpath: str | None = Field(default=None, max_length=2000)
    attachment_url_pattern: str = r"(?i)\.(?:pdf|docx?|rtf|xlsx?|zip)(?:$|[?#])"
    volume_pattern: str | None = Field(
        default=r"(?i)(?:phần|quyển|tập|part|vol\.?|volume|卷|第)\s*0*([1-9][0-9]{0,2})\b",
        max_length=500,
    )
    attachment_title: str = Field(default="link_text", pattern="^(link_text|file_name)$")
    detail_body: str = Field(default="auto", pattern="^(auto|always|never)$")
    content_xpath: str | None = Field(default=None, max_length=1000)
    require_complete_text: bool = False
    binary_title_from_link: bool = False
    fields: dict[str, FieldRule] = Field(default_factory=dict)

    @field_validator("detail_url_patterns")
    @classmethod
    def valid_patterns(cls, values: list[str]) -> list[str]:
        return [regex(v) for v in values]

    @field_validator("attachment_url_pattern", "volume_pattern")
    @classmethod
    def valid_pattern(cls, value: str | None) -> str | None:
        return regex(value)

    @field_validator("content_xpath", "detail_xpath")
    @classmethod
    def valid_content(cls, value: str | None) -> str | None:
        return xpath(value)

    @field_validator("fields")
    @classmethod
    def known_fields(cls, value: dict[str, FieldRule]) -> dict[str, FieldRule]:
        if set(value) - set(FIELD_NAMES):
            raise ValueError("unknown_document_field")
        return value


class MetadataDefaults(Strict):
    source_name: str | None = Field(default=None, max_length=500)
    document_type: str = Field(default="其他")
    language: str | None = Field(default=None, max_length=32)
    jurisdiction: str | None = Field(default=None, max_length=32)

    @field_validator("document_type")
    @classmethod
    def controlled(cls, value: str) -> str:
        if value not in DOCUMENT_TYPES:
            raise ValueError("invalid_document_type")
        return value


class OCRConfig(Strict):
    engine: str = Field(default="br-ocr-v1", pattern="^(tesseract|br-ocr-v1)$")
    languages: str = Field(
        default="eng+chi_sim+vie", pattern=r"^[a-z_]+(\+[a-z_]+)*$", max_length=128
    )
    model: str = Field(default="br-ocr-v1", pattern=r"^[A-Za-z0-9._-]{1,64}$")
    timeout_seconds: int = Field(default=180, ge=1, le=3600)
    poll_seconds: float = Field(default=1.0, ge=0.05, le=30.0)
    max_pages: int = Field(default=200, ge=1, le=10000)
    dpi: int = Field(default=144, ge=72, le=300)
    max_page_pixels: int = Field(default=20000000, ge=10000, le=100000000)
    max_result_bytes: int = Field(default=16777216, ge=1024, le=67108864)


class ParserConfig(Strict):
    office_text_policy: str = Field(default="prefer", pattern="^(prefer|off)$")
    libreoffice_timeout_seconds: int = Field(default=120, ge=5, le=1800)
    quality_accept_routes: list[str] = Field(
        default_factory=lambda: ["accept", "accept_ocr", "accept_with_warning"], min_length=1
    )

    @field_validator("quality_accept_routes")
    @classmethod
    def routes(cls, values: list[str]) -> list[str]:
        if set(values) - set(QUALITY_ROUTES) or len(set(values)) != len(values):
            raise ValueError("invalid_quality_route")
        return sorted(values)


class SourceConfig(Strict):
    id: str = Field(pattern=r"^dd-[0-9]{3,}$")
    adapter: str = Field(default="web")
    adapter_options: dict[str, Any] = Field(default_factory=dict)
    entry_urls: list[str] = Field(min_length=1, max_length=1000)
    allowed_hosts: list[str] = Field(min_length=1)
    allowed_path_prefixes: list[str] = Field(default_factory=lambda: ["/"], min_length=1)
    host_path_scopes: dict[str, list[str]] = Field(default_factory=dict)
    archive_approved: bool
    scope: str = Field(min_length=5, max_length=2000)
    robots: bool = True
    robots_redirect_urls: list[str] = Field(default_factory=list, max_length=20)
    user_agent: str = Field(default="WLH/0.1 (+authorized archival crawler)", max_length=256)
    recheck_seconds: int = Field(default=86400, ge=0, le=31536000)
    concurrency: int = Field(default=2, ge=1, le=32)
    delay: float = Field(default=0.1, ge=0.0, le=60.0)
    budget: Budget = Field(default_factory=Budget)
    fetch: FetchConfig = Field(default_factory=FetchConfig)
    metadata: MetadataDefaults = Field(default_factory=MetadataDefaults)
    documents: DocumentRules = Field(default_factory=DocumentRules)
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    parser: ParserConfig = Field(default_factory=ParserConfig)
    blocked_patterns: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("adapter")
    @classmethod
    def known_adapter(cls, value: str) -> str:
        if value not in ADAPTERS:
            raise ValueError("unknown_adapter")
        return value

    @field_validator("entry_urls")
    @classmethod
    def urls(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(public_url(url) for url in values))

    @field_validator("robots_redirect_urls")
    @classmethod
    def policy_urls(cls, values: list[str]) -> list[str]:
        urls = list(dict.fromkeys(public_url(url) for url in values))
        for url in urls:
            check_address(urlsplit(url).hostname)
        return urls

    @field_validator("blocked_patterns")
    @classmethod
    def blockers(cls, values: list[str]) -> list[str]:
        return [regex(v) for v in values]

    @model_validator(mode="after")
    def boundaries(self) -> SourceConfig:
        if not self.archive_approved:
            raise WlhError("source_archive_approval_required")
        if any(not valid_prefix(p) for p in self.allowed_path_prefixes):
            raise WlhError("invalid_path_scope")
        if self.host_path_scopes and (
            set(self.host_path_scopes) != set(self.allowed_hosts)
            or any(
                not paths or any(not valid_prefix(p) for p in paths)
                for paths in self.host_path_scopes.values()
            )
        ):
            raise WlhError("invalid_host_path_scope")
        for host in self.allowed_hosts:
            check_address(host)
        for url in self.entry_urls:
            parsed = urlsplit(url)
            if not paths_for(self.model_dump(), parsed.hostname, parsed.path):
                raise WlhError("source_out_of_scope")
        from wlh.adapters import validate_options

        validate_options(self.adapter, self.adapter_options)
        return self
