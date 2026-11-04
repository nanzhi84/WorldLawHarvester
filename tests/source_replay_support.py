"""HTTP/CLI acceptance plumbing; never imports a source parser or extraction implementation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import unicodedata
from collections.abc import Callable, Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import psycopg
import pytest
import yaml
from lxml import html as html_library  # type: ignore[import-untyped]
from markdown_it import MarkdownIt
from psycopg.rows import dict_row

from .conftest import run_wlh_cli

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_ARTIFACTS = Path(tempfile.gettempdir()) / "wlh_test_artifacts" / "us-jp"
ARTIFACTS = Path(os.environ.get("WLH_TEST_ARTIFACTS_DIR", str(DEFAULT_TEST_ARTIFACTS)))
Route = tuple[bytes, str]

KFS_FIXTURES = ROOT / "tests" / "fixtures" / "dd-582" / "content"
KFS_OUTPUT = ARTIFACTS / "kfs-content-contract" / os.environ.get("WLH_KFS_ACCEPTANCE_STAGE", "latest")
_HREF = re.compile(r"(<a\b[^>]*?\bhref\s*=\s*)([\"'])(.*?)(\2)", re.I | re.S)
_BODY_CONTROL = "本文中の引用語句「トップに戻る」は原文の内容として保持する。"


def recorded_redirect(source_id: str, path: str, primary_host: str) -> tuple[int, str] | None:
    """Replay observed cross-origin HTTP redirects without changing source host scopes."""
    fixture = ROOT / "tests" / "fixtures" / source_id / "redirects.json"
    if not fixture.exists():
        return None
    spec = json.loads(fixture.read_text()).get(path)
    if spec is None:
        return None
    return int(spec["status"]), str(spec["location"]).replace("{primary_host}", primary_host)


def normalized(text: str) -> str:
    """Ignore presentation whitespace/punctuation, while preserving every letter and digit."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"</?(?:ruby|rt|rp|b|strong|em|i|u|span|sup|sub|del|ins)(?:\s[^>]*)?>", "", text)
    return "".join(c for c in unicodedata.normalize("NFKC", text) if c.isalnum())


def rendered_table_rows(markdown: str, cell_text: Callable[[str], str]) -> list[list[str]]:
    """Observe rendered table cells; native spans occupy their declared coordinates."""
    parser: Any = html_library
    rendered = MarkdownIt("commonmark", {"html": True}).enable("table").render(markdown)
    document = parser.fragment_fromstring(rendered, create_parent="div")
    output: list[list[str]] = []
    for table in document.xpath(".//table"):
        grid: dict[tuple[int, int], str] = {}
        rows = table.xpath("./tr | ./thead/tr | ./tbody/tr | ./tfoot/tr")
        for row_index, row in enumerate(rows):
            column = 0
            for cell in row.xpath("./th | ./td"):
                while (row_index, column) in grid:
                    column += 1
                width, height = int(cell.get("colspan", "1")), int(cell.get("rowspan", "1"))
                value = cell_text(parser.tostring(cell, encoding="unicode", method="html"))
                for dy in range(height):
                    for dx in range(width):
                        grid[row_index + dy, column + dx] = value if dx == dy == 0 else ""
                column += width
        width = max((column for _, column in grid), default=-1) + 1
        output.extend([[grid.get((row, column), "") for column in range(width)]
                       for row in range(len(rows))])
    return output


def fixture_routes(source_id: str) -> dict[str, Route]:
    from wlh.fixtures import FixtureRouter

    return FixtureRouter(ROOT / "tests" / "fixtures").load_route_map(source_id)


def recorded_scope_routes(source_id: str) -> dict[str, Route]:
    directory = ROOT / "tests" / "fixtures" / source_id
    manifest = json.loads((directory / "scope-manifest.json").read_text())
    routes: dict[str, Route] = {}
    for route in manifest["routes"]:
        response = ((directory / route["fixture"]).read_bytes(), route["content_type"])
        routes[route["path"]] = response
        route_path: str = route["path"]
        parts = urlsplit(route_path)
        parameters = parse_qs(parts.query)
        if "page" in parameters:
            routes[f"{parts.path}?page={parameters['page'][0]}"] = response
    return routes


