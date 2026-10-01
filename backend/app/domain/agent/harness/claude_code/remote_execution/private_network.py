"""The network gate in front of a private chat's container.

A private chat's commands run in a container on the session host, and from
there they could reach whatever the host can: its own services, the other
containers, the LAN, a cloud metadata endpoint. The gate leaves them the public
internet and the platform's own endpoint, and nothing else internal.

It owns the network namespace the container joins (``--network
container:<gate>``), starts as root with ``NET_ADMIN`` only long enough to
install the rules below in that namespace, and then serves DNS as an
unprivileged user. The container itself keeps every capability dropped, so
nothing it runs can change the rules.

- An address that is not public (``addresses.py``, the same definition
  ``/fetch`` refuses by) is rejected, except the one ``host:port`` the platform
  is reached at (``CHEESE_API``).
- Every DNS query from the container is redirected to the resolver here. On a
  host whose resolver answers every name with a fake-IP placeholder
  (``198.18.0.0/15``) and lets a transparent proxy connect by name, the
  placeholders are rejected like any internal address, so the resolver answers
  with the real addresses instead (asked over DNS over HTTPS, as ``/fetch``
  does) and refuses a name with any internal one. A connection to a literal
  internal address is refused by the rules alone.

Standard library only: this runs from the executor image.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import struct
import subprocess
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

try:
    from app.domain.fetch.addresses import (
        NOT_PUBLIC_V4,
        PUBLIC_WITHIN_V4,
        is_placeholder,
        is_public,
    )
except ImportError:  # In the image, beside addresses.py.
    from addresses import (  # type: ignore[no-redef]
        NOT_PUBLIC_V4,
        PUBLIC_WITHIN_V4,
        is_placeholder,
        is_public,
    )

#: The user the resolver runs as: the only one whose DNS leaves the namespace.
RESOLVER_UID = 1001
RESOLVER_PORT = 5353
READY = "/run/cheese-network-ready"
TTL = 30
WORKERS = 16


def rules(api_addresses, api_port):
    """The ``iptables-restore`` input for the gate's namespace."""
    lines = [
        "*filter",
        ":INPUT ACCEPT [0:0]",
        ":FORWARD ACCEPT [0:0]",
        ":OUTPUT ACCEPT [0:0]",
        ":REFUSE - [0:0]",
        "-A REFUSE -p tcp -j REJECT --reject-with tcp-reset",
        "-A REFUSE -j REJECT --reject-with icmp-net-prohibited",
        "-A OUTPUT -o lo -j ACCEPT",
        # The namespace's own loopback, which is all 127/8 reaches from here.
        # Said by address too: a DNS query redirected to the resolver below is
        # still filtered as leaving by the interface it was first routed to.
        "-A OUTPUT -d 127.0.0.0/8 -j ACCEPT",
    ]
    lines += [
        f"-A OUTPUT -d {address}/32 -p tcp --dport {api_port} -j ACCEPT"
        for address in api_addresses
    ]
    lines += [
        f"-A OUTPUT -p {protocol} --dport 53 -m owner --uid-owner {RESOLVER_UID} "
        "-j ACCEPT"
        for protocol in ("udp", "tcp")
    ]
    lines += [f"-A OUTPUT -d {network} -j ACCEPT" for network in PUBLIC_WITHIN_V4]
    lines += [f"-A OUTPUT -d {network} -j REFUSE" for network in NOT_PUBLIC_V4]
    lines += [
        "COMMIT",
        "*nat",
        ":PREROUTING ACCEPT [0:0]",
        ":INPUT ACCEPT [0:0]",
        ":OUTPUT ACCEPT [0:0]",
        ":POSTROUTING ACCEPT [0:0]",
    ]
    lines += [
        f"-A OUTPUT -p {protocol} --dport 53 -m owner ! --uid-owner {RESOLVER_UID} "
        f"-j REDIRECT --to-ports {RESOLVER_PORT}"
        for protocol in ("udp", "tcp")
    ]
    lines.append("COMMIT")
    return "\n".join(lines) + "\n"


#: IPv6 leaves only over loopback; the resolver hands out IPv4 addresses only.
RULES_V6 = """*filter
:INPUT ACCEPT [0:0]
:FORWARD ACCEPT [0:0]
:OUTPUT ACCEPT [0:0]
-A OUTPUT -o lo -j ACCEPT
-A OUTPUT -p tcp -j REJECT --reject-with tcp-reset
-A OUTPUT -j REJECT
COMMIT
"""


