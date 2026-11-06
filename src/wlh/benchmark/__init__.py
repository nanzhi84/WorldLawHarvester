"""WorldLawHarvester quantitative benchmark suite."""

from __future__ import annotations

from wlh.benchmark.evaluator import (
    evaluate_benchmark_dataset,
    evaluate_dataset,
    evaluate_sample,
)
from wlh.benchmark.metrics import (
    evaluate_accuracy,
    evaluate_cleanliness,
)
from wlh.benchmark.models import (
    AccuracyResult,
    AccuracySubScores,
    AdjudicationMetadata,
    BenchmarkReport,
    BenchmarkSampleRecord,
    BenchmarkSummaryReport,
    CleanlinessResult,
    CleanlinessSubScores,
    GroundTruthData,
    SampleEvaluationResult,
    SampleScoreReport,
    VerificationStatusType,
)
from wlh.benchmark.reporter import (
    render_benchmark_json_report,
    render_benchmark_markdown_report,
    write_benchmark_reports,
)
from wlh.benchmark.sampler import (
    CANDIDATE_PLAN,
    extract_representative_candidates,
    write_benchmark_candidates,
)

__all__ = [
    "AccuracyResult",
    "AccuracySubScores",
    "AdjudicationMetadata",
    "BenchmarkReport",
    "BenchmarkSampleRecord",
    "BenchmarkSummaryReport",
    "CANDIDATE_PLAN",
    "CleanlinessResult",
    "CleanlinessSubScores",
    "GroundTruthData",
    "SampleEvaluationResult",
    "SampleScoreReport",
    "VerificationStatusType",
    "evaluate_accuracy",
    "evaluate_benchmark_dataset",
    "evaluate_cleanliness",
    "evaluate_dataset",
    "evaluate_sample",
    "extract_representative_candidates",
    "render_benchmark_json_report",
    "render_benchmark_markdown_report",
    "write_benchmark_candidates",
    "write_benchmark_reports",
]
