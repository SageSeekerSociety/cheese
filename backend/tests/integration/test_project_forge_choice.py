"""A new project may wait for GitHub without creating another authority."""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.core.errors import GatewayUnavailableError
from app.domain.project import forge
from app.domain.project.models import Project, ProjectForge
from app.domain.project.repositories import ProjectGitInstallationRepository
from scripts import migrate_forge
from tests.integration.conftest import session_auth_headers


@pytest.mark.parametrize("choice", [None, "forgejo", "github_app"])
def test_creation_honors_repository_choice(client, monkeypatch, choice):
    provision = AsyncMock()
    monkeypatch.setattr(forge, "provision_repository", provision)
    body = {"name": "Repository choice", "owner_handle": "alice"}
    if choice:
        body["forge_kind"] = choice
    response = client.post("/projects", json=body)
    assert response.status_code == 200, response.text
    project_id = uuid.UUID(response.json()["data"]["id"])
    expected = choice or "forgejo"

    async def stored():
        async with client.test_factory() as session:
            project = await session.get(Project, project_id)
            assert project.settings["forge_kind"] == expected

    client.portal.call(stored)
    assert provision.await_count == (0 if expected == "github_app" else 1)


def test_github_choice_can_connect_without_cross_forge_migration(client):
    response = client.post(
        "/projects",
        json={
            "name": "Existing GitHub work",
            "owner_handle": "alice",
            "forge_kind": "github_app",
        },
    )
    assert response.status_code == 200, response.text
    project_id = uuid.UUID(response.json()["data"]["id"])

    async def connect():
        async with client.test_factory() as session:
            assert await forge.binding_for_project(project_id, session) is None
            with pytest.raises(GatewayUnavailableError, match="连接 GitHub"):
                await forge.provision_repository(project_id, session)
            await ProjectGitInstallationRepository(session).upsert(
                project_id=project_id,
                installation_id=42,
                repo="example/existing",
                account="example",
            )
            await session.commit()
        async with client.test_factory() as session:
            binding = await forge.provision_repository(project_id, session)
            assert binding.kind == "github_app"
            assert binding.url == "https://github.com/example/existing.git"

    client.portal.call(connect)


def test_migration_leaves_pending_github_choice_unprovisioned(
    client, monkeypatch, tmp_path
):
    response = client.post(
        "/projects", json={"name": "Awaiting GitHub", "forge_kind": "github_app"}
    )
    assert response.status_code == 200, response.text
    project_id = uuid.UUID(response.json()["data"]["id"])
    monkeypatch.setattr(migrate_forge, "async_session_factory", client.test_factory)

    async def migrate():
        assert (
            await migrate_forge.migrate(project_id, tmp_path, apply=True) == "skipped"
        )
        async with client.test_factory() as session:
            assert await forge.binding_for_project(project_id, session) is None

    client.portal.call(migrate)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["forgejo", "github_app"])
def test_forge_status_uses_binding_and_excludes_credentials(client, kind):
    response = client.post(
        "/projects",
        json={
            "name": "Repository status",
            "owner_handle": "alice",
            "forge_kind": "github_app",
        },
    )
    project_id = response.json()["data"]["id"]
    route = f"/projects/{project_id}/forge"
    headers = session_auth_headers("alice")
    pending = client.get(route, headers=headers)
    assert pending.status_code == 200
    assert pending.json()["data"] == {
        "kind": "github_app",
        "connected": False,
        "repo": None,
        "url": None,
    }

    async def bind():
        async with client.test_factory() as session:
            session.add(
                ProjectForge(
                    project_id=uuid.UUID(project_id),
                    kind=kind,
                    url="https://forge.example/owner/project.git",
                    api_url="http://internal-forge/api/v1",
                    repo="owner/project",
                    account_password="private-account-credential",
                )
            )
            await session.commit()

    client.portal.call(bind)
    connected = client.get(route, headers=headers)
    assert connected.status_code == 200
    assert connected.json()["data"] == {
        "kind": kind,
        "connected": True,
        "repo": "owner/project",
        "url": "https://forge.example/owner/project",
    }
    denied = client.get(route, headers=session_auth_headers("outsider"))
    assert denied.status_code in (401, 403, 404)
    refused = client.put(
        f"/projects/{project_id}/upstream",
        headers=headers,
        json={"url": "https://github.com/another/repository"},
    )
    assert refused.status_code == 409, refused.text


def test_requester_credit_setting_can_override_and_restore_deployment_default(
    client, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "forge_attribution_default", True)
    response = client.post(
        "/projects",
        json={
            "name": "Credit policy",
            "owner_handle": "alice",
            "forge_kind": "github_app",
        },
    )
    project_id = response.json()["data"]["id"]
    route = f"/projects/{project_id}/forge-attribution"
    headers = session_auth_headers("alice")
    assert client.get(route, headers=headers).json()["data"] == {
        "requester_coauthor": None,
        "effective": True,
        "deployment_default": True,
    }
    for override, effective in ((False, False), (True, True), (None, True)):
        saved = client.put(
            route, headers=headers, json={"requester_coauthor": override}
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"] == {
            "requester_coauthor": override,
            "effective": effective,
            "deployment_default": True,
        }
        assert client.get(route, headers=headers).json() == saved.json()
    refused = client.put(
        route,
        headers=session_auth_headers("outsider"),
        json={"requester_coauthor": False},
    )
    assert refused.status_code in (401, 403, 404)
    assert client.get(route, headers=headers).json()["data"]["effective"] is True


def test_github_repository_selection_uses_project_settings_without_local_git(
    client, monkeypatch
):
    from app.api.routes.github_install import _upstream_repo
    from app.domain.workspace import service as ws

    def forbidden(*args, **kwargs):
        raise AssertionError("repository selection must not create a local git store")

    monkeypatch.setattr(ws, "ensure_repo", forbidden)
    response = client.post(
        "/projects",
        json={
            "name": "Select repository",
            "owner_handle": "alice",
            "forge_kind": "github_app",
        },
    )
    assert response.status_code == 200, response.text
    project_id = response.json()["data"]["id"]
    route = f"/projects/{project_id}/upstream"
    headers = session_auth_headers("alice")
    saved = client.put(
        route, headers=headers, json={"url": "https://github.com/Example/Existing.git"}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["url"] == "https://github.com/Example/Existing"
    assert client.get(route, headers=headers).json() == saved.json()

    async def selected():
        async with client.test_factory() as session:
            return await _upstream_repo(uuid.UUID(project_id), session)

    assert client.portal.call(selected) == "example/existing"
    invalid = client.put(route, headers=headers, json={"url": "/private/local-repo"})
    assert invalid.status_code == 422
    assert client.post(route + "/sync", headers=headers).status_code == 404
    cleared = client.put(route, headers=headers, json={"url": ""})
    assert cleared.status_code == 200
    assert client.portal.call(selected) is None
