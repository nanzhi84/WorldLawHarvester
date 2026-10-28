"""Keep legal XML annotations and scientific scripts distinct from base text."""

from __future__ import annotations

import re
from typing import Any

from wlh.core import WlhError
from wlh.extraction.markdown_literals import literal_text

# GPO's published CFR stylesheet defines these typographic codes. Other E
# codes can encode compound mathematical levels; flattening them is not safe.
# https://www.govinfo.gov/bulkdata/CFR/resources/cfr.xsl
EMPHASIS_TAGS = {
    None: "strong", "": "strong", "01": "strong", "02": "strong", "03": "em",
    "04": "strong", "05": "small-caps", "15": "small-caps",
    "22": "sub", "52": "sub", "51": "sup",
}
INLINE_TAGS = {
    "Ruby", "SU", "sup", "SUP", "Sup", "SUB", "sub", "Sub", "E",
    "BR", "br", "I", "i", "EM", "em", "B", "b", "STRONG", "strong",
}


def inline_content(node: Any, *, html_output: bool = False) -> str:
    def local(element: Any) -> str:
        return str(element.tag).rsplit("}", 1)[-1]

    def content(element: Any, skip_reading: bool = False) -> str:
        value = fragment(element.text)
        for child in element:
            tag = local(child)
            if not (skip_reading and tag == "Rt"):
                value += marked(child, skip_reading)
            value += fragment(child.tail)
        return value

    def fragment(value: str | None) -> str:
        return literal_text(value, html_output=html_output)

    def ruby(element: Any) -> str:
        base = content(element, skip_reading=True)
        reading = "".join("".join(child.itertext()) for child in element if local(child) == "Rt")
        if not reading.strip():
            return base
        if re.fullmatch(r"[ヽ﹅﹆・●\s]+", reading):
            return "<strong>" + base + "</strong>"
        return "<ruby>" + base + "<rt>" + fragment(reading) + "</rt></ruby>"

    def marked(element: Any, skip_reading: bool = False) -> str:
        tag = local(element)
        if tag == "Ruby":
            return ruby(element)
        value = content(element, skip_reading)
        if tag.lower() == "br":
            return "<br>" + value
        if tag == "E":
            code = element.get("T")
            if code not in EMPHASIS_TAGS:
                safe_code = re.sub(r"[^A-Za-z0-9_-]", "", str(code))[:16] or "unknown"
                raise WlhError("legal_xml_typography_unverified_" + safe_code)
            tag = EMPHASIS_TAGS[code]
        if tag.lower() in {"su", "sup", "sub", "i", "em", "b", "strong", "small-caps"}:
            output_tag = {"su": "sup", "i": "em", "b": "strong"}.get(tag.lower(), tag.lower())
            if output_tag == "small-caps":
                return '<span style="font-variant:small-caps">' + value + "</span>"
            return f"<{output_tag}>" + value.strip() + f"</{output_tag}>"
        return value

    value = marked(node)
    return re.sub(r"\s+", " ", value).strip()
