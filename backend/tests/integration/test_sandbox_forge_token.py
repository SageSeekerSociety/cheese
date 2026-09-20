"""Native CLI credentials name their project repository and actual grants."""

import uuid
from types import SimpleNamespace

import pytest

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.github_app import GitHubAppError


@pytest.fixture
def configured(monkeypatch):
    project_id = uuid.uuid4()
    binding = SimpleNamespace(
        kind="github_app",
        repo="acme/widgets",
        url="https://github.com/acme/widgets.git",
        api_url="https://api.github.com",
    )
    permissions = {"contents": "write", "workflows": "write", "issues": "read"}

    async def binding_for_project(identifier, session):
        assert identifier == project_id
        return binding

    class Minter:
        async def installation_token(self):
            return "invocation-credential", "2026-09-20T10:00:00Z"

        async def granted_permissions(self):
            return permissions

    minter = Minter()

    async def tokens_for_project(identifier, session):
        assert identifier == project_id
        return minter

    monkeypatch.setattr(
        "app.domain.project.forge.binding_for_project", binding_for_project
    )
    monkeypatch.setattr(
        "app.domain.project.forge.tokens_for_project", tokens_for_project
    )
    return (
        binding,
        minter,
        permissions,
        {"X-Cheese-Token": mint_scoped_token(project_id=str(project_id))},
    )


@pytest.mark.parametrize("kind", ["github_app", "forgejo"])
def test_token_identifies_provider_repository_and_expiry(client, configured, kind):
    binding, _, permissions, headers = configured
    binding.kind = kind
    if kind == "forgejo":
        binding.url = "https://forge.invalid/acme/widgets.git"
        binding.api_url = "https://forge.invalid/api/v1"
    response = client.get("/sandbox/forge-token", headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["kind"] == kind
    assert data["token"] == "invocation-credential"
    assert data["repo"] == "acme/widgets"
    assert data["url"] == binding.url
    assert data["api_url"] == binding.api_url
    assert data["username"] == ("x-access-token" if kind == "github_app" else "acme")
    assert data["expiry_enforcement"] == (
        "provider" if kind == "github_app" else "platform_revocation"
    )
    assert data["expires_at"] == "2026-09-20T10:00:00Z"
    assert data["permissions"] == "contents: write, issues: read, workflows: write"
    assert response.headers["Cache-Control"] == "no-store"
    permissions.clear()
    permissions["checks"] = "read"
    assert (
        client.get("/sandbox/forge-token", headers=headers).json()["data"][
            "permissions"
        ]
        == "checks: read"
    )


def test_unscoped_caller_is_rejected(client):
    response = client.get(
        "/sandbox/forge-token", headers={"X-Cheese-Token": "nonsense"}
    )
    assert response.status_code == 401


def test_missing_credentials_is_actionable_service_error(
    client, configured, monkeypatch
):
    async def unavailable(*args):
        return None

    monkeypatch.setattr("app.domain.project.forge.tokens_for_project", unavailable)
    response = client.get("/sandbox/forge-token", headers=configured[3])
    assert response.status_code == 503
    assert "代码托管凭据尚未配置" in response.json()["message"]


def test_provider_failure_does_not_expose_credentials(client, configured, monkeypatch):
    async def failed():
        raise GitHubAppError("provider failure containing a secret")

    monkeypatch.setattr(configured[1], "installation_token", failed)
    response = client.get("/sandbox/forge-token", headers=configured[3])
    assert response.status_code == 503
    assert "暂时无法签发" in response.json()["message"]
    assert "secret" not in response.text


def test_retired_github_token_route_is_absent(client, configured):
    assert client.get("/sandbox/github-token", headers=configured[3]).status_code == 404
