"""The client address the backend sees, served the way the image serves it.

The image runs `uvicorn app.main:app` with no proxy flags, so the address a
request carries is whatever uvicorn's defaults and FORWARDED_ALLOW_IPS make of
the peer and its X-Forwarded-For. Login sessions and real-name access audits
record that address, so it must be the user's, not a proxy's, and never a
value the user wrote into the header themselves.
"""

from __future__ import annotations

from typing import Any, cast

import httpx
import uvicorn

# The dev box's chain: docker's bridge gateway in front of the container, the
# box's own nginx, and the edge proxy.
DEV_PROXIES = "127.0.0.1,172.18.0.0/16,192.168.16.11"
BRIDGE_GATEWAY = "172.18.0.1"


async def _echo_client(scope, receive, send) -> None:
    assert scope["type"] == "http"
    host = scope["client"][0] if scope.get("client") else ""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": host.encode()})


async def _client_seen(peer: str, forwarded_for: str | None) -> str:
    # Built after the environment is set: uvicorn reads FORWARDED_ALLOW_IPS
    # when the server's config is created, exactly as the container does.
    config = uvicorn.Config(_echo_client, log_config=None)
    config.load()
    served = cast(Any, config.loaded_app)
    transport = httpx.ASGITransport(app=served, client=(peer, 40000))
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/", headers=headers)
    return r.text


async def test_the_address_behind_the_trusted_proxies_is_the_client(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", DEV_PROXIES)

    seen = await _client_seen(BRIDGE_GATEWAY, "203.0.113.9, 192.168.16.11")

    assert seen == "203.0.113.9"


async def test_a_client_cannot_choose_its_own_address(monkeypatch):
    """Whatever the client puts in front of the header is not believed, even
    an address that is itself one of our proxies."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", DEV_PROXIES)

    for written_by_client in ("6.6.6.6", "127.0.0.1", "6.6.6.6, 192.168.16.11"):
        seen = await _client_seen(
            BRIDGE_GATEWAY, f"{written_by_client}, 203.0.113.9, 192.168.16.11"
        )
        assert seen == "203.0.113.9", written_by_client


async def test_a_peer_that_is_not_a_proxy_is_the_client(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", DEV_PROXIES)

    seen = await _client_seen("198.51.100.7", "203.0.113.9")

    assert seen == "198.51.100.7"


async def test_unset_trusts_no_proxy_beyond_the_box_itself(monkeypatch):
    """Unset is the safe default: the proxy's address is recorded, and no
    forwarded value is believed from anything but loopback."""
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)

    seen = await _client_seen(BRIDGE_GATEWAY, "203.0.113.9")

    assert seen == BRIDGE_GATEWAY


# Per-address limits key on the address only when it is the client's; asked
# of the same request uvicorn has resolved, as a route asks it.


async def _limit_key_seen(peer: str, forwarded_for: str | None) -> str:
    from starlette.requests import Request

    from app.core.client_address import resolved_client_address

    async def echo(scope, receive, send) -> None:
        key = resolved_client_address(Request(scope)) or "-"
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": key.encode()})

    config = uvicorn.Config(echo, log_config=None)
    config.load()
    served = cast(Any, config.loaded_app)
    transport = httpx.ASGITransport(app=served, client=(peer, 40000))
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/", headers=headers)
    return r.text


async def test_limits_key_on_the_client_behind_the_trusted_proxies(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", DEV_PROXIES)

    key = await _limit_key_seen(
        BRIDGE_GATEWAY, "203.0.113.9, 127.0.0.1, 172.18.0.10, 127.0.0.1"
    )

    assert key == "203.0.113.9"


async def test_limits_key_on_a_peer_that_is_not_a_proxy(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", DEV_PROXIES)

    assert await _limit_key_seen("198.51.100.7", "203.0.113.9") == "198.51.100.7"


async def test_unset_keys_no_limit_on_the_proxy(monkeypatch):
    """Unset, every request carries the proxy's address, and a limit on it
    would refuse every client at once."""
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)

    assert await _limit_key_seen(BRIDGE_GATEWAY, "203.0.113.9") == "-"


async def test_a_request_with_no_client_behind_a_proxy_keys_no_limit(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", DEV_PROXIES)

    assert await _limit_key_seen(BRIDGE_GATEWAY, None) == "-"
    assert await _limit_key_seen(BRIDGE_GATEWAY, "127.0.0.1") == "-"


async def test_trusting_every_peer_keys_no_limit(monkeypatch):
    """With ``*`` the address is whatever the client wrote into the header,
    so keying on it would let anyone spend somebody else's budget."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")

    assert await _limit_key_seen(BRIDGE_GATEWAY, "203.0.113.9") == "-"
