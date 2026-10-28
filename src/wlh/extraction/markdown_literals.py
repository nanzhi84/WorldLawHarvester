"""Keep source text literal when embedded in generated Markdown or HTML."""

from __future__ import annotations

import html
import re


def literal_text(value: str | None, *, html_output: bool = False) -> str:
    text = html.escape(re.sub(r"\s+", " ", value or ""), quote=False)
    if html_output:
        return text
    # These are source characters, not links, emphasis or executable HTML.
    text = re.sub(r"([\\`*_\[\]~])", r"\\\1", text)
    text = re.sub(r"^(\s*\d{1,9})([.)])(?=\s)", r"\1\\\2", text)
    return re.sub(r"^(\s*)([#>+\-])(?=\s|$)", r"\1\\\2", text)
