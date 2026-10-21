"""Private S3 CAS; relational references commit only after verified object reads."""

import atexit
import json
import os
import re
import time
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit

from boto3.session import Session
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from wlh.core import WlhError, canonical, digest


@cache
def client(endpoint, access_key, secret_key, region):
    value = Session().client(
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


def work_directory():
    path = Path(os.environ.get("WLH_WORK_DIR", "work/wlh")).resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


class Objects:
    def __init__(self):
        defaults = {
            "ENDPOINT": "http://127.0.0.1:19000",
            "BUCKET": "wlh-archive",
            "ACCESS_KEY": "wlh",
            "SECRET_KEY": "wlhsecret",
        }
        settings = {
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
        self.bucket = settings["BUCKET"]
        self.client = client(
            settings["ENDPOINT"],
            settings["ACCESS_KEY"],
            settings["SECRET_KEY"],
            os.environ.get("WLH_S3_REGION", "us-east-1"),
        )

    def check(self):
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except (BotoCoreError, ClientError, OSError):
            raise WlhError("object_storage_unavailable") from None

    @staticmethod
    def key(value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise WlhError("archive_invalid")
        return value

    def put(self, body):
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
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise WlhError("archive_write_failed") from None
            self.get(key)
        except (BotoCoreError, OSError):
            raise WlhError("archive_write_failed") from None
        self.get(key)
        return key

    def get(self, key, *, max_bytes=None, deadline=None):
        self.key(key)
        if deadline is not None and time.monotonic() >= deadline:
            raise WlhError("archive_read_timeout")
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
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                raise WlhError("archive_missing") from None
            raise WlhError("archive_unreadable") from None
        except (BotoCoreError, OSError):
            raise WlhError("archive_unreadable") from None
        if digest(body) != key:
            raise WlhError("archive_corrupt")
        return body

    def put_json(self, value):
        return self.put(canonical(value))

    def json(self, key):
        return json.loads(self.get(key))
