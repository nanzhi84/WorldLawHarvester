"""Bounded operator configuration; UUIDs and duplicate identities fail closed."""

from __future__ import annotations

import json
from collections.abc import Sequence
from os import PathLike
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from wlh.core import WlhError

MAX_BYTES = 2 * 1024 * 1024
MAX_OVERRIDES = 10000


def uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise WlhError("invalid_version_overrides")
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise WlhError("invalid_version_overrides") from None
    return value


def normalize(overrides: Any) -> list[dict[str, str]]:
    if not isinstance(overrides, list) or len(cast(list[Any], overrides)) > MAX_OVERRIDES:
        raise WlhError("invalid_version_overrides")
    result: dict[str, str] = {}
    for item in cast(list[Any], overrides):
        if not isinstance(item, dict) or set(cast(dict[str, Any], item)) != {"resource_id", "version_id"}:
            raise WlhError("invalid_version_overrides")
        resource_id, version_id = uuid(item["resource_id"]), uuid(item["version_id"])
        if resource_id in result:
            raise WlhError("duplicate_override_resource")
        result[resource_id] = version_id
    return [{"resource_id": r, "version_id": v} for r, v in sorted(result.items())]


def unique_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, value in pairs:
        if key in data:
            raise WlhError("duplicate_override_key")
        data[key] = value
    return data


def load(path: str | PathLike[str]) -> list[dict[str, str]]:
    try:
        with Path(path).open("rb") as file:
            data = file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise WlhError("version_overrides_too_large")
        value = json.loads(data, object_pairs_hook=unique_pairs)
        if not isinstance(value, dict) or set(cast(dict[str, Any], value)) != {"overrides"}:
            raise WlhError("invalid_version_overrides")
        return normalize(value["overrides"])
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise WlhError("invalid_version_overrides") from None
