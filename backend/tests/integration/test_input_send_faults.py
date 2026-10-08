"""Real PostgreSQL commit faults at both sides of a scripted session host's call.

The host counts admitted input IDs. It is not a native harness or model; these
cases prove the room's failure classification and durable receipt retry only.
"""

import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.deps import get_chat_service
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.room.sessions import Live, RoomSessions
from app.domain.agent.session_host.contract import SessionRef as CoreRef
from app.domain.agent.session_host.contract import SessionStatus
from app.domain.delivery.input_identity import (
    InputEffects,
    InputOutcomeUnconfirmed,
    InputReceipt,
    InputReconciliationPending,
)
from app.domain.delivery.models import Delivery, NativeInput
from app.main import app
from tests.support.room_reader import room_reader

_COMMIT_PHASE = ContextVar("input_send_fault_phase", default=None)
# The commit listener is process-wide, and the app's periodic jobs commit too.
# A fault is injected only into commits made inside the call under test.
_UNDER_TEST = ContextVar("input_send_fault_scope", default=False)


class Host:
    """A session host whose runner takes every input it is handed, working."""

    def __init__(self, work: uuid.UUID):
        self.work = work
        self.admitted: list[dict] = []

    def reads_on_accept(self, ref) -> bool:
        return True

    async def send(self, ref, prompt, *, work_id):
        self.admitted.append(
            {"input_id": str(prompt.id), "work_id": str(work_id), "text": prompt.text}
        )

    steer = send

    async def status(self, ref):
        return SessionStatus(working=True, model="", work_id=str(self.work))


class Runtime(RoomSessions):
    def _listen(self, seat, *, recovered=False):
        # No reader/model is started by this fault-classification test.
        pass

    async def ensure(self, session, **_):
        # The seat's session is already live (`_seated`): nothing is started.
        return self.live[self._seat_of(session)]


def _seated(ref: SessionRef, host: Host) -> Runtime:
    """A room whose seat ``ref`` has a live session, working on the host's work."""
    runtime = Runtime(SimpleNamespace(name="fault-channel"), CLAUDE_CODE, host)
    seat = (ref.topic_id, ref.agent_handle)
    runtime.live[seat] = Live(
        ref, CoreRef(CLAUDE_CODE, "fault"), ref.agent_handle, "conversation"
    )
    runtime.work[seat] = host.work
    return runtime


@pytest.mark.parametrize("mode", ["initial", "busy"])
@pytest.mark.parametrize("fault", ["registration", "accepted"])
def test_commit_fault_does_not_turn_an_admitted_input_into_a_new_send(
    client, mode, fault
):
    async def run():
        factory = client.test_request_factory
        chat = app.dependency_overrides[get_chat_service]()
        ref = SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese-test", harness=CLAUDE_CODE)
        work = uuid.uuid4()
        channel = Host(work)
        runtime = _seated(ref, channel)
        delivery_id, attempt = uuid.uuid4(), uuid.uuid4()
        async with factory() as session:
            session.add(
                Delivery(
                    id=delivery_id,
                    event_id=uuid.uuid4(),
                    recipient_handle=ref.agent_handle,
                    conversation_id=ref.topic_id,
                    dedup_key=str(uuid.uuid4()),
                    type="mention",
                    payload={},
                    event_at=datetime.now(UTC),
                    recorded_at=datetime.now(UTC),
                    state="sending",
                    attempt_id=attempt,
                )
            )
            await session.commit()
        persisted = chat._input_registrar(
            InputEffects(delivery_id=delivery_id, attempt_id=attempt)
        )
        identities = []

        async def register(identity):
            identities.append(identity)
            token = _COMMIT_PHASE.set("registration")
            try:
                await persisted(identity)
            finally:
                _COMMIT_PHASE.reset(token)

        async def receipt(evidence):
            token = _COMMIT_PHASE.set(evidence.evidence)
            try:
                await chat.confirm_prompt_receipt(evidence)
            finally:
                _COMMIT_PHASE.reset(token)

        runtime.report_to(
            room_reader(receipts=receipt),
            unread=lambda _topic: None,
            memory=AsyncMock(),
        )
        injected = []

        def fail_commit(session):
            if _COMMIT_PHASE.get() == fault:
                # Flush the real writes first, then abort the PostgreSQL
                # transaction while commit is executing, not via a fake raise.
                session.flush()
                injected.append(fault)
                session.execute(text("SELECT 1 / 0"))

        event.listen(Session, "before_commit", fail_commit)
        try:
            if fault == "registration":
                expected = DBAPIError
            else:
                expected = InputOutcomeUnconfirmed
            with pytest.raises(expected) as caught:
                if mode == "initial":
                    await runtime.send(
                        ref,
                        "the first input",
                        system_prompt="",
                        work_id=work,
                        on_mark=lambda _: None,
                        register_input=register,
                    )
                else:
                    await runtime.steer(
                        ref.topic_id,
                        "an input added mid-work",
                        expected_work_id=work,
                        register_input=register,
                    )
        finally:
            event.remove(Session, "before_commit", fail_commit)
        assert injected == [fault]
        (identity,) = identities
        async with factory() as session:
            row = await session.scalar(
                select(NativeInput).where(NativeInput.input_id == identity.input_id)
            )
            assert (await session.get(Delivery, delivery_id)).state == "sending"
            if fault == "registration":
                assert channel.admitted == []
                assert row is None
                return
            assert len(channel.admitted) == 1
            assert channel.admitted[0]["input_id"] == str(identity.input_id)
            assert caught.value.identity == identity
            assert caught.value.accepted is True
            assert isinstance(caught.value.__cause__, DBAPIError)
            assert row is not None and row.accepted_at is None
            assert row.echoed_at is None and row.settled_at is None
        # Retry evidence, not transport. The same input UUID settles from the
        # durable registration; no second channel call is made.
        await chat.confirm_prompt_receipt(InputReceipt(identity, "accepted"))
        await chat.confirm_prompt_receipt(InputReceipt(identity, "native_echo"))
        await chat.confirm_prompt_receipt(InputReceipt(identity, "native_echo"))
        async with factory() as session:
            rows = list(await session.scalars(select(NativeInput)))
            assert len(rows) == 1
            assert rows[0].input_id == identity.input_id
            assert rows[0].accepted_at is not None
            assert rows[0].settled_at is not None
            assert (await session.get(Delivery, delivery_id)).state == "received"
        assert len(channel.admitted) == 1
        assert identity.work_id == work
        assert (identity.input_id == work) is (mode == "initial")

    client.portal.call(run)


