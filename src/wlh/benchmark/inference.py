# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""GPU execution; the CLI imports this only after CPU admission/validation modes."""

from __future__ import annotations

import argparse
import importlib
import json
import os
from typing import Any

from wlh.benchmark.annotator import existing_annotations, validate_result
from wlh.benchmark.budget import budget_config, required_output_tokens
from wlh.benchmark.tokenization import MODELS, REVISIONS, prompt_hash, prompt_tokens


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
        kv_cache_dtype=args.kv_cache_dtype,
        cpu_offload_gb=args.cpu_offload_gb,
        max_num_batched_tokens=8192,
        seed=0,
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
                if required_output_tokens(tokenizer, task) > args.max_output_tokens:
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
                    "budget": budget_config(
                        args.context_tokens, args.max_output_tokens, args.safety_tokens
                    ),
                }
                row, reasons = validate_result(result, task, args)
                if actual_prompt_ids != prompt["prompt_token_ids"] or not output.finished:
                    row, reasons = None, ["prompt_changed_or_generation_incomplete"]
                if row is None:
                    rejected.write(
                        json.dumps({"id": task["id"], "reasons": reasons, "completion": result})
                        + "\n"
                    )
                    failures += 1
                else:
                    row["model"] = MODELS[args.annotator]
                    row["completion"].update(
                        tokenizer_revision=REVISIONS[args.annotator],
                        kv_cache_dtype=args.kv_cache_dtype,
                        cpu_offload_gb=args.cpu_offload_gb,
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
