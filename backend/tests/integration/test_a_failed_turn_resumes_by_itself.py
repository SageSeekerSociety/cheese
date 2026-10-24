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
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from app.api.deps import get_work_runner
from app.core.config import settings
from app.domain.agent.chat import ChatService
from app.domain.agent.input_registration import confirm_receipt
from app.domain.agent.models import AgentTurn, TurnResume
from app.domain.agent.platform_failures import (
    RUNNER_REPLACED_CODE,
    runner_replaced_message,
)
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent.resumable_failures import (
    POLICIES,
    Policy,
)
from app.domain.agent.resumable_failures import (
    RATE_LIMITED as RATE_KIND,
)
from app.domain.agent.run_records import for_site
from app.domain.agent.service import AgentResult
from app.domain.agent.turn.intake.messages import RunnerIntake
from app.domain.agent.turn.state.live import LiveWork
from app.domain.agent.turn_resume import (
    RESUME_NOTE,
    after_failure,
    hand_due,
)
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.block.models import Block
from app.domain.conversation.services import room_of
from app.domain.delivery.agent import dispatch_pending
from app.domain.delivery.input_identity import InputEffects, InputIdentity, InputReceipt
from app.domain.delivery.models import Delivery
from app.domain.delivery.receipts import register_input
from app.domain.identity.handles import agent_instance_handle
from app.domain.notification.models import NotificationType
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService
from tests.conftest import StubChannel, drain_hooks, stub_compute
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
# Our gateway's lifetime cap on an upstream key (`deploy/gateway/spend_cap.py`),
# in the words a turn ends with when it is reached.
SPENT_CAP = (
    "API Error: Server is temporarily limiting requests (not your usage limit)"
    " · gemini/gemini-4-argon has reached its spend cap: $1005.08 of $1000.00."
    " Received Model Group=gemini-4-argon\nAvailable Model Group Fallbacks=None"
    "（rate_limit）"
)


class Seat(StubChannel):
    """The agent's session: its first ``failing`` turns fail with ``failure``,
    every later one ends normally. Records each input it is handed."""

    def __init__(self, failure: str, *, retrying: bool = False):
        super().__init__()
        self.failure = failure
        self.retrying = retrying
        #: How many of its first turns fail.
        self.failing = 1
        self.handed: list[str] = []
        self.handed_one = asyncio.Event()

    def emit_turn(self, topic_id, prompt, reply, *, agent=None):
        self.handed.append(prompt)
        self.starts(topic_id, agent=agent)
        self.acknowledges(topic_id, prompt, agent=agent)
        if self.retrying:
            self.record(
                topic_id,
                agent=agent,
                type="system",
                subtype="api_retry",
                attempt=1,
                max_retries=10,
                retry_delay_ms=500,
                error_status=429,
                error="rate_limit",
            )
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
    """The resumes owed once the failed turn has ended."""
    for _ in range(HANG_S * 10 if expected else 20):
        if rows := await _resumes(factory, conversation):
            return rows
        await asyncio.sleep(0.1)
    return []


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


async def _first_turn_fails(factory, tmp_path, failure: str, *, retrying=False):
    """A person gives the agent work and the turn ends on ``failure``."""
    _project, conversation, seat, _instance = await _room_with_agent(factory)
    screen = Seat(failure, retrying=retrying)
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
async def test_a_rate_limited_turn_resumes_by_itself(
    business_db_factory, tmp_path, monkeypatch
):
    factory = business_db_factory
    frames = []
    broker = get_broker()
    original_publish = broker.publish

    async def capture(channel, frame):
        frames.append(frame)
        await original_publish(channel, frame)

    monkeypatch.setattr(broker, "publish", capture)
    conversation, _seat, screen, runner, chat = await _first_turn_fails(
        factory, tmp_path, RATE_LIMITED, retrying=True
    )
    try:
        rows = await _owed_resumes(factory, conversation)
        assert len(rows) == 1, "the rate-limited turn left nothing to resume it"
        assert await _failure_cards(factory, conversation) == 0
        assert not [f for f in frames if f.get("type") == "error"]
        async with factory() as session:
            waiting = await for_site(session, conversation, since=None, until=None)
        recovery_lines = [
            r for r in waiting if r["meta"].get("event_type") == "turn_resume_wait"
        ]
        assert len(recovery_lines) == 1
        assert [
            r["meta"].get("event_type")
            for r in waiting
            if r["meta"].get("event_type") in ("api_retry", "turn_resume_wait")
        ] == ["api_retry", "turn_resume_wait"]
        assert recovery_lines[0]["meta"]["reason"] == "rate_limited"
        assert recovery_lines[0]["meta"]["i18n"]["content"]["params"]["minutes"] >= 1
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
        await runner.drain(timeout_s=10)
        # The resume's start is recorded when the session's read of it is
        # reported; the resumed turn's own retry line arrives as a record after
        # that read, on its own commit. Read the history once both have landed.
        [(room_id, _)] = screen.sessions
        await drain_hooks(screen, room_id)
        async with factory() as session:
            completed = await for_site(session, conversation, since=None, until=None)
        assert [
            r["meta"].get("event_type")
            for r in completed
            if r["meta"].get("event_type")
            in ("api_retry", "turn_resume_wait", "turn_resume_started")
        ] == [
            "api_retry",
            "turn_resume_wait",
            "turn_resume_started",
            "api_retry",
        ]
        assert await _failure_cards(factory, conversation) == 0
        assert not [f for f in frames if f.get("type") == "error"]
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
        assert await _failure_cards(factory, conversation) == 1
        later = datetime.now(UTC) + timedelta(days=1)
        assert await hand_due(factory, now=later) == 0
        assert await dispatch_pending(factory, chat=chat, runner=runner) == 0
        assert len(screen.handed) == 1
    finally:
        await runner.drain(timeout_s=10)
        await chat.stop_listening(timeout_s=2)


