"""Identity of the driver and headless browser WLH actually launches."""

from __future__ import annotations

import copy
import hashlib
import os
import re
import stat
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

from wlh.core import digest

DRIVER = "playwright"
BROWSER = "chromium-headless-shell"
INSTALL_MARKERS = {"INSTALLATION_COMPLETE", "DEPENDENCIES_VALIDATED"}

_IDENTITY_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_BUNDLE_DIGEST_CACHE: dict[tuple[str, int, int], str] = {}


def clear_identity_cache() -> None:
    """Clear the in-process browser runtime identity cache."""
    _IDENTITY_CACHE.clear()
    _BUNDLE_DIGEST_CACHE.clear()


def _find_binary_signature(root: Path) -> tuple[str, int, int] | None:
    for candidate in root.rglob("*"):
        if (
            candidate.name in {"chrome-headless-shell", "chrome-headless-shell.exe"}
            and candidate.is_file()
        ):
            if not os.access(candidate, os.X_OK):
                return None
            st = candidate.stat()
            return (str(candidate), st.st_size, st.st_mtime_ns)
    return None


def bundle_identity(root: Path, deadline: float) -> str:
    sig = _find_binary_signature(root)
    if sig is not None and sig in _BUNDLE_DIGEST_CACHE:
        return _BUNDLE_DIGEST_CACHE[sig]
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
    computed = digest(entries)
    if sig is not None:
        _BUNDLE_DIGEST_CACHE[sig] = computed
    return computed


def identity() -> dict[str, Any]:
    for (bin_path, expected_size, expected_mtime), cached_id in list(_IDENTITY_CACHE.items()):
        try:
            st = Path(bin_path).stat()
            if (
                stat.S_ISREG(st.st_mode)
                and (st.st_size, st.st_mtime_ns) == (expected_size, expected_mtime)
                and os.access(bin_path, os.X_OK)
            ):
                return copy.deepcopy(cached_id)
        except OSError:
            pass
        clear_identity_cache()
        break

    driver = {"package": DRIVER}
    try:
        driver["version"] = version(DRIVER)
        deadline = time.monotonic() + 30
        result: subprocess.CompletedProcess[bytes] = subprocess.run(
            [sys.executable, "-m", DRIVER, "install", "--dry-run", BROWSER],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
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
        resolved: dict[str, Any] = {
            "status": "available",
            "driver": driver,
            "browser": BROWSER,
            "revision": match[2],
            "browser_version": match[1],
            "bundle_sha256": bundle_identity(root, deadline),
        }
        sig = _find_binary_signature(root)
        if sig is not None:
            _IDENTITY_CACHE.clear()
            _IDENTITY_CACHE[sig] = copy.deepcopy(resolved)
        return resolved
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"status": "unavailable", "driver": driver, "error": "browser_runtime_unavailable"}
