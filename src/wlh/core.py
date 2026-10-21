"""Shared boundary primitives; never include untrusted values in error messages."""

import hashlib
import ipaddress
import json
import os
import re
from pathlib import Path
from urllib.parse import unquote_plus, urlsplit, urlunsplit
from uuid import uuid4

MAX_ORIGINAL_BYTES = 128 * 1024 * 1024


class WlhError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def uid():
    return str(uuid4())


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value)).hexdigest()


SENSITIVE = re.compile(
    r"token|password|secret|authorization|api[-_]?key|session|signature|credential", re.I
)


def safe_url(url):
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


def public_url(url):
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


def check_address(host):
    """Basic address boundary; explicit loopback opt-in is for isolated acceptance."""
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


def atomic_write(path, data):
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
