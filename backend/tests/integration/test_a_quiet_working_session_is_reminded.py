"""A session at work that owes a person an answer, or whose checklist stopped,
is reminded, whatever turn is open.

A session that read a person's message and has said nothing since is told to
answer; one that answered and then went quiet is asked, every interval of
silence, for one line on what it is doing now. One whose own checklist stopped
moving is asked to write it again (`QuietSessionReminders`). What decides is the session — the seat this
process holds whose runner says it is working, the input it read, the messages
it wrote, the checklist its current work wrote — not a turn the platform holds
open: the work here is handed to the session directly, so there is none.

Real PostgreSQL and the real room sessions over the stub runner.
"""

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.domain.agent.chat import ChatService
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.turn.steps.reminders import QuietSessionReminders
from app.domain.block.models import Block, BlockKind
from app.domain.delivery.models import NativeInput
from app.domain.event_author import AuthorType
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.topic.models import TopicProgress
from tests.conftest import StubChannel, stub_compute
from tests.integration.conftest import post_project, room_agent_seat

THRESHOLD = 600


class _KeepsWorking(StubChannel):
    """A session that takes the first input and keeps working on it."""

    def emit_turn(self, topic_id, prompt, reply, *, agent=None) -> None:
        self.starts(topic_id, agent=agent)
        self.acknowledges(topic_id, prompt, agent=agent)
        self.says(topic_id, "在改", agent=agent)


@pytest.fixture
def room(client):
    project = post_project(client, {"name": "Quiet"}, owner="alice").json()["data"]
    topic = project["root_topic_id"]
    return uuid.UUID(project["id"]), uuid.UUID(topic), room_agent_seat(client, topic)


async def _working(channel: StubChannel, session: SessionRef) -> str:
    """Until the runner says the session is working; the handle it acts under."""
    runtime = channel.runtime
    deadline = time.monotonic() + 8
    while True:
        live = runtime.live.get(runtime._seat_of(session))
        status = await runtime.host.status(live.ref) if live else None
        if status is not None and status.working:
            return live.acting
        assert time.monotonic() < deadline, "the session never started working"
        await asyncio.sleep(0.01)


async def _at_work(client, room, channel):
    """A ChatService whose seat's session was handed its work directly — no
    turn row, no turn mark — and the work id and acting handle."""
    project, topic, seat = room
    service = ChatService(
        work_runner=AsyncMock(),
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/quiet-ws",
        compute=stub_compute(channel),
    )
    session = SessionRef(project, topic, seat, harness=CLAUDE_CODE)
    work = uuid.uuid4()
    await channel.runtime.send(
        session,
        "把慢的几处改掉",
        system_prompt="system",
        work_id=work,
        on_mark=lambda _: None,
        register_input=AsyncMock(),
    )
    acting = await _working(channel, session)
    assert not service.live.active_turn_ids.get(topic)
    return service, session, work, acting


async def _put_down(channel, session: SessionRef) -> None:
    channel.sessions.pop((session.topic_id, session.agent_handle), None)
    await channel.runtime._detach(channel.runtime._seat_of(session))


def test_a_session_that_read_a_person_and_said_nothing_is_reminded(
    client, room, monkeypatch
):
    monkeypatch.setattr(settings, "chat_progress_reminder_after_s", THRESHOLD)
    client.portal.call(_reminded_of_its_silence, client, room)


async def _reminded_of_its_silence(client, room):
    project, topic, _seat = room
    channel = _KeepsWorking()
    service, session, work, acting = await _at_work(client, room, channel)
    told: list[str] = []

    async def notice(topic_id, text, **_kwargs):
        told.append(text)
        return True

    service.notify_running_session = notice  # type: ignore[method-assign]
    quiet = QuietSessionReminders(service, service.compute)
    try:
        read_at = datetime.now(UTC)
        async with client.test_factory() as db:
            asked = Block(
                project_id=project,
                conversation_id=topic,
                author="alice",
                author_type=AuthorType.participant,
                kind=BlockKind.message,
                content="首页为什么这么慢？",
            )
            db.add(asked)
            await db.flush()
            db.add(
                NativeInput(
                    project_id=project,
                    conversation_id=topic,
                    recipient_handle=acting,
                    harness=CLAUDE_CODE,
                    native_session_id="sess-quiet",
                    input_id=uuid.uuid4(),
                    work_id=work,
                    block_ids=[str(asked.id)],
                    registered_at=read_at,
                    accepted_at=read_at,
                    echoed_at=read_at,
                )
            )
            await db.commit()

        assert await quiet.silence(read_at + timedelta(seconds=THRESHOLD - 1)) == 0
        assert await quiet.silence(read_at + timedelta(seconds=THRESHOLD + 1)) == 1
        assert len(told) == 1 and "chat_send" in told[0]
    finally:
        await _put_down(channel, session)


