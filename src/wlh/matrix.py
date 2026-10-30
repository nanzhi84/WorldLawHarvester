"""One-way synchronization and file watcher: source-adaptation-matrix.csv -> source-adaptation-matrix.md."""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import os
import re
import select
from datetime import datetime
from pathlib import Path

DEFAULT_CSV_PATH = Path("docs/runbooks/source-adaptation-matrix.csv")
DEFAULT_MD_PATH = Path("docs/runbooks/source-adaptation-matrix.md")

TABLE_HEADER = (
    "| Source\u00a0ID | \u2060\u00a0\u00a0资\u2060料\u2060类\u2060型\u00a0\u00a0\u2060 "
    "| 来\u2060源\u2060名\u2060称\u00a0\u00a0\u00a0\u00a0\u00a0\u00a0\u00a0\u00a0\u2060 "
    "| \u2060\u00a0优\u2060先\u2060级\u00a0\u2060 "
    "| 推\u2060荐\u2060适\u2060配\u2060器\u00a0\u00a0\u00a0\u00a0\u00a0\u00a0\u2060 "
    "| \u2060\u00a0状\u2060态\u00a0\u2060 "
    "| 备\u2060注\u2060与\u2060实\u2060测\u2060结\u2060论 |"
)
TABLE_DIVIDER = "| :--- | :---: | :--- | :---: | :--- | :---: | :--- |"

KNOWN_ADAPTERS = (
    "html",
    "json",
    "xml",
    "juriscraper_bridge",
    "opencaselaw_bridge",
    "worldwidelaw_bridge",
    "openstates_bridge",
    "federal_register",
    "structured_xml",
    "oldp_bridge",
    "web",
)


def _strip_md_adapter_to_csv(md_adapter: str) -> str:
    """Convert MD adapter cell back to CSV plain representation for comparison."""
    s = re.sub(
        r"\[`(dd-\d+\.yaml)`\]\(\.\./\.\./sources/\1\)",
        r"sources/\1",
        md_adapter,
    )
    return s.replace("`", "").strip()


def _strip_md_note_to_csv(md_note: str) -> str:
    """Convert MD note cell back to CSV plain representation for comparison."""
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", md_note)
    s = (
        s.replace("**", "")
        .replace("`", "")
        .replace("&amp;", "&")
        .replace("&gt;", ">")
        .replace("&lt;", "<")
    )
    return s.strip()


def _extract_entry_urls(raw_url: str) -> list[str]:
    """Split multi-line or delimiter-separated entry URLs."""
    raw = (
        raw_url.replace("<br>", "\n")
        .replace("、", "\n")
        .replace(",", "\n")
        .replace("，", "\n")
    )
    urls: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        for part in line.split():
            part = part.strip().rstrip(",")
            if part.startswith(("http://", "https://")):
                urls.append(part)
    return urls


def _derive_entry_label(base_name: str, url: str, index: int) -> str:
    """Generate meaningful entry sub-labels for multiple entry points."""
    clean_base = re.sub(r"（(?:法规检索|条约协定|入口\s*\d+)）$", "", base_name).strip()
    if "LawSearchLaw" in url:
        return f"{clean_base}（法规检索）"
    if "LawSearchAgree" in url:
        return f"{clean_base}（条约协定）"
    return f"{clean_base}（入口 {index}）"


