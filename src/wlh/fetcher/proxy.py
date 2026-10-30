"""Ephemeral per-run proxy lifecycle manager (Stage 2 Fetcher)."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import re
import shlex
import shutil
import socket
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, unquote, urlsplit

import aiohttp

from wlh.core import WlhError


def _free_loopback_port() -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = int(s.getsockname()[1])
        if port != 7890:
            return port


def parse_vless_uri(uri: str) -> dict[str, Any]:
    """Parse a ``vless://uuid@host:port?params#remark`` URI into an Xray outbound dict."""
    parts = urlsplit(uri.strip())
    if (
        parts.scheme.lower() != "vless"
        or not parts.hostname
        or not parts.port
        or not parts.username
    ):
        raise WlhError("proxy_uri_invalid")
    params = {k: v[0] for k, v in parse_qs(parts.query).items() if v}
    remark = unquote(parts.fragment or "")
    security = params.get("security", "none")
    network = params.get("type", "tcp")
    flow = params.get("flow", "")
    stream_settings: dict[str, Any] = {
        "network": network,
        "security": security,
    }
    if security == "reality":
        stream_settings["realitySettings"] = {
            "serverName": params.get("sni", ""),
            "fingerprint": params.get("fp", "chrome"),
            "publicKey": params.get("pbk", ""),
            "shortId": params.get("sid", ""),
            "spiderX": params.get("spx", "/"),
        }
    elif security == "tls":
        stream_settings["tlsSettings"] = {
            "serverName": params.get("sni", parts.hostname),
            "fingerprint": params.get("fp", "chrome"),
        }
    user_entry: dict[str, Any] = {
        "id": unquote(parts.username),
        "encryption": params.get("encryption", "none"),
    }
    if flow:
        user_entry["flow"] = flow
    return {
        "remark": remark,
        "outbound": {
            "protocol": "vless",
            "settings": {
                "vnext": [
                    {
                        "address": parts.hostname,
                        "port": parts.port,
                        "users": [user_entry],
                    }
                ]
            },
            "streamSettings": stream_settings,
        },
    }


def select_vless_node(raw_text: str, region_regex: str) -> dict[str, Any]:
    """Decode base64 or plain subscription text and select the first node matching ``region_regex``."""
    text = raw_text.strip()
    if "vless://" not in text:
        padded = text + "=" * ((4 - len(text) % 4) % 4)
        try:
            text = base64.b64decode(padded).decode("utf-8", errors="replace")
        except Exception:
            try:
                text = base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
            except Exception as exc:
                raise WlhError("proxy_subscription_invalid") from exc
    pattern = re.compile(region_regex, re.IGNORECASE)
    candidates: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("vless://"):
            continue
        parsed = parse_vless_uri(line)
        candidates.append(parsed)
        if pattern.search(parsed["remark"]) or pattern.search(line):
            return parsed
    if candidates and region_regex in (".*", ""):
        return candidates[0]
    raise WlhError("proxy_region_node_not_found")


