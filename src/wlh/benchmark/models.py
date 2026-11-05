"""Data models for quantitative benchmark evaluation and Golden Set records."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _empty_penalties() -> list[dict[str, Any]]:
    return []


def _empty_mismatches() -> list[dict[str, Any]]:
    return []


def _empty_field_scores() -> dict[str, float]:
    return {}


def _empty_jur_scores() -> dict[str, dict[str, float]]:
    return {}


def _empty_worst_samples() -> list[dict[str, Any]]:
    return []


def _empty_sample_results() -> list[SampleEvaluationResult]:
    return []


ISO_DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HEX64_REGEX = re.compile(r"^[0-9a-fA-F]{64}$")

# ==============================================================================
# Task and Ground Truth Models
# ==============================================================================


class ProcessorBenchmarkTask(BaseModel):
    """Task specification for processor benchmark offline extraction."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, description="唯一任务 ID，如 list_dd-512_01 或 content_dd-578_01")
    task_type: Literal["list_page", "content_page"] = Field(
        description="页面类型：list_page（纯导航列表/翻页响应）或 content_page（单篇详情页或含 N 篇记录的批量 JSON/XML）"
    )
    source_id: str = Field(min_length=1, description="来源编号，如 dd-512")
    jurisdiction: str = Field(min_length=2, description="法域代码，如 CN, JP, VN, US")
    request_url: str = Field(
        min_length=1, description="该响应对应的公网请求 URL（用于相对路径补全）"
    )
    mime_type: Literal["text/html", "application/json", "application/xml"] = Field(
        description="原始载荷媒介类型（已剔除 PDF/Word/XLS 等二进制文件）"
    )
    raw_payload: str = Field(min_length=1, description="100% 原始未截断的 HTML/JSON/XML 文本内容")
    payload_sha256: str = Field(
        pattern=r"^[0-9a-fA-F]{64}$", description="raw_payload 的 SHA-256 摘要"
    )

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class ListPageGroundTruth(BaseModel):
    """Authoritative ground truth for list/navigation pages."""

    model_config = ConfigDict(extra="forbid")

    target_urls: list[str] = Field(
        default_factory=list,
        description=(
            "本列表页中包含的所有真实业务文档详情页 URL 或列表直挂的业务附件下载 URL（基于 request_url 解析为完整绝对 URL，按页面出现顺序排列；"
            "严禁包含网站导航栏、页脚链接、侧边栏推荐或翻页按钮本身的链接）"
        ),
    )

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


