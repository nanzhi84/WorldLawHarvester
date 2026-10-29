"""Build web document candidates from archived detail and required attachment inputs."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast
from urllib.parse import unquote

import lxml.etree as etree

from wlh.adapters.web_fields import (
    decode,
    enrich_bare_title,
    extract_detail_fields,
    file_name,
    parse_html,
)
from wlh.adapters.web_titles import directory_title
from wlh.core import WlhError, canonical, digest, public_url
from wlh.engine.transport import blocked
from wlh.extraction.html_images import require_textual_images
from wlh.extraction.markdown import (
    clean_html_tree,
    html_to_markdown,
    json_to_markdown,
    text_to_markdown,
)
from wlh.extraction.office import detect_real_format

etree: Any
TEXT_KINDS: set[str] = {"html", "txt", "json"}
BINARY_KINDS: set[str] = {"pdf", "doc", "docx", "rtf", "xls", "xlsx", "zip"}
GENERIC_ATTACHMENT_LABELS: set[str] = {
    "tải file đính kèm",
    "tai file dinh kem",
    "tải về",
    "tai ve",
    "tải văn bản",
    "tai van ban",
    "tải xuống",
    "tai xuong",
    "download",
    "attachment",
    "attachments",
    "file đính kèm",
    "file dinh kem",
    "tệp đính kèm",
    "tep dinh kem",
    "xem chi tiết",
    "xem toàn văn",
    "đính kèm",
    "dinh kem",
    "pdf",
    "doc",
    "docx",
    "odt",
    "odf",
    "檔案下載",
    "轉存pdf",
}


class WebDocumentBuilder:
    config: dict[str, Any]
    attachment: re.Pattern[str]
    volume: re.Pattern[str] | None

    def deliverable_text(
        self,
        ctx: Any,
        data: bytes,
        kind: str,
        acquisition: Mapping[str, Any],
        image_loader: Callable[[str], bytes | None] | None = None,
    ) -> tuple[str, dict[str, Any] | None, list[str]]:
        if kind == "html":
            tree, _, _ = parse_html(data, acquisition["charset"])
            return (
                self.html_markdown(tree, acquisition, image_loader=image_loader),
                None,
                ["markdown", "quality"],
            )
        if kind == "txt":
            text, _ = decode(data, acquisition["charset"])
            return text_to_markdown(text), None, ["markdown", "quality"]
        if kind == "json":
            text, _ = decode(data, acquisition["charset"])
            return json_to_markdown(text), None, ["markdown", "quality"]
        if kind in BINARY_KINDS:
            result = ctx.extract(data, kind)
            return result["markdown"], result, result["parsers"]
        raise WlhError("unsupported_document_type")

    def html_markdown(
        self,
        tree: Any,
        acquisition: Mapping[str, Any],
        image_loader: Callable[[str], bytes | None] | None = None,
    ) -> str:
        wlh_trunc = tree.xpath("//*[@data-wlh-truncated]/@data-wlh-truncated")
        if wlh_trunc:
            raise WlhError(str(wlh_trunc[0]))
        rule: Any = self.config["documents"]["content_xpath"]
        nodes = tree.xpath(rule) if rule else [tree.body if tree.body is not None else tree]
        nodes = [n for n in nodes if isinstance(n, etree._Element)]
        node_set = set(nodes)
        nodes = [n for n in nodes if not any(anc in node_set for anc in n.iterancestors())]
        if not nodes:
            if blocked(etree.tostring(tree, encoding="utf-8"), self.config):
                raise WlhError("target_content_blocked")
            raise WlhError("content_region_missing")
        base = str(acquisition.get("actual_url") or acquisition.get("request_url") or "")
        ocr_cfg = self.config.get("ocr")
        ocr_langs = (
            str(cast(Mapping[str, Any], ocr_cfg).get("languages") or "jpn+jpn_vert")
            if isinstance(ocr_cfg, Mapping)
            else "jpn+jpn_vert"
        )
        for node in nodes:
            clean_html_tree(node)
            if self.config["documents"].get("require_complete_text"):
                require_textual_images(
                    node,
                    base_url=base,
                    image_loader=image_loader,
                    ocr_languages=ocr_langs,
                )
        fragment = "".join(etree.tostring(n, encoding="unicode", method="html") for n in nodes)
        if blocked(fragment.encode("utf-8"), self.config):
            raise WlhError("target_content_blocked")
        return html_to_markdown("<html><body>" + fragment + "</body></html>", base)

    def detail_values(
        self,
        data: bytes,
        kind: str,
        acquisition: Mapping[str, Any],
        url: str,
        text: str | None = None,
    ) -> tuple[dict[str, Any], str | None, str | None]:
        """Declared field rules on the archived detail input; subclasses map source tables."""
        return extract_detail_fields(
            self.config["documents"]["fields"], data, kind, acquisition, url, text
        )

    def build(
        self, ctx: Any, target: Mapping[str, Any], inputs: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        detail = inputs[0]
        attachments = [
            item for item in inputs[1:] if item.get("role") not in ("title_context", "inline_image")
        ]
        inline_image_items = [item for item in inputs[1:] if item.get("role") == "inline_image"]
        acquisition = detail["acquisition"]
        data = ctx.read(acquisition)
        kind = detect_real_format(data, "html" if acquisition["mime_type"] == "text/html" else "")
        meta = dict(self.config["metadata"])
        url: str = str(acquisition["actual_url"] or acquisition["request_url"] or "")
        values, title, encoding = self.detail_values(data, kind, acquisition, url)
        if (
            kind in TEXT_KINDS
            and not (kind == "html" and self.config["documents"]["content_xpath"])
            and blocked(data, self.config)
        ):
            raise WlhError("target_content_blocked")
        candidate_inputs: list[dict[str, Any]] = [
            {
                "acquisition_id": acquisition["id"],
                "archive_ref": acquisition["archive_ref"],
                "role": "primary",
                "locator": {"url": public_url(url), "kind": "whole_response"},
                "charset": encoding,
                "base_url": public_url(url),
            }
        ]
        archived_images: dict[str, bytes] = {}
        seen_derived_urls: set[str] = set()
        for img_item in inline_image_items:
            img_acq_raw = img_item.get("acquisition")
            if not isinstance(img_acq_raw, Mapping):
                continue
            img_acq = cast(Mapping[str, Any], img_acq_raw)
            img_url_raw = str(
                img_acq.get("actual_url")
                or img_acq.get("request_url")
                or img_item.get("image_url")
                or ""
            )
            if not img_url_raw:
                continue
            norm_img_url = public_url(img_url_raw)
            archived_images[norm_img_url] = ctx.read(img_acq)
            if norm_img_url not in seen_derived_urls:
                seen_derived_urls.add(norm_img_url)
                candidate_inputs.append(
                    {
                        "acquisition_id": img_acq["id"],
                        "archive_ref": img_acq["archive_ref"],
                        "role": "derived",
                        "locator": {"url": norm_img_url, "kind": "whole_response"},
                    }
                )

        def load_image(img_url: str) -> bytes | None:
            norm_url = public_url(img_url)
            if norm_url in archived_images:
                return archived_images[norm_url]
            ref_cache: dict[str, tuple[str, str]] | None = getattr(
                self, "_archived_image_refs", None
            )
            if ref_cache is None and hasattr(ctx, "runtime"):
                ref_cache = {}
                for row in ctx.runtime.rows(
                    "SELECT id, archive_ref, actual_url, request_url FROM wlh.acquisition "
                    "WHERE source_id=%s AND state='archived' AND outcome='ok'",
                    (self.config["id"],),
                ):
                    aref = str(row.get("archive_ref") or "")
                    aid = str(row.get("id") or "")
                    if not aref or not aid:
                        continue
                    for u_col in ("actual_url", "request_url"):
                        u_val = str(row.get(u_col) or "")
                        if u_val:
                            try:
                                ref_cache[public_url(u_val)] = (aid, aref)
                            except WlhError:
                                pass
                setattr(self, "_archived_image_refs", ref_cache)
            if ref_cache is not None and norm_url in ref_cache:
                aid, aref = ref_cache[norm_url]
                blob: bytes = ctx.read({"id": aid, "archive_ref": aref, "state": "archived"})
                archived_images[norm_url] = blob
                return blob
            return None

        verified_title: str | None = None
        if kind in BINARY_KINDS and self.config["documents"].get("binary_title_from_link"):
            verified_title, context = directory_title(
                ctx, inputs, acquisition["request_url"], GENERIC_ATTACHMENT_LABELS,
            )
            candidate_inputs.append(context)
        deliverables: list[dict[str, Any]] = []
        parsers = {"markdown"}
        quality_fail: list[str] = []

        def add(
            data: bytes,
            kind: str,
            item_acquisition: Mapping[str, Any],
            input_index: int,
            role: str,
            title: str | None = None,
            order: int | None = None,
            source_url: str | None = None,
        ) -> None:
            markdown, result, used = self.deliverable_text(
                ctx, data, kind, item_acquisition, image_loader=load_image
            )
            parsers.update(used)
            report = (result or {}).get("quality") or ctx.text_quality(markdown)
            if report["recommended_route"] not in self.config["parser"]["quality_accept_routes"]:
                quality_fail.append(report["recommended_route"])
            for member in (result or {}).get("members", []):
                ref = ctx.store(member["data"])
                candidate_inputs.append(
                    {
                        "acquisition_id": item_acquisition["id"],
                        "archive_ref": ref,
                        "role": "derived",
                        "locator": {
                            "url": public_url(source_url or url),
                            "zip_path": member["path"],
                        },
                    }
                )
            deliverables.append(
                {
                    "role": role,
                    "title": title,
                    "volume_order": order,
                    "markdown": markdown,
                    "input": input_index,
                    "original": {
                        "ref": item_acquisition["archive_ref"],
                        "kind": kind,
                        "byte_size": item_acquisition["byte_size"],
                        "source_url": source_url or public_url(url),
                        "original_name": file_name(source_url or url),
                    }
                    if kind not in TEXT_KINDS
                    else None,
                    "quality": report,
                    "extraction": (result or {}).get("info") or {"kind": kind},
                    "parsers": used,
                }
            )

        files: list[dict[str, Any]] = []
        for item in attachments:
            att = item["acquisition"]
            source = public_url(att["actual_url"] or att["request_url"])
            if not (
                self.attachment.search(unquote(source))
                or self.attachment.search(file_name(source))
            ):
                continue
            body = ctx.read(att)
            att_kind = detect_real_format(body)
            if att_kind in TEXT_KINDS and blocked(body, self.config):
                raise WlhError("target_content_blocked")
            if att_kind not in BINARY_KINDS | TEXT_KINDS:
                raise WlhError("unsupported_attachment_type")
            text = (item.get("text") or "").strip()
            use_link_text = (
                self.config["documents"]["attachment_title"] == "link_text"
                and text.lower() not in GENERIC_ATTACHMENT_LABELS
                and not text.lower().startswith("download this version")
            )
            base_name = file_name(source)
            clean_base_name = re.sub(
                r"(?:^|[-_\s]+)[0-9a-f]{32}(?=\.[a-z0-9]{2,5}$|$)",
                "",
                base_name,
                flags=re.IGNORECASE,
            )
            clean_base_name = re.sub(
                r"\.(?:pdf|docx?|rtf|xlsx?|zip)$",
                "",
                clean_base_name,
                flags=re.IGNORECASE,
            ).strip(" -_.")
            if use_link_text and text:
                att_title = text
            elif title and clean_base_name and clean_base_name.lower() not in {"file", "download"}:
                att_title = f"{title} - {clean_base_name}"
            elif title:
                att_title = title
            else:
                att_title = clean_base_name or base_name
            match: re.Match[str] | None = (
                (self.volume.search(text) or self.volume.search(base_name))
                if self.volume
                else None
            )
            files.append(
                {
                    "acquisition": att,
                    "body": body,
                    "kind": att_kind,
                    "url": source,
                    "title": att_title,
                    "order": int(match.group(1)) if match else None,
                }
            )
        numbered = [f for f in files if f["order"] is not None]
        volumes = numbered if len(numbered) >= 2 else []
        if volumes:
            orders = [f["order"] for f in volumes]
            if len(set(orders)) != len(orders):
                for f in volumes:
                    f["order"] = None
        others = [f for f in files if f not in volumes]
        body_mode = self.config["documents"]["detail_body"]
        use_body = not volumes and (body_mode == "always" or (body_mode == "auto" and not files))
        if kind not in TEXT_KINDS | BINARY_KINDS:
            raise WlhError("unsupported_document_type")
        if use_body:
            add(data, kind, acquisition, 0, "primary", None)
            if kind in BINARY_KINDS and deliverables:
                values, title, encoding = self.detail_values(
                    data, kind, acquisition, url, deliverables[0]["markdown"]
                )
        else:
            candidate_inputs[0]["role"] = "context"
        for f in volumes:
            candidate_inputs.append(self.input_for(f, "volume", f["order"]))
            add(
                f["body"],
                f["kind"],
                f["acquisition"],
                len(candidate_inputs) - 1,
                "volume",
                None,
                f["order"],
                f["url"],
            )
        for position, f in enumerate(others):
            role: str = (
                "primary"
                if not use_body and not volumes and position == 0
                else "independent_attachment"
            )
            candidate_inputs.append(self.input_for(f, role, None))
            add(
                f["body"],
                f["kind"],
                f["acquisition"],
                len(candidate_inputs) - 1,
                role,
                None if role == "primary" else f["title"],
                None,
                f["url"],
            )
        if quality_fail:
            raise WlhError("quality_check_failed:" + sorted(set(quality_fail))[0])
        if not deliverables:
            raise WlhError("document_deliverables_required")
        if not use_body and deliverables:
            att_vals, _, _ = self.detail_values(
                data, kind, acquisition, url, deliverables[0]["markdown"]
            )
            for k, v in att_vals.items():
                if not values.get(k) and v:
                    values[k] = v
            title = enrich_bare_title(
                title,
                values.get("document_number"),
                tree=None,
                body_text=deliverables[0]["markdown"],
            )
            candidate_inputs[0]["slice_hash"] = digest(
                {
                    "title": title,
                    "fields": values,
                    "files": sorted(
                        ({k: f[k] for k in ("url", "title", "order")} for f in files),
                        key=canonical,
                    ),
                }
            )
        if kind in TEXT_KINDS and use_body:
            candidate_inputs[0]["slice_hash"] = digest(
                {"body": deliverables[0]["markdown"], "fields": values, "title": title}
            )
        meta["title"] = verified_title or title
        for name in ("document_number", "issuer", "status"):
            if values.get(name):
                meta[name] = values[name]
        dates: dict[str, Any] = {
            k: values[k] for k in ("published_at", "effective_at", "expired_at") if values.get(k)
        }
        return [
            {
                **target["identity"],
                "metadata": meta,
                "dates": dates,
                "deliverables": deliverables,
                "inputs": candidate_inputs,
                "parsers": sorted(parsers),
            }
        ]

    @staticmethod
    def input_for(file: Mapping[str, Any], role: str, order: int | None) -> dict[str, Any]:
        acquisition = file["acquisition"]
        return {
            "acquisition_id": acquisition["id"],
            "archive_ref": acquisition["archive_ref"],
            "role": role,
            "locator": {"url": file["url"], "kind": "whole_response"},
            "charset": acquisition["charset"],
            "volume_order": order,
        }

