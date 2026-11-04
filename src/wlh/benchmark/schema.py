"""Schema exports for quantitative benchmark evaluation and Golden Set records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from wlh.benchmark.models import (
    HEX64_REGEX,
    ISO_DATE_REGEX,
    AccuracyResult,
    AccuracySubScores,
    AdjudicationMetadata,
    BenchmarkReport,
    BenchmarkSampleRecord,
    BenchmarkSummaryReport,
    CleanlinessResult,
    CleanlinessSubScores,
    ContentFidelityResult,
    ContentPageGroundTruth,
    DiscoveryResult,
    DocTypeLiteral,
    GroundTruthData,
    ListPageGroundTruth,
    ProcessorBenchmarkTask,
    RecordGroundTruth,
    RecordSplitResult,
    SampleEvaluationResult,
    SampleScoreReport,
    VerificationStatusType,
)


def get_list_page_json_schema() -> dict[str, Any]:
    """Generate strict JSON Schema for ListPageGroundTruth for vLLM / XGrammar guided decoding."""
    return ListPageGroundTruth.model_json_schema()


def get_content_page_json_schema() -> dict[str, Any]:
    """Generate strict JSON Schema for ContentPageGroundTruth for vLLM / XGrammar guided decoding."""
    return ContentPageGroundTruth.model_json_schema()


def validate_xgrammar_schema(schema: Mapping[str, Any]) -> bool:
    """Pre-flight verification that schema is compliant with XGrammar / JSON Schema specification."""
    if schema.get("type") != "object":
        return False
    # Check that $ref pointers resolve within $defs
    defs_obj = schema.get("$defs")
    defs: dict[str, object] = cast(dict[str, object], defs_obj) if isinstance(defs_obj, dict) else {}
    ref_pattern = "#/$defs/"

    def _check_refs(obj: object) -> bool:
        if isinstance(obj, dict):
            d: dict[str, object] = cast(dict[str, object], obj)
            ref_val = d.get("$ref")
            if isinstance(ref_val, str) and ref_val.startswith(ref_pattern):
                target = ref_val[len(ref_pattern) :]
                if target not in defs:
                    return False
            for v in d.values():
                if not _check_refs(v):
                    return False
            return True
        if isinstance(obj, list):
            l_vals: list[object] = cast(list[object], obj)
            for v in l_vals:
                if not _check_refs(v):
                    return False
            return True
        return True

    return _check_refs(schema)


__all__ = [
    "AccuracyResult",
    "AccuracySubScores",
    "AdjudicationMetadata",
    "BenchmarkReport",
    "BenchmarkSampleRecord",
    "BenchmarkSummaryReport",
    "CleanlinessResult",
    "CleanlinessSubScores",
    "ContentFidelityResult",
    "ContentPageGroundTruth",
    "DiscoveryResult",
    "DocTypeLiteral",
    "GroundTruthData",
    "HEX64_REGEX",
    "ISO_DATE_REGEX",
    "ListPageGroundTruth",
    "ProcessorBenchmarkTask",
    "RecordGroundTruth",
    "RecordSplitResult",
    "SampleEvaluationResult",
    "SampleScoreReport",
    "VerificationStatusType",
    "get_content_page_json_schema",
    "get_list_page_json_schema",
    "validate_xgrammar_schema",
]