DocTypeLiteral = Literal[
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


class RecordGroundTruth(BaseModel):
    """Authoritative ground truth attributes for a single document record."""

    model_config = ConfigDict(extra="forbid")

    doc_type: DocTypeLiteral = Field(description="标准法律文种分类（9 选 1 必填）")
    markdown: str = Field(
        min_length=1,
        description="从原始载荷正文区域提取并转换的标准 Markdown 正文（保留标题层级与表格结构，剔除网页导航栏、面包屑、分享打印按钮及页脚套话）",
    )

    title: str | None = Field(default=None, description="文档官方标题；若当前片段无标题则为 null")
    doc_number: str | None = Field(
        default=None, description="官方文号、令号或案号；原文无文号则为 null"
    )
    published_at: str | None = Field(
        default=None, description="公布/发布/裁判日期（YYYY-MM-DD）；原文未载明则为 null"
    )
    effective_at: str | None = Field(
        default=None, description="施行/生效日期（YYYY-MM-DD）；原文未载明则为 null"
    )
    issuer: str | None = Field(default=None, description="发文机关或审理法院；原文未载明则为 null")
    attachment_urls: list[str] = Field(
        default_factory=list,
        description="正文中归属本篇文档的业务附件下载绝对 URL 列表（排除公共阅读器下载或无关图标链接）；无附件则为 []",
    )

    @field_validator("markdown")
    @classmethod
    def validate_non_empty_markdown(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("markdown must be a non-empty string")
        return v

    @field_validator("published_at", "effective_at")
    @classmethod
    def validate_iso_date(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not ISO_DATE_REGEX.match(v):
            raise ValueError(f"Date must conform to ISO format YYYY-MM-DD or be None, got '{v}'")
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(
                f"Date must be a valid calendar date in YYYY-MM-DD format, got '{v}'"
            ) from exc
        return v

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class ContentPageGroundTruth(BaseModel):
    """Ground truth container for content pages containing 1..N records."""

    model_config = ConfigDict(extra="forbid")

    records: list[RecordGroundTruth] = Field(
        min_length=1,
        description="从当前原始载荷中切分并提取出的 1..N 条独立文档记录",
    )

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class GroundTruthData(BaseModel):
    """Authoritative ground truth attributes for a benchmark sample (backwards-compatible)."""

    model_config = ConfigDict(extra="ignore")

    title: str | None = Field(
        default=None, description="Official statutory or administrative title"
    )
    doc_number: str | None = Field(default=None, description="Official docket or decree number")
    published_at: str | None = Field(
        default=None, description="Promulgation date in ISO YYYY-MM-DD"
    )
    effective_at: str | None = Field(
        default=None, description="Commencement date in ISO YYYY-MM-DD"
    )
    issuer: str | None = Field(default=None, description="Enacting authority or judicial division")
    doc_type: str = Field(default="其他", min_length=1, description="Canonical legal taxonomy type")
    markdown: str = Field(
        min_length=1,
        description="Authoritative standard answer markdown converted from raw_payload",
    )
    attachment_urls: list[str] = Field(
        default_factory=list,
        description="Discovered attachment URLs",
    )

    @field_validator("markdown")
    @classmethod
    def validate_non_empty_markdown(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("markdown must be a non-empty string")
        return v

    @field_validator("published_at", "effective_at")
    @classmethod
    def validate_iso_date(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not ISO_DATE_REGEX.match(v):
            raise ValueError(f"Date must conform to ISO format YYYY-MM-DD or be None, got '{v}'")
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(
                f"Date must be a valid calendar date in YYYY-MM-DD format, got '{v}'"
            ) from exc
        return v

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


# ==============================================================================
# Metric Subscores and Results (5 Pillars)
# ==============================================================================


class DiscoveryResult(BaseModel):
    """Evaluation result for list page URL discovery (Pillar 1)."""

    model_config = ConfigDict(extra="ignore")

    target_url_precision: float = Field(
        ge=0.0, le=100.0, description="Precision of discovered URLs"
    )
    target_url_recall: float = Field(ge=0.0, le=100.0, description="Recall of discovered URLs")
    target_url_f1: float = Field(ge=0.0, le=100.0, description="F1 score of discovered URLs")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class RecordSplitResult(BaseModel):
    """Evaluation result for content page record splitting (Pillar 2)."""

    model_config = ConfigDict(extra="ignore")

    record_split_precision: float = Field(
        ge=0.0, le=100.0, description="Precision of record splits"
    )
    record_split_recall: float = Field(ge=0.0, le=100.0, description="Recall of record splits")
    record_split_f1: float = Field(ge=0.0, le=100.0, description="F1 score of record splits")
    exact_count_match: bool = Field(description="Exact count equality flag")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class AccuracySubScores(BaseModel):
    """Sub-scores for the 6 ground-truth metadata fields and attachment URLs (0–100 scale, Pillar 3)."""

    model_config = ConfigDict(extra="ignore")

    title_accuracy: float = Field(ge=0.0, le=100.0, description="Document title accuracy")
    doc_number_accuracy: float = Field(
        ge=0.0, le=100.0, description="Official document/docket number accuracy"
    )
    published_at_accuracy: float = Field(
        ge=0.0, le=100.0, description="Promulgation/publication date accuracy"
    )
    effective_at_accuracy: float = Field(
        ge=0.0, le=100.0, description="Commencement/effective date accuracy"
    )
    issuer_accuracy: float = Field(
        ge=0.0, le=100.0, description="Enacting authority/court issuer accuracy"
    )
    doc_type_accuracy: float = Field(
        ge=0.0, le=100.0, description="Document type taxonomy classification accuracy"
    )
    attachment_url_f1: float = Field(
        default=100.0, ge=0.0, le=100.0, description="Attachment URL discovery F1"
    )


class AccuracyResult(BaseModel):
    """Aggregate result of ground-truth accuracy evaluation (Pillar 3)."""

    model_config = ConfigDict(extra="ignore")

    score: float = Field(ge=0.0, le=100.0, description="Overall accuracy score (0–100)")
    sub_scores: AccuracySubScores
    field_scores: dict[str, float] = Field(
        default_factory=_empty_field_scores, description="Field scores dictionary"
    )
    mismatches: list[dict[str, Any]] = Field(
        default_factory=_empty_mismatches, description="Detailed field mismatches"
    )

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class ContentFidelityResult(BaseModel):
    """Evaluation result for content DOM boundary fidelity (Pillar 4)."""

    model_config = ConfigDict(extra="ignore")

    content_char_recall: float = Field(
        ge=0.0, le=100.0, description="Character recall vs ground truth"
    )
    content_char_precision: float = Field(
        ge=0.0, le=100.0, description="Character precision vs ground truth"
    )
    content_f1: float = Field(ge=0.0, le=100.0, description="F1 score of text overlap")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class CleanlinessSubScores(BaseModel):
    """Sub-scores for the 5 cleanliness dimensions (0–100 scale, Pillar 5)."""

    model_config = ConfigDict(extra="ignore")

    noise_purity: float = Field(ge=0.0, le=100.0, description="HTML/entity/control char purity")
    encoding_purity: float = Field(ge=0.0, le=100.0, description="Unicode and mojibake purity")
    structural_integrity: float = Field(
        ge=0.0, le=100.0, description="Table and heading formatting integrity"
    )
    boilerplate_freedom: float = Field(
        ge=0.0, le=100.0, description="Freedom from UI navigation and boilerplate"
    )
    deduplication_purity: float = Field(
        ge=0.0, le=100.0, description="Freedom from duplicate blocks/paragraphs"
    )


class CleanlinessResult(BaseModel):
    """Aggregate result of cleanliness evaluation (Pillar 5)."""

    model_config = ConfigDict(extra="ignore")

    score: float = Field(ge=0.0, le=100.0, description="Overall cleanliness score (0–100)")
    sub_scores: CleanlinessSubScores
    penalties: list[dict[str, Any]] = Field(
        default_factory=_empty_penalties, description="Detailed penalties triggered"
    )

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


# ==============================================================================
# Sample and Benchmark Summary Reports
# ==============================================================================


class SampleEvaluationResult(BaseModel):
    """Evaluation result for a single document sample."""

    model_config = ConfigDict(extra="ignore")

    sample_id: str
    task_type: Literal["list_page", "content_page"] = "content_page"
    source_id: str = ""
    jurisdiction: str = ""
    split: str | None = None
    discovery: DiscoveryResult | None = None
    record_split: RecordSplitResult | None = None
    accuracy: AccuracyResult | None = None
    content_fidelity: ContentFidelityResult | None = None
    cleanliness: CleanlinessResult | None = None
    overall_score: float = Field(ge=0.0, le=100.0, description="Composite weighted score (0–100)")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


SampleScoreReport = SampleEvaluationResult


class BenchmarkSummaryReport(BaseModel):
    """Aggregate report across an entire benchmark dataset."""

    model_config = ConfigDict(extra="ignore")

    total_samples: int
    evaluated_at: str = ""
    evaluated_split: str = "all"
    average_overall_score: float = Field(ge=0.0, le=100.0)
    average_list_discovery_f1: float | None = None
    average_record_split_f1: float | None = None
    average_accuracy_score: float | None = None
    average_content_fidelity_score: float | None = None
    average_cleanliness_score: float | None = None
    cleanliness_dimension_scores: dict[str, float] = Field(default_factory=_empty_field_scores)
    accuracy_dimension_scores: dict[str, float] = Field(default_factory=_empty_field_scores)
    jurisdiction_scores: dict[str, dict[str, float]] = Field(default_factory=_empty_jur_scores)
    source_scores: dict[str, dict[str, float]] = Field(default_factory=dict)
    split_scores: dict[str, dict[str, Any]] = Field(default_factory=dict)
    worst_performing_samples: list[dict[str, Any]] = Field(default_factory=_empty_worst_samples)
    field_discrepancies: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Field fill rate and discrepancy statistics vs ground truth",
    )
    sample_results: list[SampleEvaluationResult] = Field(default_factory=_empty_sample_results)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


BenchmarkReport = BenchmarkSummaryReport


# ==============================================================================
# Audit and Record Envelope Models
# ==============================================================================


class AdjudicationMetadata(BaseModel):
    """Traceability metadata for blind annotation and cross-verification audit."""

    model_config = ConfigDict(extra="ignore")

    onboarded_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of dataset freezing",
    )
    pipeline_version: str = Field(default="1.0", description="Pipeline engine version")
    content_hash: str = Field(description="SHA-256 fingerprint of the untruncated state payload")
    payload_sha256: str = Field(default="", description="Alias for content_hash")

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, v: str) -> str:
        if not HEX64_REGEX.match(v):
            raise ValueError(
                f"content_hash must be a 64-character hexadecimal SHA-256 digest, got '{v}'"
            )
        return v

    @field_validator("payload_sha256")
    @classmethod
    def validate_payload_sha256(cls, v: str) -> str:
        if v and not HEX64_REGEX.match(v):
            raise ValueError(
                f"payload_sha256 must be a 64-character hexadecimal SHA-256 digest, got '{v}'"
            )
        return v

    def model_post_init(self, __context: Any) -> None:
        if not self.payload_sha256 and self.content_hash:
            self.payload_sha256 = self.content_hash


VerificationStatusType = Literal[
    "CONSENSUS",
    "ARBITRATED",
]


class BenchmarkSampleRecord(BaseModel):
    """Complete authoritative benchmark record stored in ground_truth_benchmark.jsonl."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, description="Unique sample identifier (e.g. cand_01)")
    task_type: Literal["list_page", "content_page"] = Field(
        default="content_page",
        description="Page classification",
    )
    source_id: str = Field(min_length=1, description="Origin source adapter ID (e.g. dd-501)")
    jurisdiction: str = Field(
        min_length=2, description="ISO country or jurisdiction code (e.g. CN, JP)"
    )
    detail_url: str = Field(default="", description="Authoritative detail page URL")
    request_url: str = Field(default="", description="Authoritative request URL")
    current_extracted: dict[str, Any] = Field(
        default_factory=dict, description="Crawler extracted fields"
    )
    ground_truth: Any = Field(description="Authoritative frozen ground truth")
    verification_status: VerificationStatusType = Field(
        description="Verification pathway or arbitration status"
    )
    arbitration_notes: str = Field(
        min_length=1, description="Human or algorithmic rationale for adjudication"
    )
    adjudication_metadata: AdjudicationMetadata | None = Field(
        default=None,
        description="Extended machine audit trail",
    )

    def model_post_init(self, __context: Any) -> None:
        if not self.request_url and self.detail_url:
            self.request_url = self.detail_url
        elif not self.detail_url and self.request_url:
            self.detail_url = self.request_url

    def to_jsonl_dict(self) -> dict[str, Any]:
        """Serialize cleanly to JSONL dictionary format matching ground_truth_benchmark.jsonl."""
        dumped = self.model_dump(mode="json", exclude_none=False)
        return dumped
