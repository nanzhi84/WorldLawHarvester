"""Shared boundary primitives; never include untrusted values in error messages."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import posixpath
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, unquote_plus, urlsplit, urlunsplit
from uuid import uuid4

MAX_ORIGINAL_BYTES = 512 * 1024 * 1024
FINGERPRINT_ALGORITHM = "wlh.fingerprint.v2"
ALWAYS = ("algorithm", "adapter", "fetch", "markdown")
TERMINAL_STATES = {"complete", "failed"}


class WlhError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def uid() -> str:
    return str(uuid4())


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value)).hexdigest()


SENSITIVE = re.compile(
    r"token|password|secret|authorization|api[-_]?key|session|signature|credential", re.I
)


def safe_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise WlhError("invalid_url")
        host = parts.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        port = parts.port
        if port and (parts.scheme, port) not in (("http", 80), ("https", 443)):
            host += ":" + str(port)
        # Resource identity is the serialized query, not decoded name/value pairs.
        # Redact only sensitive values for diagnostics; public_url rejects these URLs.
        query = "&".join(
            segment.partition("=")[0] + "=REDACTED"
            if SENSITIVE.search(unquote_plus(segment.partition("=")[0]))
            else segment
            for segment in parts.query.split("&")
        )
        return urlunsplit((parts.scheme, host, parts.path or "/", query, ""))
    except ValueError:
        raise WlhError("invalid_url") from None


def public_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.username
        or parts.password
        or any(
            SENSITIVE.search(unquote_plus(segment.partition("=")[0]))
            for segment in parts.query.split("&")
        )
    ):
        raise WlhError("sensitive_url_rejected")
    return safe_url(url)


def request_key(request: Mapping[str, Any]) -> str:
    form = request.get("form")
    return digest(
        {
            "algorithm": "wlh.request-key.v1",
            "method": request.get("method", "GET"),
            "url": public_url(request["url"]),
            "form": digest(form) if form else None,
            "role": request.get("role", "page"),
        }
    )


def check_address(host: str | None) -> None:
    """Basic address boundary; explicit loopback opt-in is for isolated acceptance."""
    if not host:
        raise WlhError("invalid_url")
    if host.rstrip(".").lower() == "localhost" or host.lower().endswith(
        (".localhost", ".local", ".internal")
    ):
        raise WlhError("non_public_address")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return  # Hostname scope is enforced separately; this is not a DNS-rebinding sandbox.
    if not address.is_global and not (
        address.is_loopback and os.environ.get("WLH_ALLOW_LOOPBACK") == "1"
    ):
        raise WlhError("non_public_address")


def valid_prefix(path: Any) -> bool:
    return (
        isinstance(path, str)
        and path.startswith("/")
        and ".." not in path
        and unquote(path) == path
        and "?" not in path
        and "#" not in path
        and "\\" not in path
    )


def _sched_scope_dict(config: Mapping[str, Any]) -> Mapping[str, Any]:
    sched = config.get("scheduler")
    if isinstance(sched, Mapping):
        return cast(Mapping[str, Any], sched)
    return config


def paths_for(config: Mapping[str, Any], host: str | None, path: str) -> bool:
    sched = _sched_scope_dict(config)
    allowed_hosts: list[str] = [str(h) for h in cast(Iterable[Any], sched.get("allowed_hosts") or ())]
    if not host or host not in allowed_hosts:
        return False
    scopes_raw = sched.get("host_path_scopes")
    scopes: Mapping[str, Sequence[str]] = (
        cast(Mapping[str, Sequence[str]], scopes_raw) if isinstance(scopes_raw, Mapping) else {}
    )
    raw_prefixes: Sequence[Any] = (
        scopes.get(host, ())
        if scopes
        else cast(Sequence[Any], sched.get("allowed_path_prefixes") or ("/",))
    )
    prefixes: list[str] = [str(p) for p in raw_prefixes]
    norm = posixpath.normpath(unquote(path))
    return any(norm == p.rstrip("/") or norm.startswith(p.rstrip("/") + "/") for p in prefixes)


def guard(config: Mapping[str, Any], url: str, *, policy: bool = False) -> str:
    url = public_url(url)
    parsed = urlsplit(url)
    check_address(parsed.hostname)
    sched = _sched_scope_dict(config)
    fetcher_raw = config.get("fetcher")
    fetcher_map: Mapping[str, Any] = (
        cast(Mapping[str, Any], fetcher_raw) if isinstance(fetcher_raw, Mapping) else {}
    )
    redirect_urls: list[str] = [
        str(u)
        for u in cast(
            Iterable[Any],
            sched.get("robots_redirect_urls") or fetcher_map.get("robots_redirect_urls") or (),
        )
    ]
    if policy and url in redirect_urls:
        return url
    allowed_policy = policy and parsed.path == "/robots.txt" and not parsed.query
    allowed_hosts: list[str] = [str(h) for h in cast(Iterable[Any], sched.get("allowed_hosts") or ())]
    if parsed.hostname not in allowed_hosts or not (
        allowed_policy or paths_for(sched, parsed.hostname, parsed.path)
    ):
        raise WlhError("request_out_of_scope")
    return url


def compact(value: str) -> str:
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[ \t\u00a0]+", " ", value).strip()


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("****", "")
    value = re.sub(r"【字体：\s*大\s+中\s+小\s*】", "", value)
    value = re.sub(r"\d{4}-\d{2}-\d{2}[ \t]+字体\s*\[\s*大\s+中\s+小\s*\]", "", value)
    value = re.sub(r"字体\s*\[\s*大\s+中\s+小\s*\]", "", value)
    value = re.sub(r"\[责任编辑：[^\]]*\]", "", value)
    lines = [line.rstrip() for line in value.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def fingerprint(kind: str, payload: Any) -> str:
    return digest({"algorithm": FINGERPRINT_ALGORITHM, "kind": kind, "payload": payload})


def effective(manifest: Mapping[str, Any] | None, used: Iterable[str]) -> dict[str, Any]:
    if not manifest:
        raise WlhError("parser_manifest_missing")
    selected = set(ALWAYS) | set(used)
    return {key: value for key, value in manifest.items() if key in selected}


def exhaustion_error(run: Mapping[str, Any]) -> str | None:
    if run["deadline"] <= datetime.now(timezone.utc):
        return "deadline_exceeded"
    if run["attempt_epoch"] >= run["max_attempts"]:
        return "attempts_exhausted"
    return None


def eligibility_error(source: Mapping[str, Any], run: Mapping[str, Any]) -> str | None:
    if source["generation"] != run["generation"]:
        return "source_generation_changed"
    if source["paused"]:
        return "source_paused"
    if source["write_seq"] > run["run_seq"]:
        return "run_superseded"
    return None


def atomic_write(path: str | os.PathLike[str], data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uid() + ".tmp")
    try:
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temp.unlink(missing_ok=True)


SealError = WlhError
WLH_ALLOW_LOOPBACK = "WLH_ALLOW_LOOPBACK"
