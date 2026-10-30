"""Shared URL boundary for SDK traversal, HTTP redirects, browser subrequests, and declarative rewrites."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import urlsplit

from wlh.core import WlhError, check_address, paths_for, public_url, valid_prefix


def apply_url_rewrites(
    url: str,
    config_or_rules: Mapping[str, Any] | Sequence[Any],
    *,
    depth: int | None = None,
) -> str:
    """Apply declarative URL rewrite rules configured under scheduler.url_rewrites."""
    if isinstance(config_or_rules, Mapping):
        cfg_map = config_or_rules
        sched_raw = cfg_map.get("scheduler")
        sched: Mapping[str, Any] = (
            cast(Mapping[str, Any], sched_raw) if isinstance(sched_raw, Mapping) else cfg_map
        )
        rules: Sequence[Any] = cast(
            Sequence[Any], sched.get("url_rewrites") or cfg_map.get("url_rewrites") or ()
        )
    else:
        rules = config_or_rules
    current = url
    for rule in rules:
        if isinstance(rule, Mapping):
            rule_map = cast(Mapping[str, Any], rule)
            match_pat: Any = rule_map.get("pattern") or rule_map.get("match")
            replace_pat: Any = (
                rule_map.get("replacement")
                if rule_map.get("replacement") is not None
                else rule_map.get("replace")
            )
            only_depth: Any = rule_map.get("only_at_depth")
        else:
            match_pat = getattr(rule, "pattern", None) or getattr(rule, "match", None)
            replace_pat = (
                getattr(rule, "replacement", None)
                if getattr(rule, "replacement", None) is not None
                else getattr(rule, "replace", None)
            )
            only_depth = getattr(rule, "only_at_depth", None)
        if not match_pat or replace_pat is None:
            continue
        if only_depth is not None and depth != only_depth:
            continue
        current = re.sub(str(match_pat), str(replace_pat), current)
    return current


def guard(
    config: Mapping[str, Any],
    url: str,
    *,
    policy: bool = False,
    depth: int | None = None,
) -> str:
    rewritten = apply_url_rewrites(url, config, depth=depth)
    canonical = public_url(rewritten)
    parsed = urlsplit(canonical)
    check_address(parsed.hostname)
    sched_raw = config.get("scheduler")
    sched: Mapping[str, Any] = (
        cast(Mapping[str, Any], sched_raw) if isinstance(sched_raw, Mapping) else config
    )
    fetcher_raw = config.get("fetcher")
    fetch_raw = config.get("fetch")
    f_cfg: Mapping[str, Any] = (
        cast(Mapping[str, Any], fetcher_raw)
        if isinstance(fetcher_raw, Mapping)
        else cast(Mapping[str, Any], fetch_raw)
        if isinstance(fetch_raw, Mapping)
        else config
    )
    robots_redirects: Sequence[Any] = cast(
        Sequence[Any],
        f_cfg.get("robots_redirect_urls")
        or sched.get("robots_redirect_urls")
        or config.get("robots_redirect_urls")
        or (),
    )
    if policy and canonical in robots_redirects:
        return canonical
    allowed_policy = policy and parsed.path == "/robots.txt" and not parsed.query
    allowed_hosts: Sequence[Any] = cast(
        Sequence[Any], sched.get("allowed_hosts") or config.get("allowed_hosts") or ()
    )
    if parsed.hostname not in allowed_hosts or not (
        allowed_policy or paths_for(config, parsed.hostname, parsed.path)
    ):
        raise WlhError("request_out_of_scope")
    return canonical


__all__ = ["apply_url_rewrites", "guard", "paths_for", "valid_prefix"]
