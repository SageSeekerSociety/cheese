"""A repository renamed on GitHub stays connected under its new name (FB-70).

GitHub keeps a renamed repository's id and redirects its old name, but refuses
to mint a token limited to the old name. The fake GitHub below behaves that
way; everything asserted is what a sandbox, a reviewer or the settings page
reads back.
"""

import json
import uuid

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import github_app
from app.domain.project import forge
from app.domain.project.repositories import ProjectGitInstallationRepository
from tests.integration.conftest import open_task, post_project, session_auth_headers

OLD, NEW, REPO_ID, INSTALLATION = "acme/old-name", "acme/new-name", 4242, 77


class FakeGitHub:
    """One repository, renamed from OLD to NEW, reachable through one App."""

    def __init__(self):
        self.name = NEW
        self.mints: list[dict] = []
        self.down = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if self.down:
            return httpx.Response(503, text="unavailable")
        if path == f"/app/installations/{INSTALLATION}":
            return httpx.Response(200, json={"permissions": {"contents": "write"}})
        if path == f"/app/installations/{INSTALLATION}/access_tokens":
            body = json.loads(request.content)
            self.mints.append(body)
            if body.get("repositories") not in (None, [self.name.split("/")[1]]):
                return httpx.Response(
                    422,
                    json={
                        "message": "There is at least one repository that does not "
                        "exist or is not accessible to the parent installation."
                    },
                )
            if body.get("repository_ids") not in (None, [REPO_ID]):
                return httpx.Response(422, json={"message": "no such repository"})
            return httpx.Response(
                201,
                json={
                    "token": f"ghs_{len(self.mints)}",
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            )
        if path.lower() == f"/repos/{OLD}" and self.name != OLD:
            return httpx.Response(
                301,
                headers={"Location": f"https://api.github.com/repositories/{REPO_ID}"},
            )
        if path == f"/repositories/{REPO_ID}" or path.lower() == f"/repos/{self.name}":
            return httpx.Response(200, json={"id": REPO_ID, "full_name": self.name})
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture
def github(monkeypatch, tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = tmp_path / "app.pem"
    pem.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setattr(github_app.settings, "github_app_id", 1)
    monkeypatch.setattr(github_app.settings, "github_app_private_key_path", str(pem))
    monkeypatch.setattr(github_app, "_instances", {})
    # Every look asks GitHub, standing in for the minute between looks passing.
    monkeypatch.setattr(forge, "_FOLLOW_EVERY_S", 0.0)
    fake = FakeGitHub()
    real = httpx.AsyncClient
    monkeypatch.setattr(
        "app.core.forge_http.httpx.AsyncClient",
        lambda **kwargs: real(
            **(kwargs | {"transport": httpx.MockTransport(fake.handle)})
        ),
    )
    return fake


def _bound_project(client, github, *, repository_id, card_repo=OLD):
    """A project bound to OLD, with a room, a task and a card filed under OLD,
    whose repository is then renamed to NEW on GitHub.

    GitHub is unreachable while all that is set up, so nothing is learned about
    the repository before the rename — the binding meets it as it stands.
    """
    github.down = True
    from app.domain.review.models import AcceptCard, AcceptStatus
    from app.domain.room_task.models import Task

    client.headers.update(session_auth_headers("alice"))
    project = post_project(
        client, json={"name": "Renamed", "forge_kind": "github_app"}
    ).json()["data"]["id"]
    assert (
        client.put(
            f"/projects/{project}/upstream", json={"url": f"https://github.com/{OLD}"}
        ).status_code
        == 200
    )

    async def bind():
        async with client.test_factory() as session:
            await ProjectGitInstallationRepository(session).upsert(
                project_id=uuid.UUID(project),
                installation_id=INSTALLATION,
                repo=OLD,
                repository_id=repository_id,
                account="acme",
            )
            await session.commit()

    client.portal.call(bind)
    room = client.post("/topics", json={"project_id": project, "title": "room"}).json()[
        "data"
    ]["id"]
    task = open_task(client, room, "work")["id"]

    async def file_pr():
        async with client.test_factory() as session:
            row = await session.get(Task, uuid.UUID(task))
            row.pr_url = f"https://github.com/{OLD}/pull/7"
            session.add(
                AcceptCard(
                    topic_id=uuid.UUID(room),
                    task_id=uuid.UUID(task),
                    reviewer_handle="alice",
                    status=AcceptStatus.pending,
                    pr_number=7,
                    pr_url=f"https://github.com/{card_repo}/pull/7",
                    pr_repo=card_repo,
                )
            )
            await session.commit()

    client.portal.call(file_pr)
    github.down = False
    return project, room, task


def _task_pr_url(client, task):
    from app.domain.room_task.models import Task

    async def read():
        async with client.test_factory() as session:
            return (await session.get(Task, uuid.UUID(task))).pr_url

    return client.portal.call(read)


def _card_repository(client, task):
    """What the card resolves its PR's repository to when it is next used,
    and the PR link it then carries — or the refusal."""
    from sqlalchemy import select

    from app.core.errors import ValidationError
    from app.domain.review.models import AcceptCard
    from app.domain.review.services import AcceptService
    from app.domain.topic.models import Topic

    async def use():
        async with client.test_factory() as session:
            card = await session.scalar(
                select(AcceptCard).where(AcceptCard.task_id == uuid.UUID(task))
            )
            topic = await session.get(Topic, card.topic_id)
            try:
                found = await AcceptService(session)._pr_repo_of(card, topic)
            except ValidationError as refused:
                return refused
            await session.commit()
            return found, card.pr_repo, card.pr_url

    return client.portal.call(use)


@pytest.mark.parametrize("repository_id", [None, REPO_ID])
def test_a_renamed_repository_keeps_working_under_its_new_name(
    client, github, repository_id
):
    """Bound before ids were kept (None) or after: either way it follows."""
    project, room, task = _bound_project(client, github, repository_id=repository_id)

    metadata = client.get(
        f"/projects/{project}/git/tasks/{task}",
        headers={
            "X-Cheese-Token": mint_scoped_token(project_id=project, topic_id=room)
        },
    )
    assert metadata.status_code == 200, metadata.text
    assert metadata.json()["data"]["forge_repo"] == NEW
    assert metadata.json()["data"]["remote"] == f"https://github.com/{NEW}.git"

    credential = client.get(
        "/sandbox/forge-token",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=project)},
    )
    assert credential.status_code == 200, credential.text
    assert credential.json()["data"]["repo"] == NEW
    assert credential.json()["data"]["token"].startswith("ghs_")
    # The sandbox's token is still limited to this one repository.
    assert github.mints[-1]["repository_ids"] == [REPO_ID]

    connection = client.get(f"/projects/{project}/github/connection").json()["data"]
    assert connection["repo"] == NEW
    assert client.get(f"/projects/{project}/upstream").json()["data"]["url"] == (
        f"https://github.com/{NEW}"
    )
    assert _task_pr_url(client, task) == f"https://github.com/{NEW}/pull/7"
    # The review card filed under the old name is still this repository's.
    assert _card_repository(client, task) == (
        ("acme", "new-name"),
        NEW,
        f"https://github.com/{NEW}/pull/7",
    )


def test_a_card_from_another_repository_is_still_refused(client, github):
    _, _, task = _bound_project(
        client, github, repository_id=REPO_ID, card_repo="acme/unrelated"
    )
    from app.core.errors import ValidationError

    assert isinstance(_card_repository(client, task), ValidationError)


def test_connecting_again_picks_up_a_rename_at_once(client, github):
    project, _, _ = _bound_project(client, github, repository_id=REPO_ID)
    assert (
        client.get(f"/projects/{project}/github/connection").json()["data"]["repo"]
        == NEW
    )

    github.name = "acme/third-name"
    response = client.post(f"/projects/{project}/github/connect")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["repo"] == "acme/third-name"


def test_a_github_outage_serves_the_binding_as_it_is(client, github):
    project, room, task = _bound_project(client, github, repository_id=REPO_ID)
    github.down = True
    metadata = client.get(
        f"/projects/{project}/git/tasks/{task}",
        headers={
            "X-Cheese-Token": mint_scoped_token(project_id=project, topic_id=room)
        },
    )
    assert metadata.status_code == 200, metadata.text
    assert metadata.json()["data"]["forge_repo"] == OLD
