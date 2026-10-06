"""A message someone sent while the backend changed hands still gets its turn.

Between a message being stored and its turn starting, the turn lives only in the
memory of the backend that accepted the message: waiting for that backend to
take the work over, or queued behind the project's other turns, where the room
is told it will start by itself. When that backend goes away the wait goes with
it, and the backend taking over has to start what it dropped — and only that.
"""

import asyncio
import time
import uuid

from sqlalchemy import select

from app.api.deps import get_chat_service, get_work_runner
from app.core import background
from app.core.config import settings
from app.domain.agent.chat import ChatService
from app.domain.agent.models import AgentTurn
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.block.models import Block
from app.main import app
from tests.conftest import StubChannel, settle_turn, stub_compute
from tests.integration.conftest import (
    chat_ws_url,
    post_message,
    post_project,
    session_auth_headers,
)


def _room(client) -> tuple[str, StubChannel, ChatService]:
    project = post_project(client, {"name": "Handover"}, owner="alice")
    room = project.json()["data"]["root_topic_id"]
    channel = StubChannel()
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/handover-messages-ws",
        compute=stub_compute(channel),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    return room, channel, service


def _blocks(client, room: str) -> list[Block]:
    async def read() -> list[Block]:
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(Block)
                    .where(Block.conversation_id == uuid.UUID(room))
                    .order_by(Block.created_at)
                )
            )

    return asyncio.run(read())


def _say(client, room: str, content: str) -> None:
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        post_message(client, room, "alice", {"content": content})
        while ws.receive_json()["type"] != "user_block":
            pass


def _sent_to_the_session(channel: StubChannel, room: str) -> list[str]:
    session = next(
        (s for (t, _), s in channel.sessions.items() if t == uuid.UUID(room)), None
    )
    return [
        str(message["message"]["content"])
        for message in (session.written if session else [])
        if message.get("type") == "user"
    ]


def _handed_over(client, service: ChatService) -> int:
    """The accepting backend lets go of what it held; the next one takes over."""
    runner = get_work_runner()
    client.portal.call(runner.let_go)
    runner.start_turns()
    return client.portal.call(runner.resume_lost_messages, service)


def _until_answered(client, service, channel: StubChannel, room: str) -> None:
    deadline = time.monotonic() + 10
    while channel.reply not in [block.content for block in _blocks(client, room)]:
        assert time.monotonic() < deadline, "the message was never answered"
        time.sleep(0.05)
    client.portal.call(settle_turn, service, uuid.UUID(room))


def test_a_message_waiting_for_its_turn_is_answered_by_the_next_backend(client):
    room, channel, service = _room(client)
    get_work_runner().hold_turns()
    _say(client, room, "@芝士 修一下登录页")
    assert _sent_to_the_session(channel, room) == []

    assert _handed_over(client, service) == 1

    _until_answered(client, service, channel, room)


def test_a_message_the_next_backend_already_holds_starts_one_turn(client):
    """Sent to the incoming backend before it took over: the message is already
    waiting there for the takeover, and the takeover must not start it again."""
    room, channel, service = _room(client)
    runner = get_work_runner()
    runner.hold_turns()
    _say(client, room, "@芝士 修一下登录页")

    assert client.portal.call(runner.resume_lost_messages, service) == 0
    runner.start_turns()
    _until_answered(client, service, channel, room)

    async def turns() -> int:
        async with client.test_factory() as session:
            return len(
                list(
                    await session.scalars(
                        select(AgentTurn.id).where(
                            AgentTurn.conversation_id == uuid.UUID(room)
                        )
                    )
                )
            )

    assert asyncio.run(turns()) == 1


def test_a_message_that_was_answered_is_not_answered_again(client):
    room, channel, service = _room(client)
    _say(client, room, "@芝士 修一下登录页")
    _until_answered(client, service, channel, room)
    sent = _sent_to_the_session(channel, room)

    assert _handed_over(client, service) == 0
    assert _sent_to_the_session(channel, room) == sent


def test_a_message_refused_for_spent_credits_is_not_tried_again(client, monkeypatch):
    room, channel, service = _room(client)

    async def spent(topic_id):
        return {"project_id": None, "credits_exhausted": True}

    monkeypatch.setattr(service, "work_policy", spent)
    _say(client, room, "@芝士 修一下登录页")
    deadline = time.monotonic() + 10
    while len(_blocks(client, room)) < 2:
        assert time.monotonic() < deadline, "the refusal never reached the room"
        time.sleep(0.05)
    monkeypatch.undo()

    assert _handed_over(client, service) == 0
    assert _sent_to_the_session(channel, room) == []


