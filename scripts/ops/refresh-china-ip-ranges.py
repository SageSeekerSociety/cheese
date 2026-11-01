#!/usr/bin/env python3
"""Refresh frontend/nginx/mainland/china-ip.txt from APNIC's allocation file.

    scripts/ops/refresh-china-ip-ranges.py [--check]

The mainland redirect decides "in China" from this list, so it goes stale the
way the registry does: APNIC hands out and returns blocks every day, and a list
from last year sends a new campus network the long way round. Nothing breaks —
the file is large enough that a missing block is one visitor taking the detour —
which is why nothing would notice it going stale either. Run it monthly; commit
what it changes.

--check writes nothing and exits 1 when the committed file no longer matches
APNIC's, which is what a scheduled job should ask.

Only the file is regenerated; the rules that read it are in
frontend/nginx/mainland/main.conf, and the redirect itself is
frontend/scripts/mainland-redirect.sh.
"""

import argparse
import ipaddress
import sys
import urllib.request
from pathlib import Path

SOURCE = "https://ftp.apnic.net/apnic/stats/apnic/delegated-apnic-latest"
TARGET = Path(__file__).resolve().parents[2] / "frontend/nginx/mainland/china-ip.txt"
HEADER = """\
# China's address space, one network per line, in the form nginx's `geo` reads:
# network, then the value. Generated from APNIC's delegated-latest by
# scripts/ops/refresh-china-ip-ranges.py; do not edit by hand.
"""


def networks(text: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Every block APNIC has allocated or assigned to CN, collapsed."""
    v4, v6 = [], []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 7 or parts[1] != "CN" or parts[6] not in ("allocated", "assigned"):
            continue
        start, kind, count = parts[3], parts[2], parts[4]
        if kind == "ipv4":
            # APNIC gives a count, not a prefix length: a /n holds 2**(32-n).
            v4.append(ipaddress.ip_network(f"{start}/{32 - (int(count).bit_length() - 1)}"))
        elif kind == "ipv6":
            v6.append(ipaddress.ip_network(f"{start}/{count}"))
    return [
        *ipaddress.collapse_addresses(v4),
        *ipaddress.collapse_addresses(v6),
    ]


def rendered(text: str) -> str:
    return HEADER + "".join(f"{network} 1;\n" for network in networks(text))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="only report whether the file is stale")
    parser.add_argument("--source", default=SOURCE, help=argparse.SUPPRESS)
    args = parser.parse_args()

    with urllib.request.urlopen(args.source, timeout=60) as response:
        fresh = rendered(response.read().decode("utf-8"))

    current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
    if args.check:
        if current != fresh:
            print(f"{TARGET} is stale; run scripts/ops/refresh-china-ip-ranges.py", file=sys.stderr)
            return 1
        return 0

    if current == fresh:
        print("unchanged")
        return 0
    TARGET.write_text(fresh, encoding="utf-8")
    print(f"wrote {TARGET}: {fresh.count(chr(10)) - HEADER.count(chr(10))} networks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
