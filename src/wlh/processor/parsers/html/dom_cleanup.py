"""Declarative HTML DOM cleanup and court search result helpers (Stage 3a)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from wlh.cleaners.dates import era_date
from wlh.cleaners.text_cleaner import compact
from wlh.core import WlhError, public_url
from wlh.processor.parsers.transforms import (
    reconcile_result_range,
    restore_inline_script_cells,
)

DETAIL_FIELDS = {
    "事件番号": "case_number",
    "事件名": "case_name",
    "裁判年月日": "judgment_date",
    "法廷名": "court_panel",
    "センサー": "court_panel",
    "裁判種別": "judgment_type",
    "結果": "outcome",
    "判例集等巻・号・頁": "reporter_citation",
    "原審裁判所名": "prior_court",
    "原審事件番号": "prior_case_number",
    "原審裁判年月日": "prior_judgment_date",
    "判示事項の要旨": "holding_summary",
    "裁判要旨": "judgment_summary",
    "参照法条": "referenced_statutes",
}


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
    for xp in exclude_xpaths:
        try:
            matched = node.xpath(xp)
        except Exception:
            continue
        if not isinstance(matched, list):
            continue
        for item in list(cast(list[Any], matched)):
            if item is node or not hasattr(item, "getparent"):
                continue
            parent = item.getparent()
            if parent is not None:
                parent.remove(item)
    return node


def court_id(url: str) -> str:
    query = parse_qs(urlsplit(url).query)
    if query.get("id"):
        return query["id"][0]
    match = re.search(r"/hanrei/(\d+)/detail", urlsplit(url).path)
    if match:
        return match.group(1)
    raise WlhError("court_case_id_missing")


def result_range(tree: Any) -> tuple[int, int, int]:
    return reconcile_result_range(tree)


def next_page_url(
    tree: Any, base_url: str, start: int, end: int, total: int
) -> str | None:
    if end >= total:
        return None
    for href in tree.xpath("//a[contains(normalize-space(.), '次へ')]/@href"):
        if isinstance(href, str) and href.strip() and not href.strip().startswith("#"):
            return public_url(urljoin(base_url, href.strip()))
    page_size = max(end - start + 1, 1)
    next_page = start // page_size + 2
    parts = urlsplit(base_url)
    query = parse_qs(parts.query, keep_blank_values=True)
    if "page" in query or "offset" in query:
        key = "page" if "page" in query else "offset"
        query[key] = [str(next_page if key == "page" else end)]
        return public_url(
            urlunsplit(
                (parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), "")
            )
        )
    return None


def parse_list_rows(tree: Any, base_url: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, link in enumerate(
        tree.xpath("//a[contains(@href, 'detail2') or contains(@href, '/detail')]")
    ):
        href = link.get("href")
        if not href:
            continue
        detail_url = public_url(urljoin(base_url, href))
        cid = court_id(detail_url)
        container = link.xpath("ancestor::tr[1] | ancestor::div[contains(@class, 'result')][1]")
        scope = container[0] if container else link.getparent()
        if scope is None:
            raise WlhError("court_list_row_scope_missing")
        row_nodes = [scope, *scope.xpath("following-sibling::tr[position() <= 6]")]
        pdfs: list[str] = []
        for node in row_nodes:
            for pdf_href in node.xpath(".//a[contains(@href, '.pdf')]/@href"):
                if isinstance(pdf_href, str) and pdf_href.strip():
                    pdfs.append(public_url(urljoin(base_url, pdf_href.strip())))
            if pdfs:
                break
        if not pdfs:
            raise WlhError("court_list_pdf_missing")
        text = compact(" ".join(compact(node.text_content()) for node in row_nodes))
        title = compact(link.text_content()) or text
        rows.append(
            {
                "id": cid,
                "list_index": index,
                "detail_url": detail_url,
                "pdf_url": pdfs[0],
                "title": title,
                "list_text": text,
                "judgment_date": era_date(text),
            }
        )
    return rows


def parse_detail_fields(tree: Any) -> dict[str, str]:
    values: dict[str, str] = {}
    for dt in tree.xpath("//dl/dt"):
        label = compact(dt.text_content())
        dd = dt.xpath("following-sibling::dd[1]")
        if label in DETAIL_FIELDS and dd:
            value = compact(dd[0].text_content())
            if value:
                values[DETAIL_FIELDS[label]] = value
    for tr in tree.xpath("//tr[th and td]"):
        label = compact(tr.xpath("string(./th[1])"))
        value = compact(tr.xpath("string(./td[1])"))
        if label in DETAIL_FIELDS and value:
            values.setdefault(DETAIL_FIELDS[label], value)
    return values


def verify_detail_fields(
    list_row: Mapping[str, Any], detail_fields: Mapping[str, str], pdf_url: str
) -> str:
    for required in ("case_number", "judgment_date", "court_panel"):
        if not detail_fields.get(required):
            raise WlhError(f"court_detail_missing_{required}")
    published_at = era_date(detail_fields["judgment_date"])
    if not published_at:
        raise WlhError("court_detail_invalid_judgment_date")
    list_date = list_row.get("judgment_date")
    if list_date and list_date != published_at:
        raise WlhError("court_list_detail_date_mismatch")
    if list_row.get("pdf_url") and public_url(str(list_row["pdf_url"])) != public_url(pdf_url):
        raise WlhError("court_list_detail_pdf_mismatch")
    return published_at


__all__ = [
    "DETAIL_FIELDS",
    "clean_site_dom",
    "court_id",
    "next_page_url",
    "parse_detail_fields",
    "parse_list_rows",
    "result_range",
    "verify_detail_fields",
]