def test_a_message_that_named_no_agent_waits_as_it_always_does(client):
    """Not naming an agent is a choice (发消息默认不 @); the takeover does not
    make it for the sender."""
    room, channel, service = _room(client)
    _say(client, room, "登录页好像有点问题")

    assert _handed_over(client, service) == 0
    assert _sent_to_the_session(channel, room) == []


class _SlowToSetUp(StubChannel):
    """A machine that takes forever to get one room's session ready, which
    keeps that room's turn holding its project's only slot."""

    slow: uuid.UUID | None = None

    async def precheck(self, session, *, needs_place):
        if session.topic_id == self.slow:
            await asyncio.Event().wait()
        return await super().precheck(session, needs_place=needs_place)


def test_a_message_queued_behind_other_turns_is_answered_by_the_next_backend(
    client, monkeypatch
):
    """The room was told its turn would start by itself once the ones ahead
    finished. The backend that took over keeps that promise."""
    monkeypatch.setattr(settings, "max_concurrent_turns", 1)
    project = post_project(client, {"name": "Handover"}, owner="alice")
    busy = project.json()["data"]["root_topic_id"]
    waiting = client.post(
        "/topics",
        json={
            "project_id": project.json()["data"]["id"],
            "title": "排队",
        },
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    channel = _SlowToSetUp()
    channel.slow = uuid.UUID(busy)
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/handover-messages-ws",
        compute=stub_compute(channel),
    )
    app.dependency_overrides[get_chat_service] = lambda: service

    _say(client, busy, "@芝士 跑个长任务")
    _say(client, waiting, "@芝士 修一下登录页")
    deadline = time.monotonic() + 10
    while len(_blocks(client, waiting)) < 2:
        assert time.monotonic() < deadline, "the room was never told it is queued"
        time.sleep(0.05)
    assert _sent_to_the_session(channel, waiting) == []

    assert _handed_over(client, service) == 1

    while not any(
        "修一下登录页" in text for text in _sent_to_the_session(channel, waiting)
    ):
        assert time.monotonic() < deadline, "the queued message was never started"
        time.sleep(0.05)
    client.portal.call(settle_turn, service, uuid.UUID(waiting))


def test_a_message_whose_wake_up_was_missed_starts_on_the_next_sweep(
    client, monkeypatch
):
    """Nothing nudges the room again after the message's wait was dropped: the
    periodic sweep is what starts it, within one interval."""
    room, channel, service = _room(client)
    runner = get_work_runner()
    runner.hold_turns()
    _say(client, room, "@芝士 修一下登录页")
    client.portal.call(runner.let_go)
    runner.start_turns()
    assert _sent_to_the_session(channel, room) == []

    class Unused:
        """The other jobs in the list are built, never run here."""

        def __getattr__(self, name):
            return None

    monkeypatch.setattr(settings, "queued_message_sweep_interval_s", 1)
    [sweep] = [
        job
        for job in background.periodic_jobs(
            chat=service,
            machines=Unused(),
            sandboxes=Unused(),
            compute=Unused(),
            sessions=client.test_request_factory,
        )
        if job.name == "queued message sweep"
    ]

    class Runs:
        async def record(self, name, at):
            pass

    begun = time.monotonic()
    client.portal.call(sweep.start, Runs())
    try:
        _until_answered(client, service, channel, room)
    finally:
        client.portal.call(sweep.stop)
    assert time.monotonic() - begun < 1 + 5


def test_a_message_held_by_an_open_turn_starts_when_its_stop_closes_it(client):
    """The turn ahead's completion and idle frame can both arrive before its
    Stop. Their scans find its interval still open and pass the message over;
    the Stop closing that interval is the last thing that can start it."""
    from datetime import UTC, datetime

    room, channel, service = _room(client)
    runner = get_work_runner()
    runner.hold_turns()
    _say(client, room, "@芝士 修一下登录页")
    client.portal.call(runner.let_go)
    runner.start_turns()
    ahead = uuid.uuid4()

    async def open_turn_ahead() -> None:
        async with client.test_factory() as session:
            now = datetime.now(UTC)
            await AgentTurnRepository(session).open(
                turn_id=ahead,
                conversation_id=uuid.UUID(room),
                continuation_id=ahead,
                author="alice",
                content="跑个长任务",
                is_resume=False,
                resendable=False,
                started_at=now,
                delivered_at=now,
                agent_handle="cheese",
            )
            await session.commit()

    asyncio.run(open_turn_ahead())
    # The scans the completion and the idle frame ran: the seat is still busy.
    assert client.portal.call(runner.resume_lost_messages, service) == 0
    assert _sent_to_the_session(channel, room) == []

    async def stop() -> None:
        from app.domain.agent import hook_stream

        await hook_stream.close_on_stop(service, uuid.UUID(room), ahead)

    client.portal.call(stop)

    _until_answered(client, service, channel, room)
