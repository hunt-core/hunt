from __future__ import annotations

import ipaddress
import os
from typing import ClassVar

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import Response

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _is_private(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        return False


def _parse_networks(raw: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    nets = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            pass
    return nets


def _ip_in_networks(ip: str, networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in net for net in networks)
    except ValueError:
        return False


class TrustProxies(Middleware):
    """Rewrite request IP and scheme from forwarded headers for trusted proxies.

    Configure via the TRUSTED_PROXIES env var (comma-separated IPs/CIDRs):
        TRUSTED_PROXIES=10.0.0.0/8,172.16.0.0/12

    Set TRUSTED_PROXIES=* to trust all proxies (development only — never in production).

    Or subclass and set the ``proxies`` class attribute:

        class MyTrustProxies(TrustProxies):
            proxies = ["10.0.0.0/8", "172.16.0.0/12"]
    """

    proxies: ClassVar[list[str]] = []

    async def handle(self, request: Request, next: Next) -> Response:
        raw = ", ".join(self.proxies) if self.proxies else os.environ.get("TRUSTED_PROXIES", "")
        if not raw:
            return await next(request)

        trust_all = raw.strip() in ("*", "**")
        networks = [] if trust_all else _parse_networks(raw)

        client_ip = request._scope.get("client", ("127.0.0.1", 0))[0]
        is_trusted = trust_all or _ip_in_networks(client_ip, networks)

        if is_trusted:
            self._apply_forwarded_ip(request, trust_all, networks)
            self._apply_forwarded_scheme(request)

        return await next(request)

    @staticmethod
    def _apply_forwarded_ip(
        request: Request,
        trust_all: bool,
        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
    ) -> None:
        xff = request.header("x-forwarded-for", "")
        if not xff:
            return
        ips = [ip.strip() for ip in xff.split(",")]
        # Walk right-to-left; the real client IP is the leftmost one not from a trusted proxy
        real_ip = ips[0]
        for ip in reversed(ips):
            if trust_all or not _ip_in_networks(ip, networks):
                real_ip = ip
                break
        client = request._scope.get("client", ("127.0.0.1", 0))
        request._scope["client"] = (real_ip, client[1] if client else 0)

    @staticmethod
    def _apply_forwarded_scheme(request: Request) -> None:
        proto = request.header("x-forwarded-proto", "")
        if proto in ("http", "https"):
            request._scope["scheme"] = proto
