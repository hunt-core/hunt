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


def _valid_ip(value: str) -> str | None:
    """Return *value* as a canonical IP address string, or None if it is not one."""
    value = value.strip()
    if value.startswith("[") and "]" in value:  # [v6]:port
        value = value[1 : value.index("]")]
    elif value.count(":") == 1:  # v4:port
        value = value.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


class TrustProxies(Middleware):
    """Rewrite request IP and scheme from forwarded headers for trusted proxies.

    Configure via the TRUSTED_PROXIES env var (comma-separated IPs/CIDRs):
        TRUSTED_PROXIES=10.0.0.0/8,172.16.0.0/12

    Set TRUSTED_PROXIES=* to trust all proxies (development only — never in production).
    In that mode the client IP is the *rightmost* X-Forwarded-For entry, the one
    appended by the proxy nearest the app; entries to its left are whatever the
    client sent. With a known chain of N proxies set TRUSTED_PROXY_HOPS=N (or the
    ``hops`` attribute) to take the Nth entry from the right instead.

    When the app is served over a Unix socket the ASGI server reports no peer
    address. Such a peer is trusted only when TRUSTED_PROXIES is ``*`` or
    TRUSTED_PROXIES_UNIX_SOCKET=true (or ``trust_unix_socket = True``).

    Or subclass and set the ``proxies`` class attribute:

        class MyTrustProxies(TrustProxies):
            proxies = ["10.0.0.0/8", "172.16.0.0/12"]
    """

    reads_body = False  # never touches the request body

    proxies: ClassVar[list[str]] = []
    trust_unix_socket: bool = False
    hops: int | None = None

    async def handle(self, request: Request, next: Next) -> Response:
        raw = ", ".join(self.proxies) if self.proxies else os.environ.get("TRUSTED_PROXIES", "")
        if not raw:
            return await next(request)

        trust_all = raw.strip() in ("*", "**")
        networks = [] if trust_all else _parse_networks(raw)

        # ``client`` is None (not missing) when the server listens on a Unix socket.
        client = request._scope.get("client") or (None, 0)
        client_ip = client[0]
        if client_ip is None:
            is_trusted = trust_all or self._trusts_unix_socket()
        else:
            is_trusted = trust_all or _ip_in_networks(str(client_ip), networks)

        if is_trusted:
            self._apply_forwarded_ip(request, trust_all, networks, self._hops())
            self._apply_forwarded_scheme(request)

        return await next(request)

    def _trusts_unix_socket(self) -> bool:
        if self.trust_unix_socket:
            return True
        return os.environ.get("TRUSTED_PROXIES_UNIX_SOCKET", "").strip().lower() in ("true", "1", "yes")

    def _hops(self) -> int:
        if self.hops is not None:
            return max(1, int(self.hops))
        try:
            return max(1, int(os.environ.get("TRUSTED_PROXY_HOPS", "1").strip()))
        except ValueError:
            return 1

    @staticmethod
    def _apply_forwarded_ip(
        request: Request,
        trust_all: bool,
        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
        hops: int = 1,
    ) -> None:
        xff = request.header("x-forwarded-for", "")
        if not xff:
            return
        # Anything that is not an IP address is dropped: the value ends up in
        # logs, rate-limit keys and the session registry.
        ips = [ip for ip in (_valid_ip(part) for part in xff.split(",")) if ip]
        if not ips:
            return
        if trust_all:
            # Count ``hops`` entries in from the right; never further than the list.
            real_ip = ips[-min(hops, len(ips))]
        else:
            # Walk right-to-left; the client is the first address that is not
            # one of our proxies. If every entry is a proxy, use the leftmost.
            real_ip = ips[0]
            for ip in reversed(ips):
                if not _ip_in_networks(ip, networks):
                    real_ip = ip
                    break
        client = request._scope.get("client") or (None, 0)
        request._scope["client"] = (real_ip, client[1] if len(client) > 1 else 0)

    @staticmethod
    def _apply_forwarded_scheme(request: Request) -> None:
        # A chain of proxies yields "https, http": the first value is the
        # scheme the client used.
        proto = (request.header("x-forwarded-proto", "") or "").split(",")[0].strip().lower()
        if proto in ("http", "https"):
            request._scope["scheme"] = proto
