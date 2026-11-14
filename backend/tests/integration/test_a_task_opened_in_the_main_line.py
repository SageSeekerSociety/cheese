"""A task the AI teammate opens in a channel's main line, and where it reports.

The rules a person can state without the code:

- Asked in a channel's main line, the teammate opens a task of its own: it
  hangs under the message that asked for it, and that message is where it
  came from. There is no turning the main line itself into the task.
- How the task ended is recorded back where it was opened, in the channel's
  main line, as something that happened rather than anyone's words; the
  teammate that opened the task is told there, to say the result itself. No
  支线 opens under the message for it.
"""

import uuid

from sqlalchemy import select

from app.domain.delivery.models import Delivery
from tests.integration.conftest import (
    mint_scoped_token,
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)


def _channel(client) -> tuple[str, str]:
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "开发"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    return project["id"], room


def _as_teammate(client, project: str, room: str, path: str, body: dict):
    token = mint_scoped_token(
        project_id=project, topic_id=room, agent_handle=room_agent_seat(client, room)
    )
    # The session a client carries by default outranks the teammate's
    # credential (`resolve_actor` puts a person first): the teammate speaks here.
    bearer = client.headers.pop("Authorization", None)
    try:
        return client.post(
            path, json=body, headers={"Authorization": f"Bearer {token}"}
        )
    finally:
        if bearer is not None:
            client.headers["Authorization"] = bearer


def _main_line(client, room: str) -> list[dict]:
    return client.get(
        f"/topics/{room}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]


def test_a_task_opened_in_the_main_line_hangs_under_the_message_that_asked(
    client, stub_hooks
):
    project, room = _channel(client)
    seat = room_agent_seat(client, room)
    asked = post_message(client, room, "alice", {"content": f"<@{seat}> 把登录页做好"})

    made = _as_teammate(
        client,
        project,
        room,
        f"/topics/{room}/teammate-tasks",
        {"title": "登录页", "summary": "把登录页做好", "owner_handle": "alice"},
    )
    in_place = _as_teammate(
        client,
        project,
        room,
        f"/topics/{room}/teammate-tasks",
        {"title": "登录页", "here": True},
    )

    assert made.status_code == 200, made.text
    task = made.json()["data"]
    assert task["upgraded_from_block_id"] == asked["id"]
    assert task["source_block_id"] == asked["id"]
    assert task["here"] is False
    assert in_place.status_code == 422, in_place.text


def test_how_a_task_opened_in_the_main_line_ended_is_said_there(client, stub_hooks):
    project, room = _channel(client)
    seat = room_agent_seat(client, room)
    asked = post_message(client, room, "alice", {"content": f"<@{seat}> 把登录页做好"})
    task = _as_teammate(
        client,
        project,
        room,
        f"/topics/{room}/teammate-tasks",
        {"title": "登录页", "summary": "把登录页做好", "owner_handle": "alice"},
    ).json()["data"]

    closed = client.post(
        f"/topics/{task['id']}/close",
        json={"conclusion": "做完了"},
        headers=session_auth_headers("alice"),
    )

    assert closed.status_code == 200, closed.text
    told = [
        line
        for line in _main_line(client, room)
        if (line.get("meta") or {}).get("task_id") == task["id"]
    ]
    assert len(told) == 1
    assert (told[0]["kind"], told[0]["author_type"], told[0]["shown_as"]) == (
        "event",
        "platform",
        "happening",
    )
    assert "做完了" in told[0]["content"]
    # The teammate that opened it is told there, with what it needs to say.
    [waiting] = _waiting_for(client, room, seat)
    assert "登录页" in waiting
    assert "做完了" in waiting
    under = next(line for line in _main_line(client, room) if line["id"] == asked["id"])
    assert not under.get("thread")


def _waiting_for(client, room: str, seat: str) -> list[str]:
    """The instructions waiting in the channel's main line for ``seat``."""

    async def read():
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(Delivery.payload["content"].astext).where(
                        Delivery.conversation_id == uuid.UUID(room),
                        Delivery.recipient_handle == seat,
                    )
                )
            )

    return client.portal.call(read)
