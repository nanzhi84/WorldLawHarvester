"""HTML decoding, URL cleaning, date normalization and declarative field extraction for HTMLParser."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any, cast
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

from wlh.cleaners.dates import (
    era_date,
    infer_effective_date,
    parse_date,
    parse_japanese_date,
    roc_date,
)
from wlh.cleaners.text_cleaner import compact, enrich_bare_title
from wlh.core import WlhError, public_url
from wlh.processor.extractors.html.dom_tree import decode, document_tree, first_text, parse_html

DATE_FIELDS: set[str] = {
    "published_at",
    "effective_at",
    "expired_at",
    "issued_date",
    "effective_date",
    "expired_date",
}

FIELD_ALIASES: dict[str, str] = {
    "issued_date": "published_at",
    "doc_num": "document_number",
    "effective_date": "effective_at",
    "expired_date": "expired_at",
}


def clean_url(base: str, href: str | None) -> str | None:
    if not href:
        return None
    cleaned: str = href.strip()
    if cleaned.lower().startswith("javascript:") or re.match(r"^[A-Za-z_][\w.]*\s*\(", cleaned):
        js_match: re.Match[str] | None = re.search(
            r"""['"]((?:https?://|/)[^'"]+)['"]""", cleaned
        )
        if not js_match:
            return None
        cleaned = str(js_match.group(1) or "").strip()
    if not cleaned or cleaned.lower().startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
        return None
    try:
        base_parts: Any = urlsplit(base)
        parts: Any = urlsplit(urljoin(base, cleaned))
        if parts.scheme not in ("http", "https"):
            return None
        scheme: str = (
            "https"
            if parts.scheme == "http"
            and base_parts.scheme == "https"
            and parts.netloc == base_parts.netloc
            else parts.scheme
        )
        return public_url(
            urlunsplit((scheme, parts.netloc, parts.path or "/", parts.query, ""))
        )
    except (WlhError, ValueError):
        return None


def file_name(url: str) -> str:
    parts: Any = urlsplit(url)
    query_name = parse_qs(parts.query).get("file_name", [""])[0]
    name = PurePosixPath(query_name or unquote(parts.path)).name
    return name or "file"


def _split_top_level_union(xpath_expr: str) -> list[str]:
    """Split top-level `|` union branches (outside brackets/parens/quotes) to preserve left-to-right priority."""
    branches: list[str] = []
    cur: list[str] = []
    paren_depth = 0
    bracket_depth = 0
    quote_char: str | None = None
    for ch in xpath_expr:
        if quote_char is not None:
            cur.append(ch)
            if ch == quote_char:
                quote_char = None
        elif ch in ("'", '"'):
            quote_char = ch
            cur.append(ch)
        elif ch == "(":
            paren_depth += 1
            cur.append(ch)
        elif ch == ")":
            paren_depth = max(0, paren_depth - 1)
            cur.append(ch)
        elif ch == "[":
            bracket_depth += 1
            cur.append(ch)
        elif ch == "]":
            bracket_depth = max(0, bracket_depth - 1)
            cur.append(ch)
        elif ch == "|" and paren_depth == 0 and bracket_depth == 0:
            part = "".join(cur).strip()
            if part:
                branches.append(part)
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        branches.append(tail)
    return branches if len(branches) > 1 else [xpath_expr]


def field_value(
    tree: Any, text: str | None, rule: Mapping[str, Any], *, is_date: bool = False
) -> Any:
    if rule.get("value") is not None:
        return rule["value"]
    if rule.get("xpath"):
        if tree is None:
            return None
        branches = _split_top_level_union(str(rule["xpath"]))
        if len(branches) > 1:
            for branch in branches:
                res = field_value(tree, text, {**rule, "xpath": branch}, is_date=is_date)
                if res is not None:
                    return res
            return None
        found: Any = tree.xpath(rule["xpath"])
        if isinstance(found, list):
            if not found:
                return None
            if rule.get("regex"):
                pat = re.compile(cast(str, rule["regex"]))
                for item in cast(list[Any], found):
                    val_str: Any = item if isinstance(item, str) else item.text_content()
                    cleaned = re.sub(r"[^\S\u3000]+", " ", str(val_str)).strip()
                    match = pat.search(cleaned)
                    if match:
                        val = match.groupdict().get("value") or (
                            match.group(1) if match.groups() else match[0]
                        )
                        if not val.strip():
                            continue
                        if rule.get("date_format") or is_date:
                            return parse_date(val, rule.get("date_format"))
                        return val.strip() or None
                return None
            first: Any = cast(list[Any], found)[0]
            value: Any = first if isinstance(first, str) else first.text_content()
        elif isinstance(found, str):
            value = found
        elif found is None or isinstance(found, bool):
            return None
        else:
            value = str(found)
    else:
        value = text
    if value is None:
        return None
    # Preserve ideographic spaces in source metadata.
    value = re.sub(r"[^\S\u3000]+", " ", str(value)).strip()
    if rule.get("regex"):
        match = re.search(cast(str, rule["regex"]), value)
        if not match:
            return None
        value = match.groupdict().get("value") or (
            match.group(1) if match.groups() else match[0]
        )
    if not value.strip():
        return None
    if rule.get("date_format") or is_date:
        return parse_date(value, rule.get("date_format"))
    return value.strip() or None


def _node_text(item: Any) -> str:
    if isinstance(item, str):
        return re.sub(r"\s+", " ", item).strip()
    if hasattr(item, "text_content"):
        return re.sub(r"\s+", " ", str(item.text_content())).strip()
    return re.sub(r"\s+", " ", str(item)).strip()


def extract_label_value_fields(
    tree: Any,
    rules: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """Extract metadata from two-column tables or <dl><dt><dd> pairs via ``label_value_rules``."""
    extracted: dict[str, str] = {}
    if tree is None:
        return extracted
    for rule in rules:
        row_xpath = str(rule.get("row_xpath") or "")
        label_xpath = str(rule.get("label_xpath") or ".//dt | .//th")
        value_xpath = str(rule.get("value_xpath") or ".//dd | .//td")
        mapping_raw = rule.get("mapping") or rule.get("label_map")
        if not row_xpath or not label_xpath or not value_xpath or not isinstance(mapping_raw, Mapping):
            continue
        mapping = cast(Mapping[str, Any], mapping_raw)
        try:
            rows = tree.xpath(row_xpath)
        except Exception:
            continue
        if not isinstance(rows, list):
            continue
        for row in cast(list[Any], rows):
            try:
                lbl_nodes = row.xpath(label_xpath)
                val_nodes = row.xpath(value_xpath)
            except Exception:
                continue
            pairs: list[tuple[str | None, str | None]] = []
            if (
                isinstance(lbl_nodes, list)
                and isinstance(val_nodes, list)
                and len(cast(list[Any], lbl_nodes)) > 1
                and len(cast(list[Any], lbl_nodes)) == len(cast(list[Any], val_nodes))
            ):
                pairs = [
                    (_node_text(l_item), _node_text(v_item))
                    for l_item, v_item in zip(
                        cast(list[Any], lbl_nodes), cast(list[Any], val_nodes)
                    )
                ]
            else:
                pairs = [(first_text(row, label_xpath), first_text(row, value_xpath))]
            for label, value in pairs:
                if not label or not value:
                    continue
                clean_label = re.sub(r"[\s,:：]+", "", label).casefold()
                for map_label, target_field in mapping.items():
                    norm_map_label = re.sub(r"[\s,:：]+", "", map_label).casefold()
                    if clean_label == norm_map_label or label.rstrip(":") == map_label.rstrip(":"):
                        extracted.setdefault(str(target_field), value)
    return extracted



def extract_detail_fields(
    fields: Mapping[str, Any],
    data: bytes,
    kind: str,
    acquisition: Mapping[str, Any],
    url: str,
    text: str | None = None,
    *,
    label_value_rules: Sequence[Mapping[str, Any]] = (),
    infer_effective: bool = True,
    enrich_title: bool = True,
) -> tuple[dict[str, Any], str | None, str | None]:
    values: dict[str, Any] = {}
    if kind == "html":
        tree, _, encoding = parse_html(data, acquisition.get("charset"))
        wlh_trunc = tree.xpath("//*[@data-wlh-truncated]/@data-wlh-truncated")
        if wlh_trunc:
            raise WlhError(str(wlh_trunc[0]))
        if label_value_rules:
            lv_extracted = extract_label_value_fields(tree, label_value_rules)
            for raw_key, raw_val in lv_extracted.items():
                canonical_key = FIELD_ALIASES.get(raw_key, raw_key)
                if canonical_key in DATE_FIELDS or raw_key in DATE_FIELDS:
                    try:
                        values[canonical_key] = parse_date(raw_val)
                    except WlhError:
                        pass
                else:
                    values[canonical_key] = raw_val
        for name, rule in fields.items():
            canonical_name = FIELD_ALIASES.get(name, name)
            val = field_value(
                tree, tree.text_content(), rule, is_date=(canonical_name in DATE_FIELDS)
            )
            if val is not None or canonical_name not in values:
                values[canonical_name] = val
        if (
            infer_effective
            and ("effective_at" in fields or "effective_date" in fields)
            and not values.get("effective_at")
        ):
            values["effective_at"] = infer_effective_date(
                text, published_at=values.get("published_at"), tree=tree
            )
        h1: str | None = next(
            (
                re.sub(r"\s+", " ", node.text_content()).strip()
                for node in tree.xpath("//h1")
                if re.sub(r"\s+", " ", node.text_content()).strip()
            ),
            None,
        )
        page_title: str | None = next(
            (
                re.sub(r"\s+", " ", node.text_content()).strip()
                for node in tree.xpath("//title")
                if re.sub(r"\s+", " ", node.text_content()).strip()
            ),
            None,
        )
        candidate_title = values.get("title") or h1 or page_title
        resolved_title = (
            enrich_bare_title(
                candidate_title,
                values.get("document_number"),
                tree=tree,
                body_text=text,
            )
            if enrich_title
            else (compact(candidate_title) if candidate_title else None)
        )
        return values, resolved_title, encoding
    body_source = text
    resolved_encoding = acquisition.get("charset")
    if body_source is None and kind in {"txt", "json"} and data:
        body_source, resolved_encoding = decode(data, acquisition.get("charset"))
    source_text = f"{url}\n{body_source}" if body_source else url
    for name, rule in fields.items():
        canonical_name = FIELD_ALIASES.get(name, name)
        if rule.get("value") is not None:
            values[canonical_name] = rule["value"]
        elif not rule.get("xpath") and rule.get("regex"):
            values[canonical_name] = field_value(
                None, source_text, rule, is_date=(canonical_name in DATE_FIELDS)
            )
    candidate_title = values.get("title") or file_name(url)
    resolved_title = (
        enrich_bare_title(
            candidate_title,
            values.get("document_number"),
            tree=None,
            body_text=body_source,
        )
        if enrich_title
        else (compact(candidate_title) if candidate_title else None)
    )
    return values, resolved_title, resolved_encoding


__all__ = [
    "DATE_FIELDS",
    "FIELD_ALIASES",
    "clean_url",
    "decode",
    "document_tree",
    "enrich_bare_title",
    "era_date",
    "extract_detail_fields",
    "extract_label_value_fields",
    "field_value",
    "file_name",
    "first_text",
    "infer_effective_date",
    "parse_date",
    "parse_html",
    "parse_japanese_date",
    "roc_date",
]
