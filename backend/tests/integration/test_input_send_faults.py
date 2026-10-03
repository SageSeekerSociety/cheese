"""Real PostgreSQL commit faults at both sides of a scripted channel call.

The channel counts admitted input IDs. It is not a native harness or model; these
cases prove runtime failure classification and durable receipt retry only.
"""

import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.deps import get_chat_service
from app.domain.agent.harness import Opening, SessionRef
from app.domain.agent.harness.driven.runtime import DrivenRuntime
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


class Channel:
    name = "fault-channel"

    def __init__(self, handle):
        self.handle = handle
        self.admitted = []

    async def call(self, handle, method, params):
        assert handle is self.handle
        if method == "ping":
            return {"working": True}
        self.admitted.append(params.copy())
        return {"input_id": params["input_id"]}

    async def images(self, handle, images):
        return images


class Runtime(DrivenRuntime):
    harness = "claude_code"
    steer = "steer"

    async def ensure(self, session, opening, *, work_id=None):
        return self.channel.handle

    def conversation(self, handle):
        return handle.native_session_id

    def working(self, status):
        return status["working"]

    async def _consume(self, *args):
        pass

    async def reconcile_memory(self, topic):
        pass

    def _listen(self, seat):
        # No reader/model is started by this fault-classification test.
        pass


@pytest.mark.parametrize("mode", ["initial", "busy"])
@pytest.mark.parametrize("fault", ["registration", "accepted"])
def test_commit_fault_does_not_turn_an_admitted_input_into_a_new_send(
    client, mode, fault
):
    async def run():
        factory = client.test_request_factory
        chat = app.dependency_overrides[get_chat_service]()
        ref = SessionRef(
            uuid.uuid4(), uuid.uuid4(), "cheese-test", harness="claude_code"
        )
        work = uuid.uuid4()
        handle = SimpleNamespace(
            session=ref,
            agent_handle=ref.agent_handle,
            native_session_id=str(uuid.uuid4()),
        )
        channel = Channel(handle)
        runtime = Runtime(channel)
        seat = (ref.topic_id, ref.agent_handle)
        runtime.live[seat] = handle
        runtime.work[seat] = work
        delivery_id, attempt = uuid.uuid4(), uuid.uuid4()
        async with factory() as session:
            session.add(
                Delivery(
                    id=delivery_id,
                    event_id=uuid.uuid4(),
                    recipient_handle=ref.agent_handle,
                    topic_id=ref.topic_id,
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

        runtime.bind_reader(room_reader(receipts=receipt))
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
                        "answer the grouped question",
                        Opening(system_prompt=""),
                        work_id=work,
                        on_mark=lambda _: None,
                        register_input=register,
                    )
                else:
                    await runtime.deliver(
                        ref.topic_id,
                        "correct the prior answer",
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
            uuid.UUID(project), uuid.UUID(topic), "cheese-test", harness="claude_code"
        )
        work = uuid.uuid4()
        handle = SimpleNamespace(
            session=ref,
            agent_handle=ref.agent_handle,
            native_session_id=str(uuid.uuid4()),
        )
        channel = Channel(handle)
        runtime = Runtime(channel)
        runtime.live[(ref.topic_id, ref.agent_handle)] = handle
        runtime.work[(ref.topic_id, ref.agent_handle)] = work
        runtime.bind_reader(room_reader(receipts=chat.confirm_prompt_receipt))
        monkeypatch.setattr(chat._compute, "_runtimes", lambda: [runtime])
        chat._active_turn_ids[ref.topic_id] = {work}
        injected = []

        def fail_commit(session):
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
            delivered = await chat.merge_into_running_turn(
                ref.topic_id, [], "new answer", "user-1"
            )
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
        chat._active_turn_ids.pop(ref.topic_id, None)

    client.portal.call(run)