def install(api_addresses, api_port):
    ruleset = rules(api_addresses, api_port)
    subprocess.run(["iptables-restore"], input=ruleset, text=True, check=True)
    if os.path.exists("/proc/sys/net/ipv6"):
        subprocess.run(["ip6tables-restore"], input=RULES_V6, text=True, check=True)


def _ipv4(host):
    infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _over_https(resolver, host):
    query = urllib.parse.urlencode({"name": host, "type": "A"})
    request = urllib.request.Request(
        f"{resolver}?{query}", headers={"accept": "application/dns-json"}
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=8) as response:
        answers = json.load(response).get("Answer") or []
    return [a["data"] for a in answers if a.get("type") == 1 and a.get("data")]


class Refused(Exception):
    """The name has an address the container must not reach."""


def public_addresses(host, resolver, *, platform=None):
    """``host``'s IPv4 addresses, provided all of them are public.

    The same decision ``guard.public_address`` makes for ``/fetch``: resolve,
    ask over DNS over HTTPS when every answer is a placeholder, and refuse the
    name unless every address is public. ``platform`` maps the platform's host
    to the addresses the rules let through.
    """
    if platform and host.lower() in platform:
        return platform[host.lower()]
    try:
        addresses = _ipv4(host)
    except OSError as exc:
        raise Refused(f"{host} does not resolve") from exc
    if addresses and all(is_placeholder(a) for a in addresses):
        addresses = _over_https(resolver, host)
    if not addresses:
        raise Refused(f"{host} does not resolve")
    if not all(is_public(a) for a in addresses):
        raise Refused(f"{host} is not a public address")
    return addresses


def _question(packet):
    labels, offset = [], 12
    while True:
        length = packet[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0:
            raise ValueError("compressed question")
        labels.append(packet[offset + 1 : offset + 1 + length].decode("ascii"))
        offset += 1 + length
    qtype, _ = struct.unpack(">HH", packet[offset : offset + 4])
    return ".".join(labels), qtype, packet[12 : offset + 4]


def answer(packet, lookup):
    """The reply to one DNS query. A queries only; every other type is empty."""
    (ident, flags, count) = struct.unpack(">HHH", packet[:6])
    if count != 1:
        return struct.pack(">HHHHHH", ident, 0x8000 | (flags & 0x0100) | 1, 0, 0, 0, 0)
    name, qtype, question = _question(packet)
    rcode, records = 0, []
    if qtype == 1:
        try:
            records = lookup(name.rstrip("."))
        except Refused:
            rcode = 3
        except Exception:  # noqa: BLE001 — an unknown answer is no answer
            rcode = 2
    header = struct.pack(
        ">HHHHHH", ident, 0x8080 | (flags & 0x0100) | rcode, 1, len(records), 0, 0
    )
    body = b"".join(
        struct.pack(">HHHIH", 0xC00C, 1, 1, TTL, 4)
        + ipaddress.IPv4Address(record).packed
        for record in records
    )
    return header + question + body


def serve(sock, lookup):
    def reply(packet, peer):
        try:
            sock.sendto(answer(packet, lookup), peer)
        except Exception as exc:  # noqa: BLE001 — one bad query, not the gate
            print(f"dns: {exc}", file=sys.stderr, flush=True)

    # A fixed pool: the gate runs under a small pid limit, and a burst of
    # lookups (an installer resolving its dependencies) waits here rather than
    # failing to start a thread and taking the gate, and the network, down.
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        while True:
            packet, peer = sock.recvfrom(4096)
            pool.submit(reply, packet, peer)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-host")
    parser.add_argument("--api-port", type=int)
    parser.add_argument("--resolver", required=True)
    args = parser.parse_args(argv)
    platform = {}
    if args.api_host:
        platform[args.api_host.lower()] = _ipv4(args.api_host)
    api_addresses = [a for found in platform.values() for a in found]
    install(api_addresses, args.api_port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", RESOLVER_PORT))
    with open(READY, "w") as ready:
        ready.write("ready\n")
    os.setgroups([])
    os.setgid(RESOLVER_UID)
    os.setuid(RESOLVER_UID)
    serve(
        sock,
        lambda host: public_addresses(host, args.resolver, platform=platform),
    )


if __name__ == "__main__":
    main()
