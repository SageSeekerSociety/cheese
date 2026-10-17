"""A turn cut short by a passing model-service failure goes on by itself.

Before, the seat stayed idle until a person or another agent poked it: on dev
(2026-10-03..17) the next action after a rate-limited turn came a median 23
minutes later, p90 nine hours. A failure that passes by itself now resumes once
it has: after a backoff, or at the reset the service named. A failure no wait
changes (an invalid request, a refused credential, a spent balance) does not,
and a person speaking in the meantime takes the turn back.
"""

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api.deps import get_work_runner
from app.core.config import settings
from app.domain.agent.chat import ChatService
from app.domain.agent.models import TurnResume
from app.domain.agent.service import AgentResult
from app.domain.agent.turn_resume import RESUME_NOTE, after_failure, hand_due
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.block.models import Block
from app.domain.delivery.agent import dispatch_pending
from app.domain.delivery.models import Delivery
from app.domain.identity.handles import agent_instance_handle
from app.domain.notification.models import NotificationType
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService
from tests.conftest import StubChannel, stub_compute
from tests.integration.conftest import project_service, registered
from tests.support.hang import HANG_S
from tests.support.threads import thread_in

RATE_LIMITED = (
    "API Error: Server is temporarily limiting requests (not your usage limit)"
    " · Rate limited"
)
REFUSED = (
    'API Error: 400 {"type":"error","error":{"type":"invalid_request_error",'
    '"message":"messages.3.content.0.image: unsupported image"}}'
)
COOLING = (
    "API Error: Server is temporarily limiting requests (not your usage limit)"
    " · cheese: all Claude accounts are cooling down"
)


class Seat(StubChannel):
    """The agent's session: its first ``failing`` turns fail with ``failure``,
    every later one ends normally. Records each input it is handed."""

    def __init__(self, failure: str):
        super().__init__()
        self.failure = failure
        #: How many of its first turns fail.
        self.failing = 1
        self.handed: list[str] = []
        self.handed_one = asyncio.Event()

    def emit_turn(self, topic_id, prompt, reply, *, agent=None):
        self.handed.append(prompt)
        self.starts(topic_id, agent=agent)
        self.acknowledges(topic_id, prompt, agent=agent)
        if len(self.handed) <= self.failing:
            self.record(
                topic_id,
                agent=agent,
                type="result",
                subtype="success",
                is_error=True,
                result=self.failure,
            )
        else:
            self.says(topic_id, "carried on", agent=agent)
            self.stops(topic_id, "carried on", agent=agent)
        self.handed_one.set()


async def _room_with_agent(factory):
    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
        room = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        seat = (await TopicMemberService(session).agent_handles(room.id))[0]
        agent = await AgentInstanceService(session).for_seat_handle(project, seat)
        conversation = await thread_in(session, room)
        await session.commit()
    return project.id, conversation, seat, agent.instance_id


async def _resumes(factory, conversation) -> list[TurnResume]:
    async with factory() as session:
        return list(
            await session.scalars(
                select(TurnResume)
                .where(TurnResume.conversation_id == conversation)
                .order_by(TurnResume.failed_at)
                .execution_options(populate_existing=True)
            )
        )


async def _owed_resumes(
    factory, conversation, *, expected: bool = True
) -> list[TurnResume]:
    """The resumes owed once the failure has landed. Scheduling follows the
    failure card in the same step: where one is expected, wait for it as long
    as for anything else; where none is, a moment after the card is enough."""
    async with asyncio.timeout(HANG_S):
        while not await _failure_card(factory, conversation):
            await asyncio.sleep(0.05)
    for _ in range(HANG_S * 10 if expected else 20):
        if rows := await _resumes(factory, conversation):
            return rows
        await asyncio.sleep(0.1)
    return []


async def _failure_card(factory, conversation) -> bool:
    return await _failure_cards(factory, conversation) > 0


async def _failure_cards(factory, conversation) -> int:
    async with factory() as session:
        return len(
            list(
                await session.scalars(
                    select(Block.id).where(
                        Block.conversation_id == conversation,
                        Block.meta["event_type"].as_string() == "turn_failed",
                    )
                )
            )
        )


