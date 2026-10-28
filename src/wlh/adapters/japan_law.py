"""Pure e-Gov v2 catalog and revision parsing over already archived responses.

The catalog's date fields can differ from law_data for the same revision. Only
law_data supplies delivered dates. Scheduled dates are explicitly non-authoritative
in the official v2 schema; unresolved commencement conditions stay in the text.
"""

from __future__ import annotations

import base64
import json
import re
import unicodedata
from collections.abc import Mapping
from datetime import date
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import lxml.etree as etree

from wlh.adapters.legal_xml_markdown import markdown
from wlh.core import WlhError, public_url
from wlh.engine.scope import guard
from wlh.extraction.markdown import normalized

etree: Any
_REVISION = re.compile(r"[A-Za-z0-9]+_\d{8}_[A-Za-z0-9]+")
_DIGITS = "〇一二三四五六七八九"
_NUMBER = re.compile(r"[0-9〇零一二三四五六七八九十百千元]+")
_ERA = {"明治": 1867, "大正": 1911, "昭和": 1925, "平成": 1988, "令和": 2018}
_JAPANESE_DAY = r"(明治|大正|昭和|平成|令和)(\d+)年(\d+)月(\d+)日"


def _response(
    data: bytes, acquisition: Mapping[str, Any], config: Mapping[str, Any]
) -> tuple[str, dict[str, Any]] | None:
    url = str(acquisition.get("actual_url") or acquisition.get("request_url") or "")
    path = urlsplit(url).path.rstrip("/")
    if path != "/api/2/laws" and not path.startswith("/api/2/law_data/"):
        return None
    try:
        value = json.loads(data)
    except ValueError:
        raise WlhError("egov_response_invalid") from None
    if not isinstance(value, dict):
        raise WlhError("egov_response_invalid")
    return url, cast(dict[str, Any], value)


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WlhError("egov_response_invalid")
    return cast(dict[str, Any], value)


def _day(value: Any) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _number(match: re.Match[str]) -> str:
    value = match.group().replace("元", "一").replace("零", "〇")
    digits = "".join(str(_DIGITS.index(c)) if c in _DIGITS else c for c in value)
    if digits.isdecimal():
        return str(int(digits))
    total, current = 0, ""
    for char in digits:
        if char.isdecimal():
            current += char
        else:
            total += int(current or "1") * {"十": 10, "百": 100, "千": 1000}[char]
            current = ""
    return str(total + int(current or "0"))


def _japanese_numbers(value: str) -> str:
    return _NUMBER.sub(_number, re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)))


