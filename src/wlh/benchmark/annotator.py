#!/usr/bin/env python3
# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Standalone BF16 annotator with whole-sample admission and completion receipts.

CPU selection uses both real tokenizers; it does not prove semantic completeness. Only complete,
schema-valid natural-stop completions enter the paired evaluation bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from wlh.benchmark.annotation_schema import ContentPageGroundTruth, ListPageGroundTruth
from wlh.benchmark.tokenization import (
    MODELS,
    REVISIONS,
    TokenizerPair,
    payload_tokens,
    prompt_hash,
    prompt_tokens,
    required_output_tokens,
)

POLICY = "whole-sample-tokenizers-v2"
MAX_CONTEXT, MAX_OUTPUT = 24576, 8192


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_rows(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json" and text.lstrip().startswith("{"):
        payload = json.loads(text)
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            return [row for row in payload["items"] if isinstance(row, dict)]
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip():
            try:
                row = json.loads(line)
            except ValueError:
                raise ValueError(f"invalid_json_line:{number}") from None
            if not isinstance(row, dict):
                raise ValueError(f"invalid_object_line:{number}")
            rows.append(row)
    return rows


def load_tasks(path: Path) -> list[dict[str, Any]]:
    tasks, seen = read_rows(path), set()
    for task in tasks:
        tid, raw = task.get("id"), task.get("raw_payload")
        if not isinstance(tid, str) or not tid.strip() or tid in seen:
            raise ValueError("empty_or_duplicate_task_id")
        seen.add(tid)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"empty_payload:{tid}")
        if "…(truncated)" in raw or "...(truncated)" in raw:
            raise ValueError(f"truncated_payload:{tid}")
        if task.get("payload_sha256") != digest(raw):
            raise ValueError(f"payload_hash_mismatch:{tid}")
        if task.get("task_type") not in ("list_page", "content_page"):
            raise ValueError(f"invalid_task_type:{tid}")
        if task.get("mime_type") not in ("text/html", "application/json", "application/xml"):
            raise ValueError(f"invalid_mime_type:{tid}")
        if not all(
            isinstance(task.get(k), str) and task[k].strip()
            for k in ("source_id", "jurisdiction", "request_url")
        ):
            raise ValueError(f"missing_task_metadata:{tid}")
    if not tasks:
        raise ValueError("empty_bundle")
    return tasks


def write_json(path: Path, value: Any, *, jsonl: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in value)
        if jsonl
        else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    )
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def select_tasks(tasks: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    pair = TokenizerPair(local_files_only=args.local_files_only)
    decisions = [
        pair.admission(
            t,
            context_tokens=args.context_tokens,
            output_tokens=args.max_output_tokens,
            safety_tokens=args.safety_tokens,
        )
        for t in tasks
    ]
    selected = [t for t, decision in zip(tasks, decisions, strict=True) if decision["selected"]]
    report = {
        "policy": POLICY,
        "status": "preselected_pending_model_completion",
        "tokenizers": pair.metadata,
        "total_tasks": len(tasks),
        "selected_tasks": len(selected),
        "excluded_tasks": len(tasks) - len(selected),
        "decisions": decisions,
        "context_tokens": args.context_tokens,
        "max_output_tokens": args.max_output_tokens,
        "safety_tokens": args.safety_tokens,
        "coverage": {
            k: dict(Counter(t[k] for t in selected))
            for k in ("source_id", "jurisdiction", "task_type")
        },
    }
    write_json(args.selection_report, report)
    write_json(args.selected_bundle, selected, jsonl=True)
    print(
        json.dumps(
            {k: report[k] for k in ("status", "total_tasks", "selected_tasks", "excluded_tasks")}
        )
    )
    return selected


def validate_result(
    result: dict[str, Any],
    task: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    if result.get("payload_sha256") != task["payload_sha256"]:
        reasons.append("missing_or_mismatched_payload_hash")
    if result.get("finish_reason") != "stop":
        reasons.append("not_naturally_finished")
    prompt, output = result.get("prompt_tokens"), result.get("output_tokens")
    if type(prompt) is not int or prompt <= 0:
        reasons.append("missing_or_invalid_prompt_tokens")
    elif prompt + args.max_output_tokens + args.safety_tokens > args.context_tokens:
        reasons.append("actual_context_budget_exceeded")
    if type(output) is not int or not 0 < output < args.max_output_tokens:
        reasons.append("missing_or_exhausted_output_budget")
    text = result.get("text")
    ground_truth = None
    if not isinstance(text, str) or any(
        marker in text
        for marker in (
            "Content extracted from source payload.",
            "Substantive content simulated for ",
            "…(truncated)",
        )
    ):
        reasons.append("missing_or_placeholder_output")
    else:
        candidate_text = text.strip()
        if candidate_text.startswith("<think>") and "</think>" in candidate_text:
            candidate_text = candidate_text.split("</think>", 1)[1].strip()
        if candidate_text.startswith("```") and candidate_text.endswith("```"):
            lines = candidate_text.splitlines()
            if (
                len(lines) >= 2
                and lines[0].strip().startswith("```")
                and lines[-1].strip() == "```"
            ):
                candidate_text = "\n".join(lines[1:-1]).strip()
        try:
            ground_truth = json.loads(candidate_text)
            schema = (
                ListPageGroundTruth if task["task_type"] == "list_page" else ContentPageGroundTruth
            )
            schema.model_validate(ground_truth)
        except ValueError:
            reasons.append("incomplete_or_invalid_json_schema")
    if reasons:
        return None, reasons
    assert isinstance(text, str)
    receipt = {
        k: result[k] for k in ("payload_sha256", "prompt_tokens", "output_tokens", "finish_reason")
    }
    receipt.update(
        policy=POLICY,
        output_sha256=digest(text),
        context_tokens=args.context_tokens,
        max_output_tokens=args.max_output_tokens,
        safety_tokens=args.safety_tokens,
    )
    receipt["ground_truth_sha256"] = digest(
        json.dumps(ground_truth, ensure_ascii=False, sort_keys=True)
    )
    row = {k: task[k] for k in ("id", "task_type", "source_id", "jurisdiction", "request_url")}
    return dict(row, ground_truth=ground_truth, completion=receipt), []


def validate_receipts(tasks: list[dict[str, Any]], args: argparse.Namespace) -> int:
    by_id = {t["id"]: t for t in tasks}
    accepted, rejected, seen = [], [], set()
    results = read_rows(args.validate_completions)
    duplicate_ids = {tid for tid, n in Counter(str(r.get("id")) for r in results).items() if n > 1}
    for result in results:
        tid = result.get("id")
        seen.add(tid)
        if tid not in by_id or tid in duplicate_ids:
            row, reasons = None, ["unknown_or_duplicate_task_id"]
        else:
            row, reasons = validate_result(result, by_id[tid], args)
        if row is not None:
            accepted.append(row)
        else:
            rejected.append({"id": tid, "reasons": reasons})
    rejected.extend({"id": tid, "reasons": ["missing_completion"]} for tid in by_id.keys() - seen)
    write_json(args.output_dir / "accepted.jsonl", accepted, jsonl=True)
    write_json(args.output_dir / "rejected.jsonl", rejected, jsonl=True)
    print(json.dumps({"accepted": len(accepted), "rejected": len(rejected)}))
    return int(bool(rejected))


def existing_annotations(
    path: Path, tasks: list[dict[str, Any]], args: argparse.Namespace
) -> dict[str, Any]:
    existing, by_id = {}, {t["id"]: t for t in tasks}
    for row in read_rows(path) if path.exists() else []:
        tid, receipt = row.get("id"), row.get("completion", {})
        if tid not in by_id or tid in existing or receipt.get("policy") != POLICY:
            raise ValueError("unverified_or_stale_annotations:use_a_new_output_directory")
        result = dict(receipt, text=json.dumps(row.get("ground_truth"), ensure_ascii=False))
        checked, _ = validate_result(result, by_id[tid], args)
        if (
            checked is None
            or row.get("model") != MODELS[args.annotator]
            or receipt.get("tokenizer_revision") != REVISIONS[args.annotator]
            or receipt.get("ground_truth_sha256") != checked["completion"]["ground_truth_sha256"]
        ):
            raise ValueError("unverified_or_stale_annotations:use_a_new_output_directory")
        existing[tid] = row
    return existing


def run_model(tasks: list[dict[str, Any]], args: argparse.Namespace) -> int:
    path = args.output_dir / f"annotator_{args.annotator.lower()}.jsonl"
    existing = existing_annotations(path, tasks, args)
    pending = sorted(
        (t for t in tasks if t["id"] not in existing), key=lambda t: len(t["raw_payload"])
    )
    if not pending:
        return 0
    # GPU-only dependency: CPU selection and receipt replay do not require vLLM.
    vllm = importlib.import_module("vllm")
    LLM, SamplingParams = vllm.LLM, vllm.SamplingParams

    llm = LLM(
        model=MODELS[args.annotator],
        revision=REVISIONS[args.annotator],
        tokenizer_revision=REVISIONS[args.annotator],
        dtype="bfloat16",
        max_model_len=args.context_tokens,
        max_num_seqs=args.batch_size,
        gpu_memory_utilization=0.93,
        enable_prefix_caching=True,
        enable_chunked_prefill=True,
        trust_remote_code=True,
    )
    tokenizer, failures = llm.get_tokenizer(), 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (
        path.open("a", encoding="utf-8") as out,
        (args.output_dir / f"rejected_{args.annotator}.jsonl").open(
            "a", encoding="utf-8"
        ) as rejected,
    ):
        for start in range(0, len(pending), args.batch_size):
            batch, prompts = [], []
            for task in pending[start : start + args.batch_size]:
                tokens = prompt_tokens(tokenizer, task)
                raw_tokens = payload_tokens(tokenizer, task)
                if (
                    required_output_tokens(task["task_type"], len(raw_tokens))
                    > args.max_output_tokens
                ):
                    rejected.write(
                        json.dumps({"id": task["id"], "reasons": ["actual_output_budget_exceeded"]})
                        + "\n"
                    )
                    failures += 1
                    continue
                if len(tokens) + args.max_output_tokens + args.safety_tokens > args.context_tokens:
                    rejected.write(
                        json.dumps(
                            {"id": task["id"], "reasons": ["actual_context_budget_exceeded"]}
                        )
                        + "\n"
                    )
                    failures += 1
                    continue
                batch.append(task)
                prompts.append({"prompt_token_ids": tokens})
            outputs = (
                llm.generate(
                    prompts,
                    SamplingParams(temperature=0.0, max_tokens=args.max_output_tokens),
                    use_tqdm=False,
                )
                if prompts
                else []
            )
            for task, prompt, output in zip(batch, prompts, outputs, strict=True):
                generated = output.outputs[0]
                actual_prompt_ids = list(output.prompt_token_ids or [])
                result = {
                    "id": task["id"],
                    "payload_sha256": task["payload_sha256"],
                    "prompt_tokens": len(actual_prompt_ids),
                    "output_tokens": len(generated.token_ids),
                    "finish_reason": generated.finish_reason,
                    "text": generated.text,
                }
                row, reasons = validate_result(result, task, args)
                if actual_prompt_ids != prompt["prompt_token_ids"] or not output.finished:
                    row, reasons = None, ["prompt_changed_or_generation_incomplete"]
                if row is None:
                    rejected.write(json.dumps({"id": task["id"], "reasons": reasons}) + "\n")
                    failures += 1
                else:
                    row["model"] = MODELS[args.annotator]
                    row["completion"].update(
                        tokenizer_revision=REVISIONS[args.annotator],
                        prompt_sha256=prompt_hash(prompt["prompt_token_ids"]),
                    )
                    serialized = json.dumps(row, ensure_ascii=False)
                    out.write(serialized + "\n")
                    out.flush()
                    os.fsync(out.fileno())
                    print(f"STREAM_JSONL:Annotator_{args.annotator}:{serialized}", flush=True)
            rejected.flush()
            print(
                f"[Annotator_{args.annotator}] Visited {min(start + args.batch_size, len(pending))}/{len(pending)}; rejected {failures}",
                flush=True,
            )
    return int(bool(failures))


def pair_verified_annotations(selected: list[dict[str, Any]], args: argparse.Namespace) -> int:
    paired = {t["id"] for t in selected}
    annotations: dict[str, dict[str, Any]] = {}
    for tag in MODELS:
        args.annotator = tag
        annotations[tag] = existing_annotations(
            args.output_dir / f"annotator_{tag.lower()}.jsonl", selected, args
        )
        paired &= annotations[tag].keys()
    write_json(
        args.output_dir / "verified_bundle.jsonl",
        [t for t in selected if t["id"] in paired],
        jsonl=True,
    )
    for tag, rows in annotations.items():
        write_json(
            args.output_dir / f"verified_annotator_{tag.lower()}.jsonl",
            [rows[t["id"]] for t in selected if t["id"] in paired],
            jsonl=True,
        )
    print(
        json.dumps(
            {
                "preselected": len(selected),
                "paired_complete": len(paired),
                "pending": len(selected) - len(paired),
            }
        )
    )
    return int(len(paired) != len(selected))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_input = Path("processor_batch_bundle.jsonl")
    if not default_input.exists():
        default_input = Path("data/benchmark/ground_truth_benchmark.jsonl")
    parser.add_argument("--input", type=Path, default=default_input)
    parser.add_argument("--output-dir", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--select-only", action="store_true")
    modes.add_argument("--pair-only", action="store_true")
    modes.add_argument("--validate-completions", type=Path)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--annotator", choices=MODELS)
    parser.add_argument("--selected-bundle", type=Path)
    parser.add_argument("--selection-report", type=Path)
    parser.add_argument("--context-tokens", type=int, default=MAX_CONTEXT)
    parser.add_argument("--max-output-tokens", type=int, default=MAX_OUTPUT)
    parser.add_argument("--safety-tokens", type=int, default=1024)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--local-files-only",
        action="store_true",
        help="Use cached tokenizer assets without network",
    )
    args = parser.parse_args()
    if not (
        0 < args.max_output_tokens <= MAX_OUTPUT
        and 0 < args.context_tokens <= MAX_CONTEXT
        and 0 <= args.safety_tokens < args.context_tokens
        and 0 < args.batch_size <= 64
    ):
        parser.error("invalid_budget_or_batch_size")
    args.output_dir = args.output_dir or Path(
        "work/benchmark-dry-run" if args.dry_run else "data/benchmark/complete_annotations"
    )
    args.selected_bundle = args.selected_bundle or args.output_dir / "selected_bundle.jsonl"
    args.selection_report = args.selection_report or args.output_dir / "selection.json"
    tasks = load_tasks(args.input)
    if args.validate_completions:
        return validate_receipts(tasks, args)
    if args.pair_only:
        return pair_verified_annotations(tasks, args)
    if args.annotator:
        return run_model(tasks, args)
    if len({p.resolve() for p in (args.input, args.selected_bundle, args.selection_report)}) != 3:
        raise ValueError("input_and_output_paths_must_differ")
    selected = select_tasks(tasks, args)
    if not selected:
        return 1
    if args.select_only:
        return 0
    if args.dry_run:
        ListPageGroundTruth.model_validate({"target_urls": []})
        ContentPageGroundTruth.model_validate(
            {"records": [{"doc_type": "其他", "markdown": "Schema check"}]}
        )
        for tag in MODELS:
            rows = [
                {"id": t["id"], "mode": "dry_run", "payload_sha256": t["payload_sha256"]}
                for t in selected
            ]
            write_json(args.output_dir / f"annotator_{tag.lower()}.jsonl", rows, jsonl=True)
        print(
            "Schema Smoke Test: PASSED. Dual-blind batch annotation completed successfully (dry-run only; no ground truth)."
        )
        return 0
    # Separate processes release all model/GPU state between annotators.
    for tag in MODELS:
        command = [
            sys.executable,
            "-m",
            "wlh.benchmark.annotator",
            "--annotator",
            tag,
            "--input",
            str(args.selected_bundle),
            "--output-dir",
            str(args.output_dir),
        ]
        for key in ("context_tokens", "max_output_tokens", "safety_tokens", "batch_size"):
            command.extend(["--" + key.replace("_", "-"), str(getattr(args, key))])
        status = subprocess.run(command, check=False).returncode
        if status not in (0, 1):
            return status
    return pair_verified_annotations(selected, args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, ImportError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(2)
