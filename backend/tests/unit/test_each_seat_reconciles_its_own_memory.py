"""A room with two teammates live still reconciles memory, through the seat whose
turn it is.

The memory relay used to ask for "the room's only live seat" and got no answer
once a second teammate was seated, so neither seat's session was reconciled
and an edit stayed on its disk until one of them went away (FB-79: an index
edit from 10:06 reached the platform at 15:14). Each turn's two moments — the
input going in and the turn ending — belong to one seat, and that seat's
session is the one asked.
"""

import asyncio
import time
import uuid
from unittest.mock import AsyncMock

import pytest

from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from tests.conftest import StubChannel
from tests.support.room_reader import room_reader


class _Seats(StubChannel):
    """Two scripted sessions in one room; records whose runner was asked to
    reconcile its memory."""

    def __init__(self) -> None:
        super().__init__()
        self.reconciled: list[str] = []

    async def call(self, handle, method, params):
        if method == "memory":
            self.reconciled.append(handle.agent_handle)
        return await super().call(handle, method, params)


@pytest.mark.anyio
async def test_a_turn_in_a_room_with_two_live_seats_reconciles_its_own_session(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    channel = _Seats()
    runtime = channel.runtime
    answered: list[tuple[str, dict | None]] = []

    async def memory(session: SessionRef) -> None:
        answer = await runtime.memory(session, {"scopes": {"team": {}}})
        answered.append((session.agent_handle, answer))

    runtime.report_to(
        room_reader(events=AsyncMock(), activity=AsyncMock(), receipts=AsyncMock()),
        unread=lambda _topic: None,
        memory=memory,
    )
    project, room = uuid.uuid4(), uuid.uuid4()
    first = SessionRef(project, room, "cheese", harness=CLAUDE_CODE)
    second = SessionRef(project, room, "second", harness=CLAUDE_CODE)
    try:
        for session in (first, second):
            await runtime.send(
                session,
                "记一下",
                system_prompt="system",
                work_id=uuid.uuid4(),
                on_mark=lambda _: None,
                register_input=AsyncMock(),
            )
        deadline = time.monotonic() + 8
        while channel.reconciled.count("second") < 2:
            assert time.monotonic() < deadline, f"reconciled {channel.reconciled}"
            await asyncio.sleep(0.01)
        assert runtime._seat_of(first) in runtime.live
        assert runtime._seat_of(second) in runtime.live
        assert all(answer is not None for _, answer in answered), answered
        assert [agent for agent, _ in answered].count("second") == 2
    finally:
        for session in (first, second):
            await runtime._detach(runtime._seat_of(session))
