"""An input the session host could not even try to send is not an uncertain one.

When the host stops before the write (the session is not this process's, or
could not be started again), the input is certainly not in the session. The
room must not hold it for reconciliation, which nothing would ever settle: the
registration is withdrawn, the seat is free, and the delivery attempt goes back
to being retried. An error after the write began stays uncertain as before.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.api.deps import get_chat_service
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.room.sessions import Live, RoomSessions
from app.domain.agent.session_host.contract import (
    InputUnconfirmed,
    SessionError,
    SessionStatus,
)
from app.domain.agent.session_host.contract import SessionRef as CoreRef
from app.domain.delivery.input_identity import (
    InputEffects,
    InputNotSent,
    InputOutcomeUnconfirmed,
)
from app.domain.delivery.models import Delivery, NativeInput
from app.main import app
from tests.support.room_reader import room_reader


class Host:
    """A session host whose every send fails the way ``failure`` says."""

    def __init__(self, work: uuid.UUID, failure: Exception):
        self.work = work
        self.failure = failure

    def reads_on_accept(self, ref) -> bool:
        return False

    async def send(self, ref, prompt, *, work_id):
        raise self.failure

    steer = send

    async def status(self, ref):
        return SessionStatus(
            working=True, model="", work_id=str(self.work), takes_inputs=True
        )


class Runtime(RoomSessions):
    def _listen(self, seat, *, recovered=False):
        pass

    async def ensure(self, session, **_):
        # The seat's session is already live: nothing is started.
        return self.live[self._seat_of(session)]


FAILURES = {
    # What the host raises when it never reached the write.
    "not_started": SessionError("The session was not started by this process"),
    # What it raises when the write may have reached the runner.
    "after_write": InputUnconfirmed(accepted=False),
}


@pytest.mark.parametrize("mode", ["initial", "busy"])
@pytest.mark.parametrize("failure", sorted(FAILURES))
def test_an_input_never_sent_is_withdrawn_and_one_maybe_sent_is_kept(
    client, mode, failure
):
    async def run():
        factory = client.test_request_factory
        chat = app.dependency_overrides[get_chat_service]()
        ref = SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese-test", harness=CLAUDE_CODE)
        work = uuid.uuid4()
        runtime = Runtime(
            SimpleNamespace(name="not-sent"), CLAUDE_CODE, Host(work, FAILURES[failure])
        )
        seat = (ref.topic_id, ref.agent_handle)
        runtime.live[seat] = Live(
            ref, CoreRef(CLAUDE_CODE, "not-sent"), ref.agent_handle, "conversation"
        )
        runtime.work[seat] = work
        runtime.report_to(room_reader(), unread=lambda _topic: None, memory=AsyncMock())
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
        register = chat._input_registrar(
            InputEffects(delivery_id=delivery_id, attempt_id=attempt)
        )
        expected = InputNotSent if failure == "not_started" else InputOutcomeUnconfirmed
        with pytest.raises(expected) as caught:
            if mode == "initial":
                await runtime.send(
                    ref,
                    "start on the report",
                    system_prompt="",
                    work_id=work,
                    on_mark=lambda _: None,
                    register_input=register,
                )
            else:
                await runtime.steer(
                    ref.topic_id,
                    "one more thing",
                    expected_work_id=work,
                    register_input=register,
                )
        identity = caught.value.identity
        async with factory() as session:
            row = await session.scalar(
                select(NativeInput).where(NativeInput.input_id == identity.input_id)
            )
            state = (await session.get(Delivery, delivery_id)).state
        if failure == "not_started":
            # Nothing holds the seat, and the attempt ends as a retry.
            assert row is None
            assert state == "claimed"
            assert "not started" in str(caught.value)
        else:
            assert row is not None and row.terminated_at is None
            assert state == "sending"

    client.portal.call(run)
