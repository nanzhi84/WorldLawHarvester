"""Execution states and stable error codes shared by lifecycle boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

TERMINAL_STATES = {"complete", "failed"}


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
