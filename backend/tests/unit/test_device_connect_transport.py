"""Which transport a device screen's CONNECT traffic takes to the meter.

Two decisions, kept pure so they can be pinned without standing up a screen: is
this machine on the tunnel, and what does `HTTPS_PROXY` become. Both are easy to
get subtly wrong in ways nothing else catches — a co-located screen tunnelled
for no reason still works (just slower, with an extra failure mode), and a
credential leaking into a loopback URL still works (until the token expires
under a `claude` that read it once at startup).
"""

from app.core.config import settings
from app.domain.agent.device_provider import connect_transport, uses_tunnel

_URL = "wss://gateway.example/api/llm/tunnel"


def test_only_a_remote_machine_takes_the_tunnel():
    """A co-located screen shares the box's network and reaches the listener's
    bridge address directly; tunnelling it would add a hop and a failure mode
    for nothing."""
    assert uses_tunnel(co_located=False, tunnel_url=_URL) is True
    assert uses_tunnel(co_located=True, tunnel_url=_URL) is False


def test_no_tunnel_configured_means_no_tunnel():
    """A deployment on a flat network wants the direct dial, and it is the
    behaviour every deployment has today — so the fallback is silence, not a
    guess about whether the machine can reach the box."""
    for blank in ("", "   "):
        assert uses_tunnel(co_located=False, tunnel_url=blank) is False


def test_the_tunnel_address_carries_no_credential(monkeypatch):
    """The helper is the only thing on that loopback port and it reads the token
    from a file. Keeping it out of the URL is what lets a refreshed token take
    effect without relaunching `claude`, which reads HTTPS_PROXY exactly once at
    startup (#385)."""
    monkeypatch.setattr(settings, "subscription_tunnel_local_port", 8445)

    value = connect_transport(session_token="a-real-scoped-token", via_tunnel=True)

    assert value == "http://127.0.0.1:8445"
    assert "a-real-scoped-token" not in value


def test_the_direct_address_carries_the_token_as_the_proxy_password(monkeypatch):
    """That password is what stops an exposed listener relaying for a caller
    that cannot prove which project to bill (#198)."""
    monkeypatch.setattr(settings, "subscription_device_proxy_host", "10.0.0.9")
    monkeypatch.setattr(settings, "subscription_proxy_connect_port", 8444)

    value = connect_transport(session_token="tok", via_tunnel=False)

    assert value == "http://cheese:tok@10.0.0.9:8444"


def test_the_direct_address_falls_back_to_the_box_local_host(monkeypatch):
    """Unset means "co-located", which is the safe reading: it is an address only
    the box can resolve, so a remote machine handed it fails loudly on connect
    rather than quietly reaching something else."""
    monkeypatch.setattr(settings, "subscription_device_proxy_host", "")
    monkeypatch.setattr(settings, "subscription_proxy_host", "172.17.0.1")
    monkeypatch.setattr(settings, "subscription_proxy_connect_port", 8444)

    assert (
        connect_transport(session_token="tok", via_tunnel=False)
        == "http://cheese:tok@172.17.0.1:8444"
    )
