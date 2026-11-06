"""Literal dates from visible source text and explicit publication metadata."""

from __future__ import annotations

import html
import json
import re
import unicodedata
from datetime import date
from html.parser import HTMLParser
from typing import Any, cast
from xml.etree import ElementTree

from wlh.benchmark.source_evidence import literal_date_outputs


def statute_date_evidence(raw: str) -> str:
    """Read one complete royal-assent date from the current Canadian law.

    Bill history, modification metadata and related-document dates must not
    supply an interchangeable publication date. This transforms only evidence,
    never the benchmark's archived payload or rendered law content.
    """
    if not re.match(r"\s*(?:<\?xml[^>]*>\s*)?<Statute(?:\s|>)", raw):
        return raw
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return raw
    dates = root.findall("./Identification/BillHistory/Stages[@stage='assented-to']/Date")
    assent = ""
    if len(dates) == 1:
        parts = [dates[0].findall(name) for name in ("YYYY", "MM", "DD")]
        if all(len(nodes) == 1 and (nodes[0].text or "").strip().isdigit() for nodes in parts):
            values = [(nodes[0].text or "").strip() for nodes in parts]
            try:
                assent = date(*(int(value) for value in values)).isoformat()
            except ValueError:
                pass
    excluded = {"BillHistory", "ModifiedDate", "RelatedDocument", "Description"}
    for parent in root.iter():
        for child in list(parent):
            if child.tag in excluded:
                parent.remove(child)
    return ElementTree.tostring(root, encoding="unicode") + "\x00" + assent


