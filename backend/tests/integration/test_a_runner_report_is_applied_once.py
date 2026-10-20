"""What a seat's runner reports is applied once, by the registration that holds
the seat, and says exactly what it settles.

Rules a person can state without the code:

- an event the platform applied and did not get to acknowledge (it crashed in
  between) is delivered again, and changes nothing the second time — not even
  by putting back what a later event changed;
- a runner, or a platform process, that a newer registration replaced can no
  longer change anything;
- a session's process going away settles what it never read: the seat takes
  the next message, and the batch nobody read is offered again;
- a read input is delivered, whatever the work it was read into did next: a
  failure frees nothing that was waiting on it and holds nothing back, and it
  is recorded on the input and on the message it carried;
- an input written and not read yet is the one whose fate is unknown: it holds
  its seat and its batch until the runner says what became of it.

Real PostgreSQL; the reports are built as the reading builds them.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.domain.agent import runner_reports, session_state
from app.domain.agent.harness.driven.lifecycle import StaleEpoch
from app.domain.agent.models import SessionState
from app.domain.agent.reads import (
    InputDropped,
    InputRead,
    InputWritten,
    Report,
    SessionExited,
    SessionIdle,
    SessionRunning,
)
from app.domain.agent.service import AgentResult
from app.domain.block.models import Block, BlockKind, consumed_turn, read_failed
from app.domain.conversation.models import Conversation
from app.domain.delivery.input_holds import seat_has_unfinished_input
from app.domain.delivery.input_identity import (
    InputEffects,
    InputIdentity,
    InputReceipt,
)
from app.domain.delivery.models import NativeInput
from app.domain.delivery.receipts import held_blocks, register_input
from app.domain.event_author import AuthorType
from tests.turn_log import a_topic

SEAT = "cheese-test"
HARNESS = "claude-code"
SESSION = "native-session"


class _Seat:
    """One seat's session, as its runner reports it: events under a
    registration, numbered as the runner numbers them."""

    def __init__(self, factory, topic: uuid.UUID, project: uuid.UUID):
        self.factory, self.topic, self.project = factory, topic, project
        self.key = session_state.SeatKey(topic, SEAT, HARNESS)
        self.epoch = 0
        self.seq = 0
        # What `runner_reports.apply` reads of the room's service.
        self.chat = SimpleNamespace(
            session_factory=factory, live=SimpleNamespace(unread_inputs={})
        )

    async def register(self) -> int:
        self.epoch = await session_state.register(self.factory, self.key)
        return self.epoch

    def report(self, fact, *, epoch: int | None = None) -> Report:
        self.seq += 1
        return Report(
            self.topic,
            SEAT,
            HARNESS,
            self.epoch if epoch is None else epoch,
            self.epoch,
            self.seq,
            datetime.now(UTC),
            fact,
        )

    async def apply(self, report: Report) -> None:
        await runner_reports.apply(self.chat, report)

    async def input(self, *, work: uuid.UUID | None = None) -> tuple:
        """A message, and the input that carries it, registered as a turn's
        first input is: its batch held from here on."""
        block = Block(
            id=uuid.uuid4(),
            project_id=self.project,
            conversation_id=self.topic,
            kind=BlockKind.message,
            author_type=AuthorType.participant,
            author="u",
            content="fix the login page",
            meta={"consumed_turn": None},
        )
        input_id = uuid.uuid4()
        identity = InputIdentity(
            self.project, self.topic, SEAT, HARNESS, SESSION, input_id, work or input_id
        )
        async with self.factory() as session:
            session.add(block)
            await session.flush()
            await register_input(
                session, identity, InputEffects(held_block_ids=(block.id,))
            )
            await session.commit()
        return identity, block.id

    async def status(self) -> SessionState:
        async with self.factory() as session:
            state = await session.scalar(
                select(SessionState).where(
                    SessionState.conversation_id == self.topic,
                    SessionState.agent_handle == SEAT,
                )
            )
            assert state is not None
            return state

    async def holds(self) -> tuple[bool, set]:
        async with self.factory() as session:
            return (
                await seat_has_unfinished_input(session, self.topic, SEAT),
                await held_blocks(
                    session,
                    project_id=self.project,
                    topic_id=self.topic,
                    recipient_handle=SEAT,
                ),
            )

    async def block(self, block_id) -> Block:
        async with self.factory() as session:
            block = await session.get(Block, block_id)
            assert block is not None
            return block

    async def row(self, identity: InputIdentity) -> NativeInput:
        async with self.factory() as session:
            row = await session.scalar(
                select(NativeInput).where(NativeInput.input_id == identity.input_id)
            )
            assert row is not None
            return row


async def _seat(factory) -> _Seat:
    topic = await a_topic(factory)
    async with factory() as session:
        conversation = await session.get(Conversation, topic)
        assert conversation is not None
        project = conversation.project_id
    seat = _Seat(factory, topic, project)
    await seat.register()
    return seat


def _idle(work: uuid.UUID, *read: uuid.UUID, failure: str | None = None) -> SessionIdle:
    return SessionIdle(
        work,
        AgentResult(
            text="" if failure else "done",
            is_error=failure is not None,
            session_id=SESSION,
        ),
        read,
        failure,
    )


class _Frames:
    """The realtime frames the platform publishes, as the room's pages see them."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def publish(self, channel: str, frame: dict) -> None:
        self.sent.append(frame)


