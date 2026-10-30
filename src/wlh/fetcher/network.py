"""Public-address DNS boundary shared by collection and source discovery."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from socket import AddressFamily
from typing import Any, cast

import aiohttp
from aiohttp.abc import ResolveResult

from wlh.core import check_address

FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


class Resolver(aiohttp.resolver.ThreadedResolver):
    def __init__(
        self,
        *args: Any,
        trusted_proxy_hosts: Iterable[str] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.trusted_proxy_hosts: set[str] = {
            h.strip().lower().rstrip(".") for h in trusted_proxy_hosts if h and h.strip()
        }

    async def resolve(self, host: str, port: int = 0, family: int = 0) -> list[ResolveResult]:
        norm_host = host.strip().lower().rstrip(".")
        is_trusted = norm_host in self.trusted_proxy_hosts
        if not is_trusted:
            check_address(host)
        try:
            ipaddress.ip_address(host)
            hostname = False
        except ValueError:
            hostname = True
        addresses = await super().resolve(host, port, cast(AddressFamily, family))
        if not is_trusted:
            for address in addresses:
                addr = ipaddress.ip_address(address["host"])
                if hostname and addr in FAKE_IP_NETWORK:
                    continue
                check_address(address["host"])
        return addresses
