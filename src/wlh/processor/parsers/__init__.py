"""Stage 3a Parser contract: plan / parse / build / plan_recheck over archived inputs.

Parsers never perform network I/O or write the database. They describe requests and document
targets, read already archived inputs through the Runtime context and return candidates.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from importlib import import_module
from typing import Any, cast

from wlh.config import PARSER_TYPES
from wlh.core import WlhError
from wlh.processor.records import RawRecord, RawUnitSource, RequestSpec

MODULES: dict[str, str] = {
    "html": "wlh.processor.parsers.html",
    "json": "wlh.processor.parsers.json",
    "xml": "wlh.processor.parsers.xml",
}
PARSERS: dict[str, str] = MODULES

assert set(PARSERS) == set(PARSER_TYPES)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(cast(Mapping[str, Any], value))
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
        return list(cast(Iterable[Any], value))
    return []


def effective_parser_options(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return merged parser options from 5-stage or flat config."""
    if "adapter_options" in config:
        return _as_dict(config["adapter_options"])
    proc_cfg = _as_dict(config.get("processor"))
    parser_cfg = _as_dict(proc_cfg.get("parser"))
    return _as_dict(parser_cfg.get("options"))


def normalize_parser_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return a unified dict exposing both 5-stage nested keys and flat parser convenience keys."""
    out: dict[str, Any] = dict(config)
    scheduler: dict[str, Any] = _as_dict(out.get("scheduler"))
    fetcher: dict[str, Any] = _as_dict(out.get("fetcher") or out.get("fetch"))
    processor: dict[str, Any] = _as_dict(out.get("processor"))
    cleaners: dict[str, Any] = _as_dict(out.get("cleaners"))
    storage: dict[str, Any] = _as_dict(out.get("storage"))

    raw_parser = processor.get("parser")
    parser_spec: dict[str, Any] = _as_dict(raw_parser)
    extractor_spec: dict[str, Any] = _as_dict(processor.get("extractor"))

    nested_docs: dict[str, Any] = _as_dict(parser_spec.get("documents") or out.get("documents"))
    if not parser_spec and isinstance(out.get("documents"), Mapping):
        parser_spec = {
            "type": out.get("adapter", "html"),
            "options": _as_dict(out.get("adapter_options")),
            "transforms": _as_list(out.get("transforms")),
            "documents": nested_docs,
            **nested_docs,
        }
    else:
        for k, v in nested_docs.items():
            parser_spec.setdefault(k, v)

    legacy_adapter_map = {
        "web": "html",
        "japan_courts": "html",
        "worldwidelaw_bridge": "html",
        "federal_register": "json",
        "openstates_bridge": "json",
        "opencaselaw_bridge": "json",
        "juriscraper_bridge": "json",
        "oldp_bridge": "json",
        "japan_law": "json",
    }
    parser_spec.setdefault("type", out.get("adapter", "html"))
    if parser_spec["type"] in legacy_adapter_map:
        parser_spec["type"] = legacy_adapter_map[parser_spec["type"]]
    parser_spec.setdefault("options", _as_dict(out.get("adapter_options")))
    parser_spec.setdefault("transforms", _as_list(out.get("transforms")))

    # Document rules defaults
    nested_docs.setdefault(
        "attachment_url_pattern",
        parser_spec.get(
            "attachment_url_pattern", r"(?i)\.(?:pdf|docx?|rtf|xlsx?|zip)(?:$|[?#])"
        ),
    )
    nested_docs.setdefault(
        "volume_pattern",
        parser_spec.get(
            "volume_pattern",
            r"(?i)(?:phần|quyển|tập|part|vol\.?|volume|卷|第)\s*0*([1-9][0-9]{0,2})\b",
        ),
    )
    nested_docs.setdefault("detail_url_patterns", _as_list(parser_spec.get("detail_url_patterns")))
    nested_docs.setdefault(
        "exclude_detail_url_patterns",
        _as_list(parser_spec.get("exclude_detail_url_patterns")),
    )
    nested_docs.setdefault("detail_xpath", parser_spec.get("detail_xpath"))
    nested_docs.setdefault("link_scope_xpath", parser_spec.get("link_scope_xpath"))
    nested_docs.setdefault("next_page_xpath", parser_spec.get("next_page_xpath"))
    nested_docs.setdefault(
        "attachment_exclude_xpath", parser_spec.get("attachment_exclude_xpath")
    )
    nested_docs.setdefault("attachment_title_xpath", parser_spec.get("attachment_title_xpath"))
    nested_docs.setdefault("attachment_title", parser_spec.get("attachment_title", "link_text"))
    nested_docs.setdefault("detail_body", parser_spec.get("detail_body", "auto"))
    nested_docs.setdefault("content_xpath", parser_spec.get("content_xpath"))
    nested_docs.setdefault(
        "exclude_content_xpath", parser_spec.get("exclude_content_xpath")
    )
    nested_docs.setdefault(
        "require_complete_text",
        bool(
            cleaners.get("require_complete_text")
            or parser_spec.get("require_complete_text", False)
        ),
    )
    nested_docs.setdefault(
        "binary_title_from_link", bool(parser_spec.get("binary_title_from_link", False))
    )
    nested_docs.setdefault("fields", _as_dict(parser_spec.get("fields")))
    nested_docs.setdefault("label_value_rules", _as_list(parser_spec.get("label_value_rules")))

    for k, v in nested_docs.items():
        parser_spec[k] = v
    parser_spec["exclude_xpaths"] = nested_docs["exclude_content_xpath"]
    parser_spec["volume_url_pattern"] = nested_docs["volume_pattern"]
    parser_spec["documents"] = nested_docs

    out_parser_dict = _as_dict(out.get("parser"))
    extractor_spec.setdefault("quirks", _as_list(out.get("quirks")))
    extractor_spec.setdefault(
        "libreoffice_timeout_seconds",
        out_parser_dict.get("libreoffice_timeout_seconds", 120),
    )
    extractor_spec.setdefault(
        "office_text_policy", out_parser_dict.get("office_text_policy", "prefer")
    )
    ocr_cfg: dict[str, Any] = (
        _as_dict(extractor_spec.get("ocr"))
        or _as_dict(processor.get("ocr"))
        or _as_dict(out.get("ocr"))
        or {
            "engine": "br-ocr-v1",
            "languages": "eng+chi_sim+vie",
            "model": "br-ocr-v1",
            "timeout_seconds": 180,
            "poll_seconds": 1.0,
            "max_pages": 200,
            "dpi": 144,
            "max_page_pixels": 20000000,
            "max_result_bytes": 16777216,
        }
    )
    extractor_spec["ocr"] = ocr_cfg

    if "entry_urls" in out and "entry_urls" not in scheduler:
        scheduler["entry_urls"] = _as_list(out["entry_urls"])
    if "allowed_hosts" in out and "allowed_hosts" not in scheduler:
        scheduler["allowed_hosts"] = _as_list(out["allowed_hosts"])
    if "allowed_path_prefixes" in out and "allowed_path_prefixes" not in scheduler:
        scheduler["allowed_path_prefixes"] = _as_list(out["allowed_path_prefixes"])
    if "host_path_scopes" in out and "host_path_scopes" not in scheduler:
        scheduler["host_path_scopes"] = _as_dict(out["host_path_scopes"])
    scheduler.setdefault("entry_urls", [])
    scheduler.setdefault("allowed_hosts", [])
    scheduler.setdefault("allowed_path_prefixes", ["/"])
    scheduler.setdefault("host_path_scopes", {})
    scheduler.setdefault("url_rewrites", [])
    scheduler.setdefault("max_depth", fetcher.get("max_depth", 3))
    scheduler.setdefault("concurrency", out.get("concurrency", 2))
    scheduler.setdefault("recheck_seconds", out.get("recheck_seconds", 86400))
    budget_cfg: dict[str, Any] = (
        _as_dict(scheduler.get("budget"))
        or _as_dict(out.get("budget"))
        or {
            "requests": 100,
            "seconds": 120,
            "response_bytes": 10485760,
            "documents": 10,
            "max_pages": 50,
        }
    )
    scheduler["budget"] = budget_cfg

    fetcher.setdefault("mode", "http")
    fetcher.setdefault("max_depth", scheduler["max_depth"])
    fetcher.setdefault("wait_until", "domcontentloaded")
    fetcher.setdefault("challenges", ["oracle_adf_loopback", "js_cookie_reload"])
    fetcher.setdefault("headers_from_env", {})
    fetcher.setdefault("robots", out.get("robots", True))
    fetcher.setdefault("robots_redirect_urls", _as_list(out.get("robots_redirect_urls")))
    fetcher.setdefault("blocked_patterns", _as_list(out.get("blocked_patterns")))
    fetcher.setdefault("delay", out.get("delay", 0.1))
    fetcher.setdefault("retries", 2)
    fetcher.setdefault("retry_backoff_seconds", 0.5)
    fetcher.setdefault("page_timeout_ms", 30000)
    fetcher.setdefault("user_agent", out.get("user_agent", "WLH/0.1 (+authorized archival crawler)"))

    metadata: dict[str, Any] = (
        _as_dict(storage.get("metadata"))
        or _as_dict(cleaners.get("metadata"))
        or _as_dict(out.get("metadata"))
        or {"document_type": "其他"}
    )
    metadata.setdefault("document_type", "其他")
    storage["metadata"] = metadata
    storage.setdefault("archive_approved", bool(out.get("archive_approved", True)))

    cleaners["metadata"] = metadata
    cleaners.setdefault(
        "quality_accept_routes",
        out_parser_dict.get(
            "quality_accept_routes", ["accept", "accept_ocr", "accept_with_warning"]
        ),
    )
    cleaners.setdefault("allow_single_resource", False)
    cleaners.setdefault("require_complete_text", nested_docs["require_complete_text"])
    cleaners.setdefault("infer_effective_date", False)
    cleaners.setdefault("enrich_bare_title", False)
    cleaners.setdefault("strip_text_regexes", [])
    cleaners.setdefault("title_strip_regexes", [])
    cleaners.setdefault("invalid_body_patterns", [])

    processor["parser"] = parser_spec
    processor["extractor"] = extractor_spec
    out["scheduler"] = scheduler
    out["fetcher"] = fetcher
    out["processor"] = processor
    out["cleaners"] = cleaners
    out["storage"] = storage

    # Convenience aliases for runtime and parser internals
    out["entry_urls"] = scheduler["entry_urls"]
    out["allowed_hosts"] = scheduler["allowed_hosts"]
    out["allowed_path_prefixes"] = scheduler["allowed_path_prefixes"]
    out["host_path_scopes"] = scheduler["host_path_scopes"]
    out["url_rewrites"] = scheduler["url_rewrites"]
    out["max_depth"] = scheduler["max_depth"]
    out["concurrency"] = scheduler["concurrency"]
    out["recheck_seconds"] = scheduler["recheck_seconds"]
    out["fetch"] = fetcher
    out["documents"] = nested_docs
    out["adapter"] = parser_spec["type"]
    out["adapter_options"] = parser_spec["options"]
    out["metadata"] = metadata
    out["ocr"] = ocr_cfg
    out["budget"] = budget_cfg
    out["parser"] = {
        "office_text_policy": extractor_spec["office_text_policy"],
        "libreoffice_timeout_seconds": extractor_spec["libreoffice_timeout_seconds"],
        "quality_accept_routes": cleaners["quality_accept_routes"],
    }
    out["transforms"] = _as_list(parser_spec.get("transforms"))
    out["quirks"] = _as_list(extractor_spec.get("quirks"))
    return out


def load(name: str) -> Any:
    if name not in PARSERS:
        raise WlhError("unknown_adapter")
    try:
        return import_module(PARSERS[name]).Parser
    except ModuleNotFoundError:
        raise WlhError("adapter_not_implemented") from None


def contract(name: str) -> str:
    return str(load(name).CONTRACT)


def semantic_options(name: str, options: Mapping[str, Any]) -> dict[str, Any]:
    """Only parsing semantics enter the parser fingerprint; pagination selects scope."""
    planning = load(name).PLANNING_OPTIONS
    return {key: value for key, value in options.items() if key not in planning}


def validate_options(name: str, options: Mapping[str, Any]) -> None:
    parser_cls = load(name)
    try:
        parser_cls.validate_options(options)
    except (ValueError, TypeError, KeyError):
        raise WlhError("invalid_adapter_options") from None


class Base:
    """Shared defaults; subclasses define CONTRACT and the parsing rules."""

    CONTRACT: str = "wlh.adapter.base.v0"
    PLANNING_OPTIONS: tuple[str, ...] = ()

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config: dict[str, Any] = normalize_parser_config(config)

    @property
    def parser_spec(self) -> dict[str, Any]:
        return self.config["processor"]["parser"]

    @property
    def extractor_spec(self) -> dict[str, Any]:
        return self.config["processor"]["extractor"]

    @property
    def transforms(self) -> set[str]:
        return set(self.parser_spec.get("transforms") or ())

    @property
    def quirks(self) -> set[str]:
        return set(self.extractor_spec.get("quirks") or ())

    @staticmethod
    def validate_options(options: Mapping[str, Any]) -> None:
        if options:
            raise ValueError("unexpected_adapter_options")

    def plan(self, state: dict[str, Any]) -> dict[str, Any]:
        """Initial requests and targets for a collect Run; ``state`` is adapter_state."""
        entry_urls = self.config["scheduler"]["entry_urls"]
        return {
            "requests": [
                {"url": url, "role": "page", "depth": 0} for url in entry_urls
            ],
            "targets": [],
            "state": state,
        }

    def plan_recheck(
        self, resources: Sequence[Mapping[str, Any]], state: dict[str, Any]
    ) -> dict[str, Any]:
        """Due Resources (stable identity, public_url, recheck_locator, current Version)."""
        targets: list[dict[str, Any]] = []
        for resource in resources:
            locator = resource["recheck_locator"]
            if not locator.get("url"):
                raise WlhError("recheck_locator_unsupported")
            request: dict[str, Any] = {
                "url": locator["url"],
                "role": locator.get("role", "page"),
                "depth": 0,
            }
            targets.append(
                {
                    "identity": {
                        "namespace": resource["namespace"],
                        "external_key": resource["external_key"],
                        "public_url": resource["public_url"],
                        "recheck_locator": locator,
                    },
                    "inputs": [{"request": request, "role": "detail"}],
                }
            )
        return {"requests": [], "targets": targets, "state": state}

    def parse(
        self, ctx: Any, task: Mapping[str, Any], acquisition: Mapping[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError

    def build(
        self, ctx: Any, target: Mapping[str, Any], inputs: Sequence[Mapping[str, Any]]
    ) -> Any:
        raise NotImplementedError


__all__ = [
    "MODULES",
    "PARSERS",
    "Base",
    "RawRecord",
    "RawUnitSource",
    "RequestSpec",
    "contract",
    "effective_parser_options",
    "load",
    "normalize_parser_config",
    "semantic_options",
    "validate_options",
]
