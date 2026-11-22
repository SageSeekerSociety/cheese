"""Which process an event is about is the epoch it carries, and only the seat's
current registration's process ending ends its session.

- A runner that died with its process ends the session then: the machine's
  answer that it is not there is that process's exit, and what was handed to
  it stops holding its seat.
- The runner started after it reports the earlier process's ending for it:
  that ending is not the restarted session's, which this process keeps
  holding.
- A start that finds the runner still there reads that runner's own ending as
  the session's: the host lets the session go.
- This process registering the runner again, while a read under the epoch it
  replaces is on its way, is not another process taking the session over.

Real code paths: SessionHost.start / attach / read, ClaudeCodeDriver.launch and
its greeting, the real Reading and _pump_reports, and the real claude_code
Runner's dispatch (register / ping / read), Outbox and `_report_left_exit` /
`_report_exit`. Only the transport (hub) and the screen machinery are stand-ins:
the screen keeps a live runner and opens a new one (a fresh Runner with the
launch name it was given) where the old one is gone, as `_ensure_screen` does.
No agent process is started; `alive` is set on the runner instance.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from app.domain.agent import runner_reports
from app.domain.agent.device_hub import DeviceCallError
from app.domain.agent.harness.claude_code.runner import LIVE_LAUNCH, Runner
from app.domain.agent.harness.driven import lifecycle
from app.domain.agent.models import SessionState
from app.domain.agent.reads import Ended, Report, SessionExited
from app.domain.agent.session_host import host as host_module
from app.domain.agent.session_host.contract import (
    Access,
    Owner,
    SessionRef,
    SessionSpec,
)
from app.domain.agent.session_host.host import SessionHost
from app.domain.agent.session_state import SeatKey
from app.domain.conversation.models import Conversation
from app.domain.delivery.input_holds import seat_has_unfinished_input
from tests.turn_log import a_topic

SEAT = "cheese-test"


def _live_runner(state, launch: str) -> Runner:
    """A runner as `Runner.start` leaves it, minus the agent process."""
    runner = Runner(state, launch=launch)
    runner.alive = lambda: True  # type: ignore[method-assign]
    runner.session_id = "conv"
    runner._report_left_exit()
    runner.journal.remember(LIVE_LAUNCH, runner.launch)
    return runner


class _Hub:
    """The transport to the session's host: calls go to the runner that is
    there now, and a gone one answers as the connector does."""

    def __init__(self) -> None:
        self.runner: Runner | None = None
        #: Run as a register call reaches the runner, before it is handled.
        self.before_register: Callable[[Runner], Awaitable[None]] | None = None
        self.calls: list[tuple[str, dict]] = []

    def is_online(self, device_id: str) -> bool:
        return True

    async def call_executor(self, device_id, state, method, params, **kwargs):
        self.calls.append((method, dict(params)))
        runner = self.runner
        if runner is None:
            raise DeviceCallError(
                "dial unix /x/cheese-execution-1.sock: connect: no such file or directory"
            )
        if method == "register" and self.before_register is not None:
            await self.before_register(runner)
        try:
            return await runner.dispatch(method, params)
        except lifecycle.StaleEpoch as exc:
            # A refusal comes back as its text (`lifecycle.stale`).
            raise DeviceCallError(str(exc)) from None

    async def exec(self, device_id, argv, **kwargs):
        return {"exit": 0, "stdout": ""}


class _Screens:
    """A live runner's screen is kept; a gone one is replaced by a new runner
    started under the launch name the driver chose."""

    def __init__(self, hub: _Hub, state) -> None:
        self.hub = hub
        self.state = state
        self.n = 0
        self.sid = ""
        self.launched_names: list[str] = []

    async def _ensure_screen(self, *, launch, runner_alive, **kwargs):
        if self.hub.runner is not None:
            return SimpleNamespace(sid=self.sid)
        self.n += 1
        self.sid = f"screen-{self.n}"
        self.launched_names.append(launch.launch_name)
        self.hub.runner = _live_runner(self.state, launch.launch_name)
        return SimpleNamespace(sid=self.sid)


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(host_module, "READ_WAIT_S", 0.05)
    monkeypatch.setattr(host_module, "IDLE_GONE_READ_S", 0.05)
    monkeypatch.setattr(host_module, "RETRY_S", 0.05)


def _access(project_id, place_id) -> Access:
    return Access(
        "tok",
        None,
        host="center",
        owner=Owner(project_id, place_id, "res", SEAT, SEAT, user_id=1),
    )


def _reader(
    host: SessionHost, ref, handed: list, errors: list, apply=None, every=False
):
    """Reads the session as the room does, applying each report (each event,
    for ``every``) before taking the next."""

    async def run():
        reading = host.read(ref)
        try:
            item = await anext(reading)
            while True:
                try:
                    handed.append(item.event)
                    if apply is not None and (every or isinstance(item.event, Report)):
                        await apply(item.event)
                except Exception as exc:  # the room's `_poll`: thrown back
                    errors.append(exc)
                    item = await reading.athrow(exc)
                    continue
                item = await anext(reading)
        except StopAsyncIteration:
            return

    return asyncio.create_task(run())


def _exits(handed):
    return [
        e.fact
        for e in handed
        if isinstance(e, Report) and isinstance(e.fact, SessionExited)
    ]


@pytest.mark.anyio
async def test_an_earlier_process_exit_after_a_restart_keeps_the_session(
    tmp_path, fast, db_factory
):
    topic = await a_topic(db_factory)
    async with db_factory() as session:
        project = (await session.get(Conversation, topic)).project_id
    chat = SimpleNamespace(
        session_factory=db_factory, live=SimpleNamespace(unread_inputs={})
    )

    async def apply(report):
        await runner_reports.apply(chat, report)

    hub = _Hub()
    screens = _Screens(hub, tmp_path / "state")
    host = SessionHost(hub=hub, screens=screens, session_factory=db_factory)  # type: ignore[arg-type]
    ref = SessionRef("claude-code", "p/x/seat")
    access = _access(project, topic)
    spec = SessionSpec("S", "m")

    await host.start(ref, spec, access)
    running = host._running[ref]
    handed: list = []
    errors: list = []
    reader = _reader(host, ref, handed, errors, apply=apply)
    await asyncio.sleep(0.3)

    # The runner dies with its process: nothing reports its ending, and the
    # reading of it ends.
    hub.runner = None
    await asyncio.wait_for(reader, 5)
    assert running.answering is False

    # The next message starts the session again, and it is read again.
    await host.start(ref, spec, access)
    running = host._running[ref]
    reader = _reader(host, ref, handed, errors, apply=apply)
    await asyncio.sleep(0.6)
    async with db_factory() as session:
        row = await session.scalar(
            select(SessionState).where(
                SessionState.conversation_id == topic,
                SessionState.agent_handle == SEAT,
                SessionState.harness == "claude-code",
            )
        )
    reader.cancel()
    await asyncio.gather(reader, return_exceptions=True)
    reports = [
        e for e in handed if isinstance(e, Report) and isinstance(e.fact, SessionExited)
    ]
    assert reports, "no exit was read"
    assert not reports[0].of_this_registration, (
        "the earlier process's exit was read as the restarted session's"
    )
    assert row is not None and row.exited_at is None
    assert host._running.get(ref) is running, "the restarted session was forgotten"


@pytest.mark.anyio
async def test_a_runner_gone_unreported_stops_holding_its_seat(
    tmp_path, fast, db_factory
):
    """A message handed to a session whose runner then vanishes without a
    word (killed, or its machine rebooted) does not keep the seat from the
    next message: the seat reads as idle and nothing unread holds it."""
    topic = await a_topic(db_factory)
    async with db_factory() as session:
        project = (await session.get(Conversation, topic)).project_id
    chat = SimpleNamespace(
        session_factory=db_factory, live=SimpleNamespace(unread_inputs={})
    )
    key = SeatKey(topic, SEAT, "claude-code")

    async def apply(event):
        if isinstance(event, Report):
            await runner_reports.apply(chat, event)
        elif isinstance(event, Ended) and event.epoch is not None:
            await runner_reports.gone(chat, key, event.epoch)

    hub = _Hub()
    host = SessionHost(
        hub=hub,
        screens=_Screens(hub, tmp_path / "state"),  # type: ignore[arg-type]
        session_factory=db_factory,
    )
    ref = SessionRef("claude-code", "p/x/seat")
    await host.start(ref, SessionSpec("S", "m"), _access(project, topic))
    handed: list = []
    reader = _reader(host, ref, handed, [], apply=apply, every=True)
    await asyncio.sleep(0.3)

    # The session takes a message and starts on it.
    work = uuid.uuid4()
    async with db_factory() as session:
        await session.execute(
            text(
                "INSERT INTO native_inputs (id, project_id, conversation_id, "
                "recipient_handle, harness, native_session_id, input_id, "
                "work_id, held_block_ids, released_block_ids, block_ids, "
                "registered_at) VALUES (:id, :project, :topic, :seat, "
                "'claude-code', 'conv', :work, :work, '[]', '[]', '[]', now())"
            ),
            {
                "id": uuid.uuid4(),
                "project": project,
                "topic": topic,
                "seat": SEAT,
                "work": work,
            },
        )
        await session.commit()
    runner = hub.runner
    assert runner is not None
    runner._emit(
        lifecycle.INPUT,
        status=lifecycle.WRITTEN,
        input_id=str(work),
        work_id=str(work),
        session_id="conv",
    )
    runner._emit(
        lifecycle.STATUS,
        worker_status=lifecycle.RUNNING,
        work_id=str(work),
        unsolicited=False,
    )
    runner.announce()
    await asyncio.sleep(0.4)

    async def seat():
        async with db_factory() as session:
            state = await session.scalar(
                select(SessionState).where(
                    SessionState.conversation_id == topic,
                    SessionState.agent_handle == SEAT,
                )
            )
            held = await seat_has_unfinished_input(session, topic, SEAT)
            return state.worker_status, held

    assert await seat() == (lifecycle.RUNNING, True)

    # Its runner is gone with its process, and reports nothing.
    hub.runner = None
    await asyncio.wait_for(reader, 5)

    assert await seat() == (lifecycle.IDLE, False)
    assert host._running.get(ref) is None


@pytest.mark.anyio
async def test_a_runner_a_start_found_running_ends_the_session_by_its_exit(
    tmp_path, fast
):
    async def registrar(key):
        return 2

    hub = _Hub()
    state = tmp_path / "state"
    # A runner the previous backend left running, registered under epoch 1;
    # this process holds nothing for it (its attach did not happen).
    hub.runner = _live_runner(state, "L-real")
    hub.runner.outbox.register(1)
    screens = _Screens(hub, state)
    screens.sid = "screen-old"
    host = SessionHost(hub=hub, screens=screens, register=registrar)  # type: ignore[arg-type]
    ref = SessionRef("claude-code", "p/x/seat")
    await host.start(ref, SessionSpec("S", "m"), _access(uuid.uuid4(), uuid.uuid4()))
    running = host._running[ref]
    handed: list = []
    errors: list = []
    reader = _reader(host, ref, handed, errors)
    await asyncio.sleep(0.3)

    # The session sits idle and its runner lets it go: it reports its own end.
    hub.runner.alive = lambda: False  # type: ignore[method-assign]
    hub.runner._report_exit(0, "")
    await asyncio.sleep(0.4)
    exits = _exits(handed)
    reader.cancel()
    await asyncio.gather(reader, return_exceptions=True)
    assert exits and exits[0].launch == "L-real"
    assert running.launched.name == "L-real"
    assert host._running.get(ref) is None, (
        "the runner's own exit was not read as the session's"
    )


@pytest.mark.anyio
@pytest.mark.parametrize("event_during_register", [False, True])
async def test_registering_the_runner_again_is_not_a_takeover(
    tmp_path, fast, db_factory, event_during_register
):
    topic = await a_topic(db_factory)
    async with db_factory() as session:
        project = (await session.get(Conversation, topic)).project_id
    chat = SimpleNamespace(
        session_factory=db_factory, live=SimpleNamespace(unread_inputs={})
    )

    async def apply(report):
        await runner_reports.apply(chat, report)

    hub = _Hub()
    state = tmp_path / "state"
    hub.runner = _live_runner(state, "L-real")
    screens = _Screens(hub, state)
    screens.sid = "screen-old"
    host = SessionHost(hub=hub, screens=screens, session_factory=db_factory)  # type: ignore[arg-type]
    ref = SessionRef("claude-code", "p/x/seat")
    access = _access(project, topic)

    # A deploy: the new backend takes the session up as it is.
    found = await host.attach(ref, access)
    assert found is not None and found.alive
    running = host._running[ref]
    handed: list = []
    errors: list = []
    reader = _reader(host, ref, handed, errors, apply=apply)
    await asyncio.sleep(0.3)

    if event_during_register:

        async def busy(runner):
            # The session reports something while the register call is on
            # its way to the runner (the database already issued epoch 2).
            runner._emit(
                lifecycle.STATUS,
                worker_status=lifecycle.RUNNING,
                work_id=str(uuid.uuid4()),
                unsolicited=False,
            )
            runner.announce()
            await asyncio.sleep(0.3)

        hub.before_register = busy

    # The first message after the deploy: `ensure` starts the seat's session.
    await host.start(ref, SessionSpec("S", "m"), access)
    await asyncio.sleep(0.3)
    reader.cancel()
    await asyncio.gather(reader, return_exceptions=True)
    assert host._running.get(ref) is running, "this process forgot its own session"
