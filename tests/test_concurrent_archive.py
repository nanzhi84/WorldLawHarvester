"""Concurrent archive contracts exercised against isolated, real S3 stores.

Failure modes fixed before implementation: racing equal/different writes can return
wrong hashes or bytes, duplicate writes can replace immutable objects, corrupt
stored bytes can be accepted, and one endpoint's cache can conceal another
endpoint's missing or corrupt object. No assertion depends on cache internals.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

import boto3  # type: ignore[import-untyped]
import pytest

from wlh.core import WlhError
from wlh.storage.cas import Objects

pytestmark = pytest.mark.pipeline


def _client(env: dict[str, str], endpoint: str) -> Any:
    sdk: Any = boto3
    return sdk.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=env["WLH_S3_ACCESS_KEY"],
        aws_secret_access_key=env["WLH_S3_SECRET_KEY"],
        region_name=env["WLH_S3_REGION"],
    )


def _body(client: Any, bucket: str, key: str) -> bytes:
    response = client.get_object(Bucket=bucket, Key=key)
    stream = response["Body"]
    try:
        return bytes(stream.read())
    finally:
        stream.close()


def _stored_key(client: Any, bucket: str, payload: bytes) -> str:
    """Discover the written S3 key through public listing and actual stored bytes."""
    matches: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for item in page.get("Contents", []):
            if int(item["Size"]) == len(payload):
                key = str(item["Key"])
                if _body(client, bucket, key) == payload:
                    matches.append(key)
    assert len(matches) == 1, f"Expected one immutable object, found {len(matches)}"
    return matches[0]


def _versions(client: Any, bucket: str, key: str) -> list[str]:
    versions: list[str] = []
    for page in client.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix=key):
        versions.extend(
            str(item["VersionId"]) for item in page.get("Versions", []) if item["Key"] == key
        )
    return sorted(versions)


def _outcome(call: Callable[[], str | bytes]) -> dict[str, str]:
    try:
        value = call()
    except WlhError as exc:
        return {"error": exc.code}
    except Exception as exc:
        return {"unexpected_error": type(exc).__name__}
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest(), "length": str(len(value))}
    return {"returned_hash": value}


@pytest.fixture()
def archive_evidence(request: Any) -> Generator[dict[str, Any], None, None]:
    evidence: dict[str, Any] = {"test": request.node.name, "real_s3": True}
    try:
        yield evidence
    finally:
        directory = Path(
            os.environ.get(
                "WLH_CAS_ACCEPTANCE_ARTIFACT_DIR",
                "artifacts/cas-integration-20261105/storage-results",
            )
        )
        directory.mkdir(parents=True, exist_ok=True)
        filename = request.node.name.replace("[", "-").replace("]", "") + ".json"
        (directory / filename).write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


@pytest.fixture()
def versioned_store(wlh_env: dict[str, str]) -> Generator[Any, None, None]:
    client = _client(wlh_env, wlh_env["WLH_S3_ENDPOINT"])
    client.put_bucket_versioning(
        Bucket=wlh_env["WLH_S3_BUCKET"], VersioningConfiguration={"Status": "Enabled"}
    )
    try:
        yield client
    finally:
        client.close()


@pytest.fixture()
def second_store(wlh_env: dict[str, str]) -> Generator[tuple[str, Any], None, None]:
    endpoint = os.environ.get("WLH_TEST_SECOND_S3_ENDPOINT")
    if not endpoint:
        pytest.skip("Endpoint isolation requires WLH_TEST_SECOND_S3_ENDPOINT (isolated MinIO).")
    assert endpoint != wlh_env["WLH_S3_ENDPOINT"]
    client = _client(wlh_env, endpoint)
    bucket = wlh_env["WLH_S3_BUCKET"]
    if bucket not in {item["Name"] for item in client.list_buckets()["Buckets"]}:
        client.create_bucket(Bucket=bucket)
    client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    try:
        yield endpoint, client
    finally:
        client.close()


class TestConcurrentArchive:
    @pytest.mark.parametrize("share_instance", [True, False], ids=["shared", "per-thread"])
    def test_equal_and_distinct_payloads_return_exact_bytes_without_replacing_objects(
        self,
        wlh_env: dict[str, str],
        versioned_store: Any,
        archive_evidence: dict[str, Any],
        share_instance: bool,
    ) -> None:
        prefix = uuid4().hex.encode()
        payloads = [prefix + b"\x00\xff\x80\n"] + [
            prefix + index.to_bytes(2, "big") + bytes([index]) * (256 * 1024 + index)
            for index in range(64)
        ]
        payloads.append(prefix + bytes(range(256)) * (24 * 1024))
        digests = {hashlib.sha256(payload).hexdigest(): payload for payload in payloads}
        objects = Objects()
        workers, rounds = 24, 16
        barrier = threading.Barrier(workers, timeout=60)

        def archive(worker: int) -> list[dict[str, str]]:
            client = objects if share_instance else Objects()
            failures: list[dict[str, str]] = []
            for turn in range(rounds):
                barrier.wait()
                index = 0 if turn % 4 == 0 else (worker + turn * workers) % len(payloads)
                payload = payloads[index]
                expected = hashlib.sha256(payload).hexdigest()
                try:
                    returned = client.put(payload)
                    actual = client.get(returned)
                    if returned != expected or actual != payload:
                        failures.append({"expected": expected, "returned": returned})
                except Exception as exc:
                    failures.append({"unexpected_error": type(exc).__name__})
            return failures

        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(archive, range(workers)))
        errors = [error for worker_errors in outcomes for error in worker_errors]
        bucket = wlh_env["WLH_S3_BUCKET"]
        stored: dict[str, dict[str, Any]] = {}
        for page in versioned_store.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for item in page.get("Contents", []):
                key = str(item["Key"])
                raw = _body(versioned_store, bucket, key)
                digest = hashlib.sha256(raw).hexdigest()
                if digest in digests:
                    assert raw == digests[digest]
                    stored[digest] = {
                        "length": len(raw),
                        "versions": _versions(versioned_store, bucket, key),
                    }
        expected_hashes = {
            hashlib.sha256(
                payloads[0 if turn % 4 == 0 else (worker + turn * workers) % len(payloads)]
            ).hexdigest()
            for worker in range(workers)
            for turn in range(rounds)
        }
        archive_evidence.update(
            workers=workers,
            rounds=rounds,
            operations=workers * rounds,
            errors=errors,
            expected_hashes=sorted(expected_hashes),
            stored=stored,
        )
        assert errors == []
        assert set(stored) == expected_hashes
        assert all(len(item["versions"]) == 1 for item in stored.values()), (
            "An immutable object was replaced by a duplicate concurrent put"
        )

    @pytest.mark.parametrize("operation", ["get", "put"])
    def test_corrupt_stored_bytes_are_rejected_and_never_overwritten(
        self,
        wlh_env: dict[str, str],
        versioned_store: Any,
        archive_evidence: dict[str, Any],
        operation: str,
    ) -> None:
        payload = b"cold archive corruption contract " + uuid4().hex.encode()
        digest = hashlib.sha256(payload).hexdigest()
        seeded = subprocess.run(
            [
                sys.executable,
                "-c",
                "import base64,sys; from wlh.storage.cas import Objects; "
                "print(Objects().put(base64.b64decode(sys.stdin.buffer.read())))",
            ],
            input=base64.b64encode(payload),
            capture_output=True,
            timeout=60,
            check=True,
        )
        assert seeded.stdout.decode().strip() == digest
        bucket = wlh_env["WLH_S3_BUCKET"]
        key = _stored_key(versioned_store, bucket, payload)
        corrupt = b"deliberately damaged archive " + uuid4().hex.encode()
        versioned_store.put_object(Bucket=bucket, Key=key, Body=corrupt)
        before = _versions(versioned_store, bucket, key)
        objects = Objects()
        outcome = _outcome(
            lambda: objects.get(digest) if operation == "get" else objects.put(payload)
        )
        after = _versions(versioned_store, bucket, key)
        actual = _body(versioned_store, bucket, key)
        archive_evidence.update(
            expected_hash=digest,
            actual_stored_hash=hashlib.sha256(actual).hexdigest(),
            outcome=outcome,
            versions_before=before,
            versions_after=after,
        )
        assert outcome == {"error": "archive_corrupt"}
        assert actual == corrupt
        assert after == before


class TestArchiveEndpointIsolation:
    @pytest.mark.parametrize("state", ["missing", "corrupt"])
    @pytest.mark.parametrize("operation", ["get", "put"])
    def test_warm_other_endpoint_cannot_conceal_missing_or_corrupt_local_object(
        self,
        wlh_env: dict[str, str],
        versioned_store: Any,
        second_store: tuple[str, Any],
        monkeypatch: pytest.MonkeyPatch,
        archive_evidence: dict[str, Any],
        state: str,
        operation: str,
    ) -> None:
        payload = b"same bucket and hash across distinct storage endpoints " + uuid4().hex.encode()
        digest = hashlib.sha256(payload).hexdigest()
        first = Objects()
        assert first.put(payload) == digest
        assert first.get(digest) == payload
        bucket = wlh_env["WLH_S3_BUCKET"]
        key = _stored_key(versioned_store, bucket, payload)
        endpoint, client = second_store
        corrupt = b"unrelated corrupt bytes on second endpoint " + uuid4().hex.encode()
        if state == "corrupt":
            client.put_object(Bucket=bucket, Key=key, Body=corrupt)
        before = _versions(client, bucket, key)
        assert len(before) == (1 if state == "corrupt" else 0)
        monkeypatch.setenv("WLH_S3_ENDPOINT", endpoint)
        second = Objects()
        outcome = _outcome(
            lambda: second.get(digest) if operation == "get" else second.put(payload)
        )
        after = _versions(client, bucket, key)
        actual = _body(client, bucket, key) if after else None
        archive_evidence.update(
            distinct_endpoints=True,
            same_bucket=True,
            expected_hash=digest,
            state=state,
            operation=operation,
            outcome=outcome,
            versions_before=before,
            versions_after=after,
            actual_stored_hash=hashlib.sha256(actual).hexdigest() if actual is not None else None,
        )
        if state == "missing" and operation == "put":
            assert outcome == {"returned_hash": digest}
            assert actual == payload, "A successful put must write to its own endpoint"
            assert len(after) == 1
        else:
            assert outcome == {
                "error": "archive_missing" if state == "missing" else "archive_corrupt"
            }
            assert after == before
            assert actual == (corrupt if state == "corrupt" else None)
        assert _body(versioned_store, bucket, key) == payload
