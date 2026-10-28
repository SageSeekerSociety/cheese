"""一个队友说过的话，对同一条支线里的另一位队友也是消息。

每个座位各记一个「读到哪」。同一条支线里先后 @ 两位队友，后一位要能看到前一位
的回复，也要看到它自己没读过的那条点名；谁先读过不改变别人读什么。
"""

import asyncio
import time
import uuid

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.topic_membership.services import TopicMemberService
from tests.conftest import wait_work_idle
from tests.integration.conftest import (
    in_thread,
    post_message,
    post_project,
    room_socket,
)


def _room_with_two_teammates(client) -> tuple[str, str, str]:
    """A 支线 in a channel holding the project's own agent and a second one."""
    data = post_project(client, {"name": "Two seats"}, owner="alice").json()["data"]

    async def seat() -> str:
        async with client.test_factory() as session:
            second = await AgentInstanceService(session).create(
                project_id=uuid.UUID(data["id"]),
                handle="second",
                type_name=None,
                display_name="Second",
            )
            await TopicMemberService(session).ensure_agent_seat(
                uuid.UUID(data["root_topic_id"]), agent_instance_handle(second.id)
            )
            await session.commit()
            return agent_instance_handle(second.id)

    second = asyncio.run(seat())
    room = in_thread(client, data["root_topic_id"], "alice")
    seats = [
        member["member_handle"]
        for member in client.get(f"/topics/{room}/members").json()["data"]["data"]
        if member["agent"]
    ]
    return room, next(seat for seat in seats if seat != second), second


def _say(client, room: str, handle: str, text: str) -> None:
    with room_socket(client, room, handle) as ws:
        post_message(client, room, handle, {"content": text})
        while ws.receive_json()["type"] != "user_block":
            pass


def _reply(client, room: str, seat: str, text: str) -> None:
    """``seat`` 用 chat_send 说一句：署名是它，落在它那一轮里。"""
    project_id = client.get(f"/topics/{room}").json()["data"]["project_id"]
    token = mint_scoped_token(project_id=project_id, topic_id=room, agent_handle=seat)
    response = client.post(
        f"/topics/{room}/messages",
        json={"request_id": str(uuid.uuid4()), "content": text},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Cheese-Turn": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200, response.text


def _said(client, room: str) -> str:
    blocks = client.get(f"/topics/{room}/blocks").json()["data"]["data"]
    return "\n".join(str(block.get("content") or "") for block in blocks)


def _prompt_of(stub_hooks, room: str, seat: str) -> str:
    for (topic, _), session in stub_hooks.sessions.items():
        if str(topic) == room and session.actor == seat:
            return "\n".join(
                str(message["message"]["content"])
                for message in session.written
                if message.get("type") == "user"
            )
    return ""


def _until(predicate, what: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def test_the_second_teammate_reads_the_first_one_s_reply(client, stub_hooks):
    room, first, second = _room_with_two_teammates(client)
    _say(client, room, "alice", f"<@{first}> 先说一句")
    _until(lambda: "先说一句" in _prompt_of(stub_hooks, room, first), "第一句没送进去")
    wait_work_idle()
    _reply(client, room, first, "第一位队友的结论")
    assert "第一位队友的结论" in _said(client, room)

    _say(client, room, "alice", f"<@{second}> 你怎么看")
    _until(
        lambda: "你怎么看" in _prompt_of(stub_hooks, room, second),
        "第二位队友没被叫起来",
    )
    prompt = _prompt_of(stub_hooks, room, second)

    # 前一位的回复，和点名它的那条消息，都在这一轮的输入里。
    assert "第一位队友的结论" in prompt
    assert "先说一句" in prompt


def test_a_teammate_is_not_handed_its_own_reply(client, stub_hooks):
    room, first, _ = _room_with_two_teammates(client)
    _say(client, room, "alice", f"<@{first}> 先说一句")
    _until(lambda: "先说一句" in _prompt_of(stub_hooks, room, first), "第一句没送进去")
    wait_work_idle()
    _reply(client, room, first, "我自己的结论")
    assert "我自己的结论" in _said(client, room)

    _say(client, room, "alice", f"<@{first}> 再说一句")
    _until(
        lambda: "再说一句" in _prompt_of(stub_hooks, room, first),
        "第二句话没送进去",
    )

    # 它自己上一轮的产出不再作为输入还给它。
    assert "我自己的结论" not in _prompt_of(stub_hooks, room, first)
