"""A session that opens in a room with history is told the history is there.

The docs, memory and last checklist reach a new session; what people said does
not, and they assume the agent remembers it. So the opening says how many chat
messages the room already holds and which commands read them. It says nothing
in a room where nobody has spoken yet, and nothing to a session that resumes
its own conversation, which already holds what was said in it.
"""

import re
import uuid

from app.domain.agent_session.services import AgentSessionService
from tests.integration.conftest import chat_ws_url, post_project

_LINE = re.compile(r"这个房间里已经有 (\d+) 条聊天消息")


def _room(client) -> str:
    p = post_project(client, json={"name": "P", "owner_handle": "user-1"}).json()[
        "data"
    ]
    return client.post(
        "/topics", json={"project_id": p["id"], "title": "话题", "created_by": "user-1"}
    ).json()["data"]["id"]


def _session_lost(client, room: str) -> None:
    """The room's conversation is gone (a deploy, a recycled machine): the next
    turn opens a new one."""

    async def forget() -> None:
        async with client.test_factory() as session:
            await AgentSessionService(session).forget_room(uuid.UUID(room))
            await session.commit()

    client.portal.call(forget)


def _messages(client, room: str) -> int:
    blocks = client.get(f"/topics/{room}/blocks").json()["data"]["data"]
    return len([b for b in blocks if b["kind"] == "message"])


def _turn(client, room: str, text: str) -> None:
    with client.websocket_connect(chat_ws_url(room, "user-1")) as ws:
        ws.send_json({"type": "message", "content": text})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass


def test_a_room_with_no_history_gets_no_such_line(client, stub_hooks):
    room = _room(client)
    _turn(client, room, "@芝士 hi")
    assert _LINE.search(stub_hooks.last_system_prompt or "") is None


def test_a_fresh_session_in_a_room_with_history_is_told_how_to_read_it(
    client, stub_hooks
):
    room = _room(client)
    _turn(client, room, "@芝士 先看一下这个问题")
    before = _messages(client, room)
    assert before > 0
    _session_lost(client, room)

    _turn(client, room, "@芝士 接着做")

    prompt = stub_hooks.last_system_prompt or ""
    found = _LINE.search(prompt)
    assert found is not None, prompt[-2000:]
    # The message that opened this turn is delivered with it; it is not history.
    assert int(found.group(1)) == before
    tail = prompt[found.start() :]
    assert "cheese chat list" in tail
    assert "cheese chat search" in tail


def test_a_resumed_session_is_not_told_to_read_what_it_already_has(client, stub_hooks):
    room = _room(client)
    _turn(client, room, "@芝士 先看一下这个问题")
    _turn(client, room, "@芝士 接着做")
    assert _LINE.search(stub_hooks.last_system_prompt or "") is None