@pytest.mark.anyio
async def test_a_spent_upstream_cap_is_not_resumed(business_db_factory, tmp_path):
    """The gateway's lifetime cap on an upstream key refuses under the same
    "temporarily limiting requests" wording as a rate limit, and used to be
    resumed until the streak ran out: six rounds of ten retries, all refused
    (2026-10-21)."""
    factory = business_db_factory
    conversation, _seat, screen, runner, chat = await _first_turn_fails(
        factory, tmp_path, SPENT_CAP
    )
    try:
        # Nothing about raising a cap happens by waiting: the seat stays idle
        # and the room gets the failure card, which offers no retry.
        assert await _owed_resumes(factory, conversation, expected=False) == []
        assert await _failure_cards(factory, conversation) == 1
        later = datetime.now(UTC) + timedelta(days=1)
        assert await hand_due(factory, now=later) == 0
        assert await dispatch_pending(factory, chat=chat, runner=runner) == 0
        assert len(screen.handed) == 1
    finally:
        await runner.drain(timeout_s=10)
        await chat.stop_listening(timeout_s=2)


@pytest.mark.anyio
async def test_exhausted_resume_budget_shows_failure_card(
    business_db_factory, tmp_path, monkeypatch
):
    monkeypatch.setitem(POLICIES, RATE_KIND, Policy(base_s=60, cap_s=1800, attempts=0))
    factory = business_db_factory
    conversation, _seat, _screen, runner, chat = await _first_turn_fails(
        factory, tmp_path, RATE_LIMITED
    )
    try:
        async with asyncio.timeout(HANG_S):
            while await _failure_cards(factory, conversation) == 0:
                await asyncio.sleep(0.05)
        assert await _failure_cards(factory, conversation) == 1
        async with factory() as session:
            visible = await for_site(session, conversation, since=None, until=None)
        assert [
            r["meta"].get("event_type")
            for r in visible
            if r["meta"].get("event_type") == "turn_resume_stopped"
        ] == ["turn_resume_stopped"]
        assert not [
            r for r in visible if r["meta"].get("event_type") == "turn_resume_wait"
        ]
    finally:
        await runner.drain(timeout_s=10)
        await chat.stop_listening(timeout_s=2)


@pytest.mark.anyio
async def test_a_failed_resume_delivery_keeps_wait_and_appends_failure(
    business_db_factory,
):
    from app.domain.agent.turn_resume import report_failed_deliveries

    factory = business_db_factory
    project, conversation, seat, _instance = await _room_with_agent(factory)
    row = await after_failure(
        factory,
        project_id=project,
        conversation_id=conversation,
        seat=seat,
        failed_turn_id=uuid.uuid4(),
        result=_failed(RATE_LIMITED),
        route="subscription",
    )
    assert row is not None
    assert await hand_due(factory, now=row.due_at + timedelta(seconds=1)) == 1

    async with factory() as session:
        room = await room_of(session, conversation)
        await TopicMemberService(session).remove(topic_id=room, handle=seat, actor="u")
        await session.commit()

    assert await dispatch_pending(factory, chat=None, runner=None) == 0
    assert await report_failed_deliveries(factory) == 1
    assert await report_failed_deliveries(factory) == 0
    async with factory() as session:
        delivery = await session.scalar(
            select(Delivery).where(Delivery.event_id == row.id)
        )
        visible = await for_site(session, conversation, since=None, until=None)
    assert delivery is not None
    assert delivery.state == "failed"
    assert [
        r["meta"].get("event_type")
        for r in visible
        if r["meta"].get("event_type") in ("turn_resume_wait", "turn_resume_stopped")
    ] == ["turn_resume_wait", "turn_resume_stopped"]
    assert await _failure_cards(factory, conversation) == 1


