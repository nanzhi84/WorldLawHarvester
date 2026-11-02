"""Data models for quantitative benchmark evaluation."""

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


class CleanlinessSubScores(BaseModel):
    """Sub-scores for the 5 cleanliness dimensions (0–100 scale)."""

    model_config = ConfigDict(extra="ignore")

    noise_purity: float = Field(ge=0.0, le=100.0, description="HTML/entity/control char purity")
    encoding_purity: float = Field(ge=0.0, le=100.0, description="Unicode and mojibake purity")
    structural_integrity: float = Field(ge=0.0, le=100.0, description="Table and heading formatting integrity")
    boilerplate_freedom: float = Field(ge=0.0, le=100.0, description="Freedom from UI navigation and boilerplate")
    deduplication_purity: float = Field(ge=0.0, le=100.0, description="Freedom from duplicate blocks/paragraphs")


class CleanlinessResult(BaseModel):
    """Aggregate result of cleanliness evaluation."""

    model_config = ConfigDict(extra="ignore")

    score: float = Field(ge=0.0, le=100.0, description="Overall cleanliness score (0–100)")
    sub_scores: CleanlinessSubScores
    penalties: list[dict[str, Any]] = Field(default_factory=_empty_penalties, description="Detailed penalties triggered")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class AccuracySubScores(BaseModel):
    """Sub-scores for the 6 ground-truth accuracy fields (0–100 scale)."""

    model_config = ConfigDict(extra="ignore")

    title_accuracy: float = Field(ge=0.0, le=100.0, description="Document title accuracy")
    doc_number_accuracy: float = Field(ge=0.0, le=100.0, description="Official document/docket number accuracy")
    published_at_accuracy: float = Field(ge=0.0, le=100.0, description="Promulgation/publication date accuracy")
    effective_at_accuracy: float = Field(ge=0.0, le=100.0, description="Commencement/effective date accuracy")
    issuer_accuracy: float = Field(ge=0.0, le=100.0, description="Enacting authority/court issuer accuracy")
    doc_type_accuracy: float = Field(ge=0.0, le=100.0, description="Document type taxonomy classification accuracy")


class AccuracyResult(BaseModel):
    """Aggregate result of ground-truth accuracy evaluation."""

    model_config = ConfigDict(extra="ignore")

    score: float = Field(ge=0.0, le=100.0, description="Overall accuracy score (0–100)")
    sub_scores: AccuracySubScores
    field_scores: dict[str, float] = Field(default_factory=_empty_field_scores, description="Field scores dictionary")
    mismatches: list[dict[str, Any]] = Field(default_factory=_empty_mismatches, description="Detailed field mismatches")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class SampleEvaluationResult(BaseModel):
    """Evaluation result for a single document sample."""

    model_config = ConfigDict(extra="ignore")

    sample_id: str
    source_id: str = ""
    jurisdiction: str = ""
    cleanliness: CleanlinessResult
    accuracy: AccuracyResult | None = None
    overall_score: float = Field(ge=0.0, le=100.0, description="Composite weighted score (0–100)")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


# Contract alias per PROJECT.md
SampleScoreReport = SampleEvaluationResult


class BenchmarkSummaryReport(BaseModel):
    """Aggregate report across an entire benchmark dataset."""

    model_config = ConfigDict(extra="ignore")

    total_samples: int
    evaluated_at: str = ""
    average_overall_score: float = Field(ge=0.0, le=100.0)
    average_cleanliness_score: float = Field(ge=0.0, le=100.0)
    average_accuracy_score: float | None = None
    cleanliness_dimension_scores: dict[str, float] = Field(default_factory=_empty_field_scores)
    accuracy_dimension_scores: dict[str, float] = Field(default_factory=_empty_field_scores)
    jurisdiction_scores: dict[str, dict[str, float]] = Field(default_factory=_empty_jur_scores)
    worst_performing_samples: list[dict[str, Any]] = Field(default_factory=_empty_worst_samples)
    field_discrepancies: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Field fill rate and discrepancy statistics vs ground truth",
    )
    sample_results: list[SampleEvaluationResult] = Field(default_factory=_empty_sample_results)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


# Contract alias per PROJECT.md
BenchmarkReport = BenchmarkSummaryReport

# ==============================================================================
# Golden Set & Ground Truth Data Models
# ==============================================================================

ISO_DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HEX64_REGEX = re.compile(r"^[0-9a-fA-F]{64}$")


class GroundTruthData(BaseModel):
    """Authoritative ground truth attributes for a benchmark sample."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, description="Official statutory or administrative title")
    doc_number: str | None = Field(default=None, description="Official docket or decree number")
    published_at: str | None = Field(default=None, description="Promulgation date in ISO YYYY-MM-DD")
    effective_at: str | None = Field(default=None, description="Commencement date in ISO YYYY-MM-DD")
    issuer: str | None = Field(default=None, description="Enacting authority or judicial division")
    doc_type: str = Field(min_length=1, description="Canonical legal taxonomy type")
    markdown: str = Field(
        min_length=1,
        description="Authoritative standard answer markdown converted from raw_payload",
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
            raise ValueError(f"Date must be a valid calendar date in YYYY-MM-DD format, got '{v}'") from exc
        return v

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


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
            raise ValueError(f"content_hash must be a 64-character hexadecimal SHA-256 digest, got '{v}'")
        return v

    @field_validator("payload_sha256")
    @classmethod
    def validate_payload_sha256(cls, v: str) -> str:
        if v and not HEX64_REGEX.match(v):
            raise ValueError(f"payload_sha256 must be a 64-character hexadecimal SHA-256 digest, got '{v}'")
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
    source_id: str = Field(min_length=1, description="Origin source adapter ID (e.g. dd-501)")
    jurisdiction: str = Field(min_length=2, description="ISO country or jurisdiction code (e.g. CN, JP)")
    detail_url: str = Field(min_length=1, description="Authoritative detail page URL")
    current_extracted: dict[str, Any] = Field(description="Crawler extracted fields (candidate under test)")
    ground_truth: GroundTruthData = Field(description="Authoritative frozen ground truth")
    verification_status: VerificationStatusType = Field(description="Verification pathway or arbitration status")
    arbitration_notes: str = Field(min_length=1, description="Human or algorithmic rationale for adjudication")
    adjudication_metadata: AdjudicationMetadata | None = Field(
        default=None,
        description="Extended machine audit trail",
    )

    def to_jsonl_dict(self) -> dict[str, Any]:
        """Serialize cleanly to JSONL dictionary format matching ground_truth_benchmark.jsonl."""
        dumped = self.model_dump(mode="json", exclude_none=False)
        return dumped
