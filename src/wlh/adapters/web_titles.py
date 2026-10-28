"""Resolve binary document titles from independently archived directory evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from wlh.adapters.web_fields import clean_url, file_name, parse_html
from wlh.core import WlhError, digest, public_url


def directory_title(
    ctx: Any, inputs: Sequence[Mapping[str, Any]], document_url: str,
    generic_labels: set[str],
) -> tuple[str, dict[str, Any]]:
    contexts = [item for item in inputs if item.get("role") == "title_context"]
    if len(contexts) != 1:
        raise WlhError("binary_title_evidence_missing")
    item = contexts[0]
    acquisition = item["acquisition"]
    target = public_url(document_url)
    if item.get("document_url") != target:
        raise WlhError("binary_title_evidence_mismatch")
    base = acquisition["actual_url"] or acquisition["request_url"]
    tree, _, encoding = parse_html(ctx.read(acquisition), acquisition["charset"])
    titles: set[str] = set()
    for anchor in tree.xpath("//a[@href]"):
        if clean_url(base, anchor.get("href")) != target:
            continue
        label = re.sub(r"\s+", " ", anchor.text_content()).strip()
        label = re.sub(r"[（(]\s*PDF[^()（）]*[)）]\s*$", "", label, flags=re.I).strip()
        if (
            label.casefold() in generic_labels
            or label.casefold() == file_name(target).casefold()
            or not any(char.isalpha() for char in label)
        ):
            continue
        if label:
            titles.add(label)
    if not titles:
        raise WlhError("binary_title_evidence_missing")
    if len(titles) != 1:
        raise WlhError("binary_title_evidence_ambiguous")
    title = titles.pop()
    return title, {
        "acquisition_id": acquisition["id"],
        "archive_ref": acquisition["archive_ref"],
        "role": "context",
        "locator": {"url": public_url(base), "kind": "directory_link", "target": target},
        "charset": encoding,
        "base_url": public_url(base),
        "slice_hash": digest({"target": target, "title": title}),
    }
