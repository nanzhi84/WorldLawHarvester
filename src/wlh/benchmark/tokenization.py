"""CPU-only, revision-pinned chat tokenization shared by admission and inference.

This module loads tokenizer assets only. It never loads model weights or starts
Colab. The full chat template and generation prompt are counted without clipping.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, cast

from wlh.benchmark.budget import required_output_tokens

MODELS = {"A": "Qwen/Qwen3.8-27B", "B": "google/gemma-4-31B-it"}
REVISIONS = {
    "A": "1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0",
    "B": "842da3794eaa0b77d5f08bae87a17459d91ff475",
}

LIST_PAGE_SYSTEM_PROMPT = """你是一个严谨的世界法律与政务数据库专家。
给定一个政务列表页/翻页页面的原始文本载荷（HTML/JSON/XML），请提取属于当前列表项的目标业务文档详情页 URL 或列表直挂的业务附件下载 URL。
规则：
1. 仅提取属于当前列表页真实业务项的目标链接。严禁包含全站导航栏、页眉、页脚、网站地图、翻页控件自身链接。
2. 若链接为相对路径，请基于 request_url 补全为完整公网绝对 URL。
3. 若当前列表页为空列表或无业务项，target_urls 输出空数组 []。
4. 严格输出符合 JSON Schema 规范的 JSON 对象：{"target_urls": ["..."]}，严禁包含任何额外字段。
5. 严禁输出 <think> 标签、Markdown 代码块标记（```json）或任何前后置文字，直接输出以 '{' 开头、以 '}' 结尾的纯 JSON 对象。"""

CONTENT_PAGE_SYSTEM_PROMPT = """你是一个严谨的世界法律与政务数据库专家。
给定一个法律政务页面或接口响应的原始文本载荷（HTML/JSON/XML），请准确识别文档正文边界、元数据与附件下载链接。
规则：
1. 若载荷为单篇法规/裁判文书/政策文件，records 数组包含 1 条记录；若载荷为包含多篇法规的批量 JSON/XML 容器，请准确切分为 N 条独立记录（records 至少包含 1 条记录）。
2. doc_type 必须是以下 9 个精确枚举值之一："法律法规"、"政策文件"、"案例"、"行业标准"、"通知公告"、"财务报告"、"会计准则"、"税务资料"、"其他"（严禁输出枚举外的任何词）。
3. markdown 提取正文主体 Markdown（非空字符串），保留标题层级与表格结构，彻底剥离网站面包屑、导航条、分享打印控件和页脚免责套话；若页面仅含标题、扫描件图片或附件链接而无长文，仅保留原有标题、图片引用 ![](绝对URL) 或原有附件说明，禁止编造图片说明或标题。严禁省略、摘要、改写、繁简转换或加入原文没有的解释文字。
4. title, doc_number, published_at, effective_at, issuer 必须严格忠实于原文（以字面证据为准，未出现则必须输出 null，严禁编造或推断）；published_at 与 effective_at 必须为 YYYY-MM-DD 格式字符串（如 "2026-01-15"）或 null（若仅有年月或非有效公历日期则输出 null）。
5. attachment_urls 提取正文附件下载公网绝对 URL 字符串数组，无附件则输出 []。
6. 严格输出符合 JSON Schema 规范的 JSON 对象：{"records": [{"doc_type": "其他", "markdown": "...", "title": null, "doc_number": null, "published_at": null, "effective_at": null, "issuer": null, "attachment_urls": []}]}，严禁包含任何额外字段。
7. 严禁输出 <think> 标签、Markdown 代码块标记（```json）或任何前后置文字，直接输出以 '{' 开头、以 '}' 结尾的纯 JSON 对象。"""


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def messages(task: dict[str, Any]) -> list[dict[str, str]]:
    system = (
        LIST_PAGE_SYSTEM_PROMPT if task["task_type"] == "list_page" else CONTENT_PAGE_SYSTEM_PROMPT
    )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": f"Request URL: {task['request_url']}\n\nRaw Payload:\n{task['raw_payload']}",
        },
    ]


def prompt_tokens(tokenizer: Any, task: dict[str, Any]) -> list[int]:
    result: Any = tokenizer.apply_chat_template(
        messages(task),
        tokenize=True,
        return_dict=False,
        add_generation_prompt=True,
        enable_thinking=False,
        truncation=False,
    )
    if isinstance(result, Mapping):
        mapping_result = cast(Mapping[str, Any], result)
        if "input_ids" in mapping_result:
            result = mapping_result["input_ids"]
    if not isinstance(result, list):
        raise ValueError("tokenizer_did_not_return_complete_token_ids")
    items = cast(list[object], result)
    if not items or any(type(t) is not int for t in items):
        raise ValueError("tokenizer_did_not_return_complete_token_ids")
    return cast(list[int], items)


def payload_tokens(tokenizer: Any, task: dict[str, Any]) -> list[int]:
    result: Any = tokenizer.encode(str(task["raw_payload"]), add_special_tokens=False)
    if not isinstance(result, list):
        raise ValueError("tokenizer_did_not_return_complete_payload_token_ids")
    items = cast(list[object], result)
    if not items or any(type(t) is not int for t in items):
        raise ValueError("tokenizer_did_not_return_complete_payload_token_ids")
    return cast(list[int], items)


def prompt_hash(tokens: list[int]) -> str:
    return sha256(json.dumps(tokens, separators=(",", ":")))


class TokenizerPair:
    """Hold the two real tokenizers for one bounded CPU admission pass."""

    def __init__(self, *, local_files_only: bool = False) -> None:
        import tokenizers
        import transformers
        from transformers import AutoTokenizer

        self.tokenizers: dict[str, Any] = {}
        self.metadata: dict[str, dict[str, Any]] = {}
        auto_tokenizer_cls: Any = AutoTokenizer
        for label, model_id in MODELS.items():
            tokenizer: Any = auto_tokenizer_cls.from_pretrained(
                model_id,
                revision=REVISIONS[label],
                trust_remote_code=False,
                local_files_only=local_files_only,
            )
            if not tokenizer.chat_template:
                raise ValueError(f"missing_official_chat_template:{label}")
            template = str(tokenizer.get_chat_template())
            metadata: dict[str, Any] = {
                "model_id": model_id,
                "revision": REVISIONS[label],
                "tokenizer_class": type(tokenizer).__name__,
                "chat_template_sha256": sha256(template),
                "backend_sha256": sha256(str(tokenizer.backend_tokenizer.to_str())),
                "special_tokens_map": dict(tokenizer.special_tokens_map),
                "transformers_version": transformers.__version__,
                "tokenizers_version": tokenizers.__version__,
                "template_options": {"enable_thinking": False, "add_generation_prompt": True},
            }
            metadata["fingerprint"] = sha256(
                json.dumps(metadata, sort_keys=True, ensure_ascii=False)
            )
            self.metadata[label] = metadata
            self.tokenizers[label] = tokenizer

    def admission(
        self,
        task: dict[str, Any],
        *,
        context_tokens: int,
        output_tokens: int,
        safety_tokens: int,
    ) -> dict[str, Any]:
        counts: dict[str, dict[str, Any]] = {}
        reasons: list[str] = []
        for label, tokenizer in self.tokenizers.items():
            tokens = prompt_tokens(tokenizer, task)
            raw_tokens = payload_tokens(tokenizer, task)
            required_output = required_output_tokens(tokenizer, task)
            counts[label] = {
                "model_id": MODELS[label],
                "prompt_tokens": len(tokens),
                "payload_tokens": len(raw_tokens),
                "required_output_tokens": required_output,
                "prompt_sha256": prompt_hash(tokens),
                "reserved_output_tokens": output_tokens,
                "context_tokens": context_tokens,
                "safety_tokens": safety_tokens,
            }
            if required_output > output_tokens:
                reasons.append(f"output_budget_exceeded:{label}")
            if len(tokens) + output_tokens + safety_tokens > context_tokens:
                reasons.append(f"context_budget_exceeded:{label}")
        return {
            "id": task["id"],
            "selected": not reasons,
            "reasons": reasons,
            "payload_sha256": task["payload_sha256"],
            "token_counts": counts,
            "source_id": task["source_id"],
            "jurisdiction": task["jurisdiction"],
            "task_type": task["task_type"],
        }
