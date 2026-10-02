"""A slow forge fails a project creation honestly, and leaves nothing behind.

The forge here is a stand-in for Forgejo's HTTP API that keeps what it was
asked to create. Like the real one, it keeps a repository whose creation the
client gave up waiting for.
"""

import base64
import functools
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.domain.project import forge
from app.domain.project.forge import provision_repository as real_provision
from app.domain.project.models import Project
from tests.integration.conftest import post_project

API = "http://forgejo.test/api/v1"


@dataclass
class FakeForge:
    #: account name -> when it was created
    accounts: dict[str, datetime] = field(default_factory=dict)
    repositories: set[str] = field(default_factory=set)
    stall_repository_creation: bool = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        if request.method == "POST" and path == "/admin/users":
            login = json.loads(request.content)["username"]
            if login in self.accounts:
                return httpx.Response(422, json={"message": "user already exists"})
            self.accounts[login] = datetime.now(UTC)
            return httpx.Response(201, json={"login": login})
        if request.method == "GET" and path == "/admin/users":
            if request.url.params.get("page", "1") != "1":
                return httpx.Response(200, json=[])
            return httpx.Response(
                200,
                json=[
                    {"login": login, "created": created.isoformat()}
                    for login, created in self.accounts.items()
                ],
            )
        if request.method == "DELETE" and path.startswith("/admin/users/"):
            login = path.rsplit("/", 1)[1]
            self.accounts.pop(login, None)
            self.repositories.discard(login)
            return httpx.Response(204)
        owner = _basic_user(request)
        if request.method == "GET" and path == f"/repos/{owner}/project":
            if owner in self.repositories:
                return httpx.Response(200, json={"default_branch": "main"})
            return httpx.Response(404)
        if request.method == "POST" and path == "/user/repos":
            if owner in self.repositories:
                return httpx.Response(409)
            self.repositories.add(owner)
            if self.stall_repository_creation:
                raise httpx.ReadTimeout("forge did not answer", request=request)
            return httpx.Response(201, json={"default_branch": "main"})
        return httpx.Response(500, json={"message": f"unexpected {request}"})

    def age(self, login: str, by: timedelta) -> None:
        self.accounts[login] -= by


def _basic_user(request: httpx.Request) -> str:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme != "Basic":
        return ""
    return base64.b64decode(value).decode().split(":", 1)[0]


@pytest.fixture
def fake_forge(client, monkeypatch) -> FakeForge:
    fake = FakeForge()
    transport = httpx.MockTransport(fake.handle)
    monkeypatch.setattr(settings, "forgejo_url", "http://forgejo.test")
    monkeypatch.setattr(settings, "forgejo_api_url", API)
    monkeypatch.setattr(settings, "forgejo_admin_token", "admin-token")
    monkeypatch.setattr(settings, "forge_webhook_url", "")
    monkeypatch.setattr(
        forge,
        "provision_repository",
        functools.partial(real_provision, transport=transport),
    )
    fake.transport = transport  # type: ignore[attr-defined]
    return fake


def _projects(client, project_id: uuid.UUID) -> list[Project]:
    async def read():
        async with client.test_factory() as session:
            return list(
                await session.scalars(select(Project).where(Project.id == project_id))
            )

    return client.portal.call(read)


def _sweep(client, fake: FakeForge) -> dict[str, int]:
    return client.portal.call(
        functools.partial(
            forge.sweep_orphan_accounts,
            client.test_factory,
            transport=fake.transport,  # type: ignore[attr-defined]
        )
    )


def test_a_timed_out_creation_answers_honestly_and_its_retry_reuses_the_account(
    client, fake_forge
):
    project_id = uuid.uuid4()
    body = {"id": str(project_id), "name": "Made while the forge was slow"}

    fake_forge.stall_repository_creation = True
    response = post_project(client, json=body)
    assert response.status_code == 503, response.text
    assert response.json()["message"] == "代码仓库服务响应超时，请稍后重试"
    assert _projects(client, project_id) == []

    fake_forge.stall_repository_creation = False
    response = post_project(client, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["id"] == str(project_id)
    assert list(fake_forge.accounts) == [f"cheese-{project_id.hex}"]
    assert len(_projects(client, project_id)) == 1


def test_a_creation_repeated_after_it_succeeded_returns_the_same_project(
    client, fake_forge
):
    project_id = uuid.uuid4()
    body = {"id": str(project_id), "name": "Answer lost on the way back"}
    first = post_project(client, json=body)
    again = post_project(client, json=body)
    assert first.status_code == again.status_code == 200, again.text
    assert again.json()["data"]["id"] == str(project_id)
    assert list(fake_forge.accounts) == [f"cheese-{project_id.hex}"]

    stranger = post_project(client, json={**body}, owner="mallory")
    assert stranger.status_code == 409, stranger.text


def test_the_sweep_removes_what_an_abandoned_creation_left_and_nothing_else(
    client, fake_forge
):
    fake_forge.accounts["cheese-platform"] = datetime.now(UTC)
    kept = post_project(client, json={"name": "A project that exists"})
    assert kept.status_code == 200, kept.text
    kept_account = f"cheese-{uuid.UUID(kept.json()['data']['id']).hex}"

    fake_forge.stall_repository_creation = True
    abandoned, recent = uuid.uuid4(), uuid.uuid4()
    for project_id in (abandoned, recent):
        response = post_project(client, json={"id": str(project_id), "name": "Gave up"})
        assert response.status_code == 503, response.text
    for login in ("cheese-platform", kept_account, f"cheese-{abandoned.hex}"):
        fake_forge.age(login, timedelta(hours=2))

    assert _sweep(client, fake_forge) == {"deleted": 1, "failed": 0}
    assert set(fake_forge.accounts) == {
        "cheese-platform",
        kept_account,
        # Too recent to tell from a creation still running.
        f"cheese-{recent.hex}",
    }
    assert f"cheese-{abandoned.hex}" not in fake_forge.repositories
