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

    # export-bundle subcommand
    export_parser = subparsers.add_parser(
        "export-bundle",
        help="Export 100%% untruncated HTML/JSON/XML batch bundle covering list pages and content pages",
    )
    export_parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/benchmark/processor_batch_bundle.jsonl"),
        help="Output path for processor batch bundle JSONL",
    )
    export_parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path("tests/fixtures"),
        help="Path to tests/fixtures directory",
    )
    export_parser.add_argument(
        "--candidates",
        type=Path,
        default=Path("data/benchmark/raw_candidates.jsonl"),
        help="Path to raw_candidates.jsonl",
    )
    export_parser.add_argument(
        "--csv",
        type=Path,
        default=Path("data/benchmark/sources_matrix.csv"),
        help="Path to sources_matrix.csv",
    )

    # preflight subcommand
    preflight_parser = subparsers.add_parser(
        "preflight",
        help="Run static verification preflight checks on processor batch bundle before GPU annotation",
    )
    preflight_parser.add_argument(
        "--bundle",
        type=Path,
        default=Path("data/benchmark/ground_truth_benchmark.jsonl"),
        help="Path to benchmark bundle JSONL or annotation_pipeline.json",
    )

    # arbitrate subcommand
    arbitrate_parser = subparsers.add_parser(
        "arbitrate",
        help="Cross-verify independent annotations and arbitrate ground truth benchmark dataset",
    )
    arbitrate_parser.add_argument(
        "--candidates",
        type=Path,
        default=Path("data/benchmark/raw_candidates.jsonl"),
        help="Path to raw_candidates.jsonl",
    )
    arbitrate_parser.add_argument(
        "--annotator-a",
        type=Path,
        default=Path("data/benchmark/annotations/annotator_a.jsonl"),
        help="Path to Annotator A annotations",
    )
    arbitrate_parser.add_argument(
        "--annotator-b",
        type=Path,
        default=Path("data/benchmark/annotations/annotator_b.jsonl"),
        help="Path to Annotator B annotations",
    )
    arbitrate_parser.add_argument(
        "--log-output",
        type=Path,
        default=Path("artifacts/benchmark/cross_verification_log.json"),
        help="Path to output cross_verification_log.json",
    )
    arbitrate_parser.add_argument(
        "--benchmark-output",
        type=Path,
        default=Path("data/benchmark/ground_truth_benchmark.jsonl"),
        help="Path to output ground_truth_benchmark.jsonl",
    )
    arbitrate_parser.add_argument(
        "--export-disputes",
        type=Path,
        default=None,
        help="Path to export disputes (e.g. data/benchmark/annotations/disputes.jsonl)",
    )
    arbitrate_parser.add_argument(
        "--merge-resolutions",
        type=Path,
        default=None,
        help="Path to arbitrated resolutions to merge into benchmark ground truth",
    )
    arbitrate_parser.add_argument(
        "--bundle",
        type=Path,
        default=Path("data/benchmark/processor_batch_bundle.jsonl"),
        help="Path to processor batch bundle JSONL for payload hydration in disputes",
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


def run_benchmark_export_bundle(args: argparse.Namespace) -> dict[str, Any]:
    """Export processor batch bundle."""
    from wlh.benchmark.sampler import export_processor_batch_bundle

    out_p = Path(getattr(args, "output", None) or "data/benchmark/processor_batch_bundle.jsonl")
    fix_p = Path(getattr(args, "fixtures", None) or "tests/fixtures")
    cand_p = Path(getattr(args, "candidates", None) or "data/benchmark/raw_candidates.jsonl")
    csv_p = Path(getattr(args, "csv", None) or "data/benchmark/sources_matrix.csv")

    tasks = export_processor_batch_bundle(
        output_path=out_p,
        fixtures_dir=fix_p,
        candidates_path=cand_p,
        csv_path=csv_p,
    )
    return {
        "status": "success",
        "command": "benchmark.export-bundle",
        "total_tasks": len(tasks),
        "output_path": str(out_p),
    }


def run_benchmark_preflight(args: argparse.Namespace) -> dict[str, Any]:
    """Execute preflight checks on batch bundle."""
    from wlh.benchmark.sampler import run_preflight_checks

    b_p = Path(getattr(args, "bundle", None) or "data/benchmark/ground_truth_benchmark.jsonl")
    return run_preflight_checks(bundle_path=b_p)


def run_benchmark_arbitrate(args: argparse.Namespace) -> dict[str, Any]:
    """Run arbitration or dispute export/merge."""
    from wlh.benchmark.arbitrator import (
        export_disputes,
        merge_resolutions,
        run_arbitration,
        write_benchmark_dataset,
    )

    a_p = Path(getattr(args, "annotator_a", None) or "data/benchmark/annotations/annotator_a.jsonl")
    b_p = Path(getattr(args, "annotator_b", None) or "data/benchmark/annotations/annotator_b.jsonl")

    export_disp = getattr(args, "export_disputes", None)
    bundle_p = getattr(args, "bundle", None)
    if export_disp:
        disputes = export_disputes(
            annotator_a_path=a_p,
            annotator_b_path=b_p,
            output_path=Path(export_disp),
            bundle_path=Path(bundle_p) if bundle_p else None,
        )
        return {
            "status": "success",
            "command": "benchmark.arbitrate.export-disputes",
            "total_disputes": len(disputes),
            "output_path": str(export_disp),
        }

    merge_res = getattr(args, "merge_resolutions", None)
    bench_out = Path(getattr(args, "benchmark_output", None) or "data/benchmark/ground_truth_benchmark.jsonl")
    if merge_res:
        merged = merge_resolutions(
            annotator_a_path=a_p,
            annotator_b_path=b_p,
            resolutions_path=Path(merge_res),
            output_path=bench_out,
        )
        return {
            "status": "success",
            "command": "benchmark.arbitrate.merge-resolutions",
            "total_merged": len(merged),
            "output_path": str(bench_out),
        }

    cand_p = Path(getattr(args, "candidates", None) or "data/benchmark/raw_candidates.jsonl")
    log_out = Path(getattr(args, "log_output", None) or "artifacts/benchmark/cross_verification_log.json")

    verif_log, bench_recs = run_arbitration(
        candidates_path=cand_p,
        annotator_a_path=a_p,
        annotator_b_path=b_p,
    )
    write_benchmark_dataset(
        verification_log=verif_log,
        benchmark_records=bench_recs,
        log_path=log_out,
        benchmark_path=bench_out,
    )
    return {
        "status": "success",
        "command": "benchmark.arbitrate",
        "total_records": len(bench_recs),
        "consensus_count": verif_log.get("summary", {}).get("consensus_count", 0),
        "dispute_count": verif_log.get("summary", {}).get("dispute_count", 0),
        "log_output": str(log_out),
        "benchmark_output": str(bench_out),
    }


def run_benchmark_cli(args: argparse.Namespace) -> dict[str, Any]:
    """Dispatch benchmark subcommands or default evaluation workflow."""
    subcmd = getattr(args, "subcommand", None)
    if subcmd == "run":
        return run_benchmark_evaluation(args)
    if subcmd == "sample":
        return run_benchmark_sample(args)
    if subcmd in ("export-bundle", "export_bundle"):
        return run_benchmark_export_bundle(args)
    if subcmd == "preflight":
        return run_benchmark_preflight(args)
    if subcmd == "arbitrate":
        return run_benchmark_arbitrate(args)
    if getattr(args, "input", None) is not None:
        # Backward compatibility: wlh benchmark --input ...
        return run_benchmark_evaluation(args)

    raise ValueError(
        "A subcommand (run, sample, export-bundle, preflight, arbitrate) or --input must be specified for benchmark."
    )


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
        elif "total_tasks" in result:
            print(f"Exported {result['total_tasks']} tasks to {result['output_path']}")
        elif result.get("command") == "preflight":
            print(f"Preflight checks passed ({result.get('total_tasks', 0)} tasks verified)")
        elif "total_disputes" in result:
            print(f"Exported {result['total_disputes']} disputes to {result['output_path']}")
        elif "total_merged" in result:
            print(f"Merged {result['total_merged']} resolutions into {result['output_path']}")
        elif "consensus_count" in result:
            print(
                f"Arbitration completed: {result['total_records']} benchmark records created "
                f"({result['consensus_count']} consensus, {result['dispute_count']} disputes)"
            )
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