class EphemeralProxy:
    """Async context manager that spawns an isolated local proxy subprocess per run."""

    def __init__(
        self,
        proxy_cfg: Mapping[str, Any] | None = None,
        *,
        policy: str = "direct",
        profile: str | None = None,
    ) -> None:
        if isinstance(proxy_cfg, Mapping):
            self.proxy_cfg: dict[str, Any] = dict(proxy_cfg)
            self.policy: str = str(proxy_cfg.get("policy") or policy or "direct")
            self.profile: str | None = (
                str(proxy_cfg["profile"]) if proxy_cfg.get("profile") else profile
            )
        else:
            self.proxy_cfg = {}
            self.policy = policy
            self.profile = profile
        self.process: asyncio.subprocess.Process | None = None
        self.temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self.proxy_url: str | None = None
        self.trusted_hosts: set[str] = set()

    @property
    def url(self) -> str | None:
        return self.proxy_url

    @url.setter
    def url(self, value: str | None) -> None:
        self.proxy_url = value

    def _resolve_command_template(self) -> str | None:
        if self.profile:
            prof_key = f"WLH_PROXY_CMD_{self.profile.upper().replace('-', '_')}"
            if os.environ.get(prof_key, "").strip():
                return os.environ[prof_key].strip()
        if os.environ.get("WLH_PROXY_CMD_TEMPLATE", "").strip():
            return os.environ["WLH_PROXY_CMD_TEMPLATE"].strip()
        return None

    def _resolve_static_url(self) -> str | None:
        if self.profile:
            prof_key = f"WLH_PROXY_{self.profile.upper().replace('-', '_')}"
            val = os.environ.get(prof_key, "").strip()
            if val and not val.startswith("vless://"):
                return val
        val = os.environ.get("WLH_PROXY_URL", "").strip()
        if val and not val.startswith("vless://"):
            return val
        return None

    async def _wait_for_port(self, port: int, timeout_sec: float) -> str:
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while asyncio.get_running_loop().time() < deadline:
            if self.process is not None and self.process.returncode is not None:
                await self.close()
                raise WlhError("ephemeral_proxy_startup_failed")
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                self.proxy_url = f"http://127.0.0.1:{port}"
                self.trusted_hosts.add("127.0.0.1")
                return self.proxy_url
            except OSError:
                await asyncio.sleep(0.05)
        await self.close()
        raise WlhError("ephemeral_proxy_startup_failed")

    async def start(self) -> str | None:
        if (
            self.policy == "direct"
            and not self.proxy_cfg.get("subscription_env")
            and not self.proxy_cfg.get("fallback_uri_env")
        ):
            return None

        timeout_sec = float(self.proxy_cfg.get("startup_timeout_seconds") or 15)

        static_url = self._resolve_static_url()
        if static_url:
            parts = urlsplit(static_url)
            if parts.hostname:
                self.trusted_hosts.add(parts.hostname)
            self.proxy_url = static_url
            return self.proxy_url

        cmd_template = self._resolve_command_template()
        if cmd_template:
            last_err: WlhError | None = None
            for _ in range(3):
                port = _free_loopback_port()
                rendered_cmd = cmd_template.format(
                    port=port, profile=self.profile or "default"
                )
                argv = shlex.split(rendered_cmd)
                self.process = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    return await self._wait_for_port(port, timeout_sec)
                except WlhError as exc:
                    last_err = exc
            raise last_err or WlhError("ephemeral_proxy_startup_failed")

        sub_env = str(
            self.proxy_cfg.get("subscription_env") or "WLH_PROXY_SUBSCRIPTION_URL"
        )
        fallback_env = str(self.proxy_cfg.get("fallback_uri_env") or "WLH_PROXY_URI")
        region_regex = str(self.proxy_cfg.get("region_regex") or self.profile or ".*")

        raw_source = os.environ.get(fallback_env, "").strip()
        if not raw_source and self.profile:
            prof_uri_key = f"WLH_PROXY_{self.profile.upper().replace('-', '_')}"
            candidate_uri = os.environ.get(prof_uri_key, "").strip()
            if candidate_uri.startswith("vless://"):
                raw_source = candidate_uri
        sub_url = os.environ.get(sub_env, "").strip()
        if not raw_source and sub_url:
            if sub_url.startswith("vless://"):
                raw_source = sub_url
            else:
                try:
                    async with aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=timeout_sec)
                    ) as sess:
                        async with sess.get(sub_url) as resp:
                            if resp.status != 200:
                                raise WlhError("proxy_subscription_unavailable")
                            raw_source = await resp.text()
                except WlhError:
                    raise
                except Exception as exc:
                    raise WlhError("proxy_subscription_unavailable") from exc

        if not raw_source:
            if self.policy == "auto":
                return None
            raise WlhError("proxy_required_but_unconfigured")

        node = select_vless_node(raw_source, region_regex)
        xray_bin = shutil.which(os.environ.get("WLH_XRAY_BIN", "xray"))
        if not xray_bin:
            if self.policy == "auto":
                return None
            raise WlhError("proxy_binary_unavailable")

        last_err = None
        for _ in range(3):
            port = _free_loopback_port()
            self.temp_dir = tempfile.TemporaryDirectory(prefix="wlh-xray-")
            cfg_path = Path(self.temp_dir.name) / "config.json"
            xray_config = {
                "log": {"loglevel": "none"},
                "inbounds": [
                    {
                        "listen": "127.0.0.1",
                        "port": port,
                        "protocol": "http",
                        "settings": {"allowTransparent": False},
                    }
                ],
                "outbounds": [cast(dict[str, Any], node["outbound"])],
            }
            cfg_path.write_text(json.dumps(xray_config), encoding="utf-8")
            self.process = await asyncio.create_subprocess_exec(
                xray_bin,
                "run",
                "-c",
                str(cfg_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                return await self._wait_for_port(port, timeout_sec)
            except WlhError as exc:
                last_err = exc
        raise last_err or WlhError("ephemeral_proxy_startup_failed")

    async def close(self) -> None:
        if self.process is not None:
            if self.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=3.0)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        self.process.kill()
                    with contextlib.suppress(Exception):
                        await self.process.wait()
            self.process = None
        if self.temp_dir is not None:
            with contextlib.suppress(Exception):
                self.temp_dir.cleanup()
            self.temp_dir = None
        self.proxy_url = None
        self.trusted_hosts.clear()

    async def __aenter__(self) -> str | None:
        return await self.start()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()


__all__ = [
    "EphemeralProxy",
    "parse_vless_uri",
    "select_vless_node",
]
