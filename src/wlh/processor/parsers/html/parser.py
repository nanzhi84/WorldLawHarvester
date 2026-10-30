"""Generic HTML parser: scoped link discovery, detail pages, volumes, attachments, and reconciled catalogs."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import parse_qs, unquote, urlencode, urlsplit, urlunsplit

from wlh.core import WlhError, guard, public_url
from wlh.processor.extractors.html.html_to_md import clean_html_tree
from wlh.processor.extractors.html.images import inline_glyph_text
from wlh.processor.extractors.word.office import detect_real_format
from wlh.processor.parsers import Base
from wlh.processor.parsers.html.document_builder import (
    BINARY_KINDS,
    GENERIC_ATTACHMENT_LABELS,
    TEXT_KINDS,
    WebDocumentBuilder,
)
from wlh.processor.parsers.html.document_builder import (
    doc_rules as _doc_rules,
)
from wlh.processor.parsers.html.document_builder import (
    reconcile_range_enabled as _reconcile_range_enabled,
)
from wlh.processor.parsers.html.dom_cleanup import (
    DEFAULT_ATTACHMENT_EXCLUDE_XPATHS,
    DEFAULT_ATTACHMENT_TITLE_XPATH,
    clean_attachment_title_attr,
)
from wlh.processor.parsers.html.fields import (
    DATE_FIELDS,
    clean_url,
    decode,
    field_value,
    file_name,
    parse_date,
    parse_html,
)
from wlh.processor.parsers.html.pagination import aspnet_next_page

_SEARCH = "/hanrei/search1/index.html"
_DETAIL = re.compile(r"/hanrei/(\d+)/detail(\d+)/index\.html$")
_PDF = re.compile(r"(?i)\.pdf(?:$|[?#])")
_RANGE = re.compile(r"([\d,]+)件中\s*([\d,]+)[～〜~-]([\d,]+)件を表示")
_OFFSET = re.compile(r"javascript:change_offset\((\d+)\)\s*;?", re.I)


def _text(node: Any) -> str:
    return " ".join(node.text_content().split())


def _parser_options(config: Mapping[str, Any]) -> Mapping[str, Any]:
    proc = config.get("processor")
    if isinstance(proc, Mapping):
        proc_map = cast(Mapping[str, Any], proc)
        parser_cfg = proc_map.get("parser")
        if isinstance(parser_cfg, Mapping):
            parser_map = cast(Mapping[str, Any], parser_cfg)
            if isinstance(parser_map.get("options"), Mapping):
                return cast(Mapping[str, Any], parser_map["options"])
    opts = config.get("adapter_options")
    return cast(Mapping[str, Any], opts) if isinstance(opts, Mapping) else {}


def _max_depth(config: Mapping[str, Any]) -> int:
    sched = config.get("scheduler")
    if isinstance(sched, Mapping):
        sched_map = cast(Mapping[str, Any], sched)
        if "max_depth" in sched_map:
            return int(sched_map["max_depth"])
    fetch = config.get("fetch")
    if isinstance(fetch, Mapping):
        fetch_map = cast(Mapping[str, Any], fetch)
        if "max_depth" in fetch_map:
            return int(fetch_map["max_depth"])
    return 0


def _entry_urls(config: Mapping[str, Any]) -> Sequence[str]:
    sched = config.get("scheduler")
    if isinstance(sched, Mapping):
        sched_map = cast(Mapping[str, Any], sched)
        if isinstance(sched_map.get("entry_urls"), Sequence):
            return [str(u) for u in cast(Sequence[Any], sched_map["entry_urls"])]
    entries = config.get("entry_urls")
    if isinstance(entries, Sequence):
        return [str(u) for u in cast(Sequence[Any], entries)]
    return ()


def _budget_documents(config: Mapping[str, Any]) -> int:
    sched = config.get("scheduler")
    if isinstance(sched, Mapping):
        sched_map = cast(Mapping[str, Any], sched)
        if isinstance(sched_map.get("budget"), Mapping):
            return int(cast(Mapping[str, Any], sched_map["budget"]).get("documents", 25))
    budget = config.get("budget")
    if isinstance(budget, Mapping):
        return int(cast(Mapping[str, Any], budget).get("documents", 25))
    return 25


def _url_rewrites(config: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    sched = config.get("scheduler")
    if isinstance(sched, Mapping):
        sched_map = cast(Mapping[str, Any], sched)
        if isinstance(sched_map.get("url_rewrites"), Sequence):
            return cast(Sequence[Mapping[str, Any]], sched_map["url_rewrites"])
    return ()


class HtmlParser(WebDocumentBuilder, Base):
    CONTRACT: str = "wlh.parser.html.v7"
    PLANNING_OPTIONS: tuple[str, ...] = ("max_pages",)

    @staticmethod
    def validate_options(options: Mapping[str, Any]) -> None:
        allowed = {"max_pages"}
        if set(options) - allowed:
            raise ValueError("unexpected_adapter_options")
        pages: Any = options.get("max_pages", 1)
        if type(pages) is not int or not 1 <= pages <= 10000:
            raise ValueError("invalid_max_pages")

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        opts = _parser_options(config)
        self.max_pages: int = int(opts.get("max_pages", 1))
        rules = _doc_rules(config)
        self.details: list[re.Pattern[str]] = [
            re.compile(p) for p in rules.get("detail_url_patterns", ())
        ]
        self.exclude_details: list[re.Pattern[str]] = [
            re.compile(p) for p in rules.get("exclude_detail_url_patterns", ())
        ]
        self.reconcile_range: bool = _reconcile_range_enabled(config)
        if self.reconcile_range:
            self.attachment = _PDF
            self.volume = None
        else:
            self.attachment = re.compile(
                rules.get("attachment_url_pattern")
                or r"(?i)\.(?:pdf|docx?|rtf|xlsx?|zip)(?:$|[?#])"
            )
            self.volume = (
                re.compile(rules["volume_pattern"]) if rules.get("volume_pattern") else None
            )
        self.detail_url: str | None = None

    def is_detail(self, url: str) -> bool:
        if self.detail_url and url == self.detail_url:
            return True
        if any(p.search(url) for p in self.exclude_details):
            return False
        return not self.details or any(p.search(url) for p in self.details)

    def plan_recheck(
        self, resources: Sequence[Mapping[str, Any]], state: dict[str, Any]
    ) -> dict[str, Any]:
        result = super().plan_recheck(resources, state)
        docs = _doc_rules(self.config)
        for target in result["targets"]:
            locator = target["identity"]["recheck_locator"]
            context_url = locator.get("title_context_url")
            if docs.get("binary_title_from_link") and context_url:
                target["inputs"][0]["request"]["meta"] = {"title_context_url": context_url}
                target["inputs"].append(self.title_context(context_url, locator["url"], 0))
        return result

    @staticmethod
    def title_context(url: str, document_url: str, depth: int) -> dict[str, Any]:
        return {
            "request": {"url": url, "role": "title_context", "depth": depth},
            "role": "title_context",
            "document_url": public_url(document_url),
        }

    def in_scope(self, url: str) -> bool:
        try:
            guard(self.config, url)
            return True
        except WlhError:
            return False

    def resolve_link(self, base: str, raw_href: str | None) -> str | None:
        link = clean_url(base, raw_href)
        if not link:
            return None
        for rw in _url_rewrites(self.config):
            pat = rw.get("pattern") or rw.get("match")
            rep = rw.get("replacement") if rw.get("replacement") is not None else rw.get("replace")
            if pat and rep is not None and re.search(str(pat), link):
                link = re.sub(str(pat), str(rep), link)
                break
        base_parts = urlsplit(base)
        parts = urlsplit(link)
        if (
            base_parts.query
            and not parts.query
            and parts.netloc == base_parts.netloc
            and not self.is_detail(link)
        ):
            with_query = urlunsplit(
                (parts.scheme, parts.netloc, parts.path, base_parts.query, "")
            )
            if self.is_detail(with_query):
                return with_query
        return link

    def parse(
        self, ctx: Any, task: Mapping[str, Any], acquisition: Mapping[str, Any]
    ) -> dict[str, Any]:
        if self.reconcile_range:
            return self._parse_reconciled(ctx, task, acquisition)
        request = task["payload"]
        if acquisition["outcome"] != "ok":
            return {"requests": [], "targets": []}
        role = request.get("role", "page")
        if role != "page":
            return {"requests": [], "targets": []}
        data = ctx.read(acquisition)
        kind = detect_real_format(data, "html" if acquisition["mime_type"] == "text/html" else "")
        url: str = str(acquisition["actual_url"] or acquisition["request_url"] or "")
        docs = _doc_rules(self.config)
        has_lv_rules = bool(docs.get("label_value_rules"))
        if kind == "html" and has_lv_rules and not self.details:
            tree_check, _, _ = parse_html(data, acquisition.get("charset"))
            if tree_check.xpath(
                '//*[contains(@class,"doc-number")]|//td[contains(.,"Số ký hiệu")]'
                '|//*[contains(@class,"name") and contains(.,"Số, ký hiệu")]'
            ):
                self.detail_url = url
        detail = self.is_detail(url)
        requests: list[dict[str, Any]] = []
        attachments: list[dict[str, Any]] = []
        inline_images: list[str] = []
        truncated: str | None = None
        wlh_trunc_code: str | None = None
        max_depth = _max_depth(self.config)
        entry_urls = _entry_urls(self.config)
        if kind == "html":
            tree, _, _ = parse_html(data, acquisition["charset"])
            qualification = docs.get("detail_xpath")
            if detail and qualification:
                detail = bool(tree.xpath(qualification))
            if detail and any(p.search(url) for p in self.exclude_details):
                detail = False
            if ctx.runtime.mode == "collect":
                wlh_trunc = tree.xpath("//*[@data-wlh-truncated]/@data-wlh-truncated")
                if wlh_trunc:
                    wlh_trunc_code = str(wlh_trunc[0])
                    truncated = truncated or wlh_trunc_code
            seen: set[str] = set()
            detail_paths: set[str] = set()
            unexpanded_paths: set[str] = set()
            link_scope = docs.get("link_scope_xpath")
            next_page_xp = docs.get("next_page_xpath")
            anchors: list[Any] = []
            if not detail and link_scope:
                scoped_expr = str(link_scope)
                if (
                    self.max_pages > 1
                    and next_page_xp
                    and str(next_page_xp) not in scoped_expr
                ):
                    scoped_expr = f"{scoped_expr} | {next_page_xp}"
                anchors = tree.xpath(scoped_expr)
            if not anchors:
                default_expr = (
                    "//a[@href] | //a[@docurl] | //a[@data-url] | //a[@data-href] "
                    "| //a[@onclick] | //frame[@src] | //iframe[@src]"
                )
                if not detail and self.max_pages > 1 and next_page_xp:
                    default_expr = f"{default_expr} | {next_page_xp}"
                anchors = tree.xpath(default_expr)
            raw_att_excl = docs.get("attachment_exclude_xpath")
            if isinstance(raw_att_excl, str) and raw_att_excl.strip():
                att_excl_xpaths: tuple[str, ...] = (raw_att_excl.strip(),)
            elif isinstance(raw_att_excl, Sequence) and raw_att_excl:
                att_excl_xpaths = tuple(
                    str(x).strip()
                    for x in cast(Sequence[Any], raw_att_excl)
                    if str(x).strip()
                )
            else:
                att_excl_xpaths = DEFAULT_ATTACHMENT_EXCLUDE_XPATHS
            att_title_xpath = (
                docs.get("attachment_title_xpath") or DEFAULT_ATTACHMENT_TITLE_XPATH
            )
            title_strip_regexes: Sequence[str] = tuple(
                str(x)
                for x in (
                    cast(dict[str, Any], self.config.get("cleaners") or {}).get(
                        "title_strip_regexes"
                    )
                    or ()
                )
                if x
            )
            for anchor in anchors:
                raw_href: str | None = (
                    anchor.get("href")
                    or anchor.get("docurl")
                    or anchor.get("data-url")
                    or anchor.get("data-href")
                )
                if (
                    not raw_href
                    or raw_href.strip() in ("#", "")
                    or raw_href.strip().lower().startswith("javascript:void")
                ):
                    raw_href = anchor.get("onclick") or anchor.get("src")
                link: str | None = self.resolve_link(url, raw_href)
                if not link or link in seen or not self.in_scope(link):
                    continue
                seen.add(link)
                if self.is_detail(link):
                    detail_paths.add(urlsplit(link).path)
                if self.attachment.search(unquote(link)) or self.attachment.search(file_name(link)):
                    if self.details and detail:
                        if any(anchor.xpath(xp) for xp in att_excl_xpaths):
                            continue
                        text = re.sub(r"\s+", " ", anchor.text_content()).strip()
                        if text.lower() in GENERIC_ATTACHMENT_LABELS:
                            alt_nodes: list[Any] = (
                                cast(list[Any], anchor.xpath(str(att_title_xpath)))
                                if att_title_xpath
                                else []
                            )
                            alt_text = (
                                re.sub(r"\s+", " ", str(alt_nodes[0].text_content())).strip()
                                if alt_nodes
                                else clean_attachment_title_attr(
                                    str(anchor.get("title") or ""),
                                    title_strip_regexes,
                                )
                            )
                            if alt_text and alt_text.lower() not in GENERIC_ATTACHMENT_LABELS:
                                text = alt_text
                        attachments.append({"url": link, "text": text})
                    elif not self.details or self.is_detail(link):
                        if ctx.follow_links(task):
                            follow: dict[str, Any] = {
                                "url": link,
                                "role": "page",
                                "depth": task["depth"] + 1,
                            }
                            if docs.get("binary_title_from_link"):
                                follow["meta"] = {"title_context_url": url}
                            requests.append(follow)
                        elif (
                            ctx.runtime.mode == "collect"
                            and not detail
                            and urlsplit(link).path != urlsplit(url).path
                            and link not in entry_urls
                        ):
                            unexpanded_paths.add(urlsplit(link).path)
                elif ctx.follow_links(task):
                    if (
                        not self.details
                        or self.is_detail(link)
                        or task["depth"] + 1 < max_depth
                    ):
                        requests.append({"url": link, "role": "page", "depth": task["depth"] + 1})
                    elif (
                        not detail
                        and urlsplit(link).path != urlsplit(url).path
                        and link not in entry_urls
                    ):
                        unexpanded_paths.add(urlsplit(link).path)
                elif (
                    ctx.runtime.mode == "collect"
                    and not detail
                    and urlsplit(link).path != urlsplit(url).path
                    and link not in entry_urls
                ):
                    unexpanded_paths.add(urlsplit(link).path)
            if (not self.details or not detail_paths) and (unexpanded_paths - detail_paths):
                truncated = truncated or "max_depth_exceeded"
            if not detail:
                page = cast(Mapping[str, Any], request.get("meta") or {}).get("page", 1)
                if ctx.follow_links(task):
                    next_req, asp_trunc = aspnet_next_page(
                        tree, url, page, self.max_pages, self.config
                    )
                    if next_req is not None:
                        requests.insert(0, next_req)
                    truncated = truncated or asp_trunc
                elif ctx.runtime.mode == "collect":
                    next_req, asp_trunc = aspnet_next_page(
                        tree, url, page, self.max_pages, self.config
                    )
                    if next_req is not None or asp_trunc:
                        truncated = truncated or asp_trunc or "max_depth_exceeded"
            elif docs.get("require_complete_text") or (
                isinstance(self.config.get("cleaners"), Mapping)
                and self.config["cleaners"].get("require_complete_text")
            ):
                img_tree, _, _ = parse_html(data, acquisition["charset"])
                content_xpath = docs.get("content_xpath")
                region_nodes = (
                    img_tree.xpath(content_xpath)
                    if content_xpath
                    else [img_tree.body if img_tree.body is not None else img_tree]
                )
                seen_imgs: set[str] = set()
                quirks = self.quirks
                use_glyphs = (
                    not quirks
                    or "ja_inline_glyphs" in quirks
                    or "ja_kanpo_pua" in quirks
                )
                for r_node in region_nodes:
                    clean_html_tree(r_node, quirks=tuple(quirks))
                    for img in r_node.xpath("descendant-or-self::img"):
                        if use_glyphs and inline_glyph_text(img) is not None:
                            continue
                        img_link = self.resolve_link(url, img.get("src"))
                        if img_link and img_link not in seen_imgs and self.in_scope(img_link):
                            seen_imgs.add(img_link)
                            inline_images.append(img_link)
        targets: list[dict[str, Any]] = []
        if detail and not wlh_trunc_code and (kind in TEXT_KINDS | BINARY_KINDS):
            inputs = [{"request": request, "role": "detail"}]
            locator: dict[str, Any] = {"url": public_url(request["url"]), "role": "page"}
            context_url = cast(Mapping[str, Any], request.get("meta") or {}).get("title_context_url")
            if kind in BINARY_KINDS and docs.get("binary_title_from_link"):
                if context_url:
                    locator["title_context_url"] = public_url(context_url)
                    inputs.append(self.title_context(context_url, request["url"], task["depth"]))
            for item in attachments:
                inputs.append(
                    {
                        "request": {
                            "url": item["url"],
                            "role": "attachment",
                            "depth": task["depth"],
                        },
                        "role": "attachment",
                        "text": item["text"],
                    }
                )
            for img_url in inline_images:
                inputs.append(
                    {
                        "request": {
                            "url": img_url,
                            "role": "attachment",
                            "depth": task["depth"],
                        },
                        "role": "inline_image",
                        "image_url": public_url(img_url),
                    }
                )
            targets.append(
                {
                    "identity": {
                        "namespace": "url",
                        "external_key": public_url(request["url"]),
                        "public_url": public_url(request["url"]),
                        "recheck_locator": locator,
                    },
                    "inputs": inputs,
                }
            )
        if self.details:
            requests.sort(
                key=lambda r: 0 if r.get("method") == "POST" or self.is_detail(r["url"]) else 1
            )
        result: dict[str, Any] = {
            "requests": requests,
            "targets": targets,
            **({"truncated": truncated} if truncated else {}),
        }
        if has_lv_rules and not docs.get("detail_url_patterns"):
            if result["targets"]:
                result["requests"] = []
            elif any(self.is_detail(r["url"]) for r in result["requests"]):
                limit = _budget_documents(self.config)
                det_reqs = [r for r in result["requests"] if self.is_detail(r["url"])]
                other_reqs = [r for r in result["requests"] if not self.is_detail(r["url"])]
                if (
                    len(det_reqs) > limit
                    or (len(det_reqs) == limit and bool(other_reqs))
                    or len(other_reqs) > limit
                ) and result.get("truncated") in (
                    None,
                    "document_budget_exceeded",
                    "max_depth_exceeded",
                    "page_limit_exceeded",
                ):
                    result["truncated"] = "document_budget_exceeded"
                result["requests"] = (
                    det_reqs[:limit] if len(det_reqs) >= limit else det_reqs + other_reqs[:limit]
                )
        return result

    def _parse_reconciled(
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
            return {"requests": [], "targets": [self._reconciled_target(tree, url, case[1], task)]}
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
            return {
                "requests": [
                    {
                        "url": action + "?" + urlencode({"filter[judgeDateMode]": "2"}),
                        "role": "page",
                        "depth": task["depth"],
                    }
                ],
                "targets": [],
            }
        if query.get("filter[judgeDateMode]") != ["2"] or any(
            key not in {"filter[judgeDateMode]", "offset", "sort"} for key in query
        ):
            raise WlhError("court_catalog_filtered_scope")
        return self._reconciled_catalog(ctx, tree, url, task, query)

    def _reconciled_catalog(
        self,
        ctx: Any,
        tree: Any,
        url: str,
        task: Mapping[str, Any],
        query: Mapping[str, list[str]],
    ) -> dict[str, Any]:
        raw_offset = query.get("offset", ["0"])
        if len(raw_offset) != 1 or not raw_offset[0].isdigit():
            raise WlhError("court_catalog_offset_invalid")
        offset = int(raw_offset[0])
        ranges = {
            tuple(int(part.replace(",", "")) for part in match.groups())
            for node in tree.xpath("//main//p")
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
            for anchor in row.xpath(".//a[@href]"):
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
                "requests": [],
                "targets": [],
                **(
                    {"truncated": "court_catalog_depth_exceeded"}
                    if ctx.runtime.mode == "collect"
                    else {}
                ),
            }
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
            "total": total,
            "case_ids": sorted(seen),
            "discovered": len(seen),
            "covered": covered,
            "growth": growth,
            "complete": covered >= total,
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
                requests.append(
                    {
                        "url": urlunsplit(
                            (parts.scheme, parts.netloc, parts.path, urlencode(next_query), "")
                        ),
                        "role": "page",
                        "depth": task["depth"],
                        "meta": {"catalog_total": total},
                    }
                )
        requests.extend(
            {"url": link, "role": "page", "depth": task["depth"] + 1} for link in new_links
        )
        return {"requests": requests, "targets": [], "state": state}

    def _reconciled_target(
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
                inputs.append(
                    {
                        "request": {"url": link, "role": "attachment", "depth": task["depth"]},
                        "role": "attachment",
                        "text": _text(anchor),
                    }
                )
                seen.add(link)
        if not seen:
            raise WlhError("court_required_judgment_missing")
        return {
            "identity": {
                "namespace": "japan-courts",
                "external_key": case_id,
                "public_url": public_url(url),
                "recheck_locator": {"url": public_url(url), "role": "page"},
            },
            "inputs": inputs,
        }


Adapter = HtmlParser

__all__ = [
    "Adapter",
    "BINARY_KINDS",
    "DATE_FIELDS",
    "HtmlParser",
    "TEXT_KINDS",
    "clean_url",
    "decode",
    "field_value",
    "file_name",
    "parse_date",
    "parse_html",
]
