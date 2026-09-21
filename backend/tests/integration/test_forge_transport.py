"""Native forge traffic stays within the project credential's authority."""

import base64
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.core.crypto import encrypt_text
from app.core.sandbox_auth import mint_scoped_token
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
            headers={
                "Content-Type": "application/x-git-upload-pack-result",
                "X-Total-Count": "62",
                "Set-Cookie": "upstream-private=value",
            },
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


def test_forge_api_relay_preserves_the_native_clients_pagination_count(client, relay):
    project_id, requests, _ = relay
    response = client.get(
        f"/sandbox/forge/{project_id}/api/v1/repos/project/code/pulls?page=2&limit=30",
        headers={"Authorization": "token project-token"},
    )
    assert response.status_code == 200
    assert response.headers["x-total-count"] == "62"
    assert "set-cookie" not in response.headers
    assert str(requests[0][0].url).endswith("/pulls?page=2&limit=30")


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


@pytest.fixture
def github_relay(relay, monkeypatch):
    project_id, requests, closed = relay

    async def binding_for_project(identifier, session):
        return SimpleNamespace(
            kind="github_app",
            repo="owner/repo",
            url="https://github.com/owner/repo.git",
        )

    class Minter:
        async def installation_token(self):
            return "github-installation-token", "unused"

    async def tokens_for_project(identifier, session):
        assert identifier == project_id
        return Minter()

    monkeypatch.setattr(
        "app.domain.project.forge.binding_for_project", binding_for_project
    )
    monkeypatch.setattr(
        "app.domain.project.forge.tokens_for_project", tokens_for_project
    )
    return project_id, requests, closed


@pytest.mark.parametrize(
    "operation", ["info/refs", "git-upload-pack", "git-receive-pack"]
)
def test_github_git_uses_bound_repository_and_replaces_platform_secret(
    client, github_relay, operation
):
    project_id, requests, closed = github_relay
    token = mint_scoped_token(project_id=str(project_id))
    response = client.request(
        "GET" if operation == "info/refs" else "POST",
        f"/sandbox/forge/{project_id}/owner/repo.git/{operation}",
        headers={
            "Authorization": "Basic "
            + base64.b64encode(f"git:{token}".encode()).decode()
        },
        content=b"git-pack",
    )
    assert response.status_code == 200
    assert response.content == b"first\x00second\xff"
    [(request, body)] = requests
    assert str(request.url) == f"https://github.com/owner/repo.git/{operation}"
    assert body == b"git-pack"
    assert (
        request.headers["authorization"]
        == "Basic "
        + base64.b64encode(b"x-access-token:github-installation-token").decode()
    )
    assert token not in str(request.headers)
    assert closed == [True]


@pytest.mark.parametrize(
    "path", ["owner/other.git/info/refs", "api/v3/user", "owner/repo.git/other"]
)
def test_github_relay_cannot_select_another_destination(client, github_relay, path):
    project_id, requests, _ = github_relay
    response = client.get(
        f"/sandbox/forge/{project_id}/{path}",
        headers={
            "Authorization": "Bearer " + mint_scoped_token(project_id=str(project_id)),
        },
    )
    assert response.status_code == 403
    assert requests == []


@pytest.mark.parametrize("credential", ["invalid", "other-project", "expired"])
def test_github_relay_requires_current_project_authority(
    client, github_relay, credential
):
    project_id, requests, _ = github_relay
    token = (
        mint_scoped_token(project_id=str(uuid.uuid4()))
        if credential == "other-project"
        else mint_scoped_token(project_id=str(project_id), ttl_s=-1)
        if credential == "expired"
        else credential
    )
    response = client.get(
        f"/sandbox/forge/{project_id}/owner/repo.git/info/refs",
        headers={
            "Authorization": "Bearer " + token,
        },
    )
    assert response.status_code == 401
    assert requests == []
