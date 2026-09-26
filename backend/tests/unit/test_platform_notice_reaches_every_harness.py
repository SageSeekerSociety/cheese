"""A platform notice reaches the running turn on every harness, and only it.

The silence reminder, a doc edit, a note from another thread: each goes out as
``ChatService.notify_running_turn`` → ``compute.deliver(topic, text,
expected_work_id=…)``, and each harness's runtime turns that into its own
mid-turn verb — Claude Code's ``steer`` written at the next tool boundary,
Codex's ``send`` that its runner turns into ``turn/steer``, pi's ``steer``.
The work id is the guard: a notice meant for a turn that has since been
replaced must not land in the next one.

Claude Code runs its real runner behind ``StubChannel``; Codex and pi run
their real runtimes against a runner that answers the three calls this needs.
"""

import asyncio
import time
import uuid
from unittest.mock import AsyncMock

import pytest

from app.domain.agent.harness import CLAUDE_CODE, Opening, SessionRef
from app.domain.agent.harness.codex.runtime import CodexRuntime
from app.domain.agent.harness.codex.runtime import Handle as CodexHandle
from app.domain.agent.harness.pi.runtime import Handle as PiHandle
from app.domain.agent.harness.pi.runtime import PiRuntime
from tests.conftest import StubChannel

NOTICE = "【平台】You have published nothing to this room for 10 minutes."


class _OpenTurn(StubChannel):
    """A Claude Code session that takes the first input and keeps working."""

    def __init__(self) -> None:
        super().__init__()
        self.opened = False

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
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
            return {"events": [], "entries": []}
        if method == "ping":
            return self.working(self)
        if self.turns:
            self.mid_turn.append({"method": method, **params})
        else:
            self.turns.append(params)
        return {"ok": True, "turn_id": "turn"}


def _driven(runtime_class, handle_class, harness, working, tmp_path):
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), harness=harness)
    handle = handle_class(session, "device", "/state", "id", "agent", tmp_path / "m")
    runner = _Runner(working)
    channel = AsyncMock()
    channel.ensure.return_value = handle
    channel.images.return_value = []
    channel.call.side_effect = runner.call
    runtime = runtime_class(channel)
    return session, runtime, lambda: [m["text"] for m in runner.mid_turn], runner


async def _claude_code(tmp_path):
    channel = _OpenTurn()
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese", harness=CLAUDE_CODE)

    def heard():
        said = []
        for message in channel.sessions[session.topic_id].written:
            if message.get("type") != "user":
                continue
            content = message["message"]["content"]
            said.append(content if isinstance(content, str) else content[0]["text"])
        return said[1:]

    return session, channel.runtime, heard, channel


async def _codex(tmp_path):
    session, runtime, heard, runner = _driven(
        CodexRuntime,
        CodexHandle,
        "codex",
        lambda r: {"turn_id": "turn" if r.turns else None},
        tmp_path,
    )
    return session, runtime, heard, runner


async def _pi(tmp_path):
    session, runtime, heard, runner = _driven(
        PiRuntime,
        PiHandle,
        "pi",
        lambda r: {"alive": True, "working": bool(r.turns), "work_id": None},
        tmp_path,
    )
    return session, runtime, heard, runner


async def _working(runtime, session) -> None:
    """Until the session says a turn is in flight — the state a notice is for."""
    deadline = time.monotonic() + 8
    while True:
        handle = runtime.live.get(session.topic_id)
        status = await runtime.channel.call(handle, "ping", {}) if handle else {}
        if runtime.working(status):
            return
        assert time.monotonic() < deadline, "the turn never started"
        await asyncio.sleep(0.01)


@pytest.mark.anyio
@pytest.mark.parametrize("wire", [_claude_code, _codex, _pi], ids=lambda w: w.__name__)
async def test_a_notice_reaches_the_turn_it_was_meant_for(tmp_path, wire):
    session, runtime, heard, _ = await wire(tmp_path)
    runtime.bind_events(AsyncMock())
    runtime.bind_activity(AsyncMock())
    runtime.bind_receipts(AsyncMock())
    runtime.bind_unread_probe(lambda _topic: None)
    work = uuid.uuid4()
    try:
        await runtime.send(
            session, "检查一下", Opening("system"), work_id=work, on_mark=lambda _: None
        )
        await _working(runtime, session)

        stale = await runtime.deliver(
            session.topic_id, "stale", expected_work_id=uuid.uuid4()
        )
        assert stale is False, "a notice for another turn must not land in this one"
        assert await runtime.deliver(session.topic_id, NOTICE, expected_work_id=work)
        deadline = time.monotonic() + 8
        while not any(NOTICE in said for said in heard()):
            assert time.monotonic() < deadline, f"the session heard {heard()}"
            await asyncio.sleep(0.01)
        assert not any("stale" in said for said in heard())
    finally:
        await runtime._detach(session.topic_id)
