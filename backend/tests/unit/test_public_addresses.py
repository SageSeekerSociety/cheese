"""One definition of "public" for every egress check.

`/fetch` and the egress proxy private chats reach the network through refuse
the same addresses. The proxy runs from the executor image, under that image's
Python, so the IPv4 ranges are written out; these hold them to what the
standard library calls globally routable on the backend's Python: an address
the backend would refuse to fetch is one a private chat cannot reach either.
"""

import ipaddress
import random

import pytest

from app.domain.fetch.addresses import NOT_PUBLIC_V4, PUBLIC_WITHIN_V4, is_public


def _stdlib_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast


def _edges():
    for network in (*NOT_PUBLIC_V4, *PUBLIC_WITHIN_V4):
        first = int(network.network_address)
        last = int(network.broadcast_address)
        for value in (first - 1, first, first + 1, last - 1, last, last + 1):
            if 0 <= value < 2**32:
                yield str(ipaddress.IPv4Address(value))


@pytest.mark.parametrize("address", sorted(set(_edges())))
def test_every_range_edge_agrees_with_the_standard_library(address):
    assert is_public(address) == _stdlib_public(address)


def test_addresses_across_the_whole_space_agree_with_the_standard_library():
    sample = random.Random(20261001)
    for _ in range(200_000):
        address = str(ipaddress.IPv4Address(sample.getrandbits(32)))
        assert is_public(address) == _stdlib_public(address), address


@pytest.mark.parametrize(
    "address",
    [
        "169.254.169.254",  # cloud metadata
        "127.0.0.1",
        "10.0.0.5",
        "172.17.0.1",  # the docker bridge gateway: the host itself
        "192.168.16.7",
        "100.70.193.71",  # carrier-grade NAT, as tailscale hands out
        "198.18.2.194",  # a fake-IP placeholder
        "::1",
        "fe80::1",
        "fd00::1",
        "::ffff:10.0.0.5",
    ],
)
def test_internal_addresses_are_not_public(address):
    assert not is_public(address)


@pytest.mark.parametrize("address", ["151.101.0.223", "223.5.5.5", "2606:4700::1"])
def test_internet_addresses_are_public(address):
    assert is_public(address)
