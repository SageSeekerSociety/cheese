"""Each Claude Code launch is counted once, by the turn that started it, in the
count of turns a spare Claude Code would serve
(`scripts/dev/count-fresh-cold-launches.py`).

Two ways the platform's lines used to miscount one launch, both driven through
`RoomSessions.send` with the real runner and a stand-in `claude`:

- a backend that adopted a living runner (`SessionHost.attach`) and sent to it
  again logged that send as starting the process, under the same launch, so the
  one launch was counted twice;
- a greeting whose reply was lost on the way and retried logged no greeted
  line, so the launch it started was not counted at all.
"""

import asyncio
import logging
import shlex
import sys
import time
import uuid
from datetime import UTC, datetime
from itertools import count

import pytest

from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.session_host.host import SessionHost
from tests.unit.test_a_launch_says_whether_it_began_a_conversation import STAND_IN
from tests.unit.test_claude_channel_startup import AGENT, PROJECT, TOPIC, Host
from tests.unit.test_counting_the_turns_a_spare_would_serve import counter

pytestmark = pytest.mark.anyio

SEAT = SessionRef(PROJECT, TOPIC, AGENT, harness=CLAUDE_CODE)


class _Journal(logging.Handler):
    """The platform's timing lines as `journalctl -o short-iso-precise` shows
    them."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith(("room_send_timing ", "session_launch_timing ")):
            at = datetime.fromtimestamp(record.created, UTC)
            self.lines.append(
                f"{at.strftime('%Y-%m-%dT%H:%M:%S.%f%z')} dev backend[1]: {message}\n"
            )


class _Screen(Host):
    """One screen whose runner keeps running across backends; optionally
    loses the reply to the greeting that carried the launch's timing."""

    lose_greeting = False
    lost = False

    async def _ensure_screen(self, **arguments):
        if self.runners:
            # The runner already on this screen is the one that answers.
            self.command = None
        return await super()._ensure_screen(**arguments)

    async def call_executor(self, device_id, state, method, params, **kw):
        answer = await super().call_executor(device_id, state, method, params, **kw)
        if (
            self.lose_greeting
            and not self.lost
            and method == "ping"
            and answer.get("launch_timing")
            and answer.get("alive")
            and not answer.get("starting")
        ):
            self.lost = True
            raise TimeoutError("the greeting's reply was lost on the way")
        return answer


async def _nothing(*args, **kwargs) -> None:
    return None


def _backend(screen: _Screen, tmp_path) -> tuple[SessionHost, RoomSessions]:
    epochs = count(1)

    async def register(key) -> int:
        return next(epochs)

    host = SessionHost(
        screen,  # type: ignore[arg-type]
        screens=screen,  # type: ignore[arg-type]
        mirrors=tmp_path / "mirrors",
        register=register,
        hand=_nothing,
    )
    room = RoomSessions(screen, CLAUDE_CODE, host)  # type: ignore[arg-type]
    room.reader = _nothing  # type: ignore[assignment]
    return host, room


async def _register(identity) -> None:
    return None


async def _send(room: RoomSessions) -> None:
    assert await room.send(
        SEAT,
        "hello",
        system_prompt="",
        work_id=uuid.uuid4(),
        on_mark=lambda _: None,
        register_input=_register,
        # The launch's first mark, as the machine's shell writes it.
        env={"CHEESE_LAUNCH_MARKS": f"{time.time():.6f} started;"},
    )


async def _until_ready(journal: _Journal) -> None:
    async with asyncio.timeout(10):
        while not any("stage=ready" in line for line in journal.lines):
            await asyncio.sleep(0.01)


@pytest.fixture
def journal():
    handler = _Journal()
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield handler
    root.removeHandler(handler)
    root.setLevel(level)


def _screen(tmp_path, *, lose_greeting: bool = False) -> _Screen:
    stand_in = tmp_path / "claude.py"
    stand_in.write_text(STAND_IN)
    screen = _Screen(
        tmp_path, f"{shlex.quote(sys.executable)} {shlex.quote(str(stand_in))}"
    )
    screen.lose_greeting = lose_greeting
    return screen


async def test_a_runner_adopted_by_another_backend_is_not_counted_again(
    tmp_path, journal
):
    screen = _screen(tmp_path)
    host, room = _backend(screen, tmp_path)
    try:
        await _send(room)
        await _until_ready(journal)
        live = room.live[room._seat_of(SEAT)]
        access = host._running[live.ref].access
        assert access is not None
        await room.stop_listening()
        # Another backend takes the runner over and sends to it.
        host, room = _backend(screen, tmp_path)
        adopted = await host.attach(live.ref, access)
        assert adopted is not None and adopted.alive
        await _send(room)
    finally:
        screen.stop()
        await room.stop_listening()

    assert len(screen.runners) == 1
    sends = [line for line in journal.lines if " room_send_timing " in line]
    assert len(sends) == 2
    # The second send went to the runner already there.
    assert " cold=False " in sends[1] and " launch=" not in sends[1]
    [day] = counter.count(journal.lines).values()
    assert (day["cold"], day["fresh"], day["spare_helps"]) == (1, 1, 1)
    assert len(day["saving_ms"]) == 1


async def test_a_launch_whose_greeting_reply_was_lost_is_still_counted(
    tmp_path, journal
):
    screen = _screen(tmp_path, lose_greeting=True)
    _host, room = _backend(screen, tmp_path)
    try:
        await _send(room)
        await _until_ready(journal)
    finally:
        screen.stop()
        await room.stop_listening()

    assert screen.lost
    assert not any("stage=greeted" in line for line in journal.lines)
    [day] = counter.count(journal.lines).values()
    assert (day["cold"], day["fresh"], day["unmatched"], day["spare_helps"]) == (
        1,
        1,
        0,
        1,
    )
    assert len(day["saving_ms"]) == 1