def test_a_session_whose_own_checklist_stopped_is_asked_to_write_it(
    client, room, monkeypatch
):
    monkeypatch.setattr(settings, "chat_progress_reminder_after_s", THRESHOLD)
    client.portal.call(_asked_for_its_checklist, client, room)


async def _asked_for_its_checklist(client, room):
    _project, topic, _seat = room
    channel = _KeepsWorking()
    service, session, work, _acting = await _at_work(client, room, channel)
    told: list[str] = []

    async def notice(topic_id, text, **_kwargs):
        told.append(text)
        return True

    service.notify_running_session = notice  # type: ignore[method-assign]
    quiet = QuietSessionReminders(service, service.compute)
    try:
        written_at = datetime.now(UTC)
        async with client.test_factory() as db:
            db.add(
                TopicProgress(
                    conversation_id=topic,
                    items=[
                        {"id": "1", "subject": "改慢的几处", "status": "in_progress"}
                    ],
                    turn_id=work,
                    updated_at=written_at,
                )
            )
            await db.commit()

        assert await quiet.checklist(written_at + timedelta(seconds=THRESHOLD - 1)) == 0
        assert await quiet.checklist(written_at + timedelta(seconds=THRESHOLD + 1)) == 1
        assert len(told) == 1 and "todo_write" in told[0]
    finally:
        await _put_down(channel, session)


def test_work_the_platform_started_after_an_answered_question_is_not_reminded(
    client, room, monkeypatch
):
    """A person asked hours ago and the agent answered. Now its session works on
    something the platform handed it (a check gone red, a scheduled wake-up):
    nobody is waiting on that work to speak, so the silence reminder stays out
    of it, however long ago the person's question was."""
    monkeypatch.setattr(settings, "chat_progress_reminder_after_s", THRESHOLD)
    client.portal.call(_platform_work_after_an_answer, client, room)


async def _platform_work_after_an_answer(client, room):
    project, topic, _seat = room
    channel = _KeepsWorking()
    service, session, work, acting = await _at_work(client, room, channel)
    told: list[str] = []

    async def notice(topic_id, text, **_kwargs):
        told.append(text)
        return True

    service.notify_running_session = notice  # type: ignore[method-assign]
    quiet = QuietSessionReminders(service, service.compute)
    try:
        now = datetime.now(UTC)
        asked_at = now - timedelta(hours=3)

        def read(work_id, blocks, at):
            return NativeInput(
                project_id=project,
                conversation_id=topic,
                recipient_handle=acting,
                harness=CLAUDE_CODE,
                native_session_id="sess-quiet",
                input_id=uuid.uuid4(),
                work_id=work_id,
                block_ids=blocks,
                registered_at=at,
                accepted_at=at,
                echoed_at=at,
            )

        async with client.test_factory() as db:
            asked = Block(
                project_id=project,
                conversation_id=topic,
                author="alice",
                author_type=AuthorType.participant,
                kind=BlockKind.message,
                content="首页为什么这么慢？",
                created_at=asked_at,
            )
            db.add(asked)
            await db.flush()
            # Read into earlier work, and answered a minute later.
            db.add(read(uuid.uuid4(), [str(asked.id)], asked_at))
            db.add(
                Block(
                    project_id=project,
                    conversation_id=topic,
                    author=acting,
                    author_type=AuthorType.participant,
                    kind=BlockKind.message,
                    content="慢在首页那三次查询，已经改了。",
                    created_at=asked_at + timedelta(minutes=1),
                )
            )
            # The work at hand began with an input that carried no one's words.
            db.add(read(work, [], now))
            await db.commit()

        assert await quiet.silence(now + timedelta(seconds=30)) == 0
        assert await quiet.silence(now + timedelta(seconds=THRESHOLD + 1)) == 0
        assert told == []
    finally:
        await _put_down(channel, session)