@pytest.mark.anyio
async def test_an_unread_joined_resume_is_not_recorded_as_started(business_db_factory):
    factory = business_db_factory
    project, conversation, seat, _instance = await _room_with_agent(factory)
    row = await after_failure(
        factory,
        project_id=project,
        conversation_id=conversation,
        seat=seat,
        failed_turn_id=uuid.uuid4(),
        result=_failed(RATE_LIMITED),
        route="subscription",
    )
    assert row is not None
    assert await hand_due(factory, now=row.due_at + timedelta(seconds=1)) == 1

    attempt_id = uuid.uuid4()
    async with factory() as session:
        delivery = await session.scalar(
            select(Delivery).where(Delivery.event_id == row.id)
        )
        assert delivery is not None
        delivery.attempt_id = attempt_id
        delivery.state = "sending"
        delivery_id = delivery.id
        identity = InputIdentity(
            project_id=project,
            conversation_id=conversation,
            recipient_handle=seat,
            harness="claude_code",
            native_session_id="joined-resume",
            input_id=uuid.uuid4(),
            work_id=attempt_id,
        )
        await register_input(
            session,
            identity,
            InputEffects(delivery_id=delivery_id, attempt_id=attempt_id),
        )
        await session.commit()

    async def no_op(*_args):
        return None

    class AcceptedTransport:
        session_factory = factory

        async def join_running_session(self, _delivery_id, _attempt_id):
            return True  # Transport accepted; the CLI has not echoed the input.

    intake = SimpleNamespace(_wait_to_start=no_op, _wait_for_replay=no_op)
    await RunnerIntake._run(
        intake,
        AcceptedTransport(),
        conversation,
        attempt_id,
        author="platform",
        content="resume",
        addressed=None,
        delivery_id=delivery_id,
    )
    async with factory() as session:
        unread = await for_site(session, conversation, since=None, until=None)
    assert [
        r["meta"].get("event_type")
        for r in unread
        if r["meta"].get("event_type") in ("turn_resume_wait", "turn_resume_started")
    ] == ["turn_resume_wait"]

    receipt = InputReceipt(identity=identity, evidence="native_echo")
    await confirm_receipt(factory, LiveWork(), receipt)
    await confirm_receipt(factory, LiveWork(), receipt)
    async with factory() as session:
        seen = await for_site(session, conversation, since=None, until=None)
    assert [
        r["meta"].get("event_type")
        for r in seen
        if r["meta"].get("event_type") in ("turn_resume_wait", "turn_resume_started")
    ] == ["turn_resume_wait", "turn_resume_started"]


