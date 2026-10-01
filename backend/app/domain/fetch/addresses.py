"""Which addresses count as public: the one definition every egress check uses.

The backend's fetch (``guard.py``) and the egress proxy private chats reach the
network through (``remote_execution/private_egress.py``) refuse the same
destinations, decided here. The proxy runs from the executor image, under
whatever Python that image ships, so the IPv4 ranges are written out rather than
read from ``ipaddress.is_global``, whose answer has changed between Python
releases. ``test_public_addresses.py`` holds the list to what ``is_global`` says
on the backend's Python.

Only the decision lives here; each side does its own lookups (the backend
asynchronously, the proxy with the standard library).

Standard library only: the image copies this file next to the proxy.
"""

from __future__ import annotations

import ipaddress

#: IPv4 ranges that are not globally routable (IANA special-purpose registry,
#: as ``ipaddress`` reads it), plus multicast. Nothing in them is public.
NOT_PUBLIC_V4 = tuple(
    ipaddress.IPv4Network(network)
    for network in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "255.255.255.255/32",
    )
)

#: Globally reachable anycast services inside ``192.0.0.0/24``.
PUBLIC_WITHIN_V4 = tuple(
    ipaddress.IPv4Network(network) for network in ("192.0.0.9/32", "192.0.0.10/32")
)

#: Where fake-IP DNS hands out its placeholders (the benchmarking range).
PLACEHOLDERS = ipaddress.IPv4Network("198.18.0.0/15")


def _address(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_placeholder(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    return isinstance(ip, ipaddress.IPv4Address) and ip in PLACEHOLDERS


def is_public(address: str) -> bool:
    ip = _address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        return ip.is_global and not ip.is_multicast
    if any(ip in network for network in PUBLIC_WITHIN_V4):
        return True
    return not any(ip in network for network in NOT_PUBLIC_V4)


class NotPublic(Exception):
    """The destination is somewhere egress must not go."""


def needs_real_addresses(addresses: list[str]) -> bool:
    """True when every answer is a fake-IP placeholder.

    Some machines resolve every name to a placeholder in ``198.18.0.0/15`` and
    let a transparent proxy connect to the real host by name. Those say nothing
    about where a connection goes, so the name has to be asked again over DNS
    over HTTPS, and the connection made to the real address it gives.
    """
    return bool(addresses) and all(is_placeholder(a) for a in addresses)


def over_https_answers(payload: dict) -> list[str]:
    """The IPv4 addresses in a DNS-over-HTTPS resolver's JSON answer."""
    answers = payload.get("Answer") or []
    return [a["data"] for a in answers if a.get("type") == 1 and a.get("data")]


def vetted(host: str, addresses: list[str]) -> list[str]:
    """``addresses``, IPv4 first, provided ALL of them are public.

    All, not any: a name with one public and one private record would otherwise
    be allowed and then connected to whichever the resolver hands back next.
    """
    if not addresses:
        raise NotPublic(f"{host} does not resolve")
    for address in addresses:
        if not is_public(address):
            raise NotPublic(f"{host} is not a public address")
    # IPv4 first: it is what every deployment so far has a route for.
    return sorted(dict.fromkeys(addresses), key=lambda a: ":" in a)