def _strip_md_name_url(md_cell: str) -> tuple[str, str]:
    """Extract (source_name, url) from MD cell `[Name](URL)` or multiple links."""
    matches = re.findall(r"\[(.*?)\]\((https?://[^)]+)\)", md_cell.strip())
    if len(matches) == 1:
        name = (
            matches[0][0]
            .replace("&amp;", "&")
            .replace("&gt;", ">")
            .replace("&lt;", "<")
            .strip()
        )
        return name, matches[0][1].strip()
    if len(matches) > 1:
        urls = [m[1].strip() for m in matches]
        base_names: list[str] = []
        for m in matches:
            raw_n = (
                m[0]
                .replace("&amp;", "&")
                .replace("&gt;", ">")
                .replace("&lt;", "<")
                .strip()
            )
            base_n = re.sub(r"（(?:法规检索|条约协定|入口\s*\d+)）$", "", raw_n).strip()
            base_names.append(base_n)
        common_name = (
            base_names[0]
            if all(b == base_names[0] for b in base_names)
            else "<br>".join(m[0] for m in matches)
        )
        return common_name, "\n".join(urls)
    name = (
        md_cell.replace("&amp;", "&")
        .replace("&gt;", ">")
        .replace("&lt;", "<")
        .strip()
    )
    return name, ""


def _parse_existing_md_rows(md_text: str) -> dict[str, list[str]]:
    """Parse existing MD rows keyed by `dd-xxx` to preserve inline rich formatting when unchanged."""
    existing: dict[str, list[str]] = {}
    for line in md_text.splitlines():
        if line.startswith('| <a id="dd-'):
            parts = [p.strip() for p in line.strip().strip("|").split("|")]
            if len(parts) >= 7:
                m = re.search(r"(dd-\d+)", parts[0])
                if m:
                    existing[m.group(1)] = parts[:7]
    return existing


def _format_adapter_md(csv_adapter: str, existing_md_cell: str | None = None) -> str:
    val = csv_adapter.strip()
    if existing_md_cell and _strip_md_adapter_to_csv(existing_md_cell) == val:
        return existing_md_cell
    s = re.sub(
        r"(?:sources/)?(dd-\d+\.yaml)",
        r"[`\1`](../../sources/\1)",
        val,
    )
    for token in KNOWN_ADAPTERS:
        s = re.sub(rf"(?<!`)\b({re.escape(token)})\b(?!`)", r"`\1`", s)
    return s


def _format_status_md(csv_status: str) -> str:
    val = csv_status.strip().lower()
    if val.startswith("[x]") or val in {"x", "已完成", "已适配", "已实网跑通", "true"}:
        return "[x]"
    if val.startswith("[~]") or val in {"~", "[-]", "部分跑通", "已部分跑通", "partial"}:
        return "[~]"
    return "[ ]"


def _format_name_url_md(
    name: str, url: str, existing_md_cell: str | None = None
) -> str:
    clean_name = name.strip()
    urls = _extract_entry_urls(url)
    if not urls:
        return clean_name
    if existing_md_cell:
        ex_name, ex_url = _strip_md_name_url(existing_md_cell)
        if ex_name == clean_name and _extract_entry_urls(ex_url) == urls:
            return existing_md_cell
    if len(urls) == 1:
        return f"[{clean_name}]({urls[0]})"
    links = [
        f"[{_derive_entry_label(clean_name, u, idx)}]({u})"
        for idx, u in enumerate(urls, 1)
    ]
    return "<br>".join(links)


def _format_note_md(csv_note: str, existing_md_cell: str | None = None) -> str:
    val = csv_note.strip()
    if existing_md_cell and _strip_md_note_to_csv(existing_md_cell) == val:
        return existing_md_cell
    s = re.sub(r"([^\s(]+)\s+\((https?://[^)]+)\)", r"[\1](\2)", val)
    s = re.sub(
        r"^(实网已跑通[^：:]*|部分跑通[^：:]*|适配器契约已验证[^：:]*|实测受阻[^：:]*)([：:])",
        r"**\1**\2",
        s,
    )
    return s


def _format_subcat_heading(subcat: str, count: int) -> str:
    s = subcat.strip()
    s = re.sub(r"\((dd-\d+)–(dd-\d+)\)", r"(`\1`–`\2`)", s)
    s = re.sub(r"（共\s*\d+\s*项）", f"（共 {count} 项）", s)
    return f"#### {s}"


