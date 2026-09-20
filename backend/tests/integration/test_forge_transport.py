"""Native forge traffic stays within the project credential's authority."""

import base64
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.core.crypto import encrypt_text
from app.domain.project.models import ForgeToken


@pytest.fixture
def relay(client, monkeypatch):
    project_id = uuid.uuid4()
    binding = SimpleNamespace(
        kind="forgejo", repo="project/code", api_url="http://forgejo:3000/api/v1"
    )

    async def binding_for_project(identifier, session):
        return binding

    monkeypatch.setattr(
        "app.domain.project.forge.binding_for_project", binding_for_project
    )

    async def seed():
        async with client.test_factory() as session:
            session.add(
                ForgeToken(
                    project_id=project_id,
                    api_url=binding.api_url,
                    username="project",
                    value=encrypt_text("project-token"),
                    expires_at=datetime.now(UTC) + timedelta(minutes=10),
                )
            )
            await session.commit()

    client.portal.call(seed)
    requests = []
    closed = []

    class Pack(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"first\x00"
            yield b"second\xff"

        async def aclose(self):
            closed.append(True)

    async def receive(request):
        requests.append((request, await request.aread()))
        return httpx.Response(
            200,
            headers={"Content-Type": "application/x-git-upload-pack-result"},
            stream=Pack(),
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.api.routes.forge_token.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(receive), **kwargs),
    )
    return project_id, requests, closed


@pytest.mark.parametrize("scheme", ["token", "Bearer", "Basic"])
def test_native_traffic_streams_with_original_project_authority(client, relay, scheme):
    project_id, requests, closed = relay
    token = "project-token"
    if scheme == "Basic":
        token = base64.b64encode(b"project:project-token").decode()
    response = client.post(
        f"/sandbox/forge/{project_id}/project/code.git/git-upload-pack?x=1&x=2",
        headers={
            "Authorization": f"{scheme} {token}",
            "Git-Protocol": "version=2",
            "Content-Type": "application/x-git-upload-pack-request",
            "X-Cheese-Token": "must-not-forward",
            "Cookie": "must-not-forward",
        },
        content=b"request\x00bytes",
    )
    assert response.status_code == 200
    assert response.content == b"first\x00second\xff"
    assert response.headers["cache-control"] == "no-store"
    [forwarded] = requests
    request, body = forwarded
    assert str(request.url) == (
        "http://forgejo:3000/project/code.git/git-upload-pack?x=1&x=2"
    )
    assert body == b"request\x00bytes"
    assert request.headers["authorization"] == f"{scheme} {token}"
    assert request.headers["git-protocol"] == "version=2"
    assert "x-cheese-token" not in request.headers
    assert "cookie" not in request.headers
    assert closed == [True]


@pytest.mark.parametrize("auth", ["", "token wrong", "Basic !!!"])
def test_invalid_credentials_never_reach_forge(client, relay, auth):
    project_id, requests, _ = relay
    response = client.get(
        f"/sandbox/forge/{project_id}/project/code.git/info/refs",
        headers={"Authorization": auth},
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic ")
    assert requests == []


def test_token_cannot_select_another_projects_transport(client, relay):
    _, requests, _ = relay
    response = client.get(
        f"/sandbox/forge/{uuid.uuid4()}/api/v1/user",
        headers={"Authorization": "token project-token"},
    )
    assert response.status_code == 401
    assert requests == []


def test_expired_token_is_refused_even_before_upstream_revocation(client, relay):
    from sqlalchemy import update

    project_id, requests, _ = relay

    async def expire():
        async with client.test_factory() as session:
            await session.execute(
                update(ForgeToken).values(
                    expires_at=datetime.now(UTC) - timedelta(seconds=1)
                )
            )
            await session.commit()

    client.portal.call(expire)
    response = client.get(
        f"/sandbox/forge/{project_id}/api/v1/user",
        headers={"Authorization": "token project-token"},
    )
    assert response.status_code == 401
    assert requests == []
