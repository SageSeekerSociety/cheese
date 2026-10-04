"""Old runners retain their conversation without gaining new input guarantees."""

import uuid
from types import SimpleNamespace

import pytest

from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.harness.claude_code.protocol import INPUT_PROTOCOL, accepts_inputs
from app.domain.agent.room.sessions import Live, RoomSessions
from app.domain.agent.session_host.contract import (
    InputProtocolUnavailable,
    SessionStatus,
)
from app.domain.agent.session_host.contract import SessionRef as CoreRef


@pytest.mark.parametrize("version", [None, 1, True, "2", 3])
def test_only_exact_current_capability_allows_new_inputs(version):
    assert not accepts_inputs({"input_protocol": version})
    assert accepts_inputs({"input_protocol": INPUT_PROTOCOL})


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["send", "steer"])
async def test_old_runner_refuses_before_registration_or_external_input(operation):
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese-a", harness=CLAUDE_CODE)
    work = uuid.uuid4()
    asked = []
    registrations = []

    class Host:
        """A session host whose runner is working, from before the receipt
        protocol."""

        async def status(self, ref):
            asked.append("status")
            return SessionStatus(
                working=True, model="", work_id=str(work), takes_inputs=False
            )

        def __getattr__(self, name):
            raise AssertionError(f"the session host was asked to {name}")

    runtime = RoomSessions(SimpleNamespace(name="device"), CLAUDE_CODE, Host())
    seat = (session.topic_id, session.agent_handle)
    live = Live(
        session,
        CoreRef(CLAUDE_CODE, "old"),
        session.agent_handle,
        "conversation",
        takes_inputs=False,
    )
    runtime.live[seat] = live
    runtime.work[seat] = work

    async def register(identity):
        registrations.append(identity)

    with pytest.raises(InputProtocolUnavailable):
        if operation == "send":
            await runtime.send(
                session,
                "answer",
                system_prompt="",
                work_id=work,
                on_mark=lambda _: pytest.fail("must not open a work"),
                register_input=register,
            )
        else:
            await runtime.steer(
                session.topic_id,
                "answer",
                agent_handle=session.agent_handle,
                register_input=register,
            )
    assert registrations == []
    assert set(asked) <= {"status"}
    assert runtime.live[seat] is live
    assert runtime.work[seat] == work
