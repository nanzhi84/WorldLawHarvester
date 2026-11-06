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
            if attribute:
                value: str | None = node.get(attribute)
                if not value:
                    suffix = f"}}{attribute}"
                    value = next(
                        (v for k, v in node.attrib.items() if k.endswith(suffix) and v),
                        None,
                    )
            else:
                value = text(node)
            if value:
                return value.strip() or None
    return None
