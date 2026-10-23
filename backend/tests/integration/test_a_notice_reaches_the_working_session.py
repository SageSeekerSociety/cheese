"""The platform's words reach the session that is working, whatever turn is open.

A doc edit, a note from another thread, the progress reminder: each is told to
the session at work in the conversation (`ChatService.notify_running_session`),
and so is a person's message to a seat whose session is at work
(`merge_into_running_session`). Whether a session is at work is what its
runner says. No turn interval decides it: a session working on something the
platform opened no turn for — work it took up on its own, a session recovered
after a restart — hears them, and a seat whose session is idle hears nothing.

Real PostgreSQL and the real room sessions over the stub runner; the work is
handed to the session directly, so no turn row is ever opened for it.
"""

import asyncio
import time
import uuid
from unittest.mock import AsyncMock

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from tests.conftest import StubChannel, stub_compute
from tests.integration.conftest import post_project, room_agent_seat

NOTICE = "实况文档已被 <@alice> 更新至第 2 版。"


class _KeepsWorking(StubChannel):
    """A session that takes the first input and keeps working on it."""

    def emit_turn(self, topic_id, prompt, reply, *, agent=None) -> None:
        self.starts(topic_id, agent=agent)
        self.acknowledges(topic_id, prompt, agent=agent)
        self.says(topic_id, "在改", agent=agent)


def _heard(channel: StubChannel, topic: uuid.UUID, seat: str) -> list[str]:
    said = []
    for message in channel._session_for(topic, seat).written:
        if message.get("type") != "user":
            continue
        content = message["message"]["content"]
        said.append(content if isinstance(content, str) else content[0]["text"])
    return said


async def _working(channel: StubChannel, session: SessionRef) -> None:
    runtime = channel.runtime
    deadline = time.monotonic() + 8
    while True:
        live = runtime.live.get(runtime._seat_of(session))
        status = await runtime.host.status(live.ref) if live else None
        if status is not None and status.working:
            return
        assert time.monotonic() < deadline, "the session never started working"
        await asyncio.sleep(0.01)


@pytest.fixture
def room(client):
    project = post_project(client, {"name": "Notices"}, owner="alice").json()["data"]
    topic = project["root_topic_id"]
    return uuid.UUID(project["id"]), uuid.UUID(topic), room_agent_seat(client, topic)


def _service(client, channel) -> ChatService:
    return ChatService(
        work_runner=AsyncMock(),
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/notice-ws",
        compute=stub_compute(channel),
    )


async def _start_work(channel, session: SessionRef) -> None:
    """Hand the session work directly: no turn row, no turn mark."""
    await channel.runtime.send(
        session,
        "把慢的几处改掉",
        system_prompt="system",
        work_id=uuid.uuid4(),
        on_mark=lambda _: None,
        register_input=AsyncMock(),
    )
    await _working(channel, session)


async def _put_down(channel, session: SessionRef) -> None:
    """The work is left running on purpose; nothing is waiting on it now."""
    channel.sessions.pop((session.topic_id, session.agent_handle), None)
    await channel.runtime._detach(channel.runtime._seat_of(session))


def test_a_notice_reaches_a_working_session_no_turn_is_open_for(client, room):
    client.portal.call(_a_notice_reaches_a_working_session, client, room)


async def _a_notice_reaches_a_working_session(client, room):
    project, topic, seat = room
    channel = _KeepsWorking()
    service = _service(client, channel)
    session = SessionRef(project, topic, seat, harness=CLAUDE_CODE)
    try:
        # Idle: there is nobody to tell.
        assert await service.notify_running_session(topic, "早了") is False

        await _start_work(channel, session)
        assert not service.live.active_turn_ids.get(topic)
        assert not await service.has_running_turn(topic)

        assert await service.notify_running_session(topic, NOTICE) is True
        deadline = time.monotonic() + 8
        while not any(NOTICE in said for said in _heard(channel, topic, seat)):
            assert time.monotonic() < deadline, _heard(channel, topic, seat)
            await asyncio.sleep(0.01)
        assert not any("早了" in said for said in _heard(channel, topic, seat))
    finally:
        await _put_down(channel, session)


def test_a_message_joins_a_working_session_no_turn_is_open_for(client, room):
    client.portal.call(_a_message_joins_a_working_session, client, room)


async def _a_message_joins_a_working_session(client, room):
    project, topic, seat = room
    channel = _KeepsWorking()
    service = _service(client, channel)
    session = SessionRef(project, topic, seat, harness=CLAUDE_CODE)
    try:
        # Idle: the message is for the queue, not a degraded delivery.
        assert (
            await service.merge_into_running_session(
                topic, [], "先别动", "alice", None, seat
            )
            is None
        )

        await _start_work(channel, session)
        assert not await service.has_running_turn(topic)

        delivered = await service.merge_into_running_session(
            topic, [], "顺便把首页也测一下", "alice", None, seat
        )
        assert delivered is True, delivered
        deadline = time.monotonic() + 8
        while not any(
            "顺便把首页也测一下" in said for said in _heard(channel, topic, seat)
        ):
            assert time.monotonic() < deadline, _heard(channel, topic, seat)
            await asyncio.sleep(0.01)
        # Another seat's message is not this session's to take.
        assert (
            await service.merge_into_running_session(
                topic, [], "给别人的", "alice", None, "somebody-else"
            )
            is None
        )
    finally:
        await _put_down(channel, session)
