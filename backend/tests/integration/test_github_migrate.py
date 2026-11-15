"""POST /projects/{id}/github/migrate: a platform-hosted project moves to GitHub.

The git copy (`github_migration.mirror_to_github`, tested on real repositories
in ``tests/unit/test_github_migration.py``) and GitHub itself are faked at the
module seams; these tests pin what the route decides around them: which
projects may move, what is refused before anything is pushed, and what the
project is bound to afterwards.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update

from app.domain.project import forge
from app.domain.project.models import Project, ProjectForge
from app.domain.project.repositories import ProjectGitInstallationRepository
from app.domain.project.starter import Remote
from app.domain.room_task.models import Task
from tests.integration.conftest import open_task, post_project, session_auth_headers

pytestmark = pytest.mark.usefixtures("github_binding_user")

_REPO = {
    "id": 9001,
    "full_name": "alice/homework",
    "owner": {"login": "alice"},
    "permissions": {"push": True},
}


def _hosted_project(client, monkeypatch) -> tuple[uuid.UUID, str]:
    """A project on the platform's Forgejo, as every 题目 claim makes one."""
    monkeypatch.setattr(forge, "provision_repository", AsyncMock())
    response = post_project(client, json={"name": "Claimed task"}, owner="alice")
    assert response.status_code == 200, response.text
    project_id = uuid.UUID(response.json()["data"]["id"])
    root = response.json()["data"]["root_topic_id"]

    async def bind():
        async with client.test_factory() as session:
            session.add(
                ProjectForge(
                    project_id=project_id,
                    kind="forgejo",
                    repo=f"cheese-{project_id.hex}/project",
                    url="http://forgejo.invalid/x/project.git",
                    api_url="http://forgejo.invalid/api/v1",
                    default_branch="main",
                    account_password="sealed",
                )
            )
            await session.commit()

    client.portal.call(bind)
    return project_id, root


class _Minter:
    async def write_token(self):
        return "installation-token", "2099-01-01T00:00:00+00:00"


def _github_world(monkeypatch, *, repos: list[dict]):
    from app.api import github_migration
    from app.api.routes import github_install

    copied: list[tuple[str, str]] = []

    async def installations(_token):
        return [{"id": 77}]

    async def installation_repos(_token, installation_id):
        return repos if installation_id == 77 else []

    def mirror(source, destination, token):
        copied.append((destination, token))
        return {"refs/heads/main": "0" * 40}

    monkeypatch.setattr(github_install, "list_user_installations", installations)
    monkeypatch.setattr(
        github_install, "fetch_user_installation_repos", installation_repos
    )
    monkeypatch.setattr(github_migration, "mirror_to_github", mirror)
    monkeypatch.setattr(
        github_migration, "github_app_tokens_for_repo", lambda *a, **k: _Minter()
    )
    monkeypatch.setattr(
        github_migration,
        "starter_remote",
        AsyncMock(return_value=Remote(url="x", username="u", password="p")),
    )
    return copied


def _migrate(client, project_id, url="https://github.com/alice/homework"):
    return client.post(
        f"/projects/{project_id}/github/migrate",
        json={"url": url},
        headers=session_auth_headers("alice"),
    )


def _task_with_a_forgejo_pr(client, room) -> uuid.UUID:
    task_id = uuid.UUID(open_task(client, room, "写作业", owner="alice")["id"])

    async def record_pr():
        async with client.test_request_factory() as session:
            await session.execute(
                update(Task)
                .where(Task.id == task_id)
                .values(pr_number=3, pr_url="http://forgejo.invalid/x/project/pulls/3")
            )
            await session.commit()

    client.portal.call(record_pr)
    return task_id


def test_a_hosted_project_moves_and_is_bound_to_github(client, monkeypatch):
    copied = _github_world(monkeypatch, repos=[_REPO])
    project_id, room = _hosted_project(client, monkeypatch)
    task_id = _task_with_a_forgejo_pr(client, room)

    response = _migrate(client, project_id)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["connected"] is True and data["repo"] == "alice/homework"
    assert data["reopened_prs"] == 1
    assert copied == [("https://github.com/alice/homework.git", "installation-token")]

    async def bound():
        async with client.test_factory() as session:
            binding = await forge.binding_for_project(project_id, session)
            assert binding.kind == "github_app"
            assert binding.repo == "alice/homework"
            assert binding.account_password is None
            project = await session.get(Project, project_id)
            assert project.settings["forge_kind"] == "github_app"
            installation = await ProjectGitInstallationRepository(
                session
            ).get_by_project(project_id)
            assert installation.installation_id == 77
            # The draft sweep opens the task's PR again, on GitHub.
            task = await session.get(Task, task_id)
            assert (task.pr_number, task.pr_url) == (None, None)

    client.portal.call(bound)


def test_a_card_waiting_for_review_refuses_before_anything_is_pushed(
    client, monkeypatch
):
    from app.domain.review.services import AcceptService

    copied = _github_world(monkeypatch, repos=[_REPO])
    project_id, _ = _hosted_project(client, monkeypatch)
    monkeypatch.setattr(
        AcceptService, "open_pr_card_ids", AsyncMock(return_value=[uuid.uuid4()])
    )

    response = _migrate(client, project_id)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["i18n"]["key"] == "githubMigrationCardsPending"
    assert copied == []

    async def still_hosted():
        async with client.test_factory() as session:
            binding = await forge.binding_for_project(project_id, session)
            assert binding.kind == "forgejo"

    client.portal.call(still_hosted)


def test_a_repository_the_app_cannot_reach_yet_asks_for_the_installation(
    client, monkeypatch
):
    from app.api.routes import github_install

    copied = _github_world(monkeypatch, repos=[])
    monkeypatch.setattr(
        github_install, "_install_url", AsyncMock(return_value="https://install")
    )
    project_id, _ = _hosted_project(client, monkeypatch)

    response = _migrate(client, project_id)

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "connected": False,
        "install_url": "https://install",
    }
    assert copied == []

    async def target_remembered():
        async with client.test_factory() as session:
            project = await session.get(Project, project_id)
            assert (
                project.settings["github_repository_url"]
                == "https://github.com/alice/homework"
            )
            assert project.settings["forge_kind"] == "forgejo"

    client.portal.call(target_remembered)


def test_a_github_project_has_nothing_to_move(client, monkeypatch):
    _github_world(monkeypatch, repos=[_REPO])
    response = post_project(
        client, json={"name": "On GitHub", "forge_kind": "github_app"}, owner="alice"
    )
    project_id = response.json()["data"]["id"]

    response = _migrate(client, project_id)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["i18n"]["key"] == "githubMigrationNotHosted"


def test_a_read_only_repository_is_refused(client, monkeypatch):
    read_only = {**_REPO, "permissions": {"push": False}}
    copied = _github_world(monkeypatch, repos=[read_only])
    project_id, _ = _hosted_project(client, monkeypatch)

    response = _migrate(client, project_id)

    assert response.status_code == 403, response.text
    assert copied == []