@pytest.mark.anyio
async def test_an_event_delivered_again_after_a_crash_changes_nothing(
    db_factory, monkeypatch
):
    """The platform applied an input's read and the failed work it was read
    into, and crashed before the runner heard it had: the runner hands over
    everything since its last acknowledgement again, in order. Each effect
    happened once. The room is told once that its message was read, the
    failure is stamped once, and while the backlog comes again the seat does
    not go back to running: a redelivered event does not put back what a later
    one changed."""
    frames = _Frames()
    monkeypatch.setattr(runner_reports, "get_broker", lambda: frames)
    seat = await _seat(db_factory)
    work = uuid.uuid4()
    identity, block = await seat.input(work=work)
    async with db_factory() as session:
        row = await seat_row(session, identity)
        row.seen_block_ids = [str(block)]
        row.seen_by = SEAT
        await session.commit()
    backlog = [
        seat.report(InputWritten(identity)),
        seat.report(SessionRunning(work)),
        seat.report(InputRead(InputReceipt(identity, "native_echo", work))),
        seat.report(_idle(work, identity.input_id, failure="api_error")),
    ]
    for report in backlog:
        await seat.apply(report)
    first = await seat.row(identity)
    assert first.echoed_at is not None and first.failed_at is not None

    for report in backlog[:2]:
        await seat.apply(report)
        assert (await seat.status()).worker_status == "idle"
    for report in backlog[2:]:
        await seat.apply(report)

    told = [frame for frame in frames.sent if frame["type"] == "reaction"]
    assert len(told) == 1
    again = await seat.row(identity)
    assert (again.echoed_at, again.failed_at) == (first.echoed_at, first.failed_at)
    assert read_failed(await seat.block(block)) == "api_error"
    async with db_factory() as session:
        assert await session_state.running_conversations(session) == set()


async def seat_row(session, identity: InputIdentity) -> NativeInput:
    row = await session.scalar(
        select(NativeInput).where(NativeInput.input_id == identity.input_id)
    )
    assert row is not None
    return row


@pytest.mark.anyio
async def test_a_replaced_registration_changes_nothing(db_factory):
    """A runner registered again (another platform process took the seat
    over, or the seat's runner was replaced) holds the seat from then on: what
    the older registration delivers is refused, and applies nothing."""
    seat = await _seat(db_factory)
    old = seat.epoch
    await seat.register()
    work = uuid.uuid4()

    with pytest.raises(StaleEpoch):
        await seat.apply(seat.report(SessionRunning(work), epoch=old))
    assert (await seat.status()).worker_status == "idle"

    await seat.apply(seat.report(SessionRunning(work)))
    assert (await seat.status()).worker_status == "running"


@pytest.mark.anyio
async def test_a_session_exit_settles_what_it_never_read(db_factory):
    """Its runner reports the process gone, with an input it had written and
    not read yet: that input will never be read. The seat takes the next
    message, and the message nobody read is offered again, not swallowed."""
    seat = await _seat(db_factory)
    identity, block = await seat.input()
    await seat.apply(seat.report(InputWritten(identity)))
    assert await seat.holds() == (True, {block})

    await seat.apply(seat.report(InputDropped(identity, "exited")))
    await seat.apply(seat.report(SessionExited("launch-1", current=True)))

    assert await seat.holds() == (False, set())
    assert consumed_turn(await seat.block(block)) is None
    row = await seat.row(identity)
    assert row.echoed_at is None and row.termination == "exited"
    state = await seat.status()
    assert state.worker_status == "idle" and state.exited_at is not None


@pytest.mark.anyio
async def test_a_read_input_is_delivered_and_a_failure_after_it_is_recorded(
    db_factory,
):
    """Reading delivers: the batch is consumed and released at the echo, and
    nothing waits for the work to end. The work then fails: the seat is free —
    it was already — and the failure is written on the input and on the
    message it carried, which is how a close and an edit tell a message read
    and answered from one read and left unanswered."""
    seat = await _seat(db_factory)
    work = uuid.uuid4()
    identity, block = await seat.input(work=work)
    await seat.apply(seat.report(InputWritten(identity)))
    await seat.apply(seat.report(SessionRunning(work)))
    await seat.apply(
        seat.report(InputRead(InputReceipt(identity, "native_echo", work)))
    )
    assert await seat.holds() == (False, set())
    assert consumed_turn(await seat.block(block)) is not None

    await seat.apply(seat.report(_idle(work, identity.input_id, failure="api_error")))

    assert await seat.holds() == (False, set())
    row = await seat.row(identity)
    assert row.failure == "api_error" and row.failed_at is not None
    assert read_failed(await seat.block(block)) == "api_error"


@pytest.mark.anyio
async def test_an_input_written_and_not_read_holds_until_its_fate_is_reported(
    db_factory,
):
    """Work failed with a second input written mid-work and not read yet:
    the session may still read it, so it keeps its seat and its batch through
    the failure and through the session going idle again. Only what the runner
    says of that input lets it go: here, that its process ended with it
    unread."""
    seat = await _seat(db_factory)
    work = uuid.uuid4()
    first, _ = await seat.input(work=work)
    later, later_block = await seat.input(work=work)
    for identity in (first, later):
        await seat.apply(seat.report(InputWritten(identity)))
    await seat.apply(seat.report(SessionRunning(work)))
    await seat.apply(seat.report(InputRead(InputReceipt(first, "native_echo", work))))

    await seat.apply(seat.report(_idle(work, first.input_id, failure="api_error")))
    assert await seat.holds() == (True, {later_block})
    assert (await seat.row(later)).failed_at is None

    other = uuid.uuid4()
    await seat.apply(seat.report(SessionRunning(other)))
    await seat.apply(seat.report(_idle(other)))
    assert await seat.holds() == (True, {later_block})

    await seat.apply(seat.report(InputDropped(later, "exited")))
    await seat.apply(seat.report(SessionExited("launch-1", current=True)))
    assert await seat.holds() == (False, set())
    assert consumed_turn(await seat.block(later_block)) is None
