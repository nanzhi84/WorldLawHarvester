"""CAS fixture bundle exporter and manifest-driven local replay router (wlh.fixture.v1).

Replaces manual `_EXACT_ROUTES` / `_PREFIX_ROUTES` tables and hand-crafted HTML
stubs with self-describing per-source `manifest.json` bundles exported from
verified PostgreSQL `wlh.acquisition` records and S3 CAS raw payloads.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from wlh.core import WlhError, atomic_write
from wlh.db import connect
from wlh.engine.objects import Objects

FIXTURE_SCHEMA = "wlh.fixture.v1"

D1N_COOKIE_CHALLENGE = (
    b'<html><body><script>document.cookie="D1N=ok123"'
    b'+"; expires=Thu, 31-Dec-37 23:55:55 GMT; path=/";'
    b"window.location.reload(true);</script></body></html>"
)


def _is_textual_mime(mime_type: str) -> bool:
    low = mime_type.lower()
    return (
        low.startswith("text/")
        or "html" in low
        or "xml" in low
        or "json" in low
        or "javascript" in low
    )


def _ext_for_mime(mime_type: str, path: str) -> str:
    low = mime_type.lower()
    if "html" in low:
        return ".html"
    if "xml" in low:
        return ".xml"
    if "json" in low:
        return ".json"
    if "pdf" in low:
        return ".pdf"
    if "zip" in low:
        return ".zip"
    if "plain" in low:
        return ".txt"
    for ext in (".html", ".xml", ".json", ".pdf", ".zip", ".txt", ".htm", ".shtml", ".aspx"):
        if path.lower().endswith(ext):
            return ".html" if ext in {".htm", ".shtml", ".aspx"} else ext
    return ".bin"


def _slug_filename(index: int, route_path: str, mime_type: str) -> str:
    parts = urlsplit(route_path)
    raw_name = Path(parts.path.rstrip("/") or "index").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_name).strip("-.") or "page"
    ext = _ext_for_mime(mime_type, parts.path)
    if stem.lower().endswith(ext):
        stem = stem[: -len(ext)]
    if parts.query:
        q_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", parts.query).strip("-")[:24]
        if q_slug:
            stem = f"{stem}-{q_slug}"
    return f"{index:02d}-{stem[:48]}{ext}"


def _templatize_text(
    text: str,
    primary_netlocs: list[str],
    secondary_netlocs: list[str],
) -> tuple[str, bool]:
    out = text
    changed = False
    for sec in sorted(set(secondary_netlocs), key=len, reverse=True):
        if not sec:
            continue
        for prefix in (f"https://{sec}", f"http://{sec}"):
            if prefix in out:
                out = out.replace(prefix, "{secondary_base}")
                changed = True
        if sec in out:
            out = out.replace(sec, "{sec_host}")
            changed = True

    for pri in sorted(set(primary_netlocs), key=len, reverse=True):
        if not pri:
            continue
        for prefix in (f"https://{pri}", f"http://{pri}"):
            if prefix in out:
                out = out.replace(prefix, "http://{host}")
                changed = True
        if pri in out:
            out = out.replace(pri, "{host}")
            changed = True

    return out, changed


def export_source_fixture(
    source_id: str,
    output_dir: str | os.PathLike[str],
    *,
    run_id: str | None = None,
    max_resources: int = 2,
    source_yaml: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Export verified `wlh.acquisition` records and S3 CAS bytes into a `wlh.fixture.v1` bundle."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    objects = Objects()

    with connect() as c:
        if run_id is not None:
            run_row = c.execute(
                "SELECT id, source_id, config FROM wlh.run WHERE id = %s", (run_id,)
            ).fetchone()
        else:
            run_row = c.execute(
                """
                SELECT id, source_id, config
                FROM wlh.run
                WHERE source_id = %s AND mode = 'collect' AND status = 'complete'
                ORDER BY completed_at DESC NULLS LAST, created_at DESC
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()
            if run_row is None:
                run_row = c.execute(
                    """
                    SELECT id, source_id, config
                    FROM wlh.run
                    WHERE source_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (source_id,),
                ).fetchone()
        if run_row is None:
            raise WlhError("fixture_export_run_not_found")

        resolved_run_id: str = run_row["id"]
        cfg: dict[str, Any] = dict(cast(Mapping[str, Any], run_row["config"] or {}))
        if source_yaml is not None and Path(source_yaml).is_file():
            import yaml

            loaded = yaml.safe_load(Path(source_yaml).read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cfg = dict(cast(Mapping[str, Any], loaded))

        obs_rows = c.execute(
            """
            SELECT id, resource_id
            FROM wlh.observation
            WHERE run_id = %s AND outcome <> 'failed'
            ORDER BY observed_at
            LIMIT %s
            """,
            (resolved_run_id, max_resources),
        ).fetchall()
        selected_obs_ids = {r["id"] for r in obs_rows}

        all_ev_rows = c.execute(
            """
            SELECT oe.observation_id, oe.acquisition_id
            FROM wlh.observation_evidence oe
            JOIN wlh.observation o ON o.id = oe.observation_id
            WHERE o.run_id = %s
            """,
            (resolved_run_id,),
        ).fetchall()

        acqs = c.execute(
            """
            SELECT id, role, acquisition_kind, http_method, request_url, actual_url,
                   parent_acquisition_id, mime_type, charset, archive_ref, http_status
            FROM wlh.acquisition
            WHERE run_id = %s AND state = 'archived'
            ORDER BY started_at
            """,
            (resolved_run_id,),
        ).fetchall()

    if not acqs:
        raise WlhError("fixture_export_empty_acquisitions")

    selected_acq_ids: set[str] = set()
    unselected_acq_ids: set[str] = set()
    for ev in all_ev_rows:
        if ev["observation_id"] in selected_obs_ids:
            selected_acq_ids.add(ev["acquisition_id"])
        else:
            unselected_acq_ids.add(ev["acquisition_id"])

    by_id = {a["id"]: a for a in acqs}
    # Walk parent chain for selected acquisitions
    frontier_ids = list(selected_acq_ids)
    while frontier_ids:
        cid = frontier_ids.pop()
        acq_obj = by_id.get(cid)
        if acq_obj and acq_obj.get("parent_acquisition_id"):
            pid = acq_obj["parent_acquisition_id"]
            if pid not in selected_acq_ids:
                selected_acq_ids.add(pid)
                frontier_ids.append(pid)

    allowed_hosts: list[str] = [str(h) for h in cast(list[Any], cfg.get("allowed_hosts") or [])]
    primary_host = allowed_hosts[0] if allowed_hosts else "127.0.0.1"
    secondary_hosts = allowed_hosts[1:] if len(allowed_hosts) > 1 else []

    primary_netlocs: list[str] = [primary_host]
    secondary_netlocs: list[str] = list(secondary_hosts)

    for entry_url in cast(list[Any], cfg.get("entry_urls") or []):
        ep = urlsplit(str(entry_url))
        if ep.netloc and ep.netloc not in primary_netlocs:
            primary_netlocs.append(ep.netloc)

    for a in acqs:
        for u in (a.get("request_url"), a.get("actual_url")):
            if not u:
                continue
            up = urlsplit(str(u))
            if not up.netloc:
                continue
            if up.hostname in secondary_hosts or up.netloc in secondary_netlocs:
                if up.netloc not in secondary_netlocs:
                    secondary_netlocs.append(up.netloc)
            elif up.netloc not in primary_netlocs:
                primary_netlocs.append(up.netloc)

    routes: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    file_index = 1

    for a in acqs:
        aid = a["id"]
        # Include if it belongs to selected observations OR is shared/entry/list/policy (not exclusive to unselected observations)
        if selected_obs_ids and (aid in unselected_acq_ids and aid not in selected_acq_ids):
            continue

        eff_url = str(a.get("actual_url") or a.get("request_url") or "/")
        parts = urlsplit(eff_url)
        route_path = parts.path or "/"
        if parts.query:
            route_path = f"{route_path}?{parts.query}"

        if route_path == "/robots.txt" or route_path in seen_paths:
            continue
        seen_paths.add(route_path)

        raw_bytes = objects.get(str(a["archive_ref"]))
        mime_type = str(a.get("mime_type") or "text/html").split(";")[0].strip()
        charset = str(a.get("charset") or "utf-8").strip() or "utf-8"
        status_code = int(a.get("http_status") or 200)

        filename = _slug_filename(file_index, route_path, mime_type)
        file_index += 1

        if _is_textual_mime(mime_type):
            try:
                decoded = raw_bytes.decode(charset)
            except (UnicodeDecodeError, LookupError):
                decoded = raw_bytes.decode("utf-8", errors="replace")
                charset = "utf-8"

            templated, is_template = _templatize_text(
                decoded,
                primary_netlocs=primary_netlocs,
                secondary_netlocs=secondary_netlocs,
            )
            atomic_write(out_path / filename, templated.encode("utf-8"))
            content_type = (
                f"{mime_type}; charset={charset}"
                if "charset" not in mime_type.lower()
                else mime_type
            )
            routes.append(
                {
                    "match": "exact",
                    "path": route_path,
                    "fixture": filename,
                    "content_type": content_type,
                    "encoding": charset,
                    "status": status_code,
                    "template": is_template,
                    "requires_d1n_cookie": False,
                }
            )
        else:
            atomic_write(out_path / filename, raw_bytes)
            routes.append(
                {
                    "match": "exact",
                    "path": route_path,
                    "fixture": filename,
                    "binary": True,
                    "content_type": mime_type,
                    "status": status_code,
                }
            )

    manifest_doc: dict[str, Any] = {
        "schema": FIXTURE_SCHEMA,
        "source_id": source_id,
        "run_id": resolved_run_id,
        "routes": routes,
    }
    atomic_write(
        out_path / "manifest.json",
        (json.dumps(manifest_doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )
    return {
        "schema": FIXTURE_SCHEMA,
        "source_id": source_id,
        "run_id": resolved_run_id,
        "output_dir": str(out_path),
        "route_count": len(routes),
    }


def load_fixture_manifest(fixtures_root: str | os.PathLike[str], source_id: str) -> dict[str, Any]:
    """Load and validate `<fixtures_root>/<source_id>/manifest.json`."""
    manifest_path = Path(fixtures_root) / source_id / "manifest.json"
    if not manifest_path.is_file():
        raise WlhError("fixture_manifest_missing")
    try:
        raw_data: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise WlhError("invalid_fixture_manifest") from None
    if not isinstance(raw_data, dict):
        raise WlhError("invalid_fixture_manifest")
    data = cast(dict[str, Any], raw_data)
    if (
        data.get("schema") != FIXTURE_SCHEMA
        or data.get("source_id") != source_id
        or not isinstance(data.get("routes"), list)
    ):
        raise WlhError("invalid_fixture_manifest")
    return data


class FixtureRouter:
    """Manifest-driven HTTP route resolver for local black-box replay servers."""

    def __init__(
        self,
        fixtures_root: str | os.PathLike[str],
        *,
        binary_providers: Mapping[str, bytes] | None = None,
    ) -> None:
        self.fixtures_root = Path(fixtures_root)
        self.binary_providers: dict[str, bytes] = dict(binary_providers or {})
        self._manifests: dict[str, dict[str, Any]] = {}

    def _get_manifest(self, source_id: str) -> dict[str, Any] | None:
        if not source_id:
            return None
        if source_id in self._manifests:
            return self._manifests[source_id]
        manifest_path = self.fixtures_root / source_id / "manifest.json"
        if not manifest_path.is_file():
            return None
        manifest = load_fixture_manifest(self.fixtures_root, source_id)
        self._manifests[source_id] = manifest
        return manifest

    def _match_in_manifest(
        self, manifest: Mapping[str, Any], path: str
    ) -> tuple[str, Mapping[str, Any]] | None:
        source_id = str(manifest["source_id"])
        routes: list[Mapping[str, Any]] = manifest.get("routes") or []
        for route in routes:
            if route.get("match", "exact") == "exact" and route.get("path") == path:
                return source_id, route
        for route in routes:
            if route.get("match") == "prefix" and path.startswith(str(route.get("path") or "")):
                return source_id, route
        return None

    def _resolve_route(self, source_id: str, path: str) -> tuple[str, Mapping[str, Any]] | None:
        manifest = self._get_manifest(source_id)
        if manifest is not None:
            matched = self._match_in_manifest(manifest, path)
            if matched is not None:
                return matched
            return None

        # Fallback for ephemeral synthetic source IDs without a dedicated directory (e.g. dd-998)
        if self.fixtures_root.is_dir():
            for child in sorted(self.fixtures_root.iterdir()):
                if child.is_dir() and (child / "manifest.json").is_file():
                    child_manifest = self._get_manifest(child.name)
                    if child_manifest is not None:
                        matched = self._match_in_manifest(child_manifest, path)
                        if matched is not None:
                            return matched
        return None

    def resolve_and_render(
        self,
        source_id: str,
        path: str,
        headers: Mapping[str, str] | None = None,
        secondary_netloc: str = "",
    ) -> tuple[int, str, bytes] | None:
        """Resolve `(source_id, path)` from `manifest.json` and render `(status, content_type, body)`."""
        req_headers = {k.lower(): v for k, v in (headers or {}).items()}
        if path == "/robots.txt":
            resolved = self._resolve_route(source_id, path)
            if resolved is None:
                return 200, "text/plain; charset=utf-8", b"User-agent: *\nAllow: /\n"

        resolved = self._resolve_route(source_id, path)
        if resolved is None:
            return None

        owner_source_id, spec = resolved
        if spec.get("requires_d1n_cookie") and "D1N=" not in req_headers.get("cookie", ""):
            return 200, "text/html; charset=utf-8", D1N_COOKIE_CHALLENGE

        status = int(spec.get("status", 200))
        content_type = str(spec.get("content_type") or "text/html; charset=utf-8")

        if "raw_utf8" in spec and spec["raw_utf8"] is not None:
            return status, content_type, str(spec["raw_utf8"]).encode("utf-8")

        binary_attr = spec.get("binary_attr")
        if binary_attr is not None:
            payload = self.binary_providers.get(str(binary_attr)) or self.binary_providers.get(
                "sample_pdf", b""
            )
            return status, content_type, payload

        fixture_rel = str(spec.get("fixture") or "")
        fixture_path = self.fixtures_root / owner_source_id / fixture_rel
        if not fixture_path.is_file():
            fixture_path = self.fixtures_root / fixture_rel
        if not fixture_path.is_file():
            return None

        if spec.get("binary") is True:
            return status, content_type, fixture_path.read_bytes()

        text = fixture_path.read_text(encoding="utf-8")
        if spec.get("template"):
            host = req_headers.get("host", "127.0.0.1")
            sec_host = secondary_netloc or host
            secondary_base = f"http://{secondary_netloc}" if secondary_netloc else ""
            text = (
                text.replace("{host}", host)
                .replace("{sec_host}", sec_host)
                .replace("{secondary_base}", secondary_base)
            )
        encoding = str(spec.get("encoding") or "utf-8")
        return status, content_type, text.encode(encoding)
