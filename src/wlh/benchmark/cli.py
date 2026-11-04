"""Command-line interface for the quantitative benchmark tool."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from wlh.benchmark.evaluator import evaluate_benchmark_dataset
from wlh.benchmark.reporter import write_benchmark_reports
from wlh.core import WlhError


def configure_benchmark_parser(parser: argparse.ArgumentParser) -> None:
    """Configure benchmark parser with top-level backwards-compatible args and subcommands."""
    # Top-level args for backwards compatibility (e.g. wlh benchmark --input ...)
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Path to input candidate records or crawl extractions (.json or .jsonl)",
    )
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=None,
        help="Optional path to authoritative ground truth benchmark dataset",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/benchmark"),
        help="Directory where benchmark_report.json and benchmark_report.md will be saved",
    )

    subparsers = parser.add_subparsers(dest="subcommand")

    # run subcommand
    run_parser = subparsers.add_parser(
        "run", help="Run offline evaluation benchmark against ground truth"
    )
    run_parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to input candidate records or crawl extractions (.json or .jsonl)",
    )
    run_parser.add_argument(
        "--ground-truth",
        type=Path,
        default=None,
        help="Optional path to authoritative ground truth benchmark dataset",
    )
    run_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/benchmark"),
        help="Directory where benchmark_report.json and benchmark_report.md will be saved",
    )

    # sample subcommand
    sample_parser = subparsers.add_parser(
        "sample", help="Extract representative candidate samples from verification HTML"
    )
    sample_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/benchmark"),
        help="Output directory",
    )
    sample_parser.add_argument(
        "--html",
        type=Path,
        default=None,
        help="HTML preview file override",
    )
    sample_parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="CSV adaptation matrix file override",
    )


def build_benchmark_parser() -> argparse.ArgumentParser:
    """Build argument parser for the benchmark CLI."""
    parser = argparse.ArgumentParser(
        prog="wlh.benchmark",
        description="Run quantitative cleanliness and accuracy evaluation benchmarks",
    )
    configure_benchmark_parser(parser)
    return parser


DEFAULT_GROUND_TRUTH_PATH = Path("data/benchmark/ground_truth_benchmark.jsonl")


def run_benchmark_evaluation(args: argparse.Namespace) -> dict[str, Any]:
    """Execute offline benchmark evaluation workflow from parsed arguments."""
    input_arg = getattr(args, "input", None)
    if input_arg is None:
        raise ValueError("Missing required argument --input for benchmark evaluation.")

    input_path = Path(input_arg)
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    gt_path: Path | None = None
    if getattr(args, "ground_truth", None):
        candidate_gt = Path(args.ground_truth)
        if not candidate_gt.is_file():
            raise FileNotFoundError(f"Ground truth file not found: {candidate_gt}")
        gt_path = candidate_gt
    elif DEFAULT_GROUND_TRUTH_PATH.is_file():
        gt_path = DEFAULT_GROUND_TRUTH_PATH

    out_dir = Path(getattr(args, "output_dir", None) or "artifacts/benchmark")

    report = evaluate_benchmark_dataset(input_path, ground_truth_path_or_records=gt_path)
    json_path, md_path = write_benchmark_reports(report, out_dir)

    return {
        "status": "success",
        "total_samples": report.total_samples,
        "average_overall_score": report.average_overall_score,
        "average_cleanliness_score": report.average_cleanliness_score,
        "average_accuracy_score": report.average_accuracy_score,
        "json_report": str(json_path),
        "markdown_report": str(md_path),
    }


def run_benchmark_sample(args: argparse.Namespace) -> dict[str, Any]:
    """Extract representative candidate samples."""
    from wlh.benchmark.sampler import (
        DEFAULT_CSV_PATH,
        DEFAULT_HTML_PATH,
        extract_representative_candidates,
        write_benchmark_candidates,
    )

    out_dir = Path(getattr(args, "output_dir", None) or "data/benchmark")
    html_p = Path(args.html) if getattr(args, "html", None) else DEFAULT_HTML_PATH
    csv_p = Path(args.csv) if getattr(args, "csv", None) else DEFAULT_CSV_PATH

    candidates, manifest = extract_representative_candidates(html_path=html_p, csv_path=csv_p)
    write_benchmark_candidates(out_dir, candidates, manifest)

    return {
        "status": "success",
        "command": "benchmark.sample",
        "total_candidates": len(candidates),
        "output_dir": str(out_dir),
    }


def run_benchmark_cli(args: argparse.Namespace) -> dict[str, Any]:
    """Dispatch benchmark subcommands or default evaluation workflow."""
    subcmd = getattr(args, "subcommand", None)
    if subcmd == "run":
        return run_benchmark_evaluation(args)
    if subcmd == "sample":
        return run_benchmark_sample(args)
    if getattr(args, "input", None) is not None:
        # Backward compatibility: wlh benchmark --input ...
        return run_benchmark_evaluation(args)

    raise ValueError("A subcommand (run, sample) or --input must be specified for benchmark.")


def main(argv: list[str] | None = None) -> int:
    """Entry point for standalone execution."""
    parser = build_benchmark_parser()
    try:
        args = parser.parse_args(argv)
        result = run_benchmark_cli(args)
        if result.get("status") in {"failed", "retryable"}:
            return 1
        if "average_overall_score" in result:
            print(
                f"Benchmark completed: {result['total_samples']} samples evaluated | "
                f"Overall: {result['average_overall_score']:.2f} | "
                f"Cleanliness: {result['average_cleanliness_score']:.2f} | "
                f"Accuracy: {result['average_accuracy_score']}"
            )
            print(f"Reports written to: {result['markdown_report']}")
        return 0
    except KeyboardInterrupt:
        return 130
    except (ValueError, FileNotFoundError, WlhError) as exc:
        print(f"Error during benchmark execution: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error during benchmark execution: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