async def _first_turn_fails(factory, tmp_path, failure: str):
    """A person gives the agent work and the turn ends on ``failure``."""
    _project, conversation, seat, _instance = await _room_with_agent(factory)
    screen = Seat(failure)
    runner = get_work_runner()
    chat = ChatService(
        work_runner=runner,
        session_factory=factory,
        compute=stub_compute(screen),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    await runner.receive_message(
        chat, conversation, author="u", content=f"<@{seat}> rename the module"
    )
    await asyncio.wait_for(screen.handed_one.wait(), HANG_S)
    screen.handed_one.clear()
    # The failure lands through the session's reader, after the turn's input.
    await runner.drain(timeout_s=10)
    return conversation, seat, screen, runner, chat


@pytest.mark.anyio
async def test_a_rate_limited_turn_resumes_by_itself(business_db_factory, tmp_path):
    factory = business_db_factory
    conversation, _seat, screen, runner, chat = await _first_turn_fails(
        factory, tmp_path, RATE_LIMITED
    )
    try:
        rows = await _owed_resumes(factory, conversation)
        assert len(rows) == 1, "the rate-limited turn left nothing to resume it"
        [owed] = rows
        # It waits first: a request limited a moment ago is limited now.
        assert owed.due_at > datetime.now(UTC)
        assert await hand_due(factory) == 0
        assert await dispatch_pending(factory, chat=chat, runner=runner) == 0
        assert len(screen.handed) == 1

        # Time passes. Nobody says anything; the clocks tick.
        assert await hand_due(factory, now=owed.due_at + timedelta(seconds=1)) == 1
        assert await dispatch_pending(factory, chat=chat, runner=runner) == 1
        await asyncio.wait_for(screen.handed_one.wait(), HANG_S)

        # The same session, which still holds the person's request, is told to
        # go on with it.
        assert len(screen.handed) == 2
        assert str(RESUME_NOTE) in screen.handed[-1]
        assert len(screen.sessions) == 1
        # Handed over once: the clock running again sends nothing more.
        assert await hand_due(factory, now=owed.due_at + timedelta(hours=1)) == 0
    finally:
        await runner.drain(timeout_s=10)
        await chat.stop_listening(timeout_s=2)


@pytest.mark.anyio
async def test_a_refused_request_is_not_resumed(business_db_factory, tmp_path):
    factory = business_db_factory
    conversation, _seat, screen, runner, chat = await _first_turn_fails(
        factory, tmp_path, REFUSED
    )
    try:
        # Sending the same conversation again is refused the same way.
        assert await _owed_resumes(factory, conversation, expected=False) == []
        later = datetime.now(UTC) + timedelta(days=1)
        assert await hand_due(factory, now=later) == 0
        assert await dispatch_pending(factory, chat=chat, runner=runner) == 0
        assert len(screen.handed) == 1
    finally:
        await runner.drain(timeout_s=10)
        await chat.stop_listening(timeout_s=2)


@pytest.mark.anyio
async def test_a_person_speaking_takes_the_turn_back(business_db_factory, tmp_path):
    factory = business_db_factory
    conversation, _seat, screen, runner, chat = await _first_turn_fails(
        factory, tmp_path, RATE_LIMITED
    )
    try:
        [owed] = await _owed_resumes(factory, conversation)
        # Before the resume is due, a person says something in the thread.
        await runner.receive_message(
            chat, conversation, author="u", content="hold on, I'll look first"
        )
        await runner.drain(timeout_s=10)

        assert await hand_due(factory, now=owed.due_at + timedelta(seconds=1)) == 0
        assert await dispatch_pending(factory, chat=chat, runner=runner) == 0
        assert len(screen.handed) == 1
        [dropped] = await _resumes(factory, conversation)
        assert dropped.dropped_reason == "person_spoke"
    finally:
        await runner.drain(timeout_s=10)
        await chat.stop_listening(timeout_s=2)


@pytest.mark.anyio
async def test_a_seat_that_fails_again_after_being_woken_still_resumes(
    business_db_factory, tmp_path
):
    """A person wakes the seat before its resume is due, and that turn is cut
    short the same way. The latest failure is the one owed a resume."""
    factory = business_db_factory
    conversation, seat, screen, runner, chat = await _first_turn_fails(
        factory, tmp_path, RATE_LIMITED
    )
    screen.failing = 2
    try:
        assert await _owed_resumes(factory, conversation)
        await runner.receive_message(
            chat, conversation, author="u", content=f"<@{seat}> try again please"
        )
        await asyncio.wait_for(screen.handed_one.wait(), HANG_S)
        screen.handed_one.clear()
        await runner.drain(timeout_s=10)
        async with asyncio.timeout(HANG_S):
            while await _failure_cards(factory, conversation) < 2:
                await asyncio.sleep(0.05)
        await asyncio.sleep(1)  # scheduling follows the card in the same step

        # Long after any backoff the second failure could have asked for.
        later = datetime.now(UTC) + timedelta(hours=2)
        assert await hand_due(factory, now=later) == 1, "the second failure was left"
        assert await dispatch_pending(factory, chat=chat, runner=runner) == 1
        await asyncio.wait_for(screen.handed_one.wait(), HANG_S)
        assert len(screen.handed) == 3
        assert str(RESUME_NOTE) in screen.handed[-1]
    finally:
        await runner.drain(timeout_s=10)
        await chat.stop_listening(timeout_s=2)


@pytest.mark.anyio
async def test_a_seat_something_else_woke_is_not_resumed_again(
    business_db_factory, tmp_path
):
    """An instruction for the seat runs a turn before its resume is due, and
    that turn goes fine. The seat is working again: no second "go on"."""
    factory = business_db_factory
    conversation, seat, screen, runner, chat = await _first_turn_fails(
        factory, tmp_path, RATE_LIMITED
    )
    try:
        [owed] = await _owed_resumes(factory, conversation)
        async with factory() as session:
            stamp = datetime.now(UTC)
            session.add(
                Delivery(
                    event_id=uuid.uuid4(),
                    recipient_handle=owed.recipient_handle,
                    agent_instance_id=owed.agent_instance_id,
                    conversation_id=conversation,
                    dedup_key=f"{uuid.uuid4()}:woken",
                    type=NotificationType.ROOM_NOTICE.value,
                    payload={"content": "check the open pull requests"},
                    event_at=stamp,
                    recorded_at=stamp,
                )
            )
            await session.commit()
        assert await dispatch_pending(factory, chat=chat, runner=runner) == 1
        await asyncio.wait_for(screen.handed_one.wait(), HANG_S)
        screen.handed_one.clear()
        await runner.drain(timeout_s=10)
        assert len(screen.handed) == 2

        assert await hand_due(factory, now=owed.due_at + timedelta(seconds=1)) == 0
        assert await dispatch_pending(factory, chat=chat, runner=runner) == 0
        assert len(screen.handed) == 2
    finally:
        await runner.drain(timeout_s=10)
        await chat.stop_listening(timeout_s=2)


def _failed(text: str) -> AgentResult:
    return AgentResult(text=text, session_id="s", is_error=True)


@pytest.mark.anyio
async def test_a_failing_seat_is_owed_one_resume_and_stops_after_its_streak(
    business_db_factory,
):
    factory = business_db_factory
    project, conversation, _seat, instance = await _room_with_agent(factory)
    seat = agent_instance_handle(instance)

    async def owed():
        rows = await _resumes(factory, conversation)
        return next(
            (r for r in rows if r.handed_at is None and r.dropped_at is None), None
        )

    async def fail(now):
        return await after_failure(
            factory,
            project_id=project,
            conversation_id=conversation,
            seat=seat,
            failed_turn_id=uuid.uuid4(),
            result=_failed(RATE_LIMITED),
            route="subscription",
            now=now,
        )

    now = datetime.now(UTC)
    first = await fail(now)
    # A second failure while the first resume waits is still one owed resume.
    assert (await fail(now + timedelta(seconds=5))).id == first.id
    assert len(await _resumes(factory, conversation)) == 1

    # Each resume fails again at once: every wait is longer than the last, and
    # the streak ends instead of going on for ever.
    waits = []
    for _ in range(20):
        row = await owed()
        if row is None:
            break
        waits.append((row.due_at - row.failed_at).total_seconds())
        now = row.due_at + timedelta(seconds=1)
        assert await hand_due(factory, now=now) == 1
        await fail(now + timedelta(seconds=2))
    rows = await _resumes(factory, conversation)
    assert rows[-1].dropped_reason == "attempts_used_up"
    assert 2 <= len(waits) < 20
    assert all(
        later > earlier for earlier, later in zip(waits, waits[1:], strict=False)
    )


@pytest.mark.anyio
async def test_spent_subscription_accounts_resume_when_the_first_one_thaws(
    business_db_factory, tmp_path, monkeypatch
):
    factory = business_db_factory
    project, conversation, _seat, instance = await _room_with_agent(factory)
    thaw_in = 2 * 3600
    (tmp_path / "accounts.json").write_text(
        json.dumps(
            {
                "written_at": time.time(),
                "accounts": [
                    {"name": "a", "state": "cooling", "until": time.time() + thaw_in},
                    {
                        "name": "b",
                        "state": "cooling",
                        "until": time.time() + 3 * thaw_in,
                    },
                ],
            }
        )
    )
    monkeypatch.setattr(
        settings, "subscription_usage_log", str(tmp_path / "usage.jsonl")
    )
    now = datetime.now(UTC)
    row = await after_failure(
        factory,
        project_id=project,
        conversation_id=conversation,
        seat=agent_instance_handle(instance),
        failed_turn_id=uuid.uuid4(),
        result=_failed(COOLING),
        route="subscription",
        now=now,
    )
    wait = (row.due_at - now).total_seconds()
    # After the first account thaws, not on a short backoff that fails again.
    assert thaw_in <= wait <= thaw_in + 5 * 60
