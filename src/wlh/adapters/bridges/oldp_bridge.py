"""OLDP provider/API cases, RII XML and German court HTML."""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Mapping
from typing import Any

import lxml.etree as _etree  # type: ignore[import-untyped]

from wlh.adapters.bridges.common import RecordAdapter, day, first, html_records, items, payload
from wlh.core import WlhError

etree: Any = _etree


def rii_xml(data: bytes, max_bytes: int) -> bytes:
    """Read the first XML member in memory, bounded like other archived inputs."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            member: zipfile.ZipInfo | None = next(
                (
                    m
                    for m in archive.infolist()
                    if not m.is_dir() and m.filename.lower().endswith(".xml")
                ),
                None,
            )
            if member is None:
                raise WlhError("zip_without_xml")
            if member.file_size > max_bytes:
                raise WlhError("zip_expanded_size_exceeded")
            with archive.open(member) as source:
                xml = source.read(max_bytes + 1)
            if len(xml) > max_bytes:
                raise WlhError("zip_expanded_size_exceeded")
            return xml
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError, NotImplementedError):
        raise WlhError("zip_invalid") from None


class Adapter(RecordAdapter):
    CONTRACT: str = "wlh.adapter.oldp.v2"
    NAMESPACE: str = "oldp/case"

    def records(self, data: bytes, acquisition: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw_bytes = data
        zipped = data.startswith(b"PK\x03\x04")
        if zipped:
            data = rii_xml(data, self.config["budget"]["response_bytes"])
        if data.lstrip().startswith((b"{", b"[")):
            result: list[dict[str, Any]] = []
            for raw in items(payload(data), ("results", "cases", "data"))[
                : self.config["budget"]["documents"]
            ]:
                if raw.get("model") and raw["model"] != "cases.case":
                    continue
                r = raw.get("fields", raw)
                result.append(
                    {
                        "external_key": str(r.get("ecli") or r.get("id") or r["file_number"]),
                        "title": r.get("title") or r["file_number"],
                        "doc_number": r.get("file_number"),
                        "issuer": r.get("court_name") or r.get("court"),
                        "published_at": r.get("date"),
                        "doc_type": "案例",
                        "detail_url": r.get("source_url") or r.get("url"),
                        "html": r.get("content") or r.get("text"),
                        "downloads": [{"url": r["pdf_url"]}] if r.get("pdf_url") else [],
                    }
                )
            return result
        if zipped or data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith((b"<?xml", b"<dokument")):
            tree: Any = etree.fromstring(
                data, etree.XMLParser(resolve_entities=False, no_network=True)
            )
            key: str | None = first(tree, ".//ecli/text()") or first(tree, ".//doknr/text()")
            number: str | None = first(tree, ".//aktenzeichen/text()") or first(
                tree, ".//az/text()"
            )
            title: str | None = (
                first(tree, ".//titelzeile") or first(tree, ".//titel") or number or key
            )
            published: str | None = first(tree, ".//entsch-datum/text()")
            if published and re.fullmatch(r"\d{8}", published):
                published = f"{published[:4]}-{published[4:6]}-{published[6:]}"
            return [
                {
                    "external_key": key or number,
                    "title": title,
                    "doc_number": number,
                    "issuer": first(
                        tree, ".//gertyp/text()|.//gerort/text()|.//spruchkoerper/text()"
                    ),
                    "published_at": day(published),
                    "doc_type": "案例",
                    "text": "\n\n".join(
                        " ".join(n.itertext())
                        for n in tree.xpath(
                            ".//leitsatz|.//tenor|.//tatbestand|.//entscheidungsgruende|.//gruende|.//gründe"
                        )
                    ),
                    "original": (
                        {
                            "ref": acquisition["archive_ref"],
                            "kind": "zip",
                            "byte_size": len(raw_bytes),
                        }
                        if zipped
                        else None
                    ),
                }
            ]
        return html_records(
            data,
            acquisition,
            containers="//article[@data-ecli]",
            namespace=self.NAMESPACE,
            fields={
                "title": ".//h1//text()|.//h2//text()",
                "external_key": "./@data-ecli",
                "doc_number": './/*[@class="file-number"]//text()',
                "issuer": './/*[@class="court"]//text()',
                "published_at": './/*[@class="date"]//text()',
                "doc_type": 'string("案例")',
            },
        )


OldpProviderBridge = Adapter