@contextmanager
def source_server(routes: dict[str, Route], hosts: list[str]) -> Generator[tuple[str, list[str]]]:
    from wlh.fixtures import FixtureRouter

    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            requests.append(self.path)
            origin = f"http://127.0.0.1:{self.server.server_port}"  # type: ignore[attr-defined]
            resolved = FixtureRouter.resolve_from_route_map(routes, self.path, origin, hosts)
            if resolved is None:
                self.send_response(404)
                self.end_headers()
                return
            status, media, payload = resolved
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def replay_config(
    source_id: str, origin: str, directory: Path, *, template: Path | None = None
) -> Path:
    source_file = template or ROOT / "sources" / f"{source_id}.yaml"
    assert source_file.is_file(), f"Required source config {source_file} not found"
    cfg: dict[str, Any] = yaml.safe_load(source_file.read_text())
    sched = cfg["scheduler"]
    hosts: list[str] = sched["allowed_hosts"]
    destination = urlsplit(origin)
    entry_urls: list[str] = sched["entry_urls"]
    sched["entry_urls"] = [
        urlunsplit((destination.scheme, destination.netloc, p.path, p.query, ""))
        for p in (urlsplit(url) for url in entry_urls)
    ]

    def localize(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: localize(v) for k, v in cast(dict[str, Any], value).items()}
        if isinstance(value, list):
            return [localize(v) for v in cast(list[Any], value)]
        if isinstance(value, str):
            for host in hosts:
                value = value.replace(f"https://{host}", origin)
                value = value.replace(f"http://{host}", origin)
                value = value.replace(re.escape(host), re.escape(destination.netloc))
            return re.sub(r"^\^https?://", "^http://", value)
        return value

    parser_cfg = cfg.get("processor", {}).get("parser", {})
    for section in ("documents", "options"):
        if section in parser_cfg:
            parser_cfg[section] = localize(parser_cfg[section])
    sched["allowed_hosts"] = ["127.0.0.1"]
    if "host_path_scopes" in sched:
        sched["host_path_scopes"] = {
            "127.0.0.1": sorted({p for ps in sched["host_path_scopes"].values() for p in ps})
        }
    cfg.setdefault("fetcher", {})["delay"] = 0
    path = directory / f"{source_id}.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    return path


