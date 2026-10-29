"""Official Japanese court search protocol over archived responses.

The unfiltered result range is reconciled on every page; offset pagination does
not consume directory depth. Category views share the court's case identifier.
Detail HTML supplies metadata and required PDF links, never substitute judgment
text. The shared document builder retains the PDF originals and quality gates.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from wlh.adapters import Base
from wlh.adapters.web_document import WebDocumentBuilder
from wlh.adapters.web_fields import clean_url, parse_date, parse_html
from wlh.core import WlhError, public_url
from wlh.engine.scope import guard
from wlh.extraction.office import detect_real_format

_SEARCH = "/hanrei/search1/index.html"
_DETAIL = re.compile(r"/hanrei/(\d+)/detail(\d+)/index\.html$")
_PDF = re.compile(r"(?i)\.pdf(?:$|[?#])")
_RANGE = re.compile(r"([\d,]+)件中\s*([\d,]+)[～〜~-]([\d,]+)件を表示")
_OFFSET = re.compile(r"javascript:change_offset\((\d+)\)\s*;?", re.I)


def _text(node: Any) -> str:
    return " ".join(node.text_content().split())


def _fields(tree: Any) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in tree.xpath('//main//dl[dt and dd]'):
        for dt, dd in zip(item.xpath('./dt'), item.xpath('./dd')):
            key = _text(dt)
            value = _text(dd)
            if key in fields and fields[key] != value:
                raise WlhError("court_detail_fields_ambiguous")
            fields[key] = value
    return fields


class Adapter(WebDocumentBuilder, Base):
    CONTRACT = "wlh.adapter.japan-courts.v3"

    def __init__(self, config: dict[str, Any]) -> None:
        # These are protocol invariants, including when a caller omits optional
        # document rules. Do not mutate the frozen shared source configuration.
        super().__init__({
            **config,
            "documents": {**config["documents"], "detail_body": "never"},
        })
        self.attachment = _PDF
        self.volume = None

    def parse(
        self, ctx: Any, task: Mapping[str, Any], acquisition: Mapping[str, Any]
    ) -> dict[str, Any]:
        if acquisition["outcome"] != "ok" or task["payload"].get("role", "page") != "page":
            return {"requests": [], "targets": []}
        data = ctx.read(acquisition)
        if detect_real_format(data, "html") != "html":
            raise WlhError("court_catalog_not_html")
        tree, _, _ = parse_html(data, acquisition.get("charset"))
        url = str(acquisition["actual_url"] or acquisition["request_url"])
        case = _DETAIL.fullmatch(urlsplit(url).path)
        if case:
            return {"requests": [], "targets": [self._target(tree, url, case[1], task)]}
        if urlsplit(url).path != _SEARCH:
            raise WlhError("court_catalog_entry_unrecognized")
        query = parse_qs(urlsplit(url).query)
        if not query:
            forms = tree.xpath('//form[@name="hanrei-search"]')
            if len(forms) != 1 or forms[0].get("method", "GET").upper() != "GET":
                raise WlhError("court_catalog_form_missing")
            if not forms[0].xpath('.//input[@name="filter[judgeDateMode]"][@value="2"]'):
                raise WlhError("court_catalog_unfiltered_mode_missing")
            action = clean_url(url, forms[0].get("action"))
            if not action or urlsplit(action).path != _SEARCH:
                raise WlhError("court_catalog_form_action_invalid")
            guard(self.config, action)
            if not ctx.follow_links(task):
                return {"requests": [], "targets": [], "truncated": "court_catalog_depth_exceeded"}
            return {"requests": [{
                "url": action + "?" + urlencode({"filter[judgeDateMode]": "2"}),
                "role": "page", "depth": task["depth"],
            }], "targets": []}
        if query.get("filter[judgeDateMode]") != ["2"] or any(
            key not in {"filter[judgeDateMode]", "offset", "sort"} for key in query
        ):
            raise WlhError("court_catalog_filtered_scope")
        return self._catalog(ctx, tree, url, task, query)

    def _catalog(
        self, ctx: Any, tree: Any, url: str, task: Mapping[str, Any],
        query: Mapping[str, list[str]],
    ) -> dict[str, Any]:
        raw_offset = query.get("offset", ["0"])
        if len(raw_offset) != 1 or not raw_offset[0].isdigit():
            raise WlhError("court_catalog_offset_invalid")
        offset = int(raw_offset[0])
        ranges = {
            tuple(int(part.replace(",", "")) for part in match.groups())
            for node in tree.xpath('//main//p')
            if (match := _RANGE.fullmatch(_text(node)))
        }
        if len(ranges) != 1:
            raise WlhError("court_catalog_range_missing_or_ambiguous")
        total, start, end = ranges.pop()
        request_meta: Mapping[str, Any] = task["payload"].get("meta") or {}
        previous_total = request_meta.get("catalog_total")
        if previous_total is not None and total < previous_total:
            raise WlhError("court_catalog_total_changed")
        if not (1 <= start == offset + 1 <= end <= total):
            raise WlhError("court_catalog_range_mismatch")
        cases: dict[str, str] = {}
        rows = tree.xpath(
            '//table[contains(concat(" ",normalize-space(@class)," "),'
            '" search-result-table ")]//tbody/tr'
        )
        for row in rows:
            variants: dict[str, list[tuple[int, str]]] = {}
            for anchor in row.xpath('.//a[@href]'):
                link = clean_url(url, anchor.get("href"))
                case = _DETAIL.fullmatch(urlsplit(link).path) if link else None
                if case and link:
                    guard(self.config, link)
                    variants.setdefault(case[1], []).append((int(case[2]), link))
            if len(variants) != 1:
                raise WlhError("court_catalog_row_identity_missing_or_ambiguous")
            key, links = next(iter(variants.items()))
            if key in cases:
                raise WlhError("court_catalog_duplicate_case")
            cases[key] = min(links)[1]
        if len(cases) != end - start + 1:
            raise WlhError("court_catalog_range_mismatch")
        next_offsets: set[int] = set()
        for anchor in tree.xpath('//main//a[contains(@href,"change_offset")]'):
            match = _OFFSET.fullmatch(anchor.get("href", ""))
            if "次へ" in _text(anchor):
                if not match:
                    raise WlhError("court_catalog_next_offset_invalid")
                next_offsets.add(int(match[1]))
        if (end < total and next_offsets != {end}) or (end == total and next_offsets):
            raise WlhError("court_catalog_pagination_incomplete")
        if not ctx.follow_links(task):
            return {
                "requests": [], "targets": [],
                **({"truncated": "court_catalog_depth_exceeded"}
                   if ctx.runtime.mode == "collect" else {}),
            }
        # The engine commits this detached snapshot with the new page tasks.
        # Keep the unique population in the bounded run checkpoint, not copied
        # into every permanent request or shared as mutable adapter state.
        state: dict[str, Any] = ctx.adapter_state()
        previous: Any = {} if offset == 0 else state.get("japan_courts_catalog", {})
        if not isinstance(previous, dict):
            raise WlhError("court_catalog_state_invalid")
        catalog = cast(dict[str, Any], previous)
        prior_ids: Any = catalog.get("case_ids", [])
        if not isinstance(prior_ids, list) or not all(
            isinstance(key, str) and key.isdigit() for key in cast(list[Any], prior_ids)
        ):
            raise WlhError("court_catalog_state_invalid")
        seen_ids = cast(list[str], prior_ids)
        seen = set(seen_ids)
        prior_growth_raw: Any = catalog.get("growth", 0)
        if not isinstance(prior_growth_raw, int) or prior_growth_raw < 0:
            raise WlhError("court_catalog_state_invalid")
        prior_total = catalog.get("total", total)
        if not isinstance(prior_total, int) or total < prior_total:
            raise WlhError("court_catalog_total_changed")
        growth = prior_growth_raw + (total - prior_total)
        covered_raw: Any = catalog.get("covered", len(seen) + prior_growth_raw)
        if not isinstance(covered_raw, int) or covered_raw < 0:
            raise WlhError("court_catalog_state_invalid")
        if len(seen) != len(seen_ids) or not (
            covered_raw - prior_growth_raw <= len(seen) <= covered_raw
        ):
            raise WlhError("court_catalog_unique_count_mismatch")
        if len(seen.intersection(cases)) > growth:
            raise WlhError("court_catalog_duplicate_case_across_pages")
        new_links = [link for key, link in cases.items() if key not in seen]
        seen.update(cases)
        covered = covered_raw + (end - start + 1)
        if not (covered - growth <= len(seen) <= covered):
            raise WlhError("court_catalog_unique_count_mismatch")
        state["japan_courts_catalog"] = {
            "total": total, "case_ids": sorted(seen),
            "discovered": len(seen), "covered": covered,
            "growth": growth, "complete": covered >= total,
        }
        requests: list[dict[str, Any]] = []
        if next_offsets:
            parts = urlsplit(url)
            stride = end - start + 1
            window = 8 if stride == 30 else 1
            for step in range(window):
                target_offset = end + stride * step
                if target_offset >= total:
                    break
                next_query = {key: value[0] for key, value in query.items()}
                next_query["offset"] = str(target_offset)
                requests.append({
                    "url": urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(next_query), "")),
                    "role": "page", "depth": task["depth"],
                    "meta": {"catalog_total": total},
                })
        requests.extend(
            {"url": link, "role": "page", "depth": task["depth"] + 1}
            for link in new_links
        )
        return {"requests": requests, "targets": [], "state": state}

    def _target(
        self, tree: Any, url: str, case_id: str, task: Mapping[str, Any]
    ) -> dict[str, Any]:
        inputs: list[dict[str, Any]] = [{"request": task["payload"], "role": "detail"}]
        seen: set[str] = set()
        for anchor in tree.xpath('//main//dl[dt[normalize-space(.)="全文"]]/dd//a[@href]'):
            link = clean_url(url, anchor.get("href"))
            if not link or not _PDF.search(link):
                raise WlhError("court_required_document_not_pdf")
            guard(self.config, link)
            if link not in seen:
                inputs.append({
                    "request": {"url": link, "role": "attachment", "depth": task["depth"]},
                    "role": "attachment", "text": _text(anchor),
                })
                seen.add(link)
        if not seen:
            raise WlhError("court_required_judgment_missing")
        return {
            "identity": {
                "namespace": "japan-courts", "external_key": case_id,
                "public_url": public_url(url),
                "recheck_locator": {"url": public_url(url), "role": "page"},
            },
            "inputs": inputs,
        }

    def detail_values(
        self, data: bytes, kind: str, acquisition: Mapping[str, Any], url: str,
        text: str | None = None,
    ) -> tuple[dict[str, Any], str | None, str | None]:
        if kind != "html":
            raise WlhError("court_detail_not_html")
        tree, _, encoding = parse_html(data, acquisition.get("charset"))
        fields = _fields(tree)
        number = fields.get("事件番号")
        if not number:
            raise WlhError("court_case_number_missing")
        title = fields.get("事件名") or " ".join(
            value for value in (number, fields.get("権利種別")) if value
        )
        published = parse_date(fields["裁判年月日"]) if fields.get("裁判年月日") else None
        issuer = (
            fields.get("法廷名")
            or fields.get("裁判所名・部")
            or " ".join(v for v in (fields.get("裁判所名"), fields.get("部名")) if v)
            or None
        )
        return {
            "document_number": number,
            "issuer": issuer,
            "published_at": published,
            "effective_at": None,
            "status": fields.get("結果") or fields.get("判決結果"),
        }, title, encoding

    def build(
        self, ctx: Any, target: Mapping[str, Any], inputs: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        if len(inputs) < 2:
            raise WlhError("court_required_judgment_missing")
        for item in inputs[1:]:
            if detect_real_format(ctx.read(item["acquisition"])) != "pdf":
                raise WlhError("court_required_document_not_pdf")
        records = super().build(ctx, target, inputs)
        for rec in records:
            for deliv in cast(list[dict[str, Any]], rec.get("deliverables") or []):
                md = str(deliv.get("markdown") or "")
                if len(re.sub(r"\s+", "", md)) < 15:
                    raise WlhError("quality_check_failed:vision_reparse_pages")
        return records
