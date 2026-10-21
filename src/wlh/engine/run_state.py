"""Execution states and stable error codes shared by lifecycle boundaries."""

from datetime import datetime, timezone

TERMINAL_STATES = {"complete", "failed"}


def exhaustion_error(run):
    if run["deadline"] <= datetime.now(timezone.utc):
        return "deadline_exceeded"
    if run["attempt_epoch"] >= run["max_attempts"]:
        return "attempts_exhausted"
    return None


def eligibility_error(source, run):
    if source["generation"] != run["generation"]:
        return "source_generation_changed"
    if source["paused"]:
        return "source_paused"
    if source["write_seq"] > run["run_seq"]:
        return "run_superseded"
    return None
