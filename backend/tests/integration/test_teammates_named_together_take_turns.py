"""In a channel's main line, the AI teammates one message names take their
turns in the order it names them; an AI teammate's own word there wakes no
other AI teammate.

- A person names two teammates: the first named answers first, and the second
  is woken only once the first is done, so it reads what the first said.
- An AI teammate that names another in the main line wakes nobody: work for
  another teammate is a task handed to it.
"""

import time
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


def _room_with_two(client) -> tuple[str, str, str, str]:
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "开发"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    own = room_agent_seat(client, room)
    created = client.post(f"/projects/{project['id']}/agents", json={"handle": "ops"})
    assert created.status_code == 200, created.text
    ops = created.json()["data"]["seat_handle"]
    added = client.post(
        f"/topics/{room}/members",
        json={"handle": ops, "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert added.status_code == 200, added.text
    return project["id"], room, own, ops


def _deliveries(client, room: str, seat: str) -> list[dict]:
    async def read():
        async with client.test_factory() as session:
            return [
                {"state": row.state, "held": (row.payload or {}).get("held")}
                for row in await session.scalars(
                    select(Delivery).where(
                        Delivery.conversation_id == uuid.UUID(room),
                        Delivery.recipient_handle == seat,
                    )
                )
            ]

    return client.portal.call(read)


def _until(check, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "never happened"
        time.sleep(0.05)


def test_the_second_named_waits_for_the_first(client, stub_hooks):
    _project, room, own, ops = _room_with_two(client)
    started: list = []

    def held_open(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic, agent=agent)
        stub_hooks.acknowledges(topic, prompt, agent=agent)
        started.append(topic)

    stub_hooks.emit_turn = held_open
    post_message(client, room, "alice", {"content": f"<@{own}> <@{ops}> 一起看看"})
    _until(lambda: started)

    # The first named is at work; the second has not been sent its turn.
    [waiting] = _deliveries(client, room, ops)
    assert waiting == {"state": "pending", "held": True}

    stub_hooks.stops(started[0], "我先看了前端。")
    _until(lambda: _deliveries(client, room, ops)[0]["held"] is False)


def test_an_ai_teammate_naming_another_in_the_main_line_wakes_nobody(
    client, stub_hooks
):
    project, room, own, ops = _room_with_two(client)
    token = mint_scoped_token(project_id=project, topic_id=room, agent_handle=own)
    bearer = client.headers.pop("Authorization", None)
    try:
        sent = client.post(
            f"/topics/{room}/messages",
            json={"request_id": str(uuid.uuid4()), "content": f"<@{ops}> 你来查一下"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        if bearer is not None:
            client.headers["Authorization"] = bearer

    assert sent.status_code == 200, sent.text
    assert _deliveries(client, room, ops) == []
