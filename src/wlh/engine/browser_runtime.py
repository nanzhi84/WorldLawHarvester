"""Identity of the driver and headless browser WLH actually launches."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wlh.core import digest

if TYPE_CHECKING:
    pass

DRIVER = "playwright"
BROWSER = "chromium-headless-shell"
INSTALL_MARKERS = {"INSTALLATION_COMPLETE", "DEPENDENCIES_VALIDATED"}


def bundle_identity(root: Path, deadline: float) -> str:
    entries: list[dict[str, Any]] = []
    executable = False
    paths: list[Path] = []
    for path in root.rglob("*"):
        if time.monotonic() >= deadline:
            raise TimeoutError
        if len(paths) >= 10000:
            raise ValueError
        paths.append(path)
    paths = sorted(path for path in paths if path.is_file())
    if not paths:
        raise ValueError
    for path in paths:
        if path.name in INSTALL_MARKERS:
            continue
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError
        content = hashlib.sha256()
        with path.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                if time.monotonic() >= deadline:
                    raise TimeoutError
                content.update(chunk)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            raise ValueError
        if path.name in {"chrome-headless-shell", "chrome-headless-shell.exe"}:
            if not os.access(path, os.X_OK):
                raise ValueError
            executable = True
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": content.hexdigest(),
                "executable": bool(before.st_mode & 0o111),
            }
        )
    if not executable:
        raise ValueError
    return digest(entries)


def identity() -> dict[str, Any]:
    driver = {"package": DRIVER}
    try:
        driver["version"] = version(DRIVER)
        deadline = time.monotonic() + 10
        # The driver's public dry-run resolves its own registry, host platform,
        # PLAYWRIGHT_BROWSERS_PATH and per-package installs. No browser is started.
        result: subprocess.CompletedProcess[bytes] = subprocess.run(
            [sys.executable, "-m", DRIVER, "install", "--dry-run", BROWSER],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=True,
        )
        if len(result.stdout) > 65536:
            raise ValueError
        match: re.Match[str] | None = re.search(
            r"Chrome Headless Shell ([^\n]+?) \(playwright chromium-headless-shell v([^\n)]+)\)"
            r"\s+Install location:\s+([^\n]+)",
            result.stdout.decode("utf-8"),
        )
        if match is None:
            raise ValueError
        root = Path(match[3].strip()).resolve(strict=True)
        return {
            "status": "available",
            "driver": driver,
            "browser": BROWSER,
            "revision": match[2],
            "browser_version": match[1],
            "bundle_sha256": bundle_identity(root, deadline),
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        # Never preserve command output, environment variables or local paths.
        return {"status": "unavailable", "driver": driver, "error": "browser_runtime_unavailable"}
