"""The room's agent writes on one of its threads (`cheese_tell`).

It takes the same route a person uses to say something on a card,
`POST /topics/{room}/tasks/{task}/messages`, and who is writing decides what
happens next. A person's message wakes the room to pass it on
(test_talking_to_a_thread.py). The room's own agent IS that room: the worker
doing the thread is a 分身 inside its session, which it reaches with its own
tooling, so its message is recorded where the work is and nothing is woken.
"""

import asyncio
import uuid

from sqlalchemy import select

from app.core.sandbox_auth import mint_scoped_token
from app.domain.delivery.models import Delivery
from tests.conftest import wait_work_idle
from tests.integration.conftest import (
    post_project,
    room_agent_seat,
    session_auth_headers,
)


def _project(client) -> dict:
    return post_project(client, json={"name": "P"}, owner="user-1").json()["data"]


def _room(client, project_id: str, title: str = "房间") -> dict:
    return client.post(
        "/topics",
        json={"project_id": project_id, "title": title},
    ).json()["data"]


def _split(client, room_id: str, title: str) -> dict:
    task = client.post(
        f"/topics/{room_id}/split",
        json={"title": title, "reviewer_handle": "alice"},
    ).json()["data"]
    wait_work_idle()
    return task


def _agent(project_id: str, room_id: str) -> dict:
    """The credential a turn in this room runs with."""
    return {
        "X-Cheese-Token": mint_scoped_token(project_id=project_id, topic_id=room_id)
    }


def _say(client, room_id: str, task_id: str, content: str, headers: dict):
    return client.post(
        f"/topics/{room_id}/tasks/{task_id}/messages",
        json={"content": content},
        headers=headers,
    )


def _card_blocks(client, room_id: str, task_id: str) -> list[dict]:
    r = client.get(f"/topics/{room_id}/tasks/{task_id}")
    assert r.status_code == 200, r.text
    return r.json()["data"]["blocks"]


def _record_screens(stub_hooks) -> list[str]:
    """Every topic a session screen was raised for: raising one is waking."""
    seen: list[str] = []
    original = stub_hooks.precheck

    async def _spy(session, *, needs_place):
        seen.append(str(session.topic_id))
        return await original(session, needs_place=needs_place)

    stub_hooks.precheck = _spy
    return seen


def _deliveries(client, task_id: str) -> list[Delivery]:
    async def read():
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(Delivery).where(Delivery.task_id == uuid.UUID(task_id))
            )
            return list(rows)

    return asyncio.run(read())


def test_the_rooms_agent_leaves_a_note_and_wakes_nobody(client, stub_hooks):
    p = _project(client)
    room = _room(client, p["id"])
    task = _split(client, room["id"], "数据清洗")

    screens = _record_screens(stub_hooks)
    r = _say(
        client,
        room["id"],
        task["id"],
        "口径改了：只算活跃用户",
        _agent(p["id"], room["id"]),
    )
    assert r.status_code == 200, r.text
    wait_work_idle()

    note = r.json()["data"]
    assert note["task_id"] == task["id"]
    assert note["author"] == room_agent_seat(client, room["id"])
    contents = [b["content"] for b in _card_blocks(client, room["id"], task["id"])]
    assert "口径改了：只算活跃用户" in contents
    assert screens == [], f"a note raised a session screen: {screens}"
    assert _deliveries(client, task["id"]) == [], "a note recorded a delivery"


def test_a_card_is_not_a_room_to_write_from(client):
    p = _project(client)
    room = _room(client, p["id"])
    a = _split(client, room["id"], "支线A")
    b = _split(client, room["id"], "支线B")

    r = _say(client, a["id"], b["id"], "偷偷说句话", _agent(p["id"], room["id"]))
    assert r.status_code in (401, 404)
    assert "偷偷说句话" not in [
        blk["content"] for blk in _card_blocks(client, room["id"], b["id"])
    ]


def test_a_thread_of_another_room_is_out_of_reach(client):
    p = _project(client)
    room = _room(client, p["id"])
    other = _room(client, p["id"], "别人的房间")
    theirs = _split(client, other["id"], "别人的活")

    r = _say(client, room["id"], theirs["id"], "x", _agent(p["id"], room["id"]))
    assert r.status_code == 404
    assert (
        _say(
            client, room["id"], str(uuid.uuid4()), "x", _agent(p["id"], room["id"])
        ).status_code
        == 404
    )


def test_an_agents_note_is_short_and_a_persons_message_is_not_capped_as_one(client):
    p = _project(client)
    room = _room(client, p["id"])
    task = _split(client, room["id"], "子活")
    agent = _agent(p["id"], room["id"])

    assert _say(client, room["id"], task["id"], "   ", agent).status_code == 422
    assert _say(client, room["id"], task["id"], "长" * 5000, agent).status_code == 422
    person = session_auth_headers("user-1")
    assert _say(client, room["id"], task["id"], "长" * 5000, person).status_code == 200
    wait_work_idle()


def test_a_credential_for_another_room_cannot_write_here(client):
    p = _project(client)
    room = _room(client, p["id"])
    task = _split(client, room["id"], "子活")
    elsewhere = _room(client, p["id"], "别的话题")

    r = _say(client, room["id"], task["id"], "x", _agent(p["id"], elsewhere["id"]))
    assert r.status_code in (401, 403)
    assert "x" not in [
        b["content"] for b in _card_blocks(client, room["id"], task["id"])
    ]


def test_a_closed_thread_still_takes_a_note(client):
    """收起不是冻结：a finished thread still takes a message."""
    p = _project(client)
    room = _room(client, p["id"])
    task = _split(client, room["id"], "已经收工的活")
    client.post(
        f"/topics/{room['id']}/tasks/{task['id']}/close",
        json={},
        headers=_agent(p["id"], room["id"]),
    )

    r = _say(client, room["id"], task["id"], "还有一件事", _agent(p["id"], room["id"]))
    assert r.status_code == 200, r.text
    assert "还有一件事" in [
        b["content"] for b in _card_blocks(client, room["id"], task["id"])
    ]


def test_a_thread_in_an_archived_room_still_takes_the_note(client):
    """归档冻的是工作面，不是记录：留话仍然写得进去。"""
    p = _project(client)
    room = _room(client, p["id"])
    task = _split(client, room["id"], "房间要归档了")
    client.post(
        f"/topics/{room['id']}/archive",
        json={"by": "user-1"},
        headers=session_auth_headers("user-1"),
    )

    r = _say(client, room["id"], task["id"], "还有一件事", _agent(p["id"], room["id"]))
    assert r.status_code == 200, r.text
    assert "还有一件事" in [
        b["content"] for b in _card_blocks(client, room["id"], task["id"])
    ]
