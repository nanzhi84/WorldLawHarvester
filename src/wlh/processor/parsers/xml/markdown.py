"""Render legal XML mixed content without dropping text around inline references."""

from __future__ import annotations

import re
from typing import Any

import lxml.etree as _etree  # type: ignore[import-untyped]

from wlh.cleaners.text_cleaner import normalized
from wlh.core import WlhError
from wlh.processor.extractors.html.tables_and_lists import literal_text
from wlh.processor.parsers.xml.inline import INLINE_TAGS, inline_content
from wlh.processor.parsers.xml.tables import TABLE_TAGS, table_markdown

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


def _text(node: Any) -> str:
    return inline_content(node)


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
