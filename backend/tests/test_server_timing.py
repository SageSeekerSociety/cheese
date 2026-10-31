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
    timing = response.headers.get_list("server-timing")
    assert any(re.fullmatch(r"app;dur=\d+(\.\d+)?", t) for t in timing), timing


@pytest.mark.anyio
async def test_a_response_says_how_long_it_spent_on_the_database() -> None:
    """Beside the total, the share that was the database: a request slow in
    SQL and one slow in Python look alike from the total alone."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/version")
    timing = response.headers.get_list("server-timing")
    assert any(
        re.fullmatch(r'db;dur=\d+(\.\d+)?;desc="\d+ sql"', t) for t in timing
    ), timing
