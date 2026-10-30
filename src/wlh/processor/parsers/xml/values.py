"""Small XML value readers shared by legal vocabulary parsers."""

from __future__ import annotations

import re
from typing import Any


def text(node: Any) -> str:
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def select(root: Any, *names: str, attribute: str | None = None) -> str | None:
    for name in names:
        nodes = root.xpath(".//*[local-name()=$name]", name=name)
        for node in nodes:
            value: str | None = node.get(attribute) if attribute else text(node)
            if value:
                return value
    return None
