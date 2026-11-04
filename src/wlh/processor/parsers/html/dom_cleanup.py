"""Declarative HTML DOM cleanup helpers (Stage 3a)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, cast

from wlh.processor.parsers.transforms import restore_inline_script_cells

_ATTACHMENT_TITLE_SUFFIX_RE = re.compile(
    r"\s*[(（][^()（）]*(?:檔案下載|另開新視窗|file download|opens in new window)[^()（）]*[)）]\s*$",
    re.I,
)


def clean_attachment_title_attr(raw_title: str, extra_strip_regexes: Sequence[str] = ()) -> str:
    """Strip common download/new-window suffixes and configured title_strip_regexes."""
    cleaned = _ATTACHMENT_TITLE_SUFFIX_RE.sub("", raw_title.strip()).strip()
    for pat in extra_strip_regexes:
        if pat:
            cleaned = re.sub(pat, "", cleaned).strip()
    return cleaned


def _remove_preserving_tail(el: Any) -> None:
    parent = el.getparent()
    if parent is None:
        return
    if el.tail:
        prev = el.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def clean_site_dom(
    node: Any,
    exclude_xpaths: Sequence[str] = (),
    dom_transforms: Sequence[str] = (),
) -> Any:
    """Apply declarative DOM transforms and remove nodes matching ``exclude_xpaths``."""
    if node is None:
        return node
    if "inline_script_cell_restore" in dom_transforms:
        restore_inline_script_cells(node)
    for el in list(node.xpath(".//*[@style]")):
        st = (el.get("style") or "").lower()
        if re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", st):
            _remove_preserving_tail(el)
    for xp in tuple(exclude_xpaths):
        try:
            matched = node.xpath(xp)
        except Exception:
            continue
        if not isinstance(matched, list):
            continue
        for item in list(cast(list[Any], matched)):
            if item is node or not hasattr(item, "getparent"):
                continue
            _remove_preserving_tail(item)
    return node


__all__ = [
    "clean_attachment_title_attr",
    "clean_site_dom",
]
