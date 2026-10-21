"""Freeze due Resources (stable identity, display URL, recheck locator, current Version)."""


def plan_recheck(c, source, config):
    rows = c.execute(
        """SELECT r.id,r.identity_namespace AS namespace,r.external_key,r.public_url,
        r.recheck_locator,r.current_version_id FROM wlh.resource r
        JOIN wlh.frontier f ON f.id=r.frontier_id
        WHERE f.source_id=%s ORDER BY r.next_check,r.id LIMIT %s""",
        (source["id"], config["budget"]["documents"] + 1),
    ).fetchall()
    selected = rows[: config["budget"]["documents"]]
    return {
        "selection": "known_resources",
        "resource_count": len(selected),
        "truncated": len(rows) > len(selected),
        "resources": selected,
    }
