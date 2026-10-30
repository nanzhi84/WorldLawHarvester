"""Closed 5-stage pipeline Source configuration (Scheduler -> Fetcher -> Processor -> Cleaners -> Storage)."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any, cast, overload
from urllib.parse import urlsplit

import lxml.etree as etree  # type: ignore[import-untyped]
import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from wlh.core import (
    MAX_ORIGINAL_BYTES,
    WlhError,
    check_address,
    paths_for,
    public_url,
    valid_prefix,
)

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
    "issued_date",
    "doc_num",
    "effective_date",
    "expired_date",
)
QUALITY_ROUTES = (
    "accept",
    "accept_ocr",
    "accept_with_warning",
    "vision_reparse_pages",
    "legacy_font_encoding_recovery",
)
PARSER_TYPES = ("html", "json", "xml")
CHALLENGE_HOOKS = (
    "oracle_adf_loopback",
    "js_cookie_reload",
)
PARSER_TRANSFORMS = (
    "inline_script_cell_restore",
    "gpo_xml_to_markdown",
    "verify_zip_attachments",
    "reconcile_result_range",
    "commencement_clause_verify",
    "moj_law_dates_and_meta",
)
EXTRACTOR_QUIRKS = (
    "ja_kanpo_pua",
    "ja_inline_glyphs",
    "vi_tcvn3",
    "zip_salvage",
)


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(
    loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    data: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)  # type: ignore[no-untyped-call]
        if key in data:
            raise WlhError("duplicate_config_key")
        data[key] = loader.construct_object(value_node, deep=deep)  # type: ignore[no-untyped-call]
    return data


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def validate_source(value: Any) -> SourceConfig:
    if not isinstance(value, Mapping):
        raise WlhError("invalid_source_config")
    val_map = cast(Mapping[str, Any], value)
    if "recipe" in val_map:
        raise WlhError("legacy_recipe_config_requires_migration")
    try:
        return SourceConfig.model_validate(dict(val_map))
    except WlhError:
        raise
    except ValidationError:
        raise WlhError("invalid_source_config") from None


def load_file(path: str | os.PathLike[str]) -> SourceConfig:
    try:
        with open(path, encoding="utf-8") as file:
            value: Any = yaml.load(file, Loader=UniqueLoader)
        if not isinstance(value, Mapping):
            raise WlhError("invalid_source_config")
        return validate_source(cast(Mapping[str, Any], value))
    except WlhError:
        raise
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
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class Budget(Strict):
    requests: int = Field(default=100, ge=1, le=200000)
    seconds: int = Field(default=120, ge=1, le=86400)
    response_bytes: int = Field(default=10485760, ge=1024, le=MAX_ORIGINAL_BYTES)
    documents: int = Field(default=10, ge=1, le=1000000)
    max_pages: int = Field(default=50, ge=1, le=10000)


class UrlRewriteRule(Strict):
    pattern: str = Field(
        validation_alias=AliasChoices("pattern", "match"),
        min_length=1,
        max_length=1000,
    )
    replacement: str = Field(
        validation_alias=AliasChoices("replacement", "replace"),
        min_length=1,
        max_length=1000,
    )
    only_at_depth: int | None = Field(default=None, ge=0, le=100)

    @property
    def match(self) -> str:
        return self.pattern

    @property
    def replace(self) -> str:
        return self.replacement

    @field_validator("pattern")
    @classmethod
    def valid_pattern(cls, value: str) -> str:
        return regex(value)


def _empty_url_rewrites() -> list[UrlRewriteRule]:
    return []


def _empty_label_value_rules() -> list[LabelValueRule]:
    return []


class SchedulerConfig(Strict):
    scope: str = Field(min_length=5, max_length=2000)
    entry_urls: list[str] = Field(min_length=1, max_length=1000)
    allowed_hosts: list[str] = Field(min_length=1)
    allowed_path_prefixes: list[str] = Field(default_factory=lambda: ["/"], min_length=1)
    host_path_scopes: dict[str, list[str]] = Field(default_factory=dict)
    url_rewrites: list[UrlRewriteRule] = Field(
        default_factory=_empty_url_rewrites, max_length=20
    )
    max_depth: int = Field(default=3, ge=0, le=100)
    concurrency: int = Field(default=2, ge=1, le=32)
    recheck_seconds: int = Field(default=86400, ge=0, le=31536000)
    budget: Budget = Field(default_factory=Budget)

    @field_validator("entry_urls")
    @classmethod
    def urls(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(public_url(url) for url in values))


class ProxyConfig(Strict):
    policy: str = Field(default="direct", pattern="^(direct|auto|required)$")
    profile: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,32}$")


class FetcherConfig(Strict):
    mode: str = Field(default="http", pattern="^(http|rendered_dom)$")
    user_agent: str = Field(default="WLH/0.1 (+authorized archival crawler)", max_length=256)
    delay: float = Field(default=0.1, ge=0.0, le=60.0)
    retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.5, ge=0.0, le=60.0)
    page_timeout_ms: int = Field(default=30000, ge=100, le=1800000)
    wait_until: str = Field(default="domcontentloaded", pattern="^(commit|domcontentloaded|load)$")
    robots: bool = True
    robots_redirect_urls: list[str] = Field(default_factory=list, max_length=20)
    blocked_patterns: list[str] = Field(default_factory=list, max_length=20)
    headers_from_env: dict[str, str] = Field(default_factory=dict)
    pagination_click_selector: str | None = Field(default=None, max_length=500)
    challenges: list[str] = Field(
        default_factory=lambda: ["oracle_adf_loopback", "js_cookie_reload"], max_length=10
    )
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)

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

    @field_validator("challenges")
    @classmethod
    def valid_challenges(cls, values: list[str]) -> list[str]:
        if set(values) - set(CHALLENGE_HOOKS) or len(set(values)) != len(values):
            raise ValueError("unknown_challenge_hook")
        return values


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


class LabelValueRule(Strict):
    """Declarative extraction of metadata from two-column tables, <dl><dt><dd>, or label-value rows."""

    row_xpath: str = Field(min_length=1, max_length=1000)
    label_xpath: str = Field(default=".//dt | .//th", min_length=1, max_length=500)
    value_xpath: str = Field(default=".//dd | .//td", min_length=1, max_length=500)
    mapping: dict[str, str] = Field(
        validation_alias=AliasChoices("mapping", "label_map"),
        min_length=1,
    )

    @property
    def label_map(self) -> dict[str, str]:
        return self.mapping

    @field_validator("row_xpath", "label_xpath", "value_xpath")
    @classmethod
    def valid_xpaths(cls, value: str) -> str:
        return xpath(value)

    @field_validator("mapping")
    @classmethod
    def valid_targets(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value.values()) - set(FIELD_NAMES):
            raise ValueError("unknown_document_field")
        return value


class DocumentRules(Strict):
    detail_url_patterns: list[str] = Field(default_factory=list, max_length=50)
    exclude_detail_url_patterns: list[str] = Field(default_factory=list, max_length=50)
    detail_xpath: str | None = Field(default=None, max_length=2000)
    link_scope_xpath: str | None = Field(default=None, max_length=2000)
    next_page_xpath: str | None = Field(default=None, max_length=1000)
    attachment_url_pattern: str = r"(?i)\.(?:pdf|docx?|rtf|xlsx?|zip)(?:$|[?#])"
    attachment_exclude_xpath: list[str] | str | None = Field(default=None)
    attachment_title_xpath: str | None = Field(default=None, max_length=1000)
    volume_pattern: str | None = Field(
        default=r"(?i)(?:phần|quyển|tập|part|vol\.?|volume|卷|第)\s*0*([1-9][0-9]{0,2})\b",
        validation_alias=AliasChoices("volume_pattern", "volume_url_pattern"),
        max_length=500,
    )
    attachment_title: str = Field(default="link_text", pattern="^(link_text|file_name)$")
    detail_body: str = Field(default="auto", pattern="^(auto|always|never)$")
    content_xpath: str | None = Field(default=None, max_length=1000)
    exclude_content_xpath: list[str] | str | None = Field(
        default=None,
        validation_alias=AliasChoices("exclude_content_xpath", "exclude_xpaths"),
    )
    require_complete_text: bool = False
    binary_title_from_link: bool = False
    fields: dict[str, FieldRule] = Field(default_factory=dict)
    label_value_rules: list[LabelValueRule] = Field(
        default_factory=_empty_label_value_rules, max_length=10
    )

    @field_validator("detail_url_patterns", "exclude_detail_url_patterns")
    @classmethod
    def valid_patterns(cls, values: list[str]) -> list[str]:
        return [regex(v) for v in values]

    @field_validator("attachment_url_pattern", "volume_pattern")
    @classmethod
    def valid_pattern(cls, value: str | None) -> str | None:
        return regex(value)

    @field_validator(
        "content_xpath",
        "detail_xpath",
        "link_scope_xpath",
        "next_page_xpath",
        "attachment_title_xpath",
    )
    @classmethod
    def valid_single_xpath(cls, value: str | None) -> str | None:
        return xpath(value)

    @field_validator("attachment_exclude_xpath", "exclude_content_xpath")
    @classmethod
    def valid_xpath_list_or_str(
        cls, value: list[str] | str | None
    ) -> list[str] | str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return xpath(value)
        return [xpath(v) for v in value]

    @field_validator("fields")
    @classmethod
    def known_fields(cls, value: dict[str, FieldRule]) -> dict[str, FieldRule]:
        if set(value) - set(FIELD_NAMES):
            raise ValueError("unknown_document_field")
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


class ParserSpec(Strict):
    type: str = Field(default="html")
    options: dict[str, Any] = Field(default_factory=dict)
    transforms: list[str] = Field(default_factory=list, max_length=10)
    documents: DocumentRules = Field(default_factory=DocumentRules)

    @field_validator("type")
    @classmethod
    def known_type(cls, value: str) -> str:
        if value not in PARSER_TYPES:
            raise ValueError("unknown_parser_type")
        return value

    @field_validator("transforms")
    @classmethod
    def known_transforms(cls, values: list[str]) -> list[str]:
        if set(values) - set(PARSER_TRANSFORMS) or len(set(values)) != len(values):
            raise ValueError("invalid_parser_transform")
        return values


class ExtractorSpec(Strict):
    office_text_policy: str = Field(default="prefer", pattern="^(prefer|off)$")
    libreoffice_timeout_seconds: int = Field(default=120, ge=5, le=1800)
    quirks: list[str] = Field(default_factory=list, max_length=10)
    ocr: OCRConfig = Field(default_factory=OCRConfig)

    @field_validator("quirks")
    @classmethod
    def known_quirks(cls, values: list[str]) -> list[str]:
        if set(values) - set(EXTRACTOR_QUIRKS) or len(set(values)) != len(values):
            raise ValueError("invalid_extractor_quirk")
        return values


class ProcessorConfig(Strict):
    parser: ParserSpec = Field(default_factory=ParserSpec)
    extractor: ExtractorSpec = Field(default_factory=ExtractorSpec)


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


class CleanersConfig(Strict):
    require_complete_text: bool = False
    allow_single_resource: bool = False
    infer_effective_date: bool = False
    enrich_bare_title: bool = False
    quality_accept_routes: list[str] = Field(
        default_factory=lambda: ["accept", "accept_ocr", "accept_with_warning"], min_length=1
    )
    strip_text_regexes: list[str] = Field(default_factory=list, max_length=50)
    title_strip_regexes: list[str] = Field(default_factory=list, max_length=50)
    invalid_body_patterns: list[str] = Field(default_factory=list, max_length=50)
    metadata: MetadataDefaults | None = None

    @field_validator("quality_accept_routes")
    @classmethod
    def routes(cls, values: list[str]) -> list[str]:
        if set(values) - set(QUALITY_ROUTES) or len(set(values)) != len(values):
            raise ValueError("invalid_quality_route")
        return sorted(values)

    @field_validator("strip_text_regexes", "title_strip_regexes", "invalid_body_patterns")
    @classmethod
    def valid_cleaner_regexes(cls, values: list[str]) -> list[str]:
        return [regex(v) for v in values]


class StorageConfig(Strict):
    archive_approved: bool = True
    metadata: MetadataDefaults = Field(default_factory=MetadataDefaults)


class SourceConfig(Strict):
    id: str = Field(pattern=r"^dd-[0-9]{3,}$")
    name: str | None = Field(default=None, min_length=1, max_length=256)
    scheduler: SchedulerConfig
    fetcher: FetcherConfig = Field(default_factory=FetcherConfig)
    processor: ProcessorConfig = Field(default_factory=ProcessorConfig)
    cleaners: CleanersConfig = Field(default_factory=CleanersConfig)
    storage: StorageConfig

    @model_validator(mode="after")
    def boundaries(self) -> SourceConfig:
        if not self.name:
            self.name = (
                self.storage.metadata.source_name
                or (self.cleaners.metadata.source_name if self.cleaners.metadata else None)
                or self.id
            )
        if not self.storage.archive_approved:
            raise WlhError("source_archive_approval_required")
        sched = self.scheduler
        if any(not valid_prefix(p) for p in sched.allowed_path_prefixes):
            raise WlhError("invalid_path_scope")
        if sched.host_path_scopes and (
            set(sched.host_path_scopes) != set(sched.allowed_hosts)
            or any(
                not paths or any(not valid_prefix(p) for p in paths)
                for paths in sched.host_path_scopes.values()
            )
        ):
            raise WlhError("invalid_host_path_scope")
        for host in sched.allowed_hosts:
            check_address(host)
        sched_dict = sched.model_dump()
        for url in sched.entry_urls:
            parsed = urlsplit(url)
            if not paths_for(sched_dict, parsed.hostname, parsed.path):
                raise WlhError("source_out_of_scope")
        from wlh.processor.parsers import validate_options

        validate_options(self.processor.parser.type, self.processor.parser.options)
        return self