def test_a_long_silence_is_still_reminded_after_many_notices(client, room, monkeypatch):
    """A session that read a person's question and has said nothing since keeps
    being reminded however many notices it read in the meantime — its own
    reminders, the platform's other notices, a teammate's words — all read into
    the same work."""
    monkeypatch.setattr(settings, "chat_progress_reminder_after_s", THRESHOLD)
    client.portal.call(_silent_through_notices, client, room)


async def _silent_through_notices(client, room):
    project, topic, _seat = room
    channel = _KeepsWorking()
    service, session, work, acting = await _at_work(client, room, channel)
    told: list[str] = []

    async def notice(topic_id, text, **_kwargs):
        told.append(text)
        return True

    service.notify_running_session = notice  # type: ignore[method-assign]
    quiet = QuietSessionReminders(service, service.compute)
    try:
        asked_at = datetime.now(UTC)

        def read(blocks, at):
            return NativeInput(
                project_id=project,
                conversation_id=topic,
                recipient_handle=acting,
                harness=CLAUDE_CODE,
                native_session_id="sess-quiet",
                input_id=uuid.uuid4(),
                # Each arrived on an input of its own and was read into the
                # work in flight: the runner stamps that work on the read.
                work_id=uuid.uuid4(),
                execution_work_id=work,
                block_ids=blocks,
                registered_at=at,
                accepted_at=at,
                echoed_at=at,
            )

        async with client.test_factory() as db:
            asked = Block(
                project_id=project,
                conversation_id=topic,
                author="alice",
                author_type=AuthorType.participant,
                kind=BlockKind.message,
                content="首页为什么这么慢？",
                created_at=asked_at,
            )
            db.add(asked)
            await db.flush()
            db.add(read([str(asked.id)], asked_at))
            for n in range(30):
                db.add(read([], asked_at + timedelta(seconds=n + 1)))
            await db.commit()

        assert await quiet.silence(asked_at + timedelta(seconds=THRESHOLD + 60)) == 1
        assert len(told) == 1 and "chat_send" in told[0]
    finally:
        await _put_down(channel, session)


def test_a_session_that_answered_and_works_on_silently_is_reminded_every_interval(
    client, room, monkeypatch
):
    """The person was answered and the session keeps working without a word in
    the conversation. People there cannot see 现场 from the chat, and a silent
    teammate reads as a dead one: every interval of silence it is asked for one
    short line on what it is doing now — never sooner than an interval after it
    last spoke. A new question it has not answered gets the stronger reminder,
    to answer, and only that one."""
    monkeypatch.setattr(settings, "chat_progress_reminder_after_s", THRESHOLD)
    client.portal.call(_reminded_while_working_on, client, room)


