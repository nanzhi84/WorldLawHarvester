"""Unified read boundary: a fixed Snapshot's rows and downloadable file closure.

The API reads fixed rows and authorized file references through this boundary.
"""

ROW_QUERY = """SELECT i.source_id,i.resource_id,d.*
FROM wlh.snapshot_item i
JOIN wlh.document_row d ON d.version_id=i.version_id
WHERE i.snapshot_id=%(snapshot)s AND i.source_id = ANY(%(sources)s)
AND (i.source_id COLLATE "C", i.resource_id COLLATE "C", d.row_index)
    > (%(source_after)s COLLATE "C", %(resource_after)s COLLATE "C", %(row_after)s)
ORDER BY i.source_id COLLATE "C", i.resource_id COLLATE "C", d.row_index
LIMIT %(limit)s"""


def snapshot_rows(c, snapshot_id, sources, after=("", "", 0), limit=1000):
    """Stable row order: source_id, resource_id, row_index (C collation)."""
    return c.execute(
        ROW_QUERY,
        {
            "snapshot": snapshot_id,
            "sources": list(sources),
            "source_after": after[0],
            "resource_after": after[1],
            "row_after": after[2],
            "limit": limit,
        },
    ).fetchall()


def file_entry(c, snapshot_id, sha256, sources):
    """A downloadable file of the published Snapshot within the caller's Sources."""
    return c.execute(
        """SELECT sha256,kind,media_type,file_name,byte_size,source_id FROM wlh.snapshot_file
        WHERE snapshot_id=%s AND sha256=%s AND source_id = ANY(%s)
        ORDER BY kind DESC, source_id COLLATE "C" LIMIT 1""",
        (snapshot_id, sha256, list(sources)),
    ).fetchone()
