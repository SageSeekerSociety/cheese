"""Every response says how long the server itself took, so a slow request in
the browser's Timing tab splits into server time and time on the wire."""

import re

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.anyio
async def test_a_response_says_how_long_the_server_took() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/version")
    assert response.status_code == 200
    assert re.fullmatch(r"app;dur=\d+(\.\d+)?", response.headers["server-timing"])
