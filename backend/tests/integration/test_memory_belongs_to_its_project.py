"""The memory page is a project's own: who may read it, and who may prune it.

``GET /memory`` and ``DELETE /memory/{id}`` once took no credential at all, so
anyone who could reach the API listed any project's agent memory — including
what its agents had noted about individual people — and deleted entries by id.
These tests pin the rules that replaced that:

- nobody signed in reads or prunes anything;
- a project's memory is read by the people who may read the project;
- what an agent noted about a person is read by that person;
- an entry is pruned by someone the listing would show it to, and a refused
  prune answers exactly what an unknown id answers.
"""

import asyncio
import uuid

from app.domain.agent_instance.services import AgentInstanceService
from app.domain.memory.models import MemoryScope, user_scope_id
from app.domain.memory.store import DbMemoryStore
from app.domain.project.services import ProjectService
from tests.conftest import seed_user
from tests.integration.conftest import (
    join_project_team,
    new_project,
    session_auth_headers,
)

# The harness sends the global sandbox token on every request; a caller in
# these tests presents only what it names.
NO_CREDENTIAL = {"X-Cheese-Token": ""}


def _as(handle: str) -> dict[str, str]:
    return {**NO_CREDENTIAL, **session_auth_headers(handle)}


def _agent_fact(client, project_id: str, fact: str) -> None:
    """The project's own 芝士 remembers ``fact`` in its pool."""
    r = client.post(f"/projects/{project_id}/memory", json={"content": fact})
    assert r.status_code == 200, r.text


def _fact_about(client, project_id: str, person: str, fact: str) -> None:
    """The project's own 芝士 notes ``fact`` about ``person``.

    Written by key: the writing side (a private chat's ``cheese_remember``) has
    tests of its own, and these are about who reads and prunes the result."""

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = await ProjectService(s).get_or_404(uuid.UUID(project_id))
            agent = await AgentInstanceService(s).for_project(project)
            await DbMemoryStore(s).remember(
                MemoryScope.user,
                user_scope_id(project.id, agent.handle, person),
                fact,
            )
            await s.commit()

    asyncio.run(_seed())


def _list(client, project_id: str, headers: dict, **params):
    return client.get(
        "/memory", params={"project_id": project_id, **params}, headers=headers
    )


def _contents(response) -> set[str]:
    assert response.status_code == 200, response.text
    return {e["content"] for e in response.json()["data"]["data"]}


def _id_of(client, project_id: str, handle: str, content: str, **params) -> str:
    rows = _list(client, project_id, _as(handle), **params).json()["data"]["data"]
    return next(e["id"] for e in rows if e["content"] == content)


def _project_with_memory(client) -> str:
    """alice owns it, bob is on its team; its 芝士 remembered one thing about
    the project and one about each of them."""
    project_id = new_project(client, owner="alice")["id"]
    join_project_team(client, project_id, "bob")
    _agent_fact(client, project_id, "部署脚本在 deploy/deploy.sh")
    _fact_about(client, project_id, "alice", "alice 要结论在最前面")
    _fact_about(client, project_id, "bob", "bob 周末不看消息")
    return project_id


def test_nobody_signed_in_reads_or_prunes_anything(client):
    project_id = _project_with_memory(client)
    entry = _id_of(client, project_id, "alice", "部署脚本在 deploy/deploy.sh")

    assert _list(client, project_id, NO_CREDENTIAL).status_code == 401
    assert (
        _list(client, project_id, NO_CREDENTIAL, user_handle="alice").status_code == 401
    )
    assert client.delete(f"/memory/{entry}", headers=NO_CREDENTIAL).status_code == 401
    # Answered before the id is looked at, so it says nothing about the id.
    unknown = client.delete(f"/memory/{uuid.uuid4()}", headers=NO_CREDENTIAL)
    assert unknown.status_code == 401
    assert "部署脚本在 deploy/deploy.sh" in _contents(
        _list(client, project_id, _as("alice"))
    )


