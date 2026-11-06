"""Offline execution of production parsers on benchmark source payloads."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, cast

_PROCESSOR_CACHE: dict[tuple[str, str], tuple[dict[str, Any], Any]] = {}


def clear_processor_cache() -> None:
    """Invalidate source configurations and parsers after configuration changes."""
    _PROCESSOR_CACHE.clear()


def extract_with_processor(
    task: Mapping[str, Any], *, sources_dir: Path = Path("sources")
) -> dict[str, Any]:
    """Run offline processor pipeline on a ProcessorBenchmarkTask."""
    source_id = str(task.get("source_id", ""))
    task_type = str(task.get("task_type", "content_page"))
    request_url = str(task.get("request_url", ""))
    raw_payload = str(task.get("raw_payload", ""))
    mime_type = str(task.get("mime_type", "text/html"))

    sources_dir = Path(sources_dir)
    yaml_path = sources_dir / f"{source_id}.yaml"
    if not yaml_path.is_file():
        if task_type == "list_page":
            return {"target_urls": []}
        return {
            "records": [{"title": None, "doc_number": None, "markdown": "", "attachment_urls": []}]
        }

    try:
        import yaml

        from wlh.processor.parsers import load, normalize_parser_config

        cache_key = (str(sources_dir.resolve()), source_id)
        if cache_key not in _PROCESSOR_CACHE:
            with yaml_path.open("r", encoding="utf-8") as f:
                cfg = yaml.load(f, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
            norm_cfg = normalize_parser_config(cfg)
            parser = load(norm_cfg["adapter"])(norm_cfg)
            _PROCESSOR_CACHE[cache_key] = (norm_cfg, parser)
        norm_cfg, parser = _PROCESSOR_CACHE[cache_key]
        # Per-sample discovery state must not leak through the cached parser.
        if hasattr(parser, "detail_url"):
            parser.detail_url = None
        if hasattr(parser, "truncated"):
            parser.truncated = None

        from wlh.core import digest

        payload_bytes = raw_payload.encode("utf-8")

        class _OfflineCtx:
            class _Runtime:
                mode = "collect"
                config = norm_cfg

            runtime = _Runtime()
            config = norm_cfg

            @staticmethod
            def read(_acq: Any) -> bytes:
                return payload_bytes

            @staticmethod
            def follow_links(_t: Any) -> bool:
                return True

            @staticmethod
            def text_quality(markdown: str) -> dict[str, Any]:
                from wlh.cleaners.extraction_quality import text_check

                lang = str(norm_cfg.get("metadata", {}).get("language") or "")
                return text_check(markdown, "vi" if lang.startswith("vi") else "generic")

            @staticmethod
            def find_archived_image(_url: str) -> bytes | None:
                return None

        ctx = _OfflineCtx()
        parse_task = {
            "depth": 0 if task_type == "list_page" else 1,
            "payload": {"url": request_url, "role": "page"},
        }
        acquisition = {
            "id": f"acq-{source_id}",
            "outcome": "ok",
            "actual_url": request_url,
            "request_url": request_url,
            "mime_type": mime_type,
            "charset": "utf-8",
            "archive_ref": digest(payload_bytes),
            "state": "archived",
        }

        parsed = parser.parse(ctx, parse_task, acquisition)

        parsed_dict: dict[str, Any] = (
            cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}
        )

        if task_type == "list_page":
            requests_list: list[dict[str, Any]] = (
                cast(list[dict[str, Any]], parsed_dict.get("requests"))
                if isinstance(parsed_dict.get("requests"), list)
                else []
            )
            discovered_urls = [str(r["url"]) for r in requests_list if "url" in r]
            return {"target_urls": discovered_urls}
        else:
            records: list[dict[str, Any]] = []
            targets: list[dict[str, Any]] = (
                cast(list[dict[str, Any]], parsed_dict.get("targets"))
                if isinstance(parsed_dict.get("targets"), list)
                else []
            )
            for target in targets:
                inputs_list: list[dict[str, Any]] = (
                    cast(list[dict[str, Any]], target.get("inputs"))
                    if isinstance(target.get("inputs"), list)
                    else []
                )
                for inp in inputs_list:
                    inp.setdefault("acquisition", acquisition)
                try:
                    built: Any = parser.build(ctx, target, inputs_list)
                    docs: list[object] = cast(
                        list[object], built if isinstance(built, list) else [built]
                    )
                    for d_raw in docs:
                        if not isinstance(d_raw, dict):
                            continue
                        d: dict[str, Any] = cast(dict[str, Any], d_raw)
                        meta_dict: dict[str, Any] = cast(dict[str, Any], d.get("metadata") or {})
                        dates_dict: dict[str, Any] = cast(dict[str, Any], d.get("dates") or {})
                        delivs: list[object] = cast(list[object], d.get("deliverables") or [])
                        md = ""
                        for dev_raw in delivs:
                            if isinstance(dev_raw, dict):
                                dev: dict[str, Any] = cast(dict[str, Any], dev_raw)
                                if dev.get("markdown"):
                                    md = str(dev["markdown"])
                                    break
                        if not md:
                            md_raw = d.get("markdown") or meta_dict.get("markdown") or ""
                            md = str(md_raw)

                        title: str | None = (
                            str(d["title"])
                            if d.get("title") is not None
                            else (
                                str(meta_dict["title"])
                                if meta_dict.get("title") is not None
                                else None
                            )
                        )
                        doc_num: str | None = (
                            str(meta_dict["doc_number"])
                            if meta_dict.get("doc_number") is not None
                            else (
                                str(meta_dict["document_number"])
                                if meta_dict.get("document_number") is not None
                                else None
                            )
                        )
                        pub: str | None = (
                            str(dates_dict["published_at"])
                            if dates_dict.get("published_at") is not None
                            else (
                                str(meta_dict["published_at"])
                                if meta_dict.get("published_at") is not None
                                else None
                            )
                        )
                        eff: str | None = (
                            str(dates_dict["effective_at"])
                            if dates_dict.get("effective_at") is not None
                            else (
                                str(meta_dict["effective_at"])
                                if meta_dict.get("effective_at") is not None
                                else None
                            )
                        )
                        iss: str | None = (
                            str(meta_dict["issuer"])
                            if meta_dict.get("issuer") is not None
                            else None
                        )
                        cfg_meta = cast(dict[str, Any], norm_cfg.get("metadata", {}))
                        doc_t: str = str(
                            meta_dict.get("doc_type") or cfg_meta.get("document_type", "其他")
                        )
                        inp_candidates: list[object] = [
                            *cast(list[object], d.get("inputs") or []),
                            *inputs_list,
                        ]
                        att_urls: list[str] = []
                        for item_raw in inp_candidates:
                            if isinstance(item_raw, dict):
                                item_dict: dict[str, Any] = cast(dict[str, Any], item_raw)
                                if item_dict.get("role") == "attachment":
                                    req_raw = item_dict.get("request") or item_dict.get("locator")
                                    if isinstance(req_raw, dict):
                                        req_dict: dict[str, Any] = cast(dict[str, Any], req_raw)
                                        url_val = req_dict.get("url")
                                        if url_val and str(url_val) not in att_urls:
                                            att_urls.append(str(url_val))
                        records.append(
                            {
                                "title": title,
                                "doc_number": doc_num,
                                "published_at": pub,
                                "effective_at": eff,
                                "issuer": iss,
                                "doc_type": doc_t,
                                "markdown": md,
                                "attachment_urls": att_urls,
                            }
                        )
                except Exception:
                    pass
            if not records:
                records = [
                    {
                        "title": None,
                        "doc_number": None,
                        "published_at": None,
                        "effective_at": None,
                        "issuer": None,
                        "doc_type": "其他",
                        "markdown": "",
                        "attachment_urls": [],
                    }
                ]
            return {"records": records}
    except Exception:
        if task_type == "list_page":
            return {"target_urls": []}
        return {
            "records": [{"title": None, "doc_number": None, "markdown": "", "attachment_urls": []}]
        }
