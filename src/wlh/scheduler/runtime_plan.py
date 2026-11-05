"""Frozen CAS input lineage and recovery targets for offline reparse."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from wlh.core import WlhError, request_key
from wlh.storage.cas import Objects

if TYPE_CHECKING:
    from wlh.scheduler.runtime import Runtime


def frozen_targets(runtime: Runtime) -> list[dict[str, Any]]:
    """Offline reparse: each current Version's archived adapter inputs, verified in CAS."""
    objects = Objects()
    targets: list[dict[str, Any]] = []
    plan: Mapping[str, Any] = runtime.run.get("reparse_plan") or {}
    resources: list[dict[str, Any]] = plan.get("resources", [])
    seen_identities: set[tuple[str, str]] = set()
    for resource in resources:
        manifest = objects.json(resource["inputs_ref"])
        for entry in manifest.get("inputs", []):
            cache_key: str | None = cast(dict[str, Any], entry.get("context") or {}).get(
                "ocr_cache_key"
            )
            if entry.get("role") == "derived" and cache_key:
                runtime.ocr_cache[cache_key] = entry["archive_ref"]
        frozen = manifest.get("adapter_inputs")
        if manifest.get("schema") != "wlh.inputs.v1" or not isinstance(frozen, list) or not frozen:
            raise WlhError("reparse_inputs_unavailable")
        seen_identities.add((str(resource["namespace"]), str(resource["external_key"])))
        targets.append(
            {
                "identity": {
                    "namespace": resource["namespace"],
                    "external_key": resource["external_key"],
                    "public_url": resource["public_url"],
                    "recheck_locator": resource["recheck_locator"],
                },
                "inputs": [],
                "frozen": {
                    "inputs": frozen,
                    "inputs_ref": resource["inputs_ref"],
                    "version_id": resource["version_id"],
                },
            }
        )
    for rt in cast(list[dict[str, Any]], plan.get("recovery_targets") or []):
        ident = cast(Mapping[str, Any], rt.get("identity") or {})
        pub_url = str(ident.get("public_url") or "")
        if (
            pub_url
            and hasattr(runtime.adapter, "is_detail")
            and not cast(Any, runtime.adapter).is_detail(pub_url)
        ):
            continue
        pair = (str(ident.get("namespace") or ""), str(ident.get("external_key") or ""))
        if pair in seen_identities:
            continue
        seen_identities.add(pair)
        targets.append(
            {
                "identity": rt.get("identity"),
                "context": rt.get("context") or {},
                "inputs": [],
                "frozen": {
                    "inputs": rt["inputs"],
                    "recovery": True,
                },
            }
        )
    recovery_requests = cast(list[dict[str, Any]], plan.get("recovery_requests") or [])
    if recovery_requests:
        req_aids = [
            str(r["acquisition_id"])
            for r in recovery_requests
            if r.get("acquisition_id") and str(r["acquisition_id"]) not in runtime.acquisition_cache
        ]
        if req_aids:
            for acq in runtime.rows(
                "SELECT * FROM wlh.acquisition WHERE id = ANY(%s)",
                (list(set(req_aids)),),
            ):
                if acq.get("state") == "archived":
                    runtime.acquisition_cache[str(acq["id"])] = acq
        att_acq_rows = runtime.rows(
            """SELECT DISTINCT ON (request_key) id, request_key, request_url
            FROM wlh.acquisition
            WHERE source_id = %s AND state = 'archived' AND outcome = 'ok'
            ORDER BY request_key, acquired_at DESC""",
            (runtime.run["source_id"],),
        )
        acq_by_rkey: dict[str, str] = {
            str(r["request_key"]): str(r["id"]) for r in att_acq_rows if r.get("request_key")
        }
        acq_by_url: dict[str, str] = {
            str(r["request_url"]): str(r["id"]) for r in att_acq_rows if r.get("request_url")
        }
        for req_row in recovery_requests:
            runtime.budget.active()
            acq_obj = runtime.acquisition(req_row.get("acquisition_id"))
            if not acq_obj or acq_obj.get("state") != "archived" or acq_obj.get("outcome") != "ok":
                continue
            try:
                parsed = runtime.adapter.parse(runtime.ctx, req_row, acq_obj)
            except (WlhError, Exception):
                continue
            for t in parsed.get("targets", []):
                ident = cast(Mapping[str, Any], t.get("identity") or {})
                pair = (
                    str(ident.get("namespace") or ""),
                    str(ident.get("external_key") or ""),
                )
                if pair in seen_identities:
                    continue
                frozen_inputs: list[dict[str, Any]] = []
                missing_dep = False
                for idx, item in enumerate(t.get("inputs", [])):
                    rkey = request_key(item["request"])
                    if idx == 0:
                        dep_aid: str | None = str(req_row["acquisition_id"])
                    else:
                        dep_aid = acq_by_rkey.get(rkey) or acq_by_url.get(
                            str(item["request"].get("url") or "")
                        )
                    if not dep_aid:
                        missing_dep = True
                        break
                    frozen_inputs.append(
                        {
                            **{k: v for k, v in item.items() if k != "request"},
                            "request_key": rkey,
                            "acquisition_id": dep_aid,
                        }
                    )
                if missing_dep or not frozen_inputs:
                    continue
                seen_identities.add(pair)
                targets.append(
                    {
                        "identity": t.get("identity"),
                        "context": t.get("context") or {},
                        "inputs": [],
                        "frozen": {
                            "inputs": frozen_inputs,
                            "recovery": True,
                        },
                    }
                )
    return targets
