"""A platform notice reaches the running turn on every harness, and only it.

The silence reminder, a doc edit, a note from another thread: each goes out as
``ChatService.notify_running_turn`` → ``compute.steer(topic, text,
expected_work_id=…)``, and the session core turns that into each harness's own
mid-turn verb — Claude Code's ``steer`` written at the next tool boundary,
Codex's ``send`` that its runner turns into ``turn/steer``, pi's ``steer``.
The work id is the guard: a notice meant for a turn that has since been
replaced must not land in the next one.

Claude Code runs its real runner behind ``StubChannel``; Codex and pi run
the real room sessions and session core against a runner that answers the
three calls this needs.
"""

import asyncio
import time
import uuid
from unittest.mock import AsyncMock

import pytest

from app.domain.agent.harness import CLAUDE_CODE, CODEX, PI, SessionRef
from tests.conftest import StubChannel
from tests.support.room_reader import room_reader
from tests.support.seat_channel import SeatChannel

NOTICE = "【平台】You have published nothing to this room for 10 minutes."


class _OpenTurn(StubChannel):
    """A Claude Code session that takes the first input and keeps working."""

    def __init__(self) -> None:
        super().__init__()
        self.opened = False

    def emit_turn(
        self,
        topic_id: uuid.UUID,
        prompt: str,
        reply: str,
        *,
        agent: str | None = None,
    ) -> None:
        if not self.opened:
            self.opened = True
            self.starts(topic_id)
            self.acknowledges(topic_id, prompt)
            self.says(topic_id, "working on it")


class _Runner:
    """What the runtime needs of a runner: take a turn, say it is working,
    and take words mid-turn under the harness's verb."""

    def __init__(self, working):
        self.working = working
        self.turns: list[dict] = []
        self.mid_turn: list[dict] = []

    async def call(self, handle, method, params):
        if method in ("events", "entries"):
            # Held, as a runner holds a read with nothing to answer it with.
            await asyncio.sleep(params.get("wait", 0))
            return {"events": [], "entries": []}
        if method == "ping":
            return self.working(self)
        if self.turns:
            self.mid_turn.append({"method": method, **params})
        else:
            self.turns.append(params)
        return {"ok": True, "turn_id": "turn"}


class _Seats(SeatChannel):
    def __init__(self, harness: str, runner: _Runner) -> None:
        super().__init__(harness=harness)
        self.runner = runner

    async def open(self, session, agent, launch):
        pass

    async def call(self, handle, method, params):
        return await self.runner.call(handle, method, params)


def _driven(harness, working):
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "agent", harness=harness)
    runner = _Runner(working)
    runtime = _Seats(harness, runner).runtime
    return session, runtime, lambda: [m["text"] for m in runner.mid_turn], runner


async def _claude_code(tmp_path):
    channel = _OpenTurn()
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese", harness=CLAUDE_CODE)

    def heard():
        said = []
        for message in channel._session_for(session.topic_id).written:
            if message.get("type") != "user":
                continue
            content = message["message"]["content"]
            said.append(content if isinstance(content, str) else content[0]["text"])
        return said[1:]

    return session, channel.runtime, heard, channel


async def _codex(tmp_path):
    session, runtime, heard, runner = _driven(
        CODEX, lambda r: {"alive": True, "turn_id": "turn" if r.turns else None}
    )
    return session, runtime, heard, runner


async def _pi(tmp_path):
    session, runtime, heard, runner = _driven(
        PI, lambda r: {"alive": True, "working": bool(r.turns), "work_id": None}
    )
    return session, runtime, heard, runner


async def _working(runtime, session) -> None:
    """Until the session says a turn is in flight — the state a notice is for."""
    deadline = time.monotonic() + 8
    while True:
        live = runtime.live.get(runtime._seat_of(session))
        status = await runtime.host.status(live.ref) if live else None
        if status is not None and status.working:
            return
        assert time.monotonic() < deadline, "the turn never started"
        await asyncio.sleep(0.01)


@pytest.mark.anyio
@pytest.mark.parametrize("wire", [_claude_code, _codex, _pi], ids=lambda w: w.__name__)
async def test_a_notice_reaches_the_turn_it_was_meant_for(tmp_path, wire):
    session, runtime, heard, _ = await wire(tmp_path)
    runtime.report_to(
        room_reader(events=AsyncMock(), activity=AsyncMock(), receipts=AsyncMock()),
        unread=lambda _topic: None,
        memory=AsyncMock(),
    )
    register_input = AsyncMock()
    work = uuid.uuid4()
    try:
        await runtime.send(
            session,
            "检查一下",
            system_prompt="system",
            work_id=work,
            on_mark=lambda _: None,
            register_input=register_input,
        )
        await _working(runtime, session)

        stale = await runtime.steer(
            session.topic_id,
            "stale",
            expected_work_id=uuid.uuid4(),
            register_input=register_input,
        )
        assert stale is False, "a notice for another turn must not land in this one"
        assert await runtime.steer(
            session.topic_id,
            NOTICE,
            expected_work_id=work,
            register_input=register_input,
        )
        deadline = time.monotonic() + 8
        while not any(NOTICE in said for said in heard()):
            assert time.monotonic() < deadline, f"the session heard {heard()}"
            await asyncio.sleep(0.01)
        assert not any("stale" in said for said in heard())
    finally:
        await runtime._detach(runtime._seat_of(session))
