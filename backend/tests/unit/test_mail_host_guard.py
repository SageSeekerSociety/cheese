"""Which mail servers a person may connect: public ones, even behind a fake-ip
proxy; never the platform's own network, however the name is dressed up."""

import ipaddress
import socket

import pytest

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.integration import service


def _resolves_to(monkeypatch, local: str, real: list[str] | None):
    monkeypatch.setattr(settings, "integration_allow_private_hosts", False)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [(socket.AF_INET, 0, 0, "", (local, 0))],
    )
    asked = []

    def doh(host):
        asked.append(host)
        return [ipaddress.ip_address(a) for a in (real or [])]

    monkeypatch.setattr(service, "_real_addresses", doh)
    return asked


def _guard(host="imap.qq.com"):
    service.guard_mail_hosts({"imap_host": host, "smtp_host": host, "security": "ssl"})


def test_a_fake_ip_placeholder_for_a_public_server_is_allowed(monkeypatch):
    """dev, 2026-09-27: imap.qq.com resolved to 198.18.0.198 behind the proxy."""
    asked = _resolves_to(monkeypatch, "198.18.0.198", ["58.254.165.67"])
    _guard()
    assert asked, "the placeholder was trusted without asking for the real address"


def test_a_fake_ip_placeholder_for_an_internal_address_is_refused(monkeypatch):
    _resolves_to(monkeypatch, "198.18.3.4", ["10.0.0.5"])
    with pytest.raises(ValidationError, match="内网"):
        _guard("evil.example.com")


def test_a_placeholder_whose_real_address_cannot_be_found_is_refused(monkeypatch):
    _resolves_to(monkeypatch, "198.19.0.1", None)
    with pytest.raises(ValidationError, match="真实地址"):
        _guard()


@pytest.mark.parametrize("local", ["10.0.0.5", "127.0.0.1", "192.168.1.2"])
def test_an_internal_address_is_refused_without_looking_further(monkeypatch, local):
    asked = _resolves_to(monkeypatch, local, ["58.254.165.67"])
    with pytest.raises(ValidationError, match="内网"):
        _guard()
    assert not asked, "an internal address was second-guessed by public DNS"
