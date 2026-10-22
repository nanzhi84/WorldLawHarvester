"""Public-address DNS boundary shared by collection and source discovery."""

from __future__ import annotations

import ipaddress
from socket import AddressFamily
from typing import cast

import aiohttp
from aiohttp.abc import ResolveResult

from wlh.core import check_address

FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


class Resolver(aiohttp.resolver.ThreadedResolver):
    async def resolve(self, host: str, port: int = 0, family: int = 0) -> list[ResolveResult]:
        check_address(host)
        try:
            ipaddress.ip_address(host)
            hostname = False
        except ValueError:
            hostname = True
        addresses = await super().resolve(host, port, cast(AddressFamily, family))
        for address in addresses:
            addr = ipaddress.ip_address(address["host"])
            # TUN Fake-IP DNS routes hostnames through the local tunnel. Literal
            # addresses and every other non-public DNS result keep the strict boundary.
            if hostname and addr in FAKE_IP_NETWORK:
                continue
            check_address(address["host"])
        return addresses