async def _reminded_while_working_on(client, room):
    project, topic, _seat = room
    channel = _KeepsWorking()
    service, session, work, acting = await _at_work(client, room, channel)
    told: list[str] = []

    async def notice(topic_id, text, **_kwargs):
        told.append(text)
        return True

    service.notify_running_session = notice  # type: ignore[method-assign]
    quiet = QuietSessionReminders(service, service.compute)
    try:
        asked_at = datetime.now(UTC)
        answered_at = asked_at + timedelta(minutes=1)

        def message(author: str, content: str, at: datetime) -> Block:
            return Block(
                project_id=project,
                conversation_id=topic,
                author=author,
                author_type=AuthorType.participant,
                kind=BlockKind.message,
                content=content,
                created_at=at,
            )

        def read(blocks, at):
            return NativeInput(
                project_id=project,
                conversation_id=topic,
                recipient_handle=acting,
                harness=CLAUDE_CODE,
                native_session_id="sess-quiet",
                input_id=uuid.uuid4(),
                work_id=work,
                block_ids=blocks,
                registered_at=at,
                accepted_at=at,
                echoed_at=at,
            )

        async def add(row) -> None:
            async with client.test_factory() as db:
                db.add(row)
                await db.commit()

        asked = message("alice", "首页为什么这么慢？", asked_at)
        await add(asked)
        await add(read([str(asked.id)], asked_at))
        await add(message(acting, "我先查首页的查询，改完再说。", answered_at))

        # Answered: nothing until an interval of silence after the answer.
        assert await quiet.silence(answered_at + timedelta(seconds=THRESHOLD - 1)) == 0
        first = answered_at + timedelta(seconds=THRESHOLD + 1)
        assert await quiet.silence(first) == 1
        # Still silent: once per interval, not a stream.
        assert await quiet.silence(first + timedelta(seconds=THRESHOLD - 1)) == 0
        second = first + timedelta(seconds=THRESHOLD)
        assert await quiet.silence(second) == 1
        assert len(told) == 2
        for said in told:
            assert "chat_send" in said and "分钟没在对话里说话" in said
            assert "还没回" not in said

        # It posted a line: the next interval counts from that line.
        posted_at = second + timedelta(seconds=30)
        await add(
            message(acting, "还在查：首页三次查询已经合成一次，正在跑压测。", posted_at)
        )
        assert await quiet.silence(posted_at + timedelta(seconds=THRESHOLD - 1)) == 0
        assert len(told) == 2

        # Someone asks again and is not answered: one reminder, to answer.
        again_at = posted_at + timedelta(seconds=60)
        again = message("alice", "现在到哪了？", again_at)
        await add(again)
        await add(read([str(again.id)], again_at))
        assert await quiet.silence(again_at + timedelta(seconds=THRESHOLD + 1)) == 1
        assert len(told) == 3 and "分钟了还没回" in told[2]
    finally:
        await _put_down(channel, session)


def test_a_conversation_whose_card_is_open_is_not_reminded(client, room, monkeypatch):
    """A card has been filed and waits for the reviewer. Where the work stands
    is the card's to say, and the platform must not knock on the session that
    filed it — that window is exactly when a reminder wakes a session that has
    nothing left to do."""
    monkeypatch.setattr(settings, "chat_progress_reminder_after_s", THRESHOLD)
    client.portal.call(_card_speaks, client, room)


async def _card_speaks(client, room):
    project, topic, _seat = room
    channel = _KeepsWorking()
    service, session, work, acting = await _at_work(client, room, channel)
    told: list[str] = []

    async def notice(topic_id, text, **_kwargs):
        told.append(text)
        return True

    service.notify_running_session = notice  # type: ignore[method-assign]
    quiet = QuietSessionReminders(service, service.compute)
    try:
        now = datetime.now(UTC)
        async with client.test_factory() as db:
            asked = Block(
                project_id=project,
                conversation_id=topic,
                author="alice",
                author_type=AuthorType.participant,
                kind=BlockKind.message,
                content="首页为什么这么慢？",
                created_at=now,
            )
            db.add(asked)
            await db.flush()
            db.add(
                NativeInput(
                    project_id=project,
                    conversation_id=topic,
                    recipient_handle=acting,
                    harness=CLAUDE_CODE,
                    native_session_id="sess-quiet",
                    input_id=uuid.uuid4(),
                    work_id=work,
                    block_ids=[str(asked.id)],
                    registered_at=now,
                    accepted_at=now,
                    echoed_at=now,
                )
            )
            db.add(
                TopicProgress(
                    conversation_id=topic,
                    items=[
                        {"id": "1", "subject": "改慢的几处", "status": "in_progress"}
                    ],
                    turn_id=work,
                    updated_at=now,
                )
            )
            # The room's own card: no task under it, the topic is what it speaks for.
            db.add(
                AcceptCard(
                    topic_id=topic,
                    task_id=None,
                    reviewer_handle="alice",
                    focus="",
                    status=AcceptStatus.pending,
                )
            )
            await db.commit()

        later = now + timedelta(seconds=THRESHOLD * 6)
        assert await quiet.silence(later) == 0
        assert await quiet.checklist(later) == 0
        assert told == []
    finally:
        await _put_down(channel, session)
