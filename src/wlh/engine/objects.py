"""Private S3 CAS; relational references commit only after verified object reads."""

from __future__ import annotations

import atexit
import json
import os
import re
import time
from collections import OrderedDict
from functools import cache
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from boto3.session import Session  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from wlh.core import WlhError, canonical, digest

_VERIFIED_CACHE: OrderedDict[tuple[str, str], bytes] = OrderedDict()
_verified_cache_bytes: int = 0
_MAX_CACHE_ITEM_BYTES: int = 67108864
_MAX_CACHE_TOTAL_BYTES: int = 134217728
_MAX_CACHE_ENTRIES: int = 64


def _cache_verified(bucket: str, key: str, body: bytes) -> None:
    global _verified_cache_bytes
    if len(body) > _MAX_CACHE_ITEM_BYTES:
        return
    cache_key = (bucket, key)
    old = _VERIFIED_CACHE.pop(cache_key, None)
    if old is not None:
        _verified_cache_bytes -= len(old)
    while _VERIFIED_CACHE and (
        len(_VERIFIED_CACHE) >= _MAX_CACHE_ENTRIES
        or _verified_cache_bytes + len(body) > _MAX_CACHE_TOTAL_BYTES
    ):
        _, evicted = _VERIFIED_CACHE.popitem(last=False)
        _verified_cache_bytes -= len(evicted)
    _VERIFIED_CACHE[cache_key] = body
    _verified_cache_bytes += len(body)


@cache
def client(endpoint: str, access_key: str, secret_key: str, region: str) -> Any:
    session: Any = Session()
    value: Any = session.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=3,
            read_timeout=10,
            retries={"mode": "standard", "total_max_attempts": 2},
        ),
    )
    atexit.register(value.close)
    return value


def work_directory() -> Path:
    path = Path(os.environ.get("WLH_WORK_DIR", "work/wlh")).resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


class Objects:
    def __init__(self) -> None:
        defaults: dict[str, str] = {
            "ENDPOINT": "http://127.0.0.1:19000",
            "BUCKET": "wlh-archive",
            "ACCESS_KEY": "wlh",
            "SECRET_KEY": "wlhsecret",
        }
        settings: dict[str, str] = {
            name: os.environ.get("WLH_S3_" + name, default) for name, default in defaults.items()
        }
        try:
            endpoint = urlsplit(settings["ENDPOINT"])
            if (
                endpoint.scheme not in {"http", "https"}
                or not endpoint.hostname
                or endpoint.username
                or endpoint.password
                or endpoint.query
                or endpoint.fragment
                or endpoint.path not in {"", "/"}
            ):
                raise ValueError
            _ = endpoint.port
        except ValueError:
            raise WlhError("invalid_object_storage_endpoint") from None
        self.bucket: str = settings["BUCKET"]
        if "PYTEST_CURRENT_TEST" in os.environ:
            if not (
                self.bucket.endswith(("-test", "_test"))
                or self.bucket.startswith(("test-", "test_"))
            ):
                raise WlhError("test_object_storage_isolation_violation")
        self.client: Any = client(
            settings["ENDPOINT"],
            settings["ACCESS_KEY"],
            settings["SECRET_KEY"],
            os.environ.get("WLH_S3_REGION", "us-east-1"),
        )

    def check(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except (BotoCoreError, ClientError, OSError):
            raise WlhError("object_storage_unavailable") from None

    @staticmethod
    def key(value: Any) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise WlhError("archive_invalid")
        return value

    def put(self, body: bytes) -> str:
        key = digest(body)
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType="application/octet-stream",
                IfNoneMatch="*",
            )
        except ClientError as exc:
            # A concurrent writer or a retried successful PUT may already own the key.
            # Never overwrite it: re-read and verify the original bytes instead.
            if cast(Any, exc).response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise WlhError("archive_write_failed") from None
            self.get(key)
            return key
        except (BotoCoreError, OSError):
            raise WlhError("archive_write_failed") from None
        _VERIFIED_CACHE.pop((self.bucket, key), None)
        self.get(key)
        return key

    def get(
        self, key: str, *, max_bytes: int | None = None, deadline: float | None = None
    ) -> bytes:
        self.key(key)
        if deadline is not None and time.monotonic() >= deadline:
            raise WlhError("archive_read_timeout")
        cached = _VERIFIED_CACHE.get((self.bucket, key))
        if cached is not None:
            if max_bytes is not None and len(cached) > max_bytes:
                raise WlhError("archive_too_large")
            _VERIFIED_CACHE.move_to_end((self.bucket, key))
            return cached
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            stream = response["Body"]
            try:
                if max_bytes is None:
                    body = stream.read()
                else:
                    if response.get("ContentLength", 0) > max_bytes:
                        raise WlhError("archive_too_large")
                    data = bytearray()
                    while True:
                        if deadline is not None and time.monotonic() >= deadline:
                            raise WlhError("archive_read_timeout")
                        chunk = stream.read(min(1048576, max_bytes - len(data) + 1))
                        if not chunk:
                            break
                        data.extend(chunk)
                        if len(data) > max_bytes:
                            raise WlhError("archive_too_large")
                    body = bytes(data)
                    data.clear()
            finally:
                stream.close()
        except ClientError as exc:
            if cast(Any, exc).response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                raise WlhError("archive_missing") from None
            raise WlhError("archive_unreadable") from None
        except (BotoCoreError, OSError):
            raise WlhError("archive_unreadable") from None
        if digest(body) != key:
            raise WlhError("archive_corrupt")
        _cache_verified(self.bucket, key, body)
        return body

    def put_json(self, value: Any) -> str:
        return self.put(canonical(value))

    def json(self, key: str) -> Any:
        return json.loads(self.get(key))