class DateEvidence(HTMLParser):
    """Join inline text, while preventing dates from crossing unrelated blocks."""

    INLINE = {"span", "b", "strong", "em", "i", "u", "a", "small", "font", "sup", "sub"}
    HIDDEN = {"script", "style", "head", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.HIDDEN:
            self.hidden.append(tag)
            self.parts.append("\x00")
        values = dict(attrs)
        if (
            tag == "meta"
            and (values.get("name") or "").lower() in {"dcterms.issued", "firstpublishedtime"}
            and self.hidden in ([], ["head"])
            and values.get("content")
        ):
            self.parts.extend([values["content"] or "", "\x00"])
        if self.hidden:
            return
        if tag not in self.INLINE:
            self.parts.append("\x00")
        values = dict(attrs)
        # These are explicit source date fields, never arbitrary URL attributes.
        if tag == "time" and values.get("datetime"):
            self.parts.extend([values["datetime"] or "", "\x00"])
        if tag == "law":
            allowed = {"era", "year", "promulgatemonth", "promulgateday"}
            fields = " ".join(f'{k}="{v}"' for k, v in attrs if k in allowed and v)
            self.parts.extend([f"<Law {fields}>", "\x00"])
        if tag in {"date", "approveddate"} and values.get("date"):
            self.parts.extend([values["date"] or "", "\x00"])

    def handle_endtag(self, tag: str) -> None:
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
                self.parts.append("\x00")
            return
        if tag not in self.INLINE:
            self.parts.append("\x00")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def date_evidence(raw: str) -> str:
    if raw.lstrip().startswith(("{", "[")):
        try:
            value: Any = json.loads(raw)
        except ValueError:
            value = None

        def strings(node: Any) -> list[str]:
            if isinstance(node, str):
                return [date_evidence(node)]
            if isinstance(node, dict):
                return [s for v in cast(dict[str, Any], node).values() for s in strings(v)]
            if isinstance(node, list):
                return [s for v in cast(list[Any], node) for s in strings(v)]
            return []

        if value is not None:
            return "\x00".join(strings(value))
    raw = statute_date_evidence(raw)
    if re.search(r"<[a-zA-Z][\w:-]*(?:\s|>)", raw):
        parser = DateEvidence()
        parser.feed(raw)
        raw = "\x00".join(["".join(parser.parts), *literal_date_outputs(raw)])
    return re.sub(r"https?://[^\s<>\x00]+", "\x00", raw)


def literal_dates(raw: str, *, jurisdiction: str | None = None) -> set[str]:
    text = unicodedata.normalize("NFKC", html.unescape(raw))
    dates: set[str] = set()

    def add(y: str | int, m: str | int, d: str | int) -> None:
        try:
            dates.add(date(int(y), int(m), int(d)).isoformat())
        except ValueError:
            pass

    # A compact ROC year is interpretable only with a verified Taiwan source
    # context and a visible date-field label, never an arbitrary version/URL.
    if jurisdiction == "TW":
        for year, month, day in re.findall(
            r"(?:發布日期|发布日期|發文日期|发文日期|公布日期|生效日期)\s*[：:]?\s*"
            r"([1-9]\d{0,2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})(?!\d)",
            text,
        ):
            add(1911 + int(year), month, day)
        short_date = r"(?<!\d)([1-9]\d{0,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
        # The source explicitly names its calendar; subsequent complete short
        # dates can omit the era name, but never a month or day.
        if re.search(r"民[國国]\s*" + short_date, text):
            for year, month, day in re.findall(short_date, text):
                add(1911 + int(year), month, day)
        # A current-document publication clause can bind an omitted effective
        # year in that same sentence. No cross-block/date-record inference.
        for sentence in re.split(r"[\x00。！？;；]", text):
            anchors = list(re.finditer(short_date, sentence))
            years = {m[1] for m in anchors}
            if len(years) != 1 or not any(
                re.match(r"\s*(?:修正|訂定|制定)?(?:發布|发布|公布)", sentence[m.end() :])
                for m in anchors
            ):
                continue
            year = 1911 + int(next(iter(years)))
            for month, day in re.findall(
                r"(?:並|并)?自\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(?:起)?(?:生效|施行)",
                sentence,
            ):
                add(year, month, day)

    for y, m, d in re.findall(
        r"(?<!\d)(\d{4})\s*[-/.年년]\s*(\d{1,2})\s*[-/.月월]\s*(\d{1,2})", text
    ):
        add(y, m, d)
    for era, y, m, d in re.findall(
        r"(民國|民国|令和|平成|昭和|大正|明治)\s*(元|\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})",
        text,
    ):
        base = {
            "民國": 1911,
            "民国": 1911,
            "令和": 2018,
            "平成": 1988,
            "昭和": 1925,
            "大正": 1911,
            "明治": 1867,
        }[era]
        add(base + (1 if y == "元" else int(y)), m, d)
    months = {
        name: i
        for i, name in enumerate(
            "january february march april may june july august september october november december".split(),
            1,
        )
    }
    for name, month in months.items():
        names = "|".join(dict.fromkeys([name, name[:3], *(["sept"] if month == 9 else [])]))
        month_pattern = rf"(?:{names})\.?"
        for day, year in re.findall(
            rf"\b(\d{{1,2}})\s+{month_pattern}\s*,?\s*(\d{{4}})\b", text.lower()
        ):
            add(year, month, day)
        for day, year in re.findall(
            rf"\b{month_pattern}\s+(\d{{1,2}})\s*,?\s*(\d{{4}})\b", text.lower()
        ):
            add(year, month, day)
    # e-Gov provides explicit promulgation components on the Law element. Never
    # infer a missing component from a URL, law identifier, or surrounding date.
    eras = {"meiji": 1867, "taisho": 1911, "showa": 1925, "heisei": 1988, "reiwa": 2018}
    for attributes in re.findall(r"<Law\b([^>]*)>", text, re.I):
        values = {
            key.lower(): value
            for key, _, value in re.findall(
                r"\b(Era|Year|PromulgateMonth|PromulgateDay)\s*=\s*(['\"])(.*?)\2",
                attributes,
                re.I,
            )
        }
        era = values.get("era", "").lower()
        year, month_text, day = (
            values.get(k, "") for k in ("year", "promulgatemonth", "promulgateday")
        )
        if era in eras and all(v.isdigit() for v in (year, month_text, day)) and int(year) > 0:
            add(eras[era] + int(year), month_text, day)
    for day, month, year in re.findall(r"(?<!\d)(\d{1,2})[/.](\d{1,2})[/.](\d{4})(?!\d)", text):
        add(year, month, day)
    # Day-first hyphen dates occur in visible Vietnamese document fields.
    # Keep each date inside one block and do not reinterpret version identifiers
    # or components of an ISO date as a publication-date literal.
    for match in re.finditer(r"(?<![\w-])(\d{1,2})-(\d{1,2})-(\d{4})(?![\w-])", text):
        prefix = text[: match.start()].rsplit("\x00", 1)[-1]
        if re.search(r"(?:version|revision|build)\s*[:#]?\s*$", prefix, re.I):
            continue
        day, month, year = match.groups()
        add(year, month, day)
    return dates
