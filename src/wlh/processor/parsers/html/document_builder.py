"""Build web document candidates from archived detail and required attachment inputs."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast
from urllib.parse import unquote

import lxml.etree as _etree  # type: ignore[import-untyped]

from wlh.cleaners.text_cleaner import apply_text_cleaners, blocked, clean_title
from wlh.core import WlhError, canonical, digest, public_url
from wlh.processor.extractors.html.html_to_md import (
    clean_html_tree,
    html_to_markdown,
    json_to_markdown,
    text_to_markdown,
)
from wlh.processor.extractors.html.images import require_textual_images
from wlh.processor.extractors.word.office import detect_real_format
from wlh.processor.extractors.xml.markdown import xml_to_markdown
from wlh.processor.parsers.html.dom_cleanup import clean_site_dom
from wlh.processor.parsers.html.fields import (
    decode,
    enrich_bare_title,
    extract_detail_fields,
    file_name,
    parse_date,
    parse_html,
)
from wlh.processor.parsers.html.titles import directory_title

etree: Any = _etree
TEXT_KINDS: set[str] = {"html", "txt", "json", "xml"}
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


def doc_rules(config: Mapping[str, Any]) -> Mapping[str, Any]:
    proc = config.get("processor")
    if isinstance(proc, Mapping):
        proc_map = cast(Mapping[str, Any], proc)
        parser_cfg = proc_map.get("parser")
        if isinstance(parser_cfg, Mapping):
            parser_map = cast(Mapping[str, Any], parser_cfg)
            if isinstance(parser_map.get("documents"), Mapping):
                return cast(Mapping[str, Any], parser_map["documents"])
    docs = config.get("documents")
    return cast(Mapping[str, Any], docs) if isinstance(docs, Mapping) else {}


_doc_rules = doc_rules


def _parser_transforms(config: Mapping[str, Any]) -> Sequence[str]:
    proc = config.get("processor")
    if isinstance(proc, Mapping):
        proc_map = cast(Mapping[str, Any], proc)
        parser_cfg = proc_map.get("parser")
        if isinstance(parser_cfg, Mapping):
            parser_map = cast(Mapping[str, Any], parser_cfg)
            if isinstance(parser_map.get("transforms"), Sequence):
                return [str(x) for x in cast(Sequence[Any], parser_map["transforms"])]
    return ()


def reconcile_range_enabled(config: Mapping[str, Any]) -> bool:
    if "reconcile_result_range" in _parser_transforms(config):
        return True
    proc = config.get("processor")
    if isinstance(proc, Mapping):
        proc_map = cast(Mapping[str, Any], proc)
        parser_cfg = proc_map.get("parser")
        if isinstance(parser_cfg, Mapping):
            return bool(cast(Mapping[str, Any], parser_cfg).get("reconcile_result_range"))
    return bool(config.get("reconcile_result_range"))


_reconcile_range_enabled = reconcile_range_enabled


def _metadata_cfg(config: Mapping[str, Any]) -> dict[str, Any]:
    storage = config.get("storage")
    if isinstance(storage, Mapping):
        storage_map = cast(Mapping[str, Any], storage)
        if isinstance(storage_map.get("metadata"), Mapping):
            return dict(cast(Mapping[str, Any], storage_map["metadata"]))
    meta = config.get("metadata")
    return dict(cast(Mapping[str, Any], meta)) if isinstance(meta, Mapping) else {}


def _ocr_cfg(config: Mapping[str, Any]) -> Mapping[str, Any] | None:
    proc = config.get("processor")
    if isinstance(proc, Mapping):
        proc_map = cast(Mapping[str, Any], proc)
        ext = proc_map.get("extractor")
        if isinstance(ext, Mapping):
            ext_map = cast(Mapping[str, Any], ext)
            if isinstance(ext_map.get("ocr"), Mapping):
                return cast(Mapping[str, Any], ext_map["ocr"])
    ocr = config.get("ocr")
    return cast(Mapping[str, Any], ocr) if isinstance(ocr, Mapping) else None


def _extractor_quirks(config: Mapping[str, Any]) -> Sequence[str]:
    proc = config.get("processor")
    if isinstance(proc, Mapping):
        proc_map = cast(Mapping[str, Any], proc)
        ext = proc_map.get("extractor")
        if isinstance(ext, Mapping):
            ext_map = cast(Mapping[str, Any], ext)
            if isinstance(ext_map.get("quirks"), Sequence):
                return [str(x) for x in cast(Sequence[Any], ext_map["quirks"])]
    quirks = config.get("quirks")
    if isinstance(quirks, Sequence) and not isinstance(quirks, (str, bytes)):
        return [str(x) for x in cast(Sequence[Any], quirks)]
    return ()


def _strip_text_regexes(config: Mapping[str, Any]) -> Sequence[str]:
    cleaners = config.get("cleaners")
    clean_map: Mapping[str, Any] = (
        cast(Mapping[str, Any], cleaners) if isinstance(cleaners, Mapping) else {}
    )
    raw = clean_map.get("strip_text_regexes") or config.get("strip_text_regexes")
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return [str(x) for x in cast(Sequence[Any], raw) if x]
    return ()


def _title_strip_regexes(config: Mapping[str, Any]) -> Sequence[str]:
    cleaners = config.get("cleaners")
    clean_map: Mapping[str, Any] = (
        cast(Mapping[str, Any], cleaners) if isinstance(cleaners, Mapping) else {}
    )
    raw = clean_map.get("title_strip_regexes") or config.get("title_strip_regexes")
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return [str(x) for x in cast(Sequence[Any], raw) if x]
    return ()


def _quality_routes(config: Mapping[str, Any]) -> Sequence[str]:
    cleaners = config.get("cleaners")
    if isinstance(cleaners, Mapping):
        clean_map = cast(Mapping[str, Any], cleaners)
        if isinstance(clean_map.get("quality_accept_routes"), Sequence):
            return [str(x) for x in cast(Sequence[Any], clean_map["quality_accept_routes"])]
    parser_cfg = config.get("parser")
    if isinstance(parser_cfg, Mapping):
        parser_map = cast(Mapping[str, Any], parser_cfg)
        if isinstance(parser_map.get("quality_accept_routes"), Sequence):
            return [str(x) for x in cast(Sequence[Any], parser_map["quality_accept_routes"])]
    return ("accept", "accept_ocr", "accept_with_warning")


def _dl_fields(tree: Any) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in tree.xpath("//main//dl[dt and dd]"):
        for dt, dd in zip(item.xpath("./dt"), item.xpath("./dd")):
            key = " ".join(dt.text_content().split())
            value = " ".join(dd.text_content().split())
            if key in fields and fields[key] != value:
                raise WlhError("court_detail_fields_ambiguous")
            fields[key] = value
    return fields


class WebDocumentBuilder:
    config: dict[str, Any]
    attachment: re.Pattern[str]
    volume: re.Pattern[str] | None

    def __init__(self, config: dict[str, Any]) -> None:
        try:
            super().__init__(config)  # type: ignore[call-arg]
        except TypeError:
            super().__init__()
        self.config = config
        rules = _doc_rules(config)
        self.attachment = re.compile(
            rules.get("attachment_url_pattern")
            or r"(?i)\.(?:pdf|docx?|rtf|xlsx?|zip)(?:$|[?#])"
        )
        self.volume = (
            re.compile(rules["volume_pattern"]) if rules.get("volume_pattern") else None
        )

    def deliverable_text(
        self,
        ctx: Any,
        data: bytes,
        kind: str,
        acquisition: Mapping[str, Any],
        image_loader: Callable[[str], bytes | None] | None = None,
    ) -> tuple[str, dict[str, Any] | None, list[str]]:
        strip_pats = _strip_text_regexes(self.config)
        if kind == "html":
            tree, _, _ = parse_html(data, acquisition["charset"])
            return (
                self.html_markdown(tree, acquisition, image_loader=image_loader),
                None,
                ["markdown", "quality"],
            )
        if kind == "txt":
            text, _ = decode(data, acquisition["charset"])
            md = text_to_markdown(text)
            return (
                apply_text_cleaners(md, strip_pats) if strip_pats else md,
                None,
                ["markdown", "quality"],
            )
        if kind == "json":
            text, _ = decode(data, acquisition["charset"])
            md = json_to_markdown(text)
            return (
                apply_text_cleaners(md, strip_pats) if strip_pats else md,
                None,
                ["markdown", "quality"],
            )
        if kind == "xml":
            md = xml_to_markdown(data)
            return (
                apply_text_cleaners(md, strip_pats) if strip_pats else md,
                None,
                ["markdown", "quality"],
            )
        if kind in BINARY_KINDS:
            result = ctx.extract(data, kind)
            md = str(result["markdown"])
            return (
                apply_text_cleaners(md, strip_pats) if strip_pats else md,
                result,
                result["parsers"],
            )
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
        docs = _doc_rules(self.config)
        rule: Any = docs.get("content_xpath")
        nodes = tree.xpath(rule) if rule else [tree.body if tree.body is not None else tree]
        nodes = [n for n in nodes if isinstance(n, etree._Element)]
        node_set = set(nodes)
        nodes = [n for n in nodes if not any(anc in node_set for anc in n.iterancestors())]
        if not nodes:
            if blocked(etree.tostring(tree, encoding="utf-8"), self.config):
                raise WlhError("target_content_blocked")
            raise WlhError("content_region_missing")
        base = str(acquisition.get("actual_url") or acquisition.get("request_url") or "")
        ocr_cfg = _ocr_cfg(self.config)
        ocr_langs = (
            str(ocr_cfg.get("languages") or "jpn+jpn_vert")
            if isinstance(ocr_cfg, Mapping)
            else "jpn+jpn_vert"
        )
        excl_xpath = docs.get("exclude_content_xpath")
        if isinstance(excl_xpath, str):
            excl_list = [excl_xpath] if excl_xpath else []
        elif isinstance(excl_xpath, Sequence):
            excl_list = [str(x) for x in cast(Sequence[Any], excl_xpath) if x]
        else:
            excl_list = []
        dom_transforms = _parser_transforms(self.config)
        quirks = _extractor_quirks(self.config)
        cleaners_cfg = self.config.get("cleaners")
        cleaners_map: Mapping[str, Any] = (
            cast(Mapping[str, Any], cleaners_cfg) if isinstance(cleaners_cfg, Mapping) else {}
        )
        req_complete = bool(
            docs.get("require_complete_text") or cleaners_map.get("require_complete_text")
        )
        for node in nodes:
            clean_site_dom(
                node,
                exclude_xpaths=excl_list,
                dom_transforms=dom_transforms or ("inline_script_cell_restore",),
                include_defaults=True,
            )
            clean_html_tree(node, quirks=quirks)
            if req_complete:
                require_textual_images(
                    node,
                    base_url=base,
                    image_loader=image_loader,
                    ocr_languages=ocr_langs,
                    quirks=quirks,
                )
        fragment = "".join(etree.tostring(n, encoding="unicode", method="html") for n in nodes)
        if blocked(fragment.encode("utf-8"), self.config):
            raise WlhError("target_content_blocked")
        md = html_to_markdown(
            "<html><body>" + fragment + "</body></html>", base, quirks=quirks
        )
        strip_pats = _strip_text_regexes(self.config)
        return apply_text_cleaners(md, strip_pats) if strip_pats else md

    def detail_values(
        self,
        data: bytes,
        kind: str,
        acquisition: Mapping[str, Any],
        url: str,
        text: str | None = None,
    ) -> tuple[dict[str, Any], str | None, str | None]:
        """Declared field rules on the archived detail input."""
        title_pats = _title_strip_regexes(self.config)
        if _reconcile_range_enabled(self.config):
            if kind != "html":
                raise WlhError("court_detail_not_html")
            tree, _, encoding = parse_html(data, acquisition.get("charset"))
            fields = _dl_fields(tree)
            number = fields.get("事件番号")
            if not number:
                raise WlhError("court_case_number_missing")
            title = fields.get("事件名") or " ".join(
                value for value in (number, fields.get("権利種別")) if value
            )
            if title and title_pats:
                title = clean_title(title, title_pats)
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
        docs = _doc_rules(self.config)
        fields_cfg = docs.get("fields")
        fields_map: Mapping[str, Any] = (
            cast(Mapping[str, Any], fields_cfg) if isinstance(fields_cfg, Mapping) else {}
        )
        lv_rules = docs.get("label_value_rules")
        lv_seq: Sequence[Mapping[str, Any]] = (
            cast(Sequence[Mapping[str, Any]], lv_rules) if isinstance(lv_rules, Sequence) else ()
        )
        cleaners_cfg = self.config.get("cleaners")
        cleaners_map: Mapping[str, Any] = (
            cast(Mapping[str, Any], cleaners_cfg) if isinstance(cleaners_cfg, Mapping) else {}
        )
        infer_eff = (
            bool(cleaners_map.get("infer_effective_date", True))
            if "infer_effective_date" in cleaners_map
            else True
        )
        enrich_t = (
            bool(cleaners_map.get("enrich_bare_title", True))
            if "enrich_bare_title" in cleaners_map
            else True
        )
        values, title, encoding = extract_detail_fields(
            fields_map,
            data,
            kind,
            acquisition,
            url,
            text,
            label_value_rules=lv_seq,
            infer_effective=infer_eff,
            enrich_title=enrich_t,
        )
        if title and title_pats:
            title = clean_title(title, title_pats)
        if kind == "html" and lv_seq:
            if not values.get("document_number") and title:
                number_m = re.search(r"\b\d+/\d{4}/[\wĐđ]+(?:-[\wĐđ]+)*", title)
                if number_m:
                    values["document_number"] = number_m[0]
        return values, title, encoding

    def build(
        self, ctx: Any, target: Mapping[str, Any], inputs: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        if _reconcile_range_enabled(self.config):
            if len(inputs) < 2:
                raise WlhError("court_required_judgment_missing")
            for item in inputs[1:]:
                if detect_real_format(ctx.read(item["acquisition"])) != "pdf":
                    raise WlhError("court_required_document_not_pdf")
        docs = _doc_rules(self.config)
        detail = inputs[0]
        attachments = [
            item for item in inputs[1:] if item.get("role") not in ("title_context", "inline_image")
        ]
        inline_image_items = [item for item in inputs[1:] if item.get("role") == "inline_image"]
        acquisition = detail["acquisition"]
        data = ctx.read(acquisition)
        kind = detect_real_format(data, "html" if acquisition["mime_type"] == "text/html" else "")
        meta = _metadata_cfg(self.config)
        url: str = str(acquisition["actual_url"] or acquisition["request_url"] or "")
        values, title, encoding = self.detail_values(data, kind, acquisition, url)
        if (
            kind in TEXT_KINDS
            and not (kind == "html" and docs.get("content_xpath"))
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
            finder = getattr(ctx, "find_archived_image", None)
            if callable(finder):
                blob = finder(norm_url)
                if isinstance(blob, bytes):
                    archived_images[norm_url] = blob
                    return blob
            return None

        verified_title: str | None = None
        if kind in BINARY_KINDS and docs.get("binary_title_from_link"):
            verified_title, context = directory_title(
                ctx, inputs, acquisition["request_url"], GENERIC_ATTACHMENT_LABELS,
            )
            candidate_inputs.append(context)
        deliverables: list[dict[str, Any]] = []
        parsers = {"markdown"}
        quality_fail: list[str] = []
        accept_routes = _quality_routes(self.config)

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
            if report["recommended_route"] not in accept_routes:
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
                docs.get("attachment_title", "link_text") == "link_text"
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
        body_mode = "never" if _reconcile_range_enabled(self.config) else docs.get("detail_body", "auto")
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
        if not use_body and deliverables and not _reconcile_range_enabled(self.config):
            att_vals, _, _ = self.detail_values(
                data, kind, acquisition, url, deliverables[0]["markdown"]
            )
            for k, v in att_vals.items():
                if not values.get(k) and v:
                    values[k] = v
            cleaners_cfg = self.config.get("cleaners")
            cleaners_map: Mapping[str, Any] = (
                cast(Mapping[str, Any], cleaners_cfg) if isinstance(cleaners_cfg, Mapping) else {}
            )
            enrich_t = (
                bool(cleaners_map.get("enrich_bare_title", True))
                if "enrich_bare_title" in cleaners_map
                else True
            )
            if enrich_t:
                title = enrich_bare_title(
                    title,
                    values.get("document_number"),
                    tree=None,
                    body_text=deliverables[0]["markdown"],
                )
            title_pats = _title_strip_regexes(self.config)
            if title and title_pats:
                title = clean_title(title, title_pats)
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
        elif not use_body and deliverables and _reconcile_range_enabled(self.config):
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
        title_pats = _title_strip_regexes(self.config)
        final_title = verified_title or title
        if final_title and title_pats:
            final_title = clean_title(final_title, title_pats)
        meta["title"] = final_title
        for name in ("document_number", "issuer", "status"):
            if values.get(name):
                meta[name] = values[name]
        dates: dict[str, Any] = {
            k: values[k] for k in ("published_at", "effective_at", "expired_at") if values.get(k)
        }
        records = [
            {
                **target["identity"],
                "metadata": meta,
                "dates": dates,
                "deliverables": deliverables,
                "inputs": candidate_inputs,
                "parsers": sorted(parsers),
            }
        ]
        if _reconcile_range_enabled(self.config):
            for rec in records:
                for deliv in cast(list[dict[str, Any]], rec.get("deliverables") or []):
                    md = str(deliv.get("markdown") or "")
                    if len(re.sub(r"\s+", "", md)) < 15:
                        raise WlhError("quality_check_failed:vision_reparse_pages")
        return records

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


__all__ = [
    "BINARY_KINDS",
    "GENERIC_ATTACHMENT_LABELS",
    "TEXT_KINDS",
    "WebDocumentBuilder",
]
