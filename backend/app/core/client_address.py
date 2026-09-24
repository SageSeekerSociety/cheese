"""Whether the address a request carries is its client's own.

uvicorn replaces the TCP peer with an address from X-Forwarded-For only when
the peer is one of the proxies named in FORWARDED_ALLOW_IPS. Left unset, it
trusts loopback alone, and behind this deployment's proxies every request
carries a proxy's address. A limit keyed on that would count every client as
one, and refuse all of them together, so per-address limits apply only where
this answers with an address.
"""

import ipaddress
import os
from functools import lru_cache

from starlette.requests import Request

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def resolved_client_address(request: Request) -> str | None:
    """The client's address, or None when the server cannot tell it apart
    from a proxy's.

    None when FORWARDED_ALLOW_IPS is unset: the address is then the peer,
    which behind a proxy is the proxy. None when it is ``*``: uvicorn then
    takes the leftmost forwarded address, which the client writes itself.
    None when the address is one of the listed proxies: the request reached
    the server without a client address in front of it.
    """
    host = request.client.host if request.client else ""
    configured = os.environ.get("FORWARDED_ALLOW_IPS", "").strip()
    if not host or not configured or configured == "*":
        return None
    if _is_proxy(host, configured):
        return None
    return host


def _is_proxy(host: str, configured: str) -> bool:
    literals, networks = _proxies(configured)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host in literals
    return any(address in network for network in networks)


@lru_cache(maxsize=8)
def _proxies(configured: str) -> tuple[frozenset[str], tuple[_Network, ...]]:
    """Parsed as uvicorn parses the same list: an address or a network each,
    and anything that is neither kept as a literal name."""
    literals: set[str] = set()
    networks: list[_Network] = []
    for entry in (item.strip() for item in configured.split(",")):
        if not entry:
            continue
        try:
            if "/" in entry:
                networks.append(ipaddress.ip_network(entry))
            else:
                networks.append(ipaddress.ip_network(ipaddress.ip_address(entry)))
        except ValueError:
            literals.add(entry)
    return frozenset(literals), tuple(networks)