@pytest.mark.anyio
async def test_a_resume_without_a_record_is_not_owed(business_db_factory, monkeypatch):
    from app.domain.agent import turn_resume

    factory = business_db_factory
    project, conversation, seat, _instance = await _room_with_agent(factory)

    async def record_unavailable(*_args, **_kwargs):
        raise RuntimeError("run record unavailable")

    monkeypatch.setattr(turn_resume, "record", record_unavailable)
    with pytest.raises(RuntimeError, match="run record unavailable"):
        await after_failure(
            factory,
            project_id=project,
            conversation_id=conversation,
            seat=seat,
            failed_turn_id=uuid.uuid4(),
            result=_failed(RATE_LIMITED),
            route="subscription",
        )
    assert await _resumes(factory, conversation) == []


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
        async with factory() as session:
            visible = await for_site(session, conversation, since=None, until=None)
        assert [
            r["meta"].get("event_type")
            for r in visible
            if r["meta"].get("event_type")
            in ("turn_resume_wait", "turn_resume_stopped")
        ] == ["turn_resume_wait", "turn_resume_stopped"]
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
        [first] = await _owed_resumes(factory, conversation)
        await runner.receive_message(
            chat, conversation, author="u", content=f"<@{seat}> try again please"
        )
        await asyncio.wait_for(screen.handed_one.wait(), HANG_S)
        screen.handed_one.clear()
        await runner.drain(timeout_s=10)
        # The next failure replaces the owed resume without posting a card.
        for _ in range(HANG_S * 10):
            rows = await _resumes(factory, conversation)
            if any(r.failed_at > first.failed_at for r in rows):
                break
            await asyncio.sleep(0.1)
        assert await _failure_cards(factory, conversation) == 0

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
async def test_a_failure_racing_the_hand_over_still_gets_its_resume(
    business_db_factory,
):
    """The clock settles the owed row (here: drops it) while a newer failure of
    the same seat is being recorded. The newer failure must end up owed a
    resume of its own, not written onto the row the clock just settled."""
    factory = business_db_factory
    project, conversation, _seat, instance = await _room_with_agent(factory)
    seat = agent_instance_handle(instance)
    now = datetime.now(UTC)

    def fail(turn_id, at):
        return after_failure(
            factory,
            project_id=project,
            conversation_id=conversation,
            seat=seat,
            failed_turn_id=turn_id,
            result=_failed(RATE_LIMITED),
            route="subscription",
            now=at,
        )

    await fail(uuid.uuid4(), now)
    second = uuid.uuid4()
    async with factory() as clock:
        # The clock's pass holds the row and settles it.
        row = await clock.scalar(
            select(TurnResume)
            .where(TurnResume.conversation_id == conversation)
            .with_for_update()
        )
        row.dropped_at = now
        row.dropped_reason = "seat_ran_again"
        await clock.flush()
        recording = asyncio.create_task(fail(second, now + timedelta(seconds=5)))
        # Wait until the newer failure is actually blocked on that row.
        async with asyncio.timeout(HANG_S):
            while True:
                async with factory() as probe:
                    waiting = await probe.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE wait_event_type = 'Lock' "
                            "AND datname = current_database()"
                        )
                    )
                if waiting or recording.done():
                    break
                await asyncio.sleep(0.05)
        await clock.commit()
    await asyncio.wait_for(recording, HANG_S)

    rows = await _resumes(factory, conversation)
    owed = [r for r in rows if r.handed_at is None and r.dropped_at is None]
    assert [r.failed_turn_id for r in owed] == [second]


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


#: A rate limit whose service names a reset hours away.
LIMITED_FOR_HOURS = (
    "API Error: Server is temporarily limiting requests (not your usage limit)"
    " · Rate limited. Please try again in 4 hours"
)


@pytest.mark.anyio
async def test_an_upgrade_after_a_wake_up_resumes_at_once(business_db_factory):
    """The seat was waiting out a rate limit when a teammate's delivery woke it,
    which ends that wait. The upgrade then cuts the woken turn short: that turn
    is resumed within seconds, not after the limit the wake-up already ended."""
    factory = business_db_factory
    project, conversation, _seat, instance = await _room_with_agent(factory)
    seat = agent_instance_handle(instance)
    t0 = datetime.now(UTC)
    waiting = await after_failure(
        factory,
        project_id=project,
        conversation_id=conversation,
        seat=seat,
        failed_turn_id=uuid.uuid4(),
        result=_failed(LIMITED_FOR_HOURS),
        route="subscription",
        now=t0,
    )
    assert waiting is not None and waiting.due_at > t0 + timedelta(hours=1)
    woken = uuid.uuid4()
    async with factory() as session:
        session.add(
            AgentTurn(
                id=woken,
                conversation_id=conversation,
                continuation_id=woken,
                author="teammate",
                agent_handle=seat,
                content="go on",
                started_at=t0 + timedelta(seconds=10),
                delivered_at=t0 + timedelta(seconds=10),
            )
        )
        await session.commit()
    t1 = t0 + timedelta(seconds=60)

    resumed = await after_failure(
        factory,
        project_id=project,
        conversation_id=conversation,
        seat=seat,
        failed_turn_id=woken,
        result=AgentResult(
            text=runner_replaced_message(seat),
            session_id="s",
            is_error=True,
            failure_code=RUNNER_REPLACED_CODE,
        ),
        route="subscription",
        now=t1,
    )

    assert resumed is not None and resumed.dropped_at is None
    assert resumed.reason == "runner_replaced"
    assert resumed.failed_turn_id == woken
    assert resumed.due_at <= t1 + timedelta(seconds=10)
    assert await hand_due(factory, now=t1 + timedelta(seconds=10)) == 1
