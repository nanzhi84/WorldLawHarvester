"""Offline evidence checks shared by annotation, review and dataset publication.

These checks reject demonstrably unsupported output. They do not certify that a
model selected every relevant source paragraph; that requires a recorded review.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any, cast
from urllib.parse import unquote, urljoin, urlsplit

from wlh.benchmark.annotation_schema import ContentPageGroundTruth, ListPageGroundTruth
from wlh.benchmark.date_evidence import date_evidence, literal_dates
from wlh.benchmark.markdown_evidence import markdown_evidence
from wlh.benchmark.source_evidence import (
    document_number_groups,
    literal_navigation_target,
    source_body_blocks,
)

OMISSIONS = re.compile(
    r"(?:此处|其余|其餘|后续|後續|下文|详细|詳細).{0,20}(?:省略|略去)|"
    r"(?:content|text|remaining|sections?).{0,25}(?:truncated|omitted)|"
    r"(?:此处为图片内容|本页面包含|原文未提供|content fallback|substantive content simulated)",
    re.IGNORECASE,
)
POLLUTION = re.compile(
    r"#unselected|\{(?:host|port|base_url)\}|data-wlh-pages\s*=|<script[^>]+id=[\"']wlh-", re.I
)


class VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "head", "noscript", "svg"}:
            self.hidden.append(tag)
        if self.hidden:
            return
        for name, value in attrs:
            if value and name in {"href", "src", "data-src"}:
                self.links.append(value)
            elif value and tag == "button" and name == "data-bind":
                target = literal_navigation_target(value)
                if target is not None:
                    self.links.append(target)
            if value and name in {"alt", "title"}:
                self.parts.append(value)

    def handle_endtag(self, tag: str) -> None:
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()

    def handle_data(self, data: str) -> None:
        if not self.hidden and data.strip():
            self.parts.append(data)


class ExplicitClauses(HTMLParser):
    """Find numbered provisions in explicitly marked article/main paragraphs.

    This is deliberately a narrow omission detector, not an article extractor.
    Generic pages, attachment shells and unmarked body boundaries still need review.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.container = 0
        self.paragraph: list[str] | None = None
        self.clauses: list[str] = []
        self.table_depth = 0
        self.table_interactive = False
        self.table_text: list[str] = []
        self.tables: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"article", "main"}:
            self.container += 1
        if tag == "p" and self.container:
            self.paragraph = []
        if tag == "table" and self.container:
            if not self.table_depth:
                self.table_text = []
                self.table_interactive = False
            self.table_depth += 1
        if self.table_depth and (
            tag in {"form", "input", "button", "select", "textarea", "script"}
            or any(name.startswith("on") for name, _ in attrs)
        ):
            # Access controls and tooltips may share a metadata table with
            # public facts. Their business boundary requires explicit review.
            self.table_interactive = True
        if self.table_depth and tag == "img" and dict(attrs).get("alt"):
            # An explicit article table's labels include image alternatives.
            # A valid icon target does not make omission of its label complete.
            self.table_text.append(dict(attrs)["alt"] or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self.paragraph is not None:
            text = "".join(self.paragraph).strip()
            if re.match(
                r"(?:第[\d一二三四五六七八九十百千万零〇]+[条條]|最后一条|最後一條|(?:Article|Section)\s+\d)",
                text,
                re.I,
            ):
                if len(normalize(text)) >= 15:
                    self.clauses.append(normalize(text))
            self.paragraph = None
        if tag == "table" and self.table_depth:
            self.table_depth -= 1
            if not self.table_depth:
                text = normalize("".join(self.table_text))
                if text and not self.table_interactive:
                    self.tables.append(text)
        if tag in {"article", "main"}:
            self.container = max(0, self.container - 1)

    def handle_data(self, data: str) -> None:
        if self.paragraph is not None:
            self.paragraph.append(data)
        if self.table_depth:
            self.table_text.append(data)


def normalize(text: str) -> str:
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", html.unescape(text))).lower()


def visible_text(raw: str) -> str:
    if raw.lstrip().startswith(("{", "[")):
        try:
            parsed: Any = json.loads(raw)
        except ValueError:
            return raw

        def strings(value: Any) -> list[str]:
            if isinstance(value, str):
                return [visible_text(value) if "<" in value and ">" in value else value]
            if isinstance(value, dict):
                return [s for v in cast(dict[str, Any], value).values() for s in strings(v)]
            if isinstance(value, list):
                return [s for v in cast(list[Any], value) for s in strings(v)]
            return [str(value)] if isinstance(value, (int, float)) else []

        return "\n".join(strings(parsed))
    parser = VisibleText()
    parser.feed(raw)
    return "\n".join(parser.parts) or raw


def markdown_text(markdown: str) -> str:
    return normalize(markdown_evidence(markdown).text)


def json_url_targets(raw: str, request_url: str) -> set[str]:
    """Resolve explicit JSON URL fields, never derive links from record IDs."""
    if not raw.lstrip().startswith(("{", "[")):
        return set()
    try:
        value: Any = json.loads(raw)
    except ValueError:
        return set()
    targets: set[str] = set()

    def visit(node: Any, field: str = "") -> None:
        if isinstance(node, dict):
            for key, child in cast(dict[str, Any], node).items():
                visit(child, key.lower())
        elif isinstance(node, list):
            for child in cast(list[Any], node):
                visit(child, field)
        elif isinstance(node, str) and (
            field in {"url", "urls", "href", "src", "uri", "@id"}
            or field.endswith(("_url", "_urls"))
        ):
            if node.strip():
                targets.add(urljoin(request_url, html.unescape(node)))

    visit(value)
    return targets


class UrlEvidence:
    """Reuse one original response's URL evidence within one validation call.

    Complete native XML inventories may contain tens of thousands of targets.
    Index literal URLs once, retaining the previous substring/relative-link
    checks for unusual source representations. No cross-input cache or truncation.
    """

    def __init__(self, raw: str, request_url: str) -> None:
        self.raw = raw
        self.decoded = html.unescape(raw).replace("\\/", "/")
        self.request_url = request_url
        self.literal_urls = set(re.findall(r"https?://[^\s<>\"']+", self.decoded))
        self.literal_urls.update(unquote(url) for url in tuple(self.literal_urls))
        self.literal_urls.update(json_url_targets(raw, request_url))
        self.unquoted: str | None = None
        self.relative_urls: set[str] | None = None

    def contains(self, url: str) -> bool:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            return False
        if (
            re.search(r"[\x00-\x1f\x7f{}]", url)
            or any(char.isspace() and char != " " for char in url)
            or " " in parsed.netloc
            or url != url.strip()
        ):
            return False
        if url in self.literal_urls or unquote(url) in self.literal_urls:
            return True
        if url in self.decoded:
            return True
        if self.unquoted is None:
            self.unquoted = unquote(self.decoded)
        if unquote(url) in self.unquoted:
            return True
        if self.relative_urls is None:
            parser = VisibleText()
            # Decode entities inside attributes, after parsing their boundaries.
            # Pre-decoding &quot; would turn a literal target into broken markup.
            parser.feed(self.raw)
            self.relative_urls = {urljoin(self.request_url, link) for link in parser.links}
            self.relative_urls.update(unquote(link) for link in tuple(self.relative_urls))
        return url in self.relative_urls or unquote(url) in self.relative_urls


def source_fragments(raw: str) -> list[str]:
    """Inspect complete JSON text members separately, without joining records."""
    if not raw.lstrip().startswith(("{", "[")):
        return [raw]
    try:
        value: Any = json.loads(raw)
    except ValueError:
        return [raw]

    def strings(node: Any) -> list[str]:
        if isinstance(node, str):
            return [node]
        if isinstance(node, dict):
            return [part for v in cast(dict[str, Any], node).values() for part in strings(v)]
        if isinstance(node, list):
            return [part for v in cast(list[Any], node) for part in strings(v)]
        return []

    return strings(value)


def validate_ground_truth_integrity(
    gt: object,
    task_type: str = "content_page",
    raw_payload: str | None = None,
    request_url: str | None = None,
    *,
    mime_type: str = "text/html",
    jurisdiction: str | None = None,
) -> tuple[bool, str]:
    if not isinstance(gt, Mapping):
        return False, "ground_truth_not_object"
    if mime_type not in {"text/html", "application/json", "application/xml"}:
        return False, "unsupported_original_media"
    try:
        schema = ListPageGroundTruth if task_type == "list_page" else ContentPageGroundTruth
        data: dict[str, Any] = schema.model_validate(gt).model_dump()
    except ValueError:
        return False, "invalid_ground_truth_schema"
    raw = raw_payload or ""
    base = request_url or ""
    url_evidence = UrlEvidence(raw, base)
    if task_type == "list_page":
        urls: list[str] = data["target_urls"]
        if len(urls) != len(set(urls)):
            return False, "duplicate_target_urls"
        if raw and any(not url_evidence.contains(u) for u in urls):
            return False, "unattested_target_url"
        return True, "valid_list_ground_truth"
    pool = normalize(visible_text(raw))
    raw_pool = normalize(raw)
    body_pool = "\x00".join(normalize(block) for block in source_body_blocks(raw, mime_type))
    dates = literal_dates(date_evidence(raw), jurisdiction=jurisdiction)
    number_groups: set[str] = (
        {normalize(group) for group in document_number_groups(raw)}
        if jurisdiction == "JP"
        else set()
    )
    for i, record in enumerate(data["records"]):
        md = record["markdown"]
        rendered = markdown_evidence(md)
        if rendered.hidden_content:
            return False, f"record_{i}:hidden_output_content"
        if any(match.group(0) not in raw for match in OMISSIONS.finditer(md)):
            return False, f"record_{i}:unattested_omission_or_commentary"
        if md.rstrip().endswith(("…(truncated)", "...(truncated)", "\\")):
            return False, f"record_{i}:truncated_markdown"
        if not raw:
            continue
        for label in re.findall(r"\*\*([^*\n]{1,40})\*\*\s*[：:]", md):
            if normalize(label) not in pool and normalize(label) not in raw_pool:
                return False, f"record_{i}:unattested_metadata_label"
        for field in ("title", "doc_number", "issuer"):
            value = record.get(field)
            if field == "doc_number" and value and normalize(value) in number_groups:
                continue
            if field == "issuer" and value and "、" in value:
                # Joining complete co-signers is formatting; joining fragments
                # across source blocks or borrowing a script literal is not.
                names = [normalize(name) for name in value.split("、")]
                if all(
                    name and any(name in block for block in body_pool.split("\x00"))
                    for name in names
                ):
                    continue
                return False, f"record_{i}:unattested_{field}"
            if value and normalize(value) not in pool and normalize(value) not in raw_pool:
                return False, f"record_{i}:unattested_{field}"
        for field in ("published_at", "effective_at"):
            value = record.get(field)
            if value and value not in dates:
                return False, f"record_{i}:unattested_{field}"
        urls = list(record.get("attachment_urls", []))
        urls.extend(rendered.urls)
        # urljoin strips ASCII controls; reject them before resolving a relative
        # reference so an altered destination cannot normalize into valid evidence.
        if any(
            re.search(r"[\x00-\x1f\x7f]", u) or not url_evidence.contains(urljoin(base, u))
            for u in urls
        ):
            return False, f"record_{i}:unattested_attachment_url"
        # Compare continuous content, rather than bags of characters. Adjacent
        # HTML text nodes join before matching so inline tags do not fabricate gaps.
        # Paragraph boundaries need not be adjacent in the source: navigation or
        # metadata can sit between a heading and its body/attachment. Validate
        # each block without inventing cross-boundary n-grams. This remains a
        # heuristic rejection check, not semantic completeness certification.
        grams: list[str] = []
        # A soft line break or an explicit label/value colon may preserve a
        # source paragraph/cell boundary even within one rendered paragraph.
        # Do not require invented n-grams across those visible separators.
        blocks = [part for block in rendered.blocks for part in re.split(r"[\r\n：:]+", block)]
        for block in blocks:
            text = normalize(block)
            width = min(12, len(text))
            if not width:
                continue
            grams.extend(
                text[n : n + width] for n in range(0, len(text) - width + 1, max(1, width // 2))
            )
        if grams:
            unsupported = [g for g in grams if g not in body_pool]
            if len(unsupported) / len(grams) > 0.08:
                return False, f"record_{i}:unsupported_body_spans:{len(unsupported)}/{len(grams)}"
    if raw:
        clauses = ExplicitClauses()
        for fragment in source_fragments(raw):
            clauses.feed(fragment)
        output_pool = "".join(markdown_text(record["markdown"]) for record in data["records"])
        for clause in clauses.clauses:
            if clause not in output_pool:
                return False, "missing_explicit_source_clause"
        for table in clauses.tables:
            if table not in output_pool:
                return False, "missing_explicit_source_table"
    return True, "grounded_output_pending_completeness_review"