def test_an_outsider_reads_nothing_of_another_projects_memory(client):
    project_id = _project_with_memory(client)

    assert _list(client, project_id, _as("mallory")).status_code == 403
    assert (
        _list(client, project_id, _as("mallory"), user_handle="alice").status_code
        == 403
    )


def test_an_outsiders_prune_is_answered_like_an_unknown_id(client):
    project_id = _project_with_memory(client)
    entry = _id_of(client, project_id, "alice", "部署脚本在 deploy/deploy.sh")

    refused = client.delete(f"/memory/{entry}", headers=_as("mallory"))
    unknown = client.delete(f"/memory/{uuid.uuid4()}", headers=_as("mallory"))

    assert refused.status_code == unknown.status_code == 404
    assert refused.json() == unknown.json()
    assert "部署脚本在 deploy/deploy.sh" in _contents(
        _list(client, project_id, _as("alice"))
    )


def test_a_member_reads_and_prunes_the_projects_memory(client):
    project_id = _project_with_memory(client)

    assert _contents(_list(client, project_id, _as("bob"))) == {
        "部署脚本在 deploy/deploy.sh"
    }
    entry = _id_of(client, project_id, "bob", "部署脚本在 deploy/deploy.sh")
    assert client.delete(f"/memory/{entry}", headers=_as("bob")).status_code == 200
    assert _contents(_list(client, project_id, _as("alice"))) == set()


def test_what_was_noted_about_a_person_is_read_by_that_person(client):
    project_id = _project_with_memory(client)

    assert _contents(_list(client, project_id, _as("alice"), user_handle="alice")) == {
        "部署脚本在 deploy/deploy.sh",
        "alice 要结论在最前面",
    }
    # bob is in the project, and still not the person those notes are about.
    assert _list(client, project_id, _as("bob"), user_handle="alice").status_code == 403


def test_a_note_about_a_person_is_pruned_by_that_person_alone(client):
    project_id = _project_with_memory(client)
    about_alice = _id_of(
        client, project_id, "alice", "alice 要结论在最前面", user_handle="alice"
    )

    refused = client.delete(f"/memory/{about_alice}", headers=_as("bob"))
    unknown = client.delete(f"/memory/{uuid.uuid4()}", headers=_as("bob"))
    assert refused.status_code == unknown.status_code == 404
    assert refused.json() == unknown.json()

    assert (
        client.delete(f"/memory/{about_alice}", headers=_as("alice")).status_code == 200
    )
    assert "alice 要结论在最前面" not in _contents(
        _list(client, project_id, _as("alice"), user_handle="alice")
    )


def _project_credential(client, project_id: str, steward: str) -> str:
    """The project's agent credential, with the agent granted a member row — a
    credential by itself grants no role (see test_project_agent_credential)."""
    owner = {**NO_CREDENTIAL, "Authorization": f"Bearer {seed_user(client, steward)}"}
    issued = client.post(
        f"/projects/{project_id}/agent-credential", json={}, headers=owner
    )
    assert issued.status_code == 200, issued.text
    added = client.post(
        f"/projects/{project_id}/members",
        json={"user_handle": issued.json()["data"]["agent_handle"]},
        headers=owner,
    )
    assert added.status_code == 200, added.text
    return issued.json()["data"]["token"]


def test_the_projects_agent_reads_its_own_projects_memory_and_no_other(client):
    project_id = _project_with_memory(client)
    other_id = new_project(client, name="Q", owner="carol")["id"]
    _agent_fact(client, other_id, "Q 的事")
    token = _project_credential(client, project_id, "alice")

    listed = _list(client, project_id, {"X-Cheese-Token": token})
    assert _contents(listed) == {"部署脚本在 deploy/deploy.sh"}
    assert {"updated_at", "scope_id"} <= set(listed.json()["data"]["data"][0])
    assert _list(client, other_id, {"X-Cheese-Token": token}).status_code == 403
