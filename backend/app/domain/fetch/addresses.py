"""Which addresses count as public: the one definition every egress check uses.

The backend's fetch (``guard.py``) and the network gate in front of a private
chat's container (``remote_execution/private_network.py``) both refuse what is
not public. The gate runs inside the executor image, under whatever Python that
image ships, and turns the list into firewall rules; so the IPv4 ranges are
written out here rather than read from ``ipaddress.is_global``, whose answer has
changed between Python releases. ``test_public_addresses.py`` holds the list to
what ``is_global`` says on the backend's Python.

Standard library only: the image copies this file next to the gate.
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
