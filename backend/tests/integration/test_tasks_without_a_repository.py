"""Work that writes no code is a task too, in a project with no repository.

The rules a person can state without the code:

- A project with no code repository (a GitHub project not connected yet) can
  have tasks: a person opens one, its AI teammate opens one, and a 讨论串 turns
  into one. Such a task has no branch.
- Its session is told there is no checkout to start in, which is an answer and
  not a failure: it works where its session is.
- It ends with a conclusion, or hands over a file or an address for review,
  which a person accepts. Asking to merge changes is refused with a reason that
  says what to do instead.
- Once the project has a repository, such a task gets its branch the first
  time its session opens it.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import make_transient

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import execution
from app.domain.agent.harness import harness_for
from app.domain.agent.harness.claude_code.remote_execution.runtime import Executor
from app.domain.project.models import ProjectForge
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic
from tests.integration.conftest import (
    join_project_team,
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)
from tests.integration.test_accept_pr import _rendered_head, _rendered_revision
from tests.support.environments import remember_place

ALICE = session_auth_headers("alice")


@pytest.fixture
def workspace(client, monkeypatch, tmp_path):
    """The machine the task's session works on: its room's workspace."""
    executor = object.__new__(Executor)
    executor.env = {"HOME": str(tmp_path / "machine")}
    executor.root = tmp_path / "machine" / "room"
    executor.root.mkdir(parents=True)

    async def call(target, method, params):
        assert target["kind"] == "device" and method == "task_fs"
        return executor.task_fs(params)

    monkeypatch.setattr(execution, "call", call)
    return executor.root


def _project(client, *, forge_kind: str = "github_app") -> tuple[str, str]:
    """A project whose repository is not connected, and a channel in it."""
    project = post_project(
        client, json={"name": "P", "forge_kind": forge_kind}, owner="alice"
    ).json()["data"]
    join_project_team(client, project["id"], "alice")
    room = client.post(
        "/topics", json={"project_id": project["id"], "title": "调研"}, headers=ALICE
    ).json()["data"]["id"]
    return project["id"], room


def _as(client, token: str, method: str, path: str, body: dict | None = None):
    # The session a client carries by default outranks the teammate's
    # credential (`resolve_actor` puts a person first): the teammate speaks here.
    bearer = client.headers.pop("Authorization", None)
    try:
        return client.request(
            method, path, json=body, headers={"Authorization": f"Bearer {token}"}
        )
    finally:
        if bearer is not None:
            client.headers["Authorization"] = bearer


def _teammate(client, project: str, conversation: str, room: str) -> str:
    return mint_scoped_token(
        project_id=project,
        topic_id=conversation,
        agent_handle=room_agent_seat(client, room),
    )


def _task_row(client, task_id: str) -> Task:
    async def read():
        async with client.test_factory() as session:
            return await session.get(Task, uuid.UUID(task_id))

    return client.portal.call(read)


def _opened_by_a_person(client, project: str, room: str) -> str:
    made = client.post(f"/topics/{room}/tasks", json={"title": "调研"}, headers=ALICE)
    assert made.status_code == 200, made.text
    return made.json()["data"]["id"]


def test_every_way_of_opening_a_task_works_without_a_repository(client, stub_hooks):
    project, room = _project(client)
    seat = room_agent_seat(client, room)

    by_person = _opened_by_a_person(client, project, room)

    post_message(client, room, "alice", {"content": f"<@{seat}> 查一下竞品"})
    by_teammate = _as(
        client,
        _teammate(client, project, room, room),
        "POST",
        f"/topics/{room}/teammate-tasks",
        {"title": "竞品调研", "summary": "查一下竞品", "owner_handle": "alice"},
    )
    assert by_teammate.status_code == 200, by_teammate.text

    root = post_message(client, room, "alice", {"content": "整理一下这周的资料"})
    turned = client.post(f"/blocks/{root['id']}/upgrade", headers=ALICE)
    assert turned.status_code == 200, turned.text

    for task_id in (
        by_person,
        by_teammate.json()["data"]["id"],
        turned.json()["data"]["id"],
    ):
        row = _task_row(client, task_id)
        assert row.branch_name is None
        assert row.base_branch is None


def test_its_session_is_told_there_is_no_checkout(client, stub_hooks):
    project, room = _project(client)
    task = _opened_by_a_person(client, project, room)

    opened = _as(
        client,
        _teammate(client, project, task, room),
        "POST",
        f"/projects/{project}/git/tasks/{task}",
    )

    assert opened.status_code == 404, opened.text
    assert opened.json()["error"]["i18n"]["key"] == "taskWithoutRepository"
    assert _task_row(client, task).branch_name is None


def test_it_ends_with_a_conclusion(client, stub_hooks):
    """Research ends in a conclusion, once its owner says it is over: a
    teammate closing it on its own word is refused (`review.close_grounds`)."""
    project, room = _project(client)
    task = _opened_by_a_person(client, project, room)
    teammate = _teammate(client, project, task, room)
    conclusion = {"conclusion": "三家竞品都不支持离线，结论写进了任务文档"}

    refused = _as(client, teammate, "POST", f"/topics/{task}/close", conclusion)
    assert refused.status_code == 409, refused.text
    said = post_message(client, task, "alice", {"content": "调研够了，结束吧"})

    closed = _as(
        client,
        teammate,
        "POST",
        f"/topics/{task}/close",
        {**conclusion, "owner_message_id": said["id"]},
    )

    assert closed.status_code == 200, closed.text
    assert _task_row(client, task).status == "closed"


def test_asking_to_merge_is_refused_with_what_to_do_instead(client, stub_hooks):
    project, room = _project(client)
    task = _opened_by_a_person(client, project, room)

    filed = _as(
        client,
        _teammate(client, project, task, room),
        "POST",
        f"/topics/{task}/accept-card",
        {"change_subject": "docs: the survey", "reviewer_handle": "alice"},
    )

    assert filed.status_code == 422, filed.text
    error = filed.json()["error"]
    assert error["i18n"]["key"] == "taskNoRepositoryToMerge"
    assert "deliver" in error["message"]


def _place(client, task_id: str) -> None:
    """The task's own session takes the machine its room works on."""

    async def place():
        async with client.test_factory() as session:
            task = await session.get(Task, uuid.UUID(task_id))
            room = await session.get(Topic, task.room_id)
            await remember_place(
                session,
                conversation_id=task.id,
                agent_handle="cheese",
                environment={"kind": "device"},
                runtime_location={
                    "device_id": "test-device",
                    "channel": "central",
                    "resource_id": str(room.resource_id or room.id),
                },
                harness=harness_for(None),
            )
            await session.commit()

    asyncio.run(place())


