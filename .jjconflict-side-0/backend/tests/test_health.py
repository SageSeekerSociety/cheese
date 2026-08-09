import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.anyio
async def test_health_check() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_version_endpoint_reports_the_build() -> None:
    """/api/version powers the 内测 badge: it returns the running sha and the
    box's opt-in flag. Public — no auth, no cheese token."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/version")
    assert response.status_code == 200
    data = response.json()["data"]
    assert "sha" in data
    assert "short" in data
    assert isinstance(data["badge"], bool)


@pytest.mark.anyio
async def test_health_carries_version() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")
    assert response.json()["data"]["version"]
