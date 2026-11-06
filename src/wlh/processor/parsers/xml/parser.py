"""Official legal XML, e-Gov v2 catalog/revision JSON+XML, and XML packages."""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Mapping
from typing import Any, cast

import lxml.etree as _etree  # type: ignore[import-untyped]

from wlh.config import MAX_ORIGINAL_BYTES
from wlh.core import WlhError
from wlh.processor.parsers.record_base import (
    RecordAdapter,
    japan_records,
    japan_requests,
    verify_attachment_zip,
)
from wlh.processor.parsers.xml.discovery import discover
from wlh.processor.parsers.xml.schemas import ERA_OFFSETS, extract_xml_records
from wlh.processor.parsers.xml.values import select

etree: Any = _etree


class XmlParser(RecordAdapter):
    CONTRACT: str = "wlh.parser.xml.v9"
    NAMESPACE: str = "legislative-xml"

    def records(self, data: bytes, acquisition: Mapping[str, Any]) -> list[dict[str, Any]]:
        doc_budget = self.budget_documents
        cache_key = (
            acquisition.get("archive_ref"),
            acquisition.get("actual_url"),
            doc_budget,
        )
        cached: tuple[list[dict[str, Any]], str | None] | None = getattr(
            self, "_records_cache", {}
        ).get(cache_key)
        if cached is not None and cache_key[0] is not None:
            self.truncated = cached[1] or self.truncated
            return cached[0]
        official_records = japan_records(data, acquisition, self.config)
        if official_records is not None:
            return official_records
        raw_bytes = data
        zipped = data.startswith(b"PK\x03\x04")
        extra_xml_blobs: list[bytes] = []
        if zipped:
            max_bytes = max(self.budget_response_bytes, MAX_ORIGINAL_BYTES)
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    members = [
                        m
                        for m in sorted(archive.infolist(), key=lambda item: item.filename)
                        if not m.is_dir() and m.filename.lower().endswith(".xml")
                    ]
                    if not members:
                        raise WlhError("zip_without_xml")
                    total_expanded = 0
                    xml_blobs: list[bytes] = []
                    for member in members:
                        if member.file_size > max_bytes:
                            raise WlhError("zip_expanded_size_exceeded")
                        with archive.open(member) as source:
                            blob = source.read(max_bytes - total_expanded + 1)
                        total_expanded += len(blob)
                        if total_expanded > max_bytes:
                            raise WlhError("zip_expanded_size_exceeded")
                        xml_blobs.append(blob)
                    data = xml_blobs[0]
                    extra_xml_blobs = xml_blobs[1:]
            except (zipfile.BadZipFile, OSError, ValueError, RuntimeError):
                raise WlhError("zip_invalid") from None
        if not zipped:
            if data.lstrip().startswith(b"{"):
                try:
                    obj = json.loads(data)
                except ValueError:
                    raise WlhError("legal_xml_invalid") from None
                if isinstance(obj, dict) and (
                    isinstance(cast(dict[str, Any], obj).get("children"), list)
                    or isinstance(cast(dict[str, Any], obj).get("titles"), list)
                ):
                    return []
                raise WlhError("legal_xml_invalid")
            if re.search(rb"<!DOCTYPE\s+html\b|<html(?:\s|>)", data[:2048], re.I):
                return []
        if b"<!ENTITY" in data.upper():
            raise WlhError("legal_xml_doctype_rejected")
        try:
            root: Any = etree.fromstring(
                data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
            )
        except etree.XMLSyntaxError:
            raise WlhError("legal_xml_invalid") from None
        tag = etree.QName(root).localname
        if not zipped and (
            tag in {"feed", "items", "ActsRegsList", "rss"}
            or (
                tag == "DataRoot"
                and not (select(root, "LawTitle") or root.xpath('.//*[local-name()="LawBody"]'))
                and bool(root.xpath(".//*[local-name()='LawId']/text()"))
            )
        ):
            return []
        detail_tpl = str(self.parser_options.get("detail_url_template") or "")
        result, truncated = extract_xml_records(
            root,
            acquisition,
            raw_bytes=raw_bytes,
            zipped=zipped,
            extra_xml_blobs=extra_xml_blobs,
            doc_budget=doc_budget,
            detail_url_template=detail_tpl,
        )
        if truncated:
            self.truncated = truncated
        if cache_key[0] is not None:
            cache_dict: dict[Any, Any] = getattr(self, "_records_cache", {})
            while len(cache_dict) >= 8:
                del cache_dict[next(iter(cache_dict))]
            cache_dict[cache_key] = (result, self.truncated)
            setattr(self, "_records_cache", cache_dict)
        return result

    def next_requests(
        self,
        data: bytes,
        acquisition: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        official_requests = japan_requests(data, acquisition, self.config)
        if official_requests is not None:
            return official_requests
        requests, truncated = discover(data, acquisition, self.config)
        self.truncated = truncated or self.truncated
        return requests


Adapter = XmlParser

__all__ = [
    "Adapter",
    "ERA_OFFSETS",
    "XmlParser",
    "japan_records",
    "japan_requests",
    "verify_attachment_zip",
]
