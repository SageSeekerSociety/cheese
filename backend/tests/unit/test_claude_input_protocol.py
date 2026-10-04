"""Old runners retain their conversation without gaining new input guarantees."""

import uuid
from types import SimpleNamespace

import pytest

from app.domain.agent.harness import CLAUDE_CODE, Opening, SessionRef
from app.domain.agent.harness.claude_code.protocol import (
    INPUT_PROTOCOL,
    InputProtocolUnavailable,
    accepts_inputs,
)
from app.domain.agent.harness.claude_code.runtime import ClaudeCodeRuntime


@pytest.mark.parametrize("version", [None, 1, True, "2", 3])
def test_only_exact_current_capability_allows_new_inputs(version):
    assert not accepts_inputs({"input_protocol": version})
    assert accepts_inputs({"input_protocol": INPUT_PROTOCOL})


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["send", "steer"])
async def test_old_runner_refuses_before_registration_or_external_input(operation):
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese-a", harness=CLAUDE_CODE)
    work = uuid.uuid4()
    handle = SimpleNamespace(session=session, agent_handle=session.agent_handle)
    calls = []
    registrations = []

    class Channel:
        async def call(self, held, method, params):
            assert held is handle
            calls.append(method)
            assert method == "ping"
            return {"alive": True, "working": True, "work_id": str(work)}

    runtime = ClaudeCodeRuntime(Channel())
    seat = (session.topic_id, session.agent_handle)
    runtime.live[seat] = handle
    runtime.work[seat] = work

    async def register(identity):
        registrations.append(identity)

    with pytest.raises(InputProtocolUnavailable):
        if operation == "send":
            await runtime.send(
                session,
                "answer",
                Opening(system_prompt=""),
                work_id=work,
                on_mark=lambda _: pytest.fail("must not open a work"),
                register_input=register,
            )
        else:
            await runtime.deliver(
                session.topic_id,
                "answer",
                agent_handle=session.agent_handle,
                register_input=register,
            )
    assert registrations == []
    assert set(calls) == {"ping"}
    assert runtime.live[seat] is handle
    assert runtime.work[seat] == work
