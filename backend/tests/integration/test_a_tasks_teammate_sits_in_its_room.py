"""An AI teammate given a task works and speaks in the task's room as itself.

The rules, stated before the code:

- giving a task to an AI teammate, when the task is made or later, seats that
  teammate in the task's room, so the room's roster shows who is at work in it;
- someone who could not add that teammate to the room cannot give it the task
  either: the change is refused and the task keeps the teammate it had;
- what a task's turn writes is signed by the teammate working the task, never
  by the room's own agent; a teammate not in the room does not work there, so
  a task given to one before giving one seated it, or whose teammate was taken
  out of the room since, is refused rather than worked under another's seat.

On dev (2026-10-12, FB-99) a task was given to 芝士派, which sat in no seat of
the task's room: its turns ran under the room's seat, which was 芝士's, so it
posted as 芝士 and shared 芝士's machine and credential.
"""

import uuid

import pytest
from sqlalchemy import text

from app.api import deps as session_turn_deps
from app.core.errors import UnprocessableEntityError
from app.domain.agent.chat import ChatService
from tests.conftest import StubChannel, settle_turn, stub_compute
from tests.integration.conftest import (
    join_project_team,
    open_task,
    post_message,
    post_project,
    session_auth_headers,
)


def _project_with_a_second_teammate(client) -> tuple[dict, str, str]:
    """A project, a channel, and a second AI teammate seated in no room."""
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    channel = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "可观测性"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    made = client.post(f"/projects/{project['id']}/agents", json={"handle": "pai"})
    assert made.status_code == 200, made.text
    return project, channel, made.json()["data"]["seat_handle"]


def _seated_agents(client, room: str) -> list[str]:
    rows = client.get(
        f"/topics/{room}/members", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return [m["member_handle"] for m in rows if m["agent"]]


def _worked_by(client, task: str, handle: str = "alice") -> str | None:
    r = client.get(f"/topics/{task}/task", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]["agent_handle"]


def _give(client, task: str, seat: str, handle: str = "alice"):
    return client.patch(
        f"/topics/{task}/task",
        json={"agent_handle": seat},
        headers=session_auth_headers(handle),
    )


def _turn(client, tmp_path, task: str, reply: str) -> None:
    screen = StubChannel()
    screen.reply = reply
    svc = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        compute=stub_compute(screen, sessions=client.test_request_factory),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    cid = uuid.UUID(task)

    async def go() -> None:
        async for _ in svc.converse(
            topic_id=cid, author="alice", content="做吧", summon=True
        ):
            pass
        await settle_turn(svc, cid)

    client.portal.call(go)


def _author_of(client, task: str, content: str) -> str:
    r = client.get(f"/topics/{task}/transcript", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    authors = [b["author"] for b in r.json()["data"]["data"] if content in b["content"]]
    assert authors, r.json()["data"]["data"]
    return authors[0]


def test_a_teammate_given_a_task_takes_a_seat_in_its_room(client):
    _project, channel, seat = _project_with_a_second_teammate(client)
    task = open_task(client, channel)["id"]

    given = _give(client, task, seat)

    assert given.status_code == 200, given.text
    assert _worked_by(client, task) == seat
    assert seat in _seated_agents(client, channel)


def test_a_task_made_for_a_teammate_seats_it_in_its_room(client):
    """A message asking a teammate, turned into a task after the teammate left
    the room, is worked by that teammate, which sits in the room again."""
    _project, channel, seat = _project_with_a_second_teammate(client)
    seated = client.post(
        f"/topics/{channel}/members",
        json={"handle": seat},
        headers=session_auth_headers("alice"),
    )
    assert seated.status_code == 200, seated.text
    asked = post_message(client, channel, "alice", {"content": f"<@{seat}> 看一下后台"})
    left = client.delete(
        f"/topics/{channel}/members/{seat}", headers=session_auth_headers("alice")
    )
    assert left.status_code == 200, left.text
    assert seat not in _seated_agents(client, channel)

    upgraded = client.post(
        f"/blocks/{asked['id']}/upgrade", headers=session_auth_headers("alice")
    )

    assert upgraded.status_code == 200, upgraded.text
    assert _worked_by(client, upgraded.json()["data"]["id"]) == seat
    assert seat in _seated_agents(client, channel)


def test_a_teammate_its_owner_cannot_add_to_the_room_is_not_given_the_task(client):
    """A member whose role does not bring people into channels cannot seat a
    teammate in 综合, so a task of theirs there cannot be handed to one that is
    not seated."""
    project, _channel, seat = _project_with_a_second_teammate(client)
    general = project["root_topic_id"]
    join_project_team(client, project["id"], "bob")
    _take_bringing_people_in_from_members(client, project["id"])
    task = open_task(client, general, owner="bob")["id"]

    refused = _give(client, task, seat, handle="bob")

    assert refused.status_code == 403, refused.text
    assert _worked_by(client, task, handle="bob") is None
    assert seat not in _seated_agents(client, general)


def test_a_member_gives_a_task_in_general_to_a_teammate_and_seats_it(client):
    """Everyone in the project is in 综合, so a member whose role brings people
    into channels seats the teammate there by giving it the task."""
    project, _channel, seat = _project_with_a_second_teammate(client)
    general = project["root_topic_id"]
    join_project_team(client, project["id"], "bob")
    task = open_task(client, general, owner="bob")["id"]

    given = _give(client, task, seat, handle="bob")

    assert given.status_code == 200, given.text
    assert _worked_by(client, task, handle="bob") == seat
    assert seat in _seated_agents(client, general)


def _take_bringing_people_in_from_members(client, project_id: str) -> None:
    roles = client.get(
        f"/projects/{project_id}/roles", headers=session_auth_headers("alice")
    ).json()["data"]["roles"]
    member = next(r for r in roles if r["key"] == "member")
    changed = client.patch(
        f"/projects/{project_id}/roles/member",
        json={
            "permissions": [g for g in member["permissions"] if g != "create_channels"]
        },
        headers=session_auth_headers("alice"),
    )
    assert changed.status_code == 200, changed.text


def test_a_tasks_turn_is_signed_by_the_teammate_working_it(client, tmp_path):
    _project, channel, seat = _project_with_a_second_teammate(client)
    task = open_task(client, channel)["id"]
    assert _give(client, task, seat).status_code == 200

    _turn(client, tmp_path, task, "派来看看")

    assert _author_of(client, task, "派来看看") == seat


def test_a_teammate_not_in_the_room_does_not_work_the_task_as_the_rooms(
    client, tmp_path
):
    """A task given to a teammate while giving one seated nobody, or whose
    teammate has since been taken out of the room: its turn is refused, and
    nothing is written under the room's agent's seat."""
    _project, channel, seat = _project_with_a_second_teammate(client)
    task = open_task(client, channel)["id"]

    async def given_unseated() -> None:
        async with client.test_factory() as s:
            await s.execute(
                text("UPDATE tasks SET agent_handle = :h WHERE id = :t"),
                {"h": seat, "t": task},
            )
            await s.commit()

    client.portal.call(given_unseated)

    with pytest.raises(UnprocessableEntityError) as refused:
        _turn(client, tmp_path, task, "派接着做")

    assert refused.value.args[0].key == "teammateNotSeatedHere"
    r = client.get(f"/topics/{task}/blocks", headers=session_auth_headers("alice"))
    assert not [b for b in r.json()["data"]["data"] if "派接着做" in b["content"]]
    assert seat not in _seated_agents(client, channel)