def render_markdown_from_csv(csv_path: Path, md_path: Path) -> str:
    """Render `source-adaptation-matrix.md` content from `source-adaptation-matrix.csv`."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    existing_md_text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    existing_rows = _parse_existing_md_rows(existing_md_text)

    if "## 1. " in existing_md_text:
        preamble = existing_md_text.split("## 1. ")[0].rstrip()
    else:
        preamble = (
            "# 多法域资源适配与推进台账 (Source Adaptation Matrix)\n\n"
            "> **唯一事实源声明（Single Source of Truth）**：本文件与同目录下的扁平化宽表 "
            "[`source-adaptation-matrix.csv`](source-adaptation-matrix.csv) 自动联动同步；"
            "各已跑通（`[x]`）及部分跑通（`[~]`）信源的双栏核验样本见 "
            "[`source-verification-samples.md`](source-verification-samples.md)。\n\n---"
        )

    # Count items per (continent, jurisdiction, subcat)
    subcat_counts: dict[tuple[str, str, str], int] = {}
    for r in rows:
        key = (
            r["大洲/区域"].strip(),
            r["法域/国家"].strip(),
            r["业务子分类"].strip(),
        )
        subcat_counts[key] = subcat_counts.get(key, 0) + 1

    out_lines: list[str] = [preamble, ""]
    current_continent = None
    current_jurisdiction = None
    current_subcat = None
    in_table = False

    for r in rows:
        sid = r["Source ID"].strip()
        if not sid:
            continue
        continent = r["大洲/区域"].strip()
        jurisdiction = r["法域/国家"].strip()
        subcat = r["业务子分类"].strip()

        if continent != current_continent:
            if current_continent is not None:
                out_lines.append("")
                out_lines.append("---")
                out_lines.append("")
            out_lines.append(f"## {continent}")
            out_lines.append("")
            current_continent = continent
            current_jurisdiction = None
            current_subcat = None
            in_table = False

        if jurisdiction != current_jurisdiction:
            if in_table:
                out_lines.append("")
            out_lines.append(f"### {jurisdiction}")
            out_lines.append("")
            current_jurisdiction = jurisdiction
            current_subcat = None
            in_table = False

        if subcat != current_subcat:
            if in_table:
                out_lines.append("")
            if subcat and subcat != "—":
                cnt = subcat_counts[(continent, jurisdiction, subcat)]
                out_lines.append(_format_subcat_heading(subcat, cnt))
                out_lines.append("")
            out_lines.append(TABLE_HEADER)
            out_lines.append(TABLE_DIVIDER)
            current_subcat = subcat
            in_table = True

        ex = existing_rows.get(sid)
        col_id = f'<a id="{sid}"></a>{sid}'
        col_type = r["资料类型"].strip()
        col_name_url = _format_name_url_md(
            r["来源名称"], r["官方入口 URL"], ex[2] if ex else None
        )
        col_prio = r["优先级"].strip()
        col_adapter = _format_adapter_md(
            r["推荐适配器"], ex[4] if ex else None
        )
        col_status = _format_status_md(r["状态"])
        col_note = _format_note_md(
            r["备注与实测结论"], ex[6] if ex else None
        )

        out_lines.append(
            f"| {col_id} | {col_type} | {col_name_url} | {col_prio} | {col_adapter} | {col_status} | {col_note} |"
        )

    return "\n".join(out_lines) + "\n"


def sync_csv_to_md(
    csv_path: Path = DEFAULT_CSV_PATH, md_path: Path = DEFAULT_MD_PATH
) -> bool:
    """Synchronize CSV to MD if resulting content differs. Returns True if MD was updated."""
    lock_path = md_path.with_suffix(".md.lock")
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            rendered = render_markdown_from_csv(csv_path, md_path)
            current = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
            if rendered == current:
                return False
            tmp_path = md_path.with_suffix(f".md.{os.getpid()}.tmp")
            tmp_path.write_text(rendered, encoding="utf-8")
            tmp_path.replace(md_path)
            return True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass


def _file_digest(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def watch_csv(
    csv_path: Path = DEFAULT_CSV_PATH,
    md_path: Path = DEFAULT_MD_PATH,
) -> None:
    """Wait on OS kernel VFS events (kqueue EVFILT_VNODE) for CSV modifications without polling."""
    csv_path = csv_path.resolve()
    md_path = md_path.resolve()
    parent_dir = csv_path.parent
    last_digest = _file_digest(csv_path)
    html_path = md_path.parent / "source-verification-samples.html"
    if last_digest:
        changed = sync_csv_to_md(csv_path, md_path)
        if html_path.is_file():
            from wlh.runbook_samples import sync_csv_to_html

            sync_csv_to_html(csv_path, html_path)
        ts = datetime.now().strftime("%H:%M:%S")
        print(
            f"[{ts}] [matrix-hook] Initial sync check complete (updated={changed}). Waiting for kernel VFS events on {csv_path.name}",
            flush=True,
        )

    kq = select.kqueue()
    dir_fd = os.open(str(parent_dir), os.O_RDONLY)
    file_fd: int | None = None

    def _register_fds() -> list[select.kevent]:
        nonlocal file_fd
        events = [
            select.kevent(
                dir_fd,
                filter=select.KQ_FILTER_VNODE,
                flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR,
                fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_RENAME | select.KQ_NOTE_EXTEND,
            )
        ]
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
            file_fd = None
        if csv_path.exists():
            file_fd = os.open(str(csv_path), os.O_RDONLY)
            events.append(
                select.kevent(
                    file_fd,
                    filter=select.KQ_FILTER_VNODE,
                    flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR,
                    fflags=(
                        select.KQ_NOTE_WRITE
                        | select.KQ_NOTE_EXTEND
                        | select.KQ_NOTE_ATTRIB
                        | select.KQ_NOTE_RENAME
                        | select.KQ_NOTE_DELETE
                    ),
                )
            )
        return events

    try:
        reg_events = _register_fds()
        while True:
            # Blocks in kernel until VFS event fires (0% CPU, no timer polling)
            fired = kq.control(reg_events, 1, None)
            reg_events = []
            for ev in fired:
                if ev.fflags & (select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME) or ev.ident == dir_fd:
                    reg_events = _register_fds()
            digest = _file_digest(csv_path)
            if digest and digest != last_digest:
                last_digest = digest
                changed = sync_csv_to_md(csv_path, md_path)
                if html_path.is_file():
                    from wlh.runbook_samples import sync_csv_to_html

                    sync_csv_to_html(csv_path, html_path)
                ts = datetime.now().strftime("%H:%M:%S")
                print(
                    f"[{ts}] [matrix-hook] CSV modification event -> synced {md_path.name} (updated={changed})",
                    flush=True,
                )
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
        os.close(dir_fd)
        kq.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Sync docs/runbooks/source-adaptation-matrix.csv -> source-adaptation-matrix.md"
    )
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH, help="Path to source CSV")
    ap.add_argument("--md", type=Path, default=DEFAULT_MD_PATH, help="Path to target Markdown")
    ap.add_argument(
        "--watch",
        action="store_true",
        help="Block on OS kernel file-change events (kqueue) and sync on modification",
    )
    args = ap.parse_args(argv)

    if args.watch:
        try:
            watch_csv(args.csv, args.md)
        except KeyboardInterrupt:
            return 0
        return 0

    changed = sync_csv_to_md(args.csv, args.md)
    print(f"synced {args.csv} -> {args.md} (updated={changed})")
    html_target = args.md.resolve().parent / "source-verification-samples.html"
    if html_target.is_file():
        from wlh.runbook_samples import sync_csv_to_html

        html_changed = sync_csv_to_html(args.csv, html_target)
        print(f"synced {args.csv} -> {html_target} (updated={html_changed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
