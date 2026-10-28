"""Verify that a selected legal HTML region has no unrepresented image content."""

from __future__ import annotations

import re
from typing import Any

from wlh.core import WlhError


def require_textual_images(node: Any) -> None:
    """Replace explicit numbered bullets; reject substantive or unknown images.

    The caller chooses and cleans the document region first. Site logos outside
    that region are not document content and must not determine this decision.
    """
    for img in node.xpath("descendant-or-self::img"):
        alt = str(img.get("alt") or "").strip()
        width, height = str(img.get("width") or ""), str(img.get("height") or "")
        if (
            re.fullmatch(r"[0-9０-９]{1,3}", alt)
            and width.isdigit() and height.isdigit()
            and 0 < int(width) <= 32 and 0 < int(height) <= 32
        ):
            img.tag = "span"
            img.text = alt + " "
            img.attrib.clear()
        else:
            raise WlhError("html_images_unverified")