def _japanese_day(value: str) -> str | None:
    match = re.fullmatch(_JAPANESE_DAY, value)
    if match is None:
        return None
    era, year, month, day = match.groups()
    try:
        return date(_ERA[era] + int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def _effective(root: Any, revision: Mapping[str, Any]) -> str | None:
    """Corroborate this revision's API date with its own commencement clause.

    The API contains real historical dates equal to promulgation even where the
    delivered text specifies another commencement. Do not infer from those dates,
    another amendment's provisions, application dates, or an unbound main text.
    Unsupported or phased commencement stays null; the archived source and full
    legal text retain the evidence needed for later review.
    """
    candidate = _day(revision.get("amendment_enforcement_date"))
    if candidate is None or revision.get("amendment_enforcement_comment"):
        return None
    amendment = str(revision.get("amendment_law_num") or "")
    promulgated = _day(revision.get("amendment_promulgate_date"))
    provisions: list[Any] = []
    for node in root.findall("LawBody/SupplProvision"):
        label = _japanese_numbers(str(node.get("AmendLawNum") or ""))
        if revision.get("amendment_law_id"):
            match = re.match(_JAPANESE_DAY, label)
            if (
                amendment and match is not None
                and _japanese_day(match.group()) == promulgated
                and re.sub(r"\d+月\d+日", "", label, count=1)
                == _japanese_numbers(amendment)
            ):
                provisions.append(node)
        elif not label and str(revision.get("law_revision_id") or "").endswith(
            "_000000000000000"
        ):
            provisions.append(node)
    if not provisions:
        return None
    dates: set[str] = set()
    for provision in provisions:
        sentences = [
            _japanese_numbers("".join(node.itertext()))
            for node in provision.xpath(".//Sentence")
        ]
        # Exceptions and conditional commencement require a separate legal
        # interpretation. A main-clause date alone is not a document-wide date.
        if any(re.search(r"ただし|但し|但シ|ただ,|次の各号|政令で定める日", s) for s in sentences):
            return None
        for sentence in sentences:
            match = re.match(
                r"(?:この|本)(?:法律|政令|省令|府令|規則|法|令)(?:は|ハ)[、,]?"
                r"(?P<day>" + _JAPANESE_DAY + r"|公布の日)(?:から|より|ヨリ)施行(?:する|し|スル|ス)",
                sentence,
            )
            if match is not None:
                evidence = (
                    promulgated if match["day"] == "公布の日"
                    else _japanese_day(match["day"])
                )
                if evidence is not None:
                    dates.add(evidence)
    return candidate if dates == {candidate} else None


def _xml(value: Any) -> Any:
    if not isinstance(value, str):
        raise WlhError("egov_law_xml_missing")
    try:
        data = base64.b64decode(value, validate=True)
    except ValueError:
        raise WlhError("egov_law_xml_invalid") from None
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise WlhError("legal_xml_doctype_rejected")
    try:
        root = etree.fromstring(
            data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        )
    except etree.XMLSyntaxError:
        raise WlhError("egov_law_xml_invalid") from None
    if root.tag != "Law" or root.find("LawBody") is None:
        raise WlhError("egov_law_xml_invalid")
    return root


def records(
    data: bytes, acquisition: Mapping[str, Any], config: Mapping[str, Any]
) -> list[dict[str, Any]] | None:
    response = _response(data, acquisition, config)
    if response is None:
        return None
    url, obj = response
    if urlsplit(url).path.rstrip("/") == "/api/2/laws":
        return []
    law = _object(obj.get("law_info"))
    revision = _object(obj.get("revision_info"))
    law_id = str(law.get("law_id") or "")
    revision_id = str(revision.get("law_revision_id") or "")
    requested_revision = urlsplit(url).path.rsplit("/", 1)[-1]
    if (
        not law_id
        or not _REVISION.fullmatch(revision_id)
        or not revision_id.startswith(law_id + "_")
        or requested_revision != revision_id
    ):
        raise WlhError("egov_revision_mismatch")
    title = str(revision.get("law_title") or "").strip()
    if not title:
        raise WlhError("egov_title_missing")
    root = _xml(obj.get("law_full_text"))
    attachments = obj.get("attached_files_info")
    if root.xpath(".//Fig") or (
        isinstance(attachments, dict)
        and cast(dict[str, Any], attachments).get("attached_files")
    ):
        # Links alone would silently lose legal diagrams or PDF annexes. Until
        # those bytes and their visible content pass the attachment pipeline,
        # this revision is an explicit failed delivery, never a text-only success.
        raise WlhError("egov_attachments_unverified")
    body = root.find("LawBody")
    for heading in list(body.findall("LawTitle")):
        body.remove(heading)
    text = markdown(body)
    if not text.strip():
        raise WlhError("egov_law_text_missing")
    status = str(revision.get("current_revision_status") or "").strip()
    comment = str(revision.get("amendment_enforcement_comment") or "").strip()
    effective = _effective(root, revision)
    date_notes: list[str] = []
    if comment:
        date_notes.append("施行期日参考情報：" + comment)
    scheduled = _day(revision.get("amendment_scheduled_enforcement_date"))
    if scheduled:
        date_notes.append("施行期日参考情報（予定）：" + scheduled)
    number = str(law.get("law_num") or "").strip() or None
    full_text = normalized("\n\n".join([
        "# " + title, *([number] if number else []), *date_notes, text,
    ]))
    return [{
        "external_key": law_id,
        "title": title,
        "doc_number": number,
        "published_at": _day(law.get("promulgation_date")),
        "effective_at": effective,
        # Expire/LossOfEffectiveness dates are processing dates in the v2 schema.
        "expired_at": (
            _day(revision.get("repeal_date"))
            if revision.get("repeal_status") == "Repeal" else None
        ),
        "status": status or None,
        "issuer": None,
        "doc_type": "法律法规",
        "text": full_text,
        "detail_url": url,
    }]


def next_requests(
    data: bytes, acquisition: Mapping[str, Any], config: Mapping[str, Any]
) -> list[dict[str, Any]] | None:
    response = _response(data, acquisition, config)
    if response is None:
        return None
    url, obj = response
    if urlsplit(url).path.rstrip("/") != "/api/2/laws":
        return []
    laws = obj.get("laws")
    count, total = obj.get("count"), obj.get("total_count")
    if (
        not isinstance(laws, list)
        or type(count) is not int
        or type(total) is not int
        or count != len(cast(list[Any], laws))
        or count < 0
        or total < 0
    ):
        raise WlhError("egov_catalog_invalid")
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    try:
        offset = int(query.get("offset", "0"))
    except ValueError:
        raise WlhError("egov_catalog_invalid") from None
    end = offset + count
    if offset < 0 or end > total:
        raise WlhError("egov_catalog_invalid")
    next_offset = obj.get("next_offset")
    if next_offset is None:
        if end != total:
            raise WlhError("egov_catalog_truncated")
    elif type(next_offset) is not int or not offset < next_offset == end < total:
        raise WlhError("egov_catalog_pagination_invalid")
    requests: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in cast(list[Any], laws):
        row = _object(item)
        law = _object(row.get("law_info"))
        revision = _object(row.get("revision_info"))
        revision_id = str(revision.get("law_revision_id") or "")
        law_id = str(law.get("law_id") or "")
        if (
            not law_id or law_id in seen
            or not _REVISION.fullmatch(revision_id)
            or not revision_id.startswith(law_id + "_")
        ):
            raise WlhError("egov_catalog_identity_invalid")
        seen.add(law_id)
        target = public_url(urljoin(
            url, "law_data/" + revision_id + "?law_full_text_format=xml"
        ))
        guard(config, target)
        requests.append({"url": target, "role": "page"})
    if next_offset is not None:
        query["offset"] = str(next_offset)
        target = public_url(urlunsplit((
            parts.scheme, parts.netloc, parts.path, urlencode(query), "",
        )))
        guard(config, target)
        requests.append({"url": target, "role": "page"})
    return requests