def test_a_file_it_hands_over_is_reviewed_and_accepted(client, stub_hooks, workspace):
    project, room = _project(client)
    task = _opened_by_a_person(client, project, room)
    _place(client, task)
    (workspace / "survey.md").write_text("# 竞品调研\n")

    filed = _as(
        client,
        _teammate(client, project, task, room),
        "POST",
        f"/topics/{task}/accept-card",
        {
            "change_subject": "docs: the survey",
            "reviewer_handle": "alice",
            "deliver": "survey.md",
            "new_artifact": "竞品调研",
            "about": "给产品组看的竞品对比",
        },
    )
    assert filed.status_code == 200, filed.text
    card = filed.json()["data"]["id"]

    accepted = client.post(
        f"/accept-cards/{card}/accept",
        json={
            "decided_by": "alice",
            "head_sha": _rendered_head(client, card),
            "deliverable_revision": _rendered_revision(client, card),
        },
        headers=ALICE,
    )

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["status"] == "accepted"


def test_a_task_opened_with_a_repository_has_its_branch(client, stub_hooks):
    project, room = _project(client, forge_kind="forgejo")
    task = _opened_by_a_person(client, project, room)

    row = _task_row(client, task)
    assert row.branch_name is not None
    assert row.base_branch == "main"


def test_a_task_gets_its_branch_once_the_project_has_a_repository(client, stub_hooks):
    project, room = _project(client, forge_kind="forgejo")

    async def unbind():
        async with client.test_factory() as session:
            removed = (
                await session.execute(
                    delete(ProjectForge)
                    .where(ProjectForge.project_id == uuid.UUID(project))
                    .returning(ProjectForge)
                )
            ).scalar_one()
            await session.commit()
            make_transient(removed)
            return removed

    binding = client.portal.call(unbind)
    task = _opened_by_a_person(client, project, room)
    assert _task_row(client, task).branch_name is None

    async def bind_again():
        async with client.test_factory() as session:
            session.add(binding)
            await session.commit()

    client.portal.call(bind_again)
    opened = _as(
        client,
        _teammate(client, project, task, room),
        "POST",
        f"/projects/{project}/git/tasks/{task}",
    )

    assert opened.status_code == 200, opened.text
    branch = opened.json()["data"]["branch"]
    assert branch
    assert _task_row(client, task).branch_name == branch
