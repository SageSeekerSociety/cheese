"""Calls to the forge reuse one connection while the application runs.

Opening a connection to GitHub costs a TLS handshake that takes longer than the
answer; a turn whose preparation makes several forge calls paid it every time.
The forge here is a loopback HTTP server that counts the connections it
accepts, so what is asserted is what crossed the wire.
"""

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core.forge_http import reuse_forge_connections
from app.domain.project import forge


async def _forge_counting_connections():
    accepted: list[int] = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        accepted.append(1)
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                if not head:
                    return
                body = json.dumps({"default_branch": "main"}).encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode()
                    + body
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            return
        finally:
            writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"http://127.0.0.1:{port}", accepted


def _bound_to(monkeypatch, api_url: str) -> None:
    monkeypatch.setattr(
        forge,
        "binding_for_project",
        AsyncMock(return_value=SimpleNamespace(api_url=api_url, repo="acme/widgets")),
    )
    tokens = SimpleNamespace(installation_token=AsyncMock(return_value=("t", "x")))
    monkeypatch.setattr(forge, "tokens_for_project", AsyncMock(return_value=tokens))


async def test_successive_forge_calls_share_one_connection(monkeypatch):
    server, api_url, accepted = await _forge_counting_connections()
    _bound_to(monkeypatch, api_url)
    project_id = uuid.uuid4()
    async with server, reuse_forge_connections():
        for _ in range(3):
            data = await forge.repository_data(project_id, None)  # type: ignore[arg-type]
            assert data == {"default_branch": "main"}

    assert len(accepted) == 1


async def test_outside_the_application_the_forge_is_still_reached(monkeypatch):
    # A script or a job run outside the app's lifespan has no shared pool.
    server, api_url, _accepted = await _forge_counting_connections()
    _bound_to(monkeypatch, api_url)
    async with server:
        data = await forge.repository_data(uuid.uuid4(), None)  # type: ignore[arg-type]

    assert data == {"default_branch": "main"}
