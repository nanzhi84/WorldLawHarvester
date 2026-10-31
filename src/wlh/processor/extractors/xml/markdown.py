"""Render legal XML and GPO bulk printing XML mixed content into standard Markdown."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

import lxml.etree as _etree  # type: ignore[import-untyped]

from wlh.cleaners.text_cleaner import compact, normalized
from wlh.core import WlhError
from wlh.processor.extractors.html.html_to_md import html_to_markdown, text_to_markdown
from wlh.processor.extractors.html.tables_and_lists import literal_text
from wlh.processor.extractors.xml.inline import INLINE_TAGS, inline_content
from wlh.processor.extractors.xml.tables import TABLE_TAGS, table_markdown

etree: Any = _etree

HEADING_TAGS = {
    "heading",
    "longTitle",
    "docTitle",
    "LawTitle",
    "LawName",
    "法規名稱",
    "ArticleCaption",
    "ChapterTitle",
    "titel",
    "enbez",
    "HEAD",
    "Heading",
    "TitleText",
    "MarginalNote",
    "ArticleNo",
    "條號",
    "EngArticleNo",
}
PARAGRAPH_TAGS = {
    "p",
    "paragraph",
    "Sentence",
    "LawContent",
    "法規內容",
    "條文內容",
    "ParagraphSentence",
    "P",
    "PSPACE",
    "FP",
    "Text",
    "ArticleContent",
    "ArticleConctent",
    "EngArticleContent",
    "LawForeword",
    "前言",
    "EngLawForeword",
    "LawHistories",
    "沿革內容",
    "EngLawHistories",
    "content",
    "chapeau",
    "proviso",
    "continuation",
    "concludingText",
}
SKIP_TAGS = {
    "meta",
    "metadata",
    "identification",
    "Identification",
    "preface",
    "Notes",
    "附註",
    "presentation",
    "style",
    "metadaten",
    "fussnoten",
    "toc",
    "TOC",
    "LawLevel",
    "法規位階",
    "LawURL",
    "法規網址",
    "EngLawURL",
    "LawCategory",
    "法規類別",
    "LawModifiedDate",
    "最新異動日期",
    "EngLawModifiedDate",
    "LawEffectiveDate",
    "生效日期",
    "LawEffectiveNote",
    "生效內容",
    "LawAbandonNote",
    "廢止註記",
    "EngLawAbandonNote",
    "LawHasEngVersion",
    "是否英譯註記",
    "EngLawName",
    "英文法規名稱",
    "LawAttachements",
    "EngLawAttachements",
    "附件",
    "ArticleType",
    "條文型態",
    "EngArticleType",
    "FileName",
    "檔案名稱",
    "FileURL",
    "下載網址",
}
CONTAINER_TAGS = {
    "section",
    "subsection",
    "subparagraph",
    "clause",
    "subclause",
    "item",
    "article",
    "Article",
    "Paragraph",
    "Item",
    "SupplProvision",
    "Chapter",
    "Division",
    "Part",
    "DIV1",
    "DIV2",
    "DIV3",
    "DIV4",
    "DIV5",
    "DIV6",
    "DIV7",
    "DIV8",
    "DIV9",
    "SECTION",
    "NOTE",
    "footnote",
    "note",
}

HEADINGS: dict[str, str] = {
    "HD1": "##",
    "HD2": "###",
    "HD3": "####",
    "HD4": "#####",
    "HD5": "#####",
}
SKIP: set[str] = {"PRTPAGE", "FRDOC", "BILCOD", "GPH", "GID", "STARS"}


def _text(node: Any) -> str:
    return inline_content(node)


def text_of(node: Any) -> str:
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def markdown(root: Any) -> str:
    if root.xpath(
        './/*[local-name()="img" or local-name()="image" or local-name()="Image" '
        'or local-name()="graphic" or local-name()="Graphic" '
        'or (local-name()="Fig" and normalize-space(@src)!="")]'
    ):
        raise WlhError("legal_xml_images_unverified")

    def render(node: Any) -> str:
        if not isinstance(node.tag, str):
            return ""
        tag = etree.QName(node).localname
        if tag in SKIP_TAGS:
            return ""
        if tag in INLINE_TAGS:
            return inline_content(node)
        if tag in HEADING_TAGS:
            value = _text(node)
            return "\n\n## " + value + "\n\n" if value else ""
        if tag in TABLE_TAGS:
            table = table_markdown(node)
            if table:
                return table
        value = literal_text(node.text)
        for child in node:
            if (
                tag == "SupplProvision"
                and node.get("AmendLawNum")
                and isinstance(child.tag, str)
                and etree.QName(child).localname == "SupplProvisionLabel"
            ):
                continue
            value += render(child) + literal_text(child.tail)
        if tag == "SupplProvision" and node.get("AmendLawNum"):
            value = "### 附則（" + literal_text(node.get("AmendLawNum")) + "）\n\n" + value.strip()
        if tag in PARAGRAPH_TAGS | CONTAINER_TAGS | TABLE_TAGS:
            return "\n\n" + value.strip() + "\n\n" if value.strip() else ""
        if tag in {"num", "ArticleTitle", "ParagraphNum", "ItemTitle"}:
            return value.strip() + " " if value.strip() else ""
        return value

    rendered = re.sub(r"[ \t]*\n[ \t]*", "\n", render(root))
    return normalized(rendered)


def xml_to_markdown(data: bytes) -> str:
    """Full-text GPO XML → Markdown: headings, paragraphs, tables, footnotes."""
    try:
        root: Any = etree.fromstring(
            data, etree.XMLParser(resolve_entities=False, no_network=True)
        )
    except etree.XMLSyntaxError:
        raise WlhError("full_text_xml_invalid") from None
    blocks: list[str] = []

    def walk(node: Any) -> None:
        tag = node.tag if isinstance(node.tag, str) else ""
        if tag in SKIP:
            return
        if tag == "HD":
            level = HEADINGS.get(node.get("SOURCE", ""), "")
            text = text_of(node)
            if text:
                blocks.append((level + " " if level else "**") + text + ("" if level else "**"))
            return
        if tag in {"SUBJECT", "SECTNO"}:
            text = text_of(node)
            if text:
                blocks.append("## " + text if tag == "SUBJECT" else "### " + text)
            return
        if tag in {"P", "FP", "AMDPAR", "NOTE", "SIG", "NAME", "TITLE", "DATED", "FTNT"}:
            if tag in {"NOTE", "SIG", "FTNT"} and len(node):
                for child in node:
                    walk(child)
                return
            text = text_of(node)
            if text:
                blocks.append(text_to_markdown(text).strip())
            return
        if tag == "GPOTABLE":
            rows: list[list[str]] = []
            heads = [text_of(c) for c in node.iter("CHED")]
            if heads:
                rows.append(heads)
            for row in node.iter("ROW"):
                rows.append([text_of(e) for e in row.iter("ENT")])
            if rows:
                width = max(len(r) for r in rows)
                rows = [
                    [c.replace("|", "\\|") for c in r] + [""] * (width - len(r)) for r in rows
                ]
                lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
                lines.extend("| " + " | ".join(r) + " |" for r in rows[1:])
                caption: Any = node.find("TTITLE")
                if caption is not None and text_of(caption):
                    blocks.append("**" + text_of(caption) + "**")
                blocks.append("\n".join(lines))
            return
        for child in node:
            walk(child)

    walk(root)
    rendered = normalized("\n\n".join(b for b in blocks if b))
    if len(rendered.strip()) < 20:
        raise WlhError("full_text_xml_empty")
    return rendered


def gpo_xml_to_markdown(raw: bytes | str, context: Mapping[str, Any] | None = None) -> str:
    """Convert GPO bulk printing XML (HD/SECTNO/GPOTABLE/EXTRACT) into structured Markdown."""
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, recover=False, huge_tree=False
    )
    try:
        root: Any = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError:
        raise WlhError("federal_register_xml_invalid") from None
    blocks: list[str] = []
    for elem in list(root.iter()):
        if not isinstance(elem.tag, str):
            continue
        tag = elem.tag.upper()
        if tag in ("HD", "HED", "SUBAGY", "AGY", "SUBJECT"):
            text = compact("".join(cast(list[str], elem.itertext())))
            if not text:
                continue
            source_attr = str(elem.get("SOURCE") or "").upper()
            level = "##"
            if source_attr.startswith("HD1") or tag in ("AGY", "SUBJECT"):
                level = "#"
            elif source_attr.startswith("HD2") or tag == "SUBAGY":
                level = "##"
            elif source_attr.startswith("HD3"):
                level = "###"
            blocks.append(f"{level} {text}")
        elif tag in ("P", "FP", "EXTRACT"):
            ancestors = {
                a.tag.upper()
                for a in list(elem.iterancestors())
                if isinstance(a.tag, str)
            }
            if tag in ("P", "FP") and "EXTRACT" in ancestors:
                continue
            text = compact("".join(cast(list[str], elem.itertext())))
            if text:
                blocks.append(text)
    if not blocks:
        fallback = compact("".join(cast(list[str], root.itertext())))
        if fallback:
            blocks.append(fallback)
    if not blocks:
        raise WlhError("federal_register_xml_empty")
    return "\n\n".join(blocks).strip() + "\n"


def dokument_xml_to_markdown(root: Any) -> str:
    """Extract judicial decision sections from `<dokument>` XML into Markdown text."""
    return "\n\n".join(
        " ".join(n.itertext())
        for n in root.xpath(
            ".//leitsatz|.//tenor|.//tatbestand|.//entscheidungsgruende|.//gruende|.//gründe"
        )
    )


def gazette_html_to_markdown(
    html_content: str,
    base_url: str,
    fallback_parts: Sequence[str | None] = (),
) -> str:
    """Convert embedded HTML inside a Gazette XML node to Markdown with fallback sections."""
    cleaned_html = re.sub(r"<!--.*?-->", "", html_content, flags=re.S)
    cleaned_html = re.sub(r"<\s*$", "", cleaned_html.strip())
    cleaned_html = re.sub(
        r"</?(?:table|tbody|thead|tr|td|th)\b[^>]*>",
        "\n<p>",
        cleaned_html,
        flags=re.I,
    )
    body_md = html_to_markdown(
        b"<meta charset='utf-8'><div>" + cleaned_html.encode("utf-8") + b"</div>",
        base_url,
    )
    if not body_md.strip():
        body_md = normalized("\n\n".join(part for part in fallback_parts if part))
    return body_md


__all__ = [
    "CONTAINER_TAGS",
    "HEADINGS",
    "HEADING_TAGS",
    "PARAGRAPH_TAGS",
    "SKIP",
    "SKIP_TAGS",
    "dokument_xml_to_markdown",
    "gazette_html_to_markdown",
    "gpo_xml_to_markdown",
    "markdown",
    "text_of",
    "xml_to_markdown",
]
