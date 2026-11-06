"""Output cost estimation only; never replaces or clips the model's original input.

The shadow includes all text and data, not an article selected by our production
parser. It is a conservative admission estimate, not a completeness certificate.
"""

from __future__ import annotations

import json
import math
import re
from html.parser import HTMLParser
from typing import Any, cast
from urllib.parse import urljoin

OUTPUT_POLICY = "whole-content-shadow-v1"
NATIVE_CONTEXT = 262144
DEFAULT_CONTEXT, DEFAULT_OUTPUT = 24576, 8192


def budget_config(context: int, output: int, safety: int) -> dict[str, Any]:
    return {
        "context_tokens": context,
        "max_output_tokens": output,
        "safety_tokens": safety,
        "output_policy": OUTPUT_POLICY,
    }


class OutputShadow(HTMLParser):
    def __init__(self, base: str, *, xml: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.base, self.xml = base, xml
        self.parts: list[str] = []
        self.links: list[str] = []
        self.hidden: str | None = None
        self.script_data: list[str] = []
        self.data_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self.xml and tag in {"script", "style"}:
            self.hidden = tag
            self.data_script = "json" in str(dict(attrs).get("type", "")).lower()
            return
        if self.hidden:
            return
        for name, value in attrs:
            if not value:
                continue
            if name in {"href", "src", "data-src", "xlink:href"}:
                if not value.startswith(("javascript:", "data:", "#")):
                    self.links.append(urljoin(self.base, value))
            elif name in {"alt", "title", "content"} or self.xml:
                self.parts.append(value)
        if tag in {"p", "div", "tr", "li", "br", "section"}:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden == tag:
            if tag == "script" and self.data_script:
                self.parts.append(structured_shadow("".join(self.script_data), self.base))
            self.script_data = []
            self.hidden = None

    def handle_data(self, data: str) -> None:
        if self.hidden:
            if self.data_script:
                self.script_data.append(data)
        elif data.strip():
            self.parts.append(data)


def structured_shadow(raw: str, base: str) -> str:
    try:
        value: Any = json.loads(raw)
    except ValueError:
        return raw

    def walk(item: Any) -> Any:
        if isinstance(item, dict):
            return {k: walk(v) for k, v in cast(dict[str, Any], item).items()}
        if isinstance(item, list):
            return [walk(v) for v in cast(list[Any], item)]
        if isinstance(item, str) and re.search(r"<[A-Za-z][^>]*>", item):
            return markup_shadow(item, base)
        return item

    return json.dumps(walk(value), ensure_ascii=False)


def markup_shadow(raw: str, base: str, *, xml: bool = False) -> str:
    shadow = OutputShadow(base, xml=xml)
    shadow.feed(raw)
    shadow.close()
    # Repeated links can occur in both Markdown and attachment_urls. Keep their
    # occurrences, and budget serialization and Markdown syntax as well.
    return "".join(shadow.parts) + "\n" + "\n".join(f"[]({u})" for u in shadow.links)


def required_output_tokens(tokenizer: Any, task: dict[str, Any]) -> int:
    raw, base = task["raw_payload"], task["request_url"]
    if task["mime_type"] == "application/json" or raw.lstrip().startswith(("{", "[")):
        shadow = structured_shadow(raw, base)
    else:
        shadow = markup_shadow(raw, base, xml=task["mime_type"] == "application/xml")
    if task["task_type"] == "list_page":
        parser = OutputShadow(base)
        parser.feed(raw)
        urls = parser.links + re.findall(r"https?://[^\s<>\"'\\)]+", shadow)
        # The list schema requires unique targets. Repeated navigation/business
        # anchors do not create hundreds of identical output records.
        serialized = json.dumps({"target_urls": list(dict.fromkeys(urls))}, ensure_ascii=False)
    else:
        # Count escaped JSON output, every table cell, every record and complete URLs.
        serialized = json.dumps({"content": shadow}, ensure_ascii=False)
    count = len(tokenizer.encode(serialized, add_special_tokens=False))
    return math.ceil(count * 1.3) + 512
