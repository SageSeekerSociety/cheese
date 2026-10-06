"""A session that is starting is answered as soon as its runner is, and its log
is read at the pace a program run on the host can bear."""

import time
from types import SimpleNamespace

import pytest

from app.domain.agent.harness.claude_code.runner import ended
from app.domain.agent.session_host import claude_code
from app.domain.agent.session_host.claude_code import ClaudeCodeDriver
from app.domain.agent.session_host.contract import SessionRef, StartRefused

READY_AFTER_S = 0.15


class _StartingRunner:
    """A host whose runner binds its socket ``ready_after`` seconds from now,
    or never, having written ``ending`` to its log."""

    def __init__(self, ready_after: float | None, ending: str = ""):
        self.started = time.monotonic()
        self.ready_after = ready_after
        self.ending = ending
        self.pings = 0
        self.log_reads = 0
        self.hub = SimpleNamespace(exec=self._exec)

    async def call(self, host, ref, method, params, **kwargs):
        assert method == "ping"
        self.pings += 1
        up = self.ready_after is not None and (
            time.monotonic() - self.started >= self.ready_after
        )
        if not up:
            raise ConnectionRefusedError("runner socket refused")
        return {"alive": True, "session_id": "conversation"}

    async def _exec(self, host, argv, **kwargs):
        self.log_reads += 1
        return {"stdout": self.ending, "exit": 0}


def _ref():
    return SessionRef("claude-code", "harness/state")


@pytest.mark.anyio
async def test_a_runner_up_in_a_moment_is_answered_in_a_moment():
    wire = _StartingRunner(ready_after=READY_AFTER_S)
    started = time.monotonic()
    status = await ClaudeCodeDriver(screens=None)._greet(  # type: ignore[arg-type]
        wire,  # type: ignore[arg-type]
        "host",
        _ref(),
        "launch",
    )
    took = time.monotonic() - started
    assert status["alive"]
    # Answered within a few polls of the runner coming up, not at the next
    # whole second.
    assert took < READY_AFTER_S * 3
    assert took < claude_code.STARTUP_POLL_S / 2
    # Nothing to read in the log while the runner is about to answer.
    assert wire.log_reads == 0


@pytest.mark.anyio
async def test_the_log_is_read_once_a_second_while_the_runner_is_late(monkeypatch):
    monkeypatch.setattr(claude_code, "STARTUP_WAIT_S", 2.5)
    wire = _StartingRunner(ready_after=None)
    with pytest.raises(StartRefused):
        await ClaudeCodeDriver(screens=None)._greet(  # type: ignore[arg-type]
            wire,  # type: ignore[arg-type]
            "host",
            _ref(),
            "launch",
        )
    # Asked often, read rarely: the pings are cheap, a log read is a program.
    assert wire.pings > wire.log_reads * 2
    assert wire.log_reads <= 3


@pytest.mark.anyio
async def test_a_runner_that_ended_stops_the_wait_with_its_own_words():
    marker = ended("launch")
    wire = _StartingRunner(ready_after=None, ending=f"{marker}\nclaude: bad flag")
    started = time.monotonic()
    with pytest.raises(StartRefused) as refused:
        await ClaudeCodeDriver(screens=None)._greet(  # type: ignore[arg-type]
            wire,  # type: ignore[arg-type]
            "host",
            _ref(),
            "launch",
        )
    # The launch's own record travels with the refusal, for 现场.
    assert "bad flag" in (refused.value.log or "")
    # Stopped at the first log read, not at the end of the window.
    assert time.monotonic() - started < claude_code.STARTUP_POLL_S * 2
