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
    """/version powers the 内测 badge: it returns the running sha and the
    box's opt-in flag. Public — no auth, no cheese token."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/version")
    assert response.status_code == 200
    data = response.json()["data"]
    assert "sha" in data
    assert "short" in data
    assert isinstance(data["badge"], bool)


async def _version(
    monkeypatch: pytest.MonkeyPatch, *, build: str, release: str
) -> dict:
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_version", build)
    monkeypatch.setattr(settings, "app_release", release)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/version")
    assert response.status_code == 200
    return response.json()["data"]


@pytest.mark.anyio
async def test_version_names_the_released_commit_when_the_image_is_older(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A merge that changes no image is released on the previous image under
    the new commit's tag. "Is commit X live" has to be answered by the release,
    not by the commit baked into the reused image."""
    build, release = "a" * 40, "b" * 40
    data = await _version(monkeypatch, build=build, release=release)
    assert (data["sha"], data["short"], data["build"]) == (release, "bbbbbbb", build)


@pytest.mark.anyio
async def test_version_without_a_deploy_names_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build = "c" * 40
    data = await _version(monkeypatch, build=build, release="")
    assert (data["sha"], data["short"], data["build"]) == (build, "ccccccc", build)
    local = await _version(monkeypatch, build="dev", release="")
    assert (local["sha"], local["short"]) == ("dev", "dev")


@pytest.mark.anyio
async def test_health_carries_version() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")
    assert response.json()["data"]["version"]
