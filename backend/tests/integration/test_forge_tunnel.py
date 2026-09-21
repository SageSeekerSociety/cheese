"""Native HTTPS keeps TLS end to end and cannot dial unrelated destinations."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.test_llm_tunnel import _FakeListener


@pytest.fixture
def project(monkeypatch):
    identifier = uuid.uuid4()

    async def binding(project_id, session):
        assert project_id == identifier
        return SimpleNamespace(
            kind="github_app",
            url="https://github.com/team/repo",
            api_url="https://api.github.com",
        )

    monkeypatch.setattr("app.domain.project.forge.binding_for_project", binding)
    return identifier


@pytest.mark.parametrize(
    "host",
    [
        "api.github.com",
        "github.com",
        "objects.githubusercontent.com",
        "productionresultssa1.blob.core.windows.net",
    ],
)
def test_connect_strips_proxy_credentials_and_passes_binary_bytes(
    client, project, monkeypatch, host
):
    original = asyncio.open_connection
    with _FakeListener() as listener:

        async def connect(name, port):
            assert name == host and port == 443
            return await original("127.0.0.1", listener.port)

        monkeypatch.setattr(
            "app.api.routes.forge_token.asyncio.open_connection", connect
        )
        token = mint_scoped_token(project_id=str(project))
        with client.websocket_connect(
            f"/sandbox/forge-tunnel/{project}?token={token}"
        ) as ws:
            ws.send_bytes(
                (
                    f"CONNECT {host}:443 HTTP/1.1\r\n"
                    "Proxy-Authorization: secret\r\n\r\n"
                ).encode()
            )
            assert ws.receive_bytes() == b"HTTP/1.1 200 Connection Established\r\n\r\n"
            ws.send_bytes(b"opaque\x00\xff")
            assert ws.receive_bytes() == b"OPAQUE\x00\xff"
        assert bytes(listener.received) == b"opaque\x00\xff"


@pytest.mark.parametrize(
    "target",
    [
        "localhost:443",
        "api.github.com.attacker.invalid:443",
        "api.github.com:22",
        "api.github.com:443/path",
        "user@api.github.com:443",
    ],
)
def test_unrelated_destinations_never_dial(client, project, monkeypatch, target):
    async def forbidden(*args, **kwargs):
        pytest.fail("invalid destination reached TCP dial")

    monkeypatch.setattr("app.api.routes.forge_token.asyncio.open_connection", forbidden)
    token = mint_scoped_token(project_id=str(project))
    with client.websocket_connect(
        f"/sandbox/forge-tunnel/{project}?token={token}"
    ) as ws:
        ws.send_bytes(f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
        assert ws.receive_bytes() == b"HTTP/1.1 403 Forbidden\r\n\r\n"


@pytest.mark.parametrize("kind", ["invalid", "expired", "other-project"])
def test_tunnel_requires_current_project_token(client, project, kind):
    token = (
        mint_scoped_token(project_id=str(project), ttl_s=-1)
        if kind == "expired"
        else mint_scoped_token(project_id=str(uuid.uuid4()))
        if kind == "other-project"
        else "invalid"
    )
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/sandbox/forge-tunnel/{project}?token={token}"):
            pytest.fail("unauthorized socket accepted")
