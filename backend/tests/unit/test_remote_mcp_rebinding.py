"""A remote MCP server's name is checked again at the moment of connecting.

The request hook checks the name a person configured; a name that passed it can
be pointed at loopback or a cloud metadata address before the connection opens,
and the client would resolve it afresh. The connection must go to an address
checked at that moment, so the second answer is refused, not dialled.
"""

import pytest

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.fetch import guard
from app.domain.remote_mcp import http


@pytest.mark.parametrize("rebound_to", ["127.0.0.1", "169.254.169.254", "10.0.0.5"])
async def test_a_name_rebound_to_an_internal_address_is_not_dialled(
    monkeypatch: pytest.MonkeyPatch, rebound_to: str
) -> None:
    monkeypatch.setattr(settings, "remote_mcp_allow_private_hosts", False)
    # The hook's check passed earlier, while the name still pointed outside.
    monkeypatch.setattr(http, "refuse_internal_host", lambda host, what: None)
    resolved: list[str] = []

    async def rebound(host: str, port: int) -> list[str]:
        resolved.append(host)
        return [rebound_to]

    monkeypatch.setattr(guard, "_resolve", rebound)
    async with http.client() as client:
        with pytest.raises(ValidationError):
            await client.get("https://mcp.rebind.example/mcp")
    assert resolved == ["mcp.rebind.example"]


async def test_plain_http_is_still_refused_before_anything_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "remote_mcp_allow_private_hosts", False)
    async with http.client() as client:
        with pytest.raises(ValidationError):
            await client.get("http://mcp.example/mcp")