def collect_artifact(
    source_id: str,
    config: Path,
    env: dict[str, str],
    name: str,
    requests: list[str],
    output_dir: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from wlh.storage.cas import Objects

    base_dir = output_dir or ARTIFACTS
    directory = base_dir / name
    directory.mkdir(parents=True, exist_ok=True)
    code, stdout, stderr = run_wlh_cli("collect", str(config), "--no-progress", env=env)
    report: dict[str, Any] = {"exit_code": code, "stdout": stdout, "stderr": stderr}
    try:
        report["result"] = json.loads(stdout)
    except json.JSONDecodeError:
        report["result"] = {}
    with psycopg.connect(env["WLH_DATABASE_URL"]) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            rows: list[dict[str, Any]] = cursor.execute(
                "SELECT r.id AS resource_id, d.* FROM wlh.document_row d JOIN wlh.resource r "
                "ON r.current_version_id=d.version_id JOIN wlh.frontier f "
                "ON f.id=r.frontier_id WHERE f.source_id=%s "
                "ORDER BY d.detail_url, d.row_index", (source_id,)
            ).fetchall()
    objects = Objects()
    for index, row in enumerate(rows, start=1):
        row["markdown"] = objects.get(row["markdown_hash"]).decode("utf-8")
        (directory / f"{index}.md").write_text(row["markdown"])
    (directory / "collect.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (directory / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    (directory / "requests.json").write_text(json.dumps(requests, indent=2))
    return report, rows


def assert_offline_reparse(
    config: Path, env: dict[str, str], name: str, output_dir: Path | None = None
) -> None:
    code, stdout, stderr = run_wlh_cli("reparse", str(config), "--no-progress", env=env)
    base_dir = output_dir or ARTIFACTS
    target_dir = base_dir / name
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "reparse.json").write_text(stdout or stderr)
    assert code == 0, (stdout, stderr)
    result = json.loads(stdout)
    assert result["status"] == "complete", result
    assert result["report"]["external_requests"] == 0


def compute_test_signature(env: dict[str, str], source_id: str = "dd-582") -> list[list[str]]:
    with psycopg.connect(env["WLH_DATABASE_URL"]) as conn:
        rows = conn.execute(
            "SELECT d.detail_url,d.role,d.title,d.markdown_hash FROM wlh.document_row d "
            "JOIN wlh.resource r ON r.current_version_id=d.version_id "
            "JOIN wlh.frontier f ON f.id=r.frontier_id WHERE f.source_id=%s "
            "ORDER BY d.detail_url,d.row_index",
            (source_id,),
        ).fetchall()
    return [[str(value) for value in row] for row in rows]


def kfs_case_routes(
    case: str,
    name: str,
    *,
    body_control: bool = False,
    output_dir: Path | None = None,
) -> dict[str, Route]:
    if not (KFS_FIXTURES / "manifest.json").is_file():
        pytest.skip(f"Private fixture directory {KFS_FIXTURES} not present")
    manifest: dict[str, Any] = json.loads((KFS_FIXTURES / "manifest.json").read_text())
    base = f"/service/JP/{case}/"
    catalog = {
        "/service/index.html",
        "/service/JP/index.html",
        f"/service/JP/idx/{case.split('/')[0]}.html",
    }
    selected = [r for r in manifest["routes"] if r["path"] in catalog or r["path"].startswith(base)]
    allowed = {r["path"] for r in selected}
    routes: dict[str, Route] = {}
    evidence: list[dict[str, Any]] = []
    parser: Any = html_library
    for spec in selected:
        payload = (KFS_FIXTURES / spec["fixture"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == spec["sha256"]
        changes: list[str] = []
        if "text/html" in spec["content_type"]:
            text = payload.decode(spec["encoding"])
            original = parser.fromstring(text)
            protected: set[str] = set()
            if spec["role"] == "original_full_document":
                protected = set(
                    original.xpath('//*[@id="main"]//a[not(ancestor::*[@id="pankuzu"])]/@href')
                )

            def restrict(match: re.Match[str]) -> str:
                href = match.group(3)
                path = urlsplit(urljoin(spec["source_url"], href)).path
                if href.startswith("#") or href in protected or path in allowed:
                    return match.group(0)
                changes.append(href)
                return match.group(1) + match.group(2) + "#unselected" + match.group(4)

            text = _HREF.sub(restrict, text)
            if body_control and spec["path"] == base + "bessi.html":
                marker = '<p class="pagetop"><a href="#header">'
                assert text.count(marker) == 1
                text = text.replace(marker, f"<p>{_BODY_CONTROL}</p>\n" + marker)
            payload = text.encode(spec["encoding"])
        routes[spec["path"]] = (payload, spec["content_type"])
        evidence.append(
            {
                "path": spec["path"],
                "source_sha256": spec["sha256"],
                "replay_sha256_before_origin_rewrite": hashlib.sha256(payload).hexdigest(),
                "unselected_navigation_hrefs": changes,
            }
        )
    base_out = output_dir or KFS_OUTPUT
    directory = base_out / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "scope.json").write_text(
        json.dumps(
            {
                "scope": "One complete original parent and all required annexes via full recorded directories",
                "source_config": "sources/dd-582.yaml; original extraction rules unchanged",
                "live_census_claimed": False,
                "synthetic_body_control": body_control,
                "routes": evidence,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return routes


def create_test_run(
    case: str,
    name: str,
    env: dict[str, str],
    tmp_path: Path,
    *,
    body_control: bool = False,
    output_dir: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, bool]]:
    base_out = output_dir or KFS_OUTPUT
    target_dir = base_out / name
    target_dir.mkdir(parents=True, exist_ok=True)
    routes = kfs_case_routes(case, name, body_control=body_control, output_dir=base_out)
    with source_server(routes, ["www.kfs.go.jp"]) as (origin, requests):
        config = replay_config("dd-582", origin, tmp_path)
        cfg: dict[str, Any] = yaml.safe_load(config.read_text())
        cfg["scheduler"]["budget"] = {
            "requests": 40,
            "documents": 10,
            "seconds": 90,
            "response_bytes": 10485760,
        }
        config.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
        report, rows = collect_artifact(
            "dd-582", config, env, name, requests, output_dir=base_out
        )
        required = {path for path in routes if path.endswith(".html")}
        transport_ok = required <= set(requests) and not (set(requests) - set(routes) - {"/robots.txt"})
    before = compute_test_signature(env)
    code, stdout, stderr = run_wlh_cli("reparse", str(config), "--no-progress", env=env)
    offline: dict[str, Any] = {"exit_code": code, "stderr": stderr}
    try:
        offline_result: dict[str, Any] = json.loads(stdout)
    except json.JSONDecodeError:
        offline_result = {}
        offline["stdout"] = stdout
    offline["result"] = offline_result
    after = compute_test_signature(env)
    (target_dir / "reparse.json").write_text(json.dumps(offline, ensure_ascii=False, indent=2))
    report["offline"] = offline
    offline_report: dict[str, Any] = offline_result.get("report", {})
    common: dict[str, bool] = {
        "all_full_parent_and_annex_responses_fetched_without_missing_routes": transport_ok,
        "offline_reparse_external_requests_zero": offline_report.get("external_requests") == 0,
        "offline_reparse_preserves_visible_current_delivery": before == after,
    }
    return report, rows, common


def create_test_receipt(
    name: str,
    checks: dict[str, bool],
    output_dir: Path | None = None,
    **details: Any,
) -> None:
    base_out = output_dir or KFS_OUTPUT
    target_dir = base_out / name
    target_dir.mkdir(parents=True, exist_ok=True)
    result = {"checks": checks, "passed": all(checks.values()), **details}
    (target_dir / "assertions.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str)
    )
    assert result["passed"], {"failed": [k for k, v in checks.items() if not v], **details}


# Backward-compatible aliases
_signature = compute_test_signature
_run = create_test_run
_receipt = create_test_receipt
_routes = kfs_case_routes

