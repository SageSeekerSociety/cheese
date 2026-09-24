"""Which transport a device screen's CONNECT traffic takes to the meter.

Two decisions, kept pure so they can be pinned without standing up a screen: is
the tunnel configured, and what does `HTTPS_PROXY` become. A credential leaking
into a loopback URL still works until the token expires
under a `claude` that read it once at startup).
"""

from app.core.config import settings
from app.domain.agent.device_provider import connect_transport, uses_tunnel

_URL = "wss://gateway.example/api/llm/tunnel"


def test_every_device_takes_a_configured_tunnel():
    assert uses_tunnel(tunnel_url=_URL) is True


def test_no_tunnel_configured_means_no_tunnel():
    """A deployment on a flat network wants the direct dial, and it is the
    behaviour every deployment has today — so the fallback is silence, not a
    guess about whether the machine can reach the box."""
    for blank in ("", "   "):
        assert uses_tunnel(tunnel_url=blank) is False


def test_the_backend_names_no_tunnel_address():
    """Through the tunnel the address is a loopback port on the machine, chosen by
    that machine's kernel when the helper binds; the launcher exports it. The
    backend cannot know which ports are free there, so it names none."""
    assert connect_transport(session_token="scoped-token", via_tunnel=True) is None


def test_the_direct_address_carries_the_token_as_the_proxy_password(monkeypatch):
    """That password is what stops an exposed listener relaying for a caller
    that cannot prove which project to bill (#198)."""
    monkeypatch.setattr(settings, "subscription_device_proxy_host", "10.0.0.9")
    monkeypatch.setattr(settings, "subscription_proxy_connect_port", 8444)

    value = connect_transport(session_token="tok", via_tunnel=False)

    assert value == "http://cheese:tok@10.0.0.9:8444"


def test_the_direct_address_falls_back_to_the_box_local_host(monkeypatch):
    """An empty device-specific host falls back to the deployment proxy host.
    Reachability validation reports a box-local address before launch."""
    monkeypatch.setattr(settings, "subscription_device_proxy_host", "")
    monkeypatch.setattr(settings, "subscription_proxy_host", "172.17.0.1")
    monkeypatch.setattr(settings, "subscription_proxy_connect_port", 8444)

    assert (
        connect_transport(session_token="tok", via_tunnel=False)
        == "http://cheese:tok@172.17.0.1:8444"
    )
