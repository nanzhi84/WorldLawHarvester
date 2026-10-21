"""Read-only run outcomes, request counts, failure causes and coverage conclusions."""

from wlh.db import connect, one


def failure_category(error):
    """Separately reported causes: never folded into one generic failure count."""
    code = error.removeprefix("required_input_failed:")
    if code in {"interrupted", "attempt_superseded"}:
        return "cancelled"
    if code in {
        "request_budget_exceeded",
        "crawl_time_budget_exceeded",
        "document_budget_exceeded",
    }:
        return "budget_truncated"
    if error.startswith("required_input_failed:"):
        return "required_input_missing"
    if code in {"network_failed", "redirect_limit_exceeded"} or code.startswith(
        ("robots", "http_")
    ):
        return "network_failed"
    if code.startswith("quality_check_failed"):
        return "quality_check_failed"
    if code in {
        "document_original_missing",
        "document_body_missing",
        "document_deliverables_required",
        "document_missing_from_batch",
        "reparse_input_unavailable",
    }:
        return "required_input_missing"
    if code in {"inputs_unavailable", "page_limit_exceeded"}:
        return "enumeration_incomplete"
    return "parse_failed"


def build_report(run_id, adapter, checkpoint, truncated, finish_reason):
    with connect() as c:
        tasks = c.execute(
            """SELECT kind,state,count(*) AS n FROM wlh.task WHERE run_id=%s
            GROUP BY kind,state""",
            (run_id,),
        ).fetchall()
        outcomes = c.execute(
            "SELECT outcome,count(*) AS n FROM wlh.observation WHERE run_id=%s GROUP BY outcome",
            (run_id,),
        ).fetchall()
        requests = one(
            c,
            "SELECT count(*) AS n FROM wlh.acquisition WHERE run_id=%s AND acquisition_kind='http'",
            (run_id,),
        )["n"]
    counts = {"request": {}, "target": {}}
    for row in tasks:
        counts[row["kind"]][row["state"]] = row["n"]
    with connect() as c:
        errors = c.execute(
            """SELECT kind,error,count(*) AS n FROM wlh.task WHERE run_id=%s
            AND error IS NOT NULL GROUP BY kind,error""",
            (run_id,),
        ).fetchall()
    failures = {}
    for row in errors:
        category = failure_category(row["error"])
        failures[category] = failures.get(category, 0) + row["n"]
    if finish_reason == "interrupted":
        failures["cancelled"] = max(1, failures.get("cancelled", 0))
    if truncated:
        category = failure_category(truncated)
        failures[category] = max(1, failures.get(category, 0))
    pending = counts["request"].get("pending", 0)
    complete = (
        finish_reason == "finished"
        and not truncated
        and not pending
        and not counts["request"].get("failed", 0)
    )
    return {
        "adapter": adapter,
        "finish_reason": finish_reason,
        "external_requests": requests,
        "request_tasks": counts["request"],
        "targets": counts["target"],
        "observations": {r["outcome"]: r["n"] for r in outcomes},
        "truncated": truncated,
        "failures": dict(sorted(failures.items())),
        "coverage": {
            "selected_scope_enumeration": "complete" if complete else "incomplete",
            "site_population": "UNKNOWN",
            "unknown_coverage": True,
            "known_gaps": sorted(
                {value for value in (truncated, checkpoint.get("truncated")) if value}
            ),
            "absence_semantics": "unobserved_is_unknown_never_deleted",
        },
    }