@pytest.mark.parametrize("fault", ["registration", "accepted"])
def test_live_chat_retains_uncertain_input_instead_of_authorizing_queue(
    client, monkeypatch, fault
):
    from app.domain.agent.runtime import AgentWorkRunner
    from tests.integration.test_same_handle_note_and_timed_delivery import (
        _project,
        _room,
    )

    project = _project(client, "input fault routing")
    topic = _room(client, project, "live input fault")

    async def run():
        chat = app.dependency_overrides[get_chat_service]()
        factory = client.test_request_factory
        ref = SessionRef(
            uuid.UUID(project), uuid.UUID(topic), "cheese-test", harness=CLAUDE_CODE
        )
        work = uuid.uuid4()
        channel = Host(work)
        runtime = _seated(ref, channel)
        runtime.report_to(
            room_reader(receipts=chat.confirm_prompt_receipt),
            unread=lambda _topic: None,
            memory=AsyncMock(),
        )
        monkeypatch.setattr(chat._compute, "_runtimes", lambda: [runtime])
        chat.live.active_turn_ids[ref.topic_id] = {work}
        injected = []

        def fail_commit(session):
            if not _UNDER_TEST.get():
                return
            phase = (
                "accepted"
                if any(
                    isinstance(row, NativeInput) and row.accepted_at is not None
                    for row in session.dirty
                )
                else "registration"
            )
            if phase == fault:
                session.flush()
                injected.append(phase)
                session.execute(text("SELECT 1 / 0"))

        event.listen(Session, "before_commit", fail_commit)
        try:
            # The real ChatService -> ComputePool -> runtime -> channel ->
            # ChatService commit path is retained, not a fake exception result.
            scope = _UNDER_TEST.set(True)
            try:
                delivered = await chat.merge_into_running_turn(
                    ref.topic_id, [], "new answer", "user-1"
                )
            finally:
                _UNDER_TEST.reset(scope)
        finally:
            event.remove(Session, "before_commit", fail_commit)
        assert injected == [fault]
        if fault == "registration":
            assert delivered is False
            assert channel.admitted == []
            async with factory() as session:
                assert list(await session.scalars(select(NativeInput))) == []
        else:
            assert isinstance(delivered, InputReconciliationPending)
            assert delivered.accepted is True
            assert len(channel.admitted) == 1
            async with factory() as session:
                rows = list(await session.scalars(select(NativeInput)))
                assert len(rows) == 1
                assert rows[0].input_id == delivered.identity.input_id
                assert rows[0].accepted_at is None

            # Run the caller's decision too: a reconciliation result must stop
            # normal queue admission and must not be reported as delivered=True.
            async def pending(*args, **kwargs):
                return delivered

            monkeypatch.setattr(chat, "merge_into_running_turn", pending)
            notices = []

            async def post_event(*args, **kwargs):
                notices.append(args[3])

            runner = AgentWorkRunner.__new__(AgentWorkRunner)
            monkeypatch.setattr(runner, "_post_event", post_event)
            assert await runner._deliver_message(
                chat,
                ref.topic_id,
                uuid.uuid4(),
                landed_user_block_id=uuid.uuid4(),
                content="new answer",
                author="user-1",
            )
            assert len(notices) == 1
            assert len(channel.admitted) == 1
        chat.live.active_turn_ids.pop(ref.topic_id, None)

    client.portal.call(run)
