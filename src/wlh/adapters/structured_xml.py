"""Official legislative XML: AKN 3.0, USLM, Taiwan MojLawSplit and Japan e-Gov.

Namespaces identify vocabulary, never trigger external entity/schema downloads.
Body text stays in source order; this adapter does not infer a legal ontology.
"""

import re

import lxml.etree as etree

from wlh.adapters.bridges.common import RecordAdapter
from wlh.core import WlhError
from wlh.extraction.markdown import normalized


def text(node):
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def select(root, *names, attribute=None):
    for name in names:
        nodes = root.xpath(".//*[local-name()=$name]", name=name)
        for node in nodes:
            value = node.get(attribute) if attribute else text(node)
            if value:
                return value
    return None


def markdown(root):
    parts = []
    headings = {
        "heading",
        "longTitle",
        "docTitle",
        "LawTitle",
        "LawName",
        "法規名稱",
        "ArticleCaption",
        "ChapterTitle",
    }
    paragraphs = {
        "p",
        "paragraph",
        "Sentence",
        "LawContent",
        "法規內容",
        "條文內容",
        "ParagraphSentence",
    }

    def walk(node):
        if not isinstance(node.tag, str):
            return
        tag = etree.QName(node).localname
        if tag in {"meta", "metadata", "identification", "preface", "Notes", "附註"}:
            return
        if tag in headings or tag in paragraphs:
            value = text(node)
            if value:
                parts.append(("## " if tag in headings else "") + value)
            return
        if tag in {"table", "Table"}:
            rows = []
            for row in node.xpath('.//*[local-name()="tr" or local-name()="TableRow"]'):
                rows.append([text(c).replace("|", "\\|") for c in row])
            if rows:
                width = max(map(len, rows))
                rows = [r + [""] * (width - len(r)) for r in rows]
                rows.insert(1, ["---"] * width)
                parts.append("\n".join("| " + " | ".join(r) + " |" for r in rows))
            return
        if not len(node) and node.text and node.text.strip():
            parts.append(node.text.strip())
            return
        for child in node:
            walk(child)

    walk(root)
    return normalized("\n\n".join(parts))


class Adapter(RecordAdapter):
    CONTRACT = "wlh.adapter.structured-xml.v1"
    NAMESPACE = "legislative-xml"

    def records(self, data, acquisition):
        try:
            root = etree.fromstring(
                data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
            )
        except etree.XMLSyntaxError:
            raise WlhError("legal_xml_invalid") from None
        if root.getroottree().docinfo.doctype:
            raise WlhError("legal_xml_doctype_rejected")
        tag = etree.QName(root).localname
        namespace = etree.QName(root).namespace or ""
        if tag == "akomaNtoso" or "/akn/3.0" in namespace:
            number = select(root, "docNumber") or select(root, "FRBRnumber", attribute="value")
            title = select(root, "docTitle", "shortTitle", "longTitle")
            key = select(root, "FRBRuri", attribute="value") or number
            published = select(root, "FRBRdate", attribute="date")
            issuer = select(root, "FRBRauthor", attribute="href")
            if issuer and issuer.startswith("#"):
                matches = root.xpath(".//*[@eId=$id]", id=issuer[1:])
                issuer = matches[0].get("showAs") if matches else None
            entries = [(root, title, number, key, published, issuer)]
        elif "uslm" in namespace.lower() or tag in {"lawDoc", "bill", "statute"}:
            title = select(root, "docTitle", "longTitle", "dcTitle", "title")
            number = select(root, "docNumber", "number", "identifier")
            published = select(root, "date", attribute="date") or select(root, "date")
            entries = [(root, title, number, root.get("identifier") or number, published, None)]
        elif tag in {"Law", "DataRoot"} and (select(root, "LawTitle") or root.xpath(".//LawBody")):
            laws = [root] if tag == "Law" else root.xpath(".//Law")
            entries = []
            for law in laws:
                title, number = select(law, "LawTitle"), select(law, "LawNum")
                year = law.get("Year")
                if year and law.get("Era") in {"Meiji", "Taisho", "Showa", "Heisei", "Reiwa"}:
                    year = str(
                        int(year)
                        + {
                            "Meiji": 1867,
                            "Taisho": 1911,
                            "Showa": 1925,
                            "Heisei": 1988,
                            "Reiwa": 2018,
                        }[law.get("Era")]
                    )
                published = (
                    f"{int(year):04d}-{int(law.get('PromulgateMonth')):02d}-{int(law.get('PromulgateDay')):02d}"
                    if year and law.get("PromulgateMonth") and law.get("PromulgateDay")
                    else None
                )
                entries.append((law, title, number, number, published, None))
        elif tag in {"MojLawSplit", "Laws", "法規", "法規資料"} or select(
            root, "法規名稱", "LawName"
        ):
            nodes = root.xpath('.//*[local-name()="法規" or local-name()="Law"]') or [root]
            entries = [
                (
                    n,
                    select(n, "法規名稱", "LawName"),
                    select(n, "法規字號", "LawNo"),
                    select(n, "法規網址", "LawURL") or select(n, "法規名稱", "LawName"),
                    select(n, "最新異動日期", "LawModifiedDate", "LawPublishedDate"),
                    None,
                )
                for n in nodes
            ]
        else:
            raise WlhError("unsupported_legal_xml_vocabulary")
        result = []
        for node, title, number, key, published, issuer in entries:
            if not title or not key:
                raise WlhError("legal_xml_identity_missing")
            if published and re.fullmatch(r"\d{8}", published):
                published = published[:4] + "-" + published[4:6] + "-" + published[6:]
            result.append(
                {
                    "external_key": key,
                    "title": title,
                    "doc_number": number,
                    "published_at": published,
                    "issuer": issuer,
                    "doc_type": "法律法规",
                    "text": "# " + title + "\n\n" + markdown(node),
                    "detail_url": acquisition["actual_url"],
                }
            )
        return result
