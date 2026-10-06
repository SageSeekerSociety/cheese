"""Every message put to a room's session leaves one line saying where the
platform's time went on the way to the runner."""

import ast
import asyncio
import logging
import uuid
from types import SimpleNamespace

import pytest

from app.core.errors import ValidationError
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.room.sessions import Live, RoomSessions
from app.domain.agent.session_host.contract import SessionRef as CoreRef

SLOW_SUBMIT_S = 0.2


def _timing_lines(caplog) -> list[str]:
    messages = (r.getMessage() for r in caplog.records)
    return [m for m in messages if "room_send_timing" in m]


def _phases(line: str) -> dict[str, float]:
    return ast.literal_eval(line.split("phases_ms=", 1)[1])


def _runtime(host, session: SessionRef) -> tuple[RoomSessions, tuple, Live]:
    runtime = RoomSessions(SimpleNamespace(name="device"), CLAUDE_CODE, host)
    seat = (session.topic_id, session.agent_handle)
    live = Live(
        session,
        CoreRef(CLAUDE_CODE, "home"),
        session.agent_handle,
        "conversation",
        takes_inputs=True,
    )
    runtime.live[seat] = live
    return runtime, seat, live


@pytest.mark.anyio
async def test_a_message_put_to_a_live_session_says_which_step_took_the_time(caplog):
    caplog.set_level(logging.INFO, logger="app.domain.agent.room.sessions")
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese-a", harness=CLAUDE_CODE)
    work = uuid.uuid4()

    class Host:
        """A runner that takes its time to accept the input."""

        async def send(self, ref, prompt, *, work_id):
            await asyncio.sleep(SLOW_SUBMIT_S)

        def reads_on_accept(self, ref):
            return False

    runtime, seat, live = _runtime(Host(), session)
    # The seat already has its reading; this send must not start another.
    reading = asyncio.create_task(asyncio.Event().wait())
    runtime.tasks[seat] = reading
    runtime.queues[work] = asyncio.Queue()

    async def register(identity):
        pass

    try:
        assert await runtime.send(
            session,
            "the answer",
            system_prompt="",
            work_id=work,
            on_mark=lambda _: None,
            register_input=register,
            expected_native_session=live.conversation,
        )
    finally:
        reading.cancel()

    [line] = _timing_lines(caplog)
    assert f"work={work}" in line and "outcome=ok" in line
    phases = _phases(line)
    assert {"consume", "memory", "register", "submit"} <= set(phases)
    # The wait is charged to the step that waited, and nowhere else.
    assert phases["submit"] >= SLOW_SUBMIT_S * 1000 * 0.8
    assert max(v for k, v in phases.items() if k != "submit") < SLOW_SUBMIT_S * 1000 / 2


@pytest.mark.anyio
async def test_a_message_that_never_reaches_the_session_is_still_accounted_for(caplog):
    caplog.set_level(logging.INFO, logger="app.domain.agent.room.sessions")
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese-a", harness=CLAUDE_CODE)
    work = uuid.uuid4()
    runtime = RoomSessions(SimpleNamespace(name="device"), CLAUDE_CODE, object())

    async def register(identity):
        pytest.fail("nothing may be registered for a refused message")

    with pytest.raises(ValidationError):
        await runtime.send(
            session,
            "the answer",
            system_prompt="",
            work_id=work,
            on_mark=lambda _: None,
            register_input=register,
            expected_native_session="a conversation that is gone",
        )

    [line] = _timing_lines(caplog)
    assert f"work={work}" in line and "outcome=failed" in line
