"""Handing the running work from one backend process to the next (#1723).

A rollout overlaps two backends on one database: the incoming one starts and
serves before the outgoing one has stopped. What must hold across that, stated
the way a person running the platform would state it:

- only one of them owns the running work at a time, and the other takes it over
  when the owner leaves, whether it leaves in an orderly way or dies;
- a message that reaches the incoming backend before it has taken over is
  answered once it has, not by a turn started blind next to a running one;
- the outgoing backend lets go of a running turn without ending it: the turn is
  still there for the incoming one to pick up, and nothing the outgoing one was
  still sending arrives after it let go;
- a session's output lands in the room once, through whichever backend listens
  to it after the handover — never through both.
"""

import asyncio
import dataclasses
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.core.ownership import OWNER_LOCK, Ownership
from app.domain.agent import attachments
from app.domain.agent.chat import ChatService
from app.domain.agent.device_hub import DeviceOffline
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.runtime import AgentWorkRunner, addressed_to_agent
from app.domain.agent.service import AgentResult
from tests.conftest import stub_compute
from tests.support.room_reader import room_reader
from tests.turn_log import a_topic, open_turn_ids
from tests.unit.test_driven_liveness import Room, Scripted


def _url(db_factory) -> str:
    return db_factory.kw["bind"].url.render_as_string(hide_password=False)


async def _until(check, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "the condition never came true"
        await asyncio.sleep(0.01)


async def _until_open(db_factory, count: int) -> None:
    deadline = time.monotonic() + 5
    while len(await open_turn_ids(db_factory)) != count:
        assert time.monotonic() < deadline, "the turn never opened"
        await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_one_backend_owns_the_running_work_and_the_next_takes_it_over(
    db_factory,
):
    outgoing, incoming = Ownership(_url(db_factory)), Ownership(_url(db_factory))
    try:
        assert await outgoing.try_acquire()
        assert not await incoming.try_acquire()

        await outgoing.release()
        await asyncio.wait_for(incoming.acquire(), timeout=5)
        assert incoming.held
    finally:
        await outgoing.release()
        await incoming.release()


@pytest.mark.anyio
async def test_a_backend_that_dies_hands_the_work_over_too(db_factory):
    """No goodbye: its database connection is simply gone, as when the
    container is killed.

    The kill names its one victim: the session holding the owner lock in THIS
    test's database. The test cluster is shared — every xdist worker has its
    own database on it, and other tests hold advisory locks of their own (a
    room-file write takes ``pg_advisory_xact_lock``) — so a kill that matched
    any advisory lock on the server ended other workers' connections mid-test.
    A bigint advisory key shows in ``pg_locks`` as its high half in ``classid``,
    its low half in ``objid``, and ``objsubid = 1``."""
    dying, incoming = Ownership(_url(db_factory)), Ownership(_url(db_factory))
    try:
        assert await dying.try_acquire()
        async with db_factory() as session:
            killed = (
                (
                    await session.execute(
                        text(
                            "SELECT pg_terminate_backend(l.pid) FROM pg_locks l"
                            " JOIN pg_database d ON d.oid = l.database"
                            " WHERE d.datname = current_database()"
                            " AND l.locktype = 'advisory' AND l.granted"
                            " AND l.classid = (CAST(:key AS bigint) >> 32)::oid"
                            " AND l.objid = (CAST(:key AS bigint) & 4294967295)::oid"
                            " AND l.objsubid = 1"
                            " AND l.pid <> pg_backend_pid()"
                        ),
                        {"key": OWNER_LOCK},
                    )
                )
                .scalars()
                .all()
            )
        assert killed == [True], killed
        await asyncio.wait_for(incoming.acquire(), timeout=5)
        assert not await dying.still_held()
    finally:
        await dying.release()
        await incoming.release()


class _Chat(ChatService):
    """Real admission and seat identity, with a scripted conversation."""

    def __init__(self, session_factory, turn, *, work_runner: AgentWorkRunner) -> None:
        super().__init__(
            work_runner=work_runner,
            session_factory=session_factory,
            base_system_prompt="",
            workspace_root="",
            compute=stub_compute(),
        )
        self._turn = turn
        self.started = 0
        self.events: list[str] = []

    async def post_system_event(self, topic_id, text, *args, **kwargs):
        self.events.append(text)
        return None

    async def recover_native_tools(self, topic_id):
        return False

    def replaying(self, topic_id):
        return None

    async def converse(self, **kwargs):
        self.started += 1
        async for frame in self._turn():
            yield frame


async def _answers():
    yield {"type": "done"}


@pytest.mark.anyio
async def test_a_message_waits_for_the_takeover_and_is_then_answered(db_factory):
    runner = AgentWorkRunner(InProcessBroker())
    chat = _Chat(db_factory, _answers, work_runner=runner)
    runner.hold_turns()

    runner.submit(
        chat,
        await a_topic(db_factory),
        author="u",
        content="fix the login page",
        addressed=addressed_to_agent("cheese-seat"),
    )
    await asyncio.sleep(0.5)
    assert chat.started == 0

    runner.start_turns()
    await _until(lambda: chat.started == 1)
    await runner.drain()


@pytest.mark.anyio
async def test_letting_go_leaves_a_delivered_turn_open_for_the_next_backend(
    db_factory,
):
    async def delivered_and_working():
        yield {"type": "prompt_delivered"}
        await asyncio.Event().wait()

    topic = await a_topic(db_factory)
    runner = AgentWorkRunner(InProcessBroker())
    runner.submit(
        _Chat(db_factory, delivered_and_working, work_runner=runner),
        topic,
        author="u",
        content="fix the login page",
        addressed=addressed_to_agent("cheese-seat"),
    )
    await _until_open(db_factory, 1)
    assert await runner.settle_deliveries(timeout_s=5) == set()

    runner.hold_turns()
    await runner.let_go()

    assert runner.active_work_count() == 0
    assert len(await open_turn_ids(db_factory)) == 1


@pytest.mark.anyio
async def test_a_send_cut_off_by_letting_go_never_arrives_afterwards(db_factory):
    arrived: list[str] = []

    async def still_sending():
        await asyncio.sleep(0.5)
        arrived.append("the prompt")
        yield {"type": "prompt_delivered"}

    runner = AgentWorkRunner(InProcessBroker())
    runner.submit(
        _Chat(db_factory, still_sending, work_runner=runner),
        await a_topic(db_factory),
        author="u",
        content="fix the login page",
        addressed=addressed_to_agent("cheese-seat"),
    )
    await _until_open(db_factory, 1)
    assert len(await runner.settle_deliveries(timeout_s=0.1)) == 1

    runner.hold_turns()
    await runner.let_go()
    await asyncio.sleep(1)

    assert arrived == []
    # Still open: the incoming backend re-sends it, as a turn nobody heard.
    assert len(await open_turn_ids(db_factory)) == 1


def _works_on_it(channel: Scripted, topic: uuid.UUID, prompt: str) -> None:
    channel.starts(topic)
    channel.acknowledges(topic, prompt)


def _said(events) -> list[str]:
    return [
        event.text
        for _, event in events
        if not isinstance(event, AgentResult) and getattr(event, "text", None)
    ]


@pytest.mark.anyio
async def test_session_output_lands_once_through_the_backend_that_took_over():
    room = Room(Scripted(_works_on_it))
    landed_by_incoming: list = []

    async def consume(_project, _topic, work, event, _eid, _seen, _unsolicited):
        landed_by_incoming.append((work, event))

    incoming = None
    try:
        await room.send("fix the login page")
        await _until(lambda: room.receipts == ["fix the login page"])

        await room.runtime.stop_listening()
        room.channel.says(room.topic, "the login page is fixed")
        # Longer than the slowest a listening backend waits between reads.
        await asyncio.sleep(1.5)
        assert "the login page is fixed" not in _said(room.events)

        # The next backend: a session core that has read nothing yet.
        incoming = room.channel.next_process()
        incoming.report_to(
            room_reader(events=consume),
            unread=lambda _topic: None,
            memory=AsyncMock(),
        )
        recovered = await incoming.recover()
        assert [ref.topic_id for ref in recovered] == [room.topic]
        await incoming.replay(recovered[0])
        await _until(lambda: "the login page is fixed" in _said(landed_by_incoming))
        await asyncio.sleep(0.3)

        assert _said(landed_by_incoming).count("the login page is fixed") == 1
        assert "the login page is fixed" not in _said(room.events)
    finally:
        await room.close()
        if incoming is not None:
            await incoming.stop_listening()


class _GatedLock:
    """A seat lock whose FIRST acquisition waits for the test's say-so.

    The stop snapshots first and then waits here; the attach that follows
    passes straight through, so its whole swap lands before the stop's first
    lock session — exactly the interleave that broke the registry (FB-56)."""

    def __init__(self, real, hold: asyncio.Event) -> None:
        self._real = real
        self._hold = hold
        self.acquisitions = 0

    async def __aenter__(self):
        self.acquisitions += 1
        if self.acquisitions == 1:
            await self._hold.wait()
        await self._real.acquire()

    async def __aexit__(self, *exc):
        self._real.release()


@pytest.mark.anyio
async def test_a_stop_whose_seat_was_won_keeps_the_new_source_and_finishes_its_own():
    """真实 stop 门闩（FB-56 attachment）：旧 stop 快照后，新 attach 先完成整个
    swap——不同会话、同一 seat——旧 stop 再进锁。新 registry、新 maps、新读取
    必须全在；另一个无干扰座位的旧读取被等完，stop 才返回，它的登记被清掉。"""
    room = Room(Scripted(_works_on_it))
    runtime = room.runtime
    try:
        await room.send("fix the login page")
        await _until(lambda: room.receipts == ["fix the login page"])
        seat_a = (room.topic, "cheese")
        live_a = runtime.live[seat_a]

        # 第二个座位（无干扰对照）：同一 runtime 上的另一个房间。
        session_b = SessionRef(
            uuid.uuid4(), uuid.uuid4(), "cheese", harness=CLAUDE_CODE
        )
        seat_b = (session_b.topic_id, "cheese")
        await runtime.send(
            session_b,
            "write the docs",
            system_prompt="",
            work_id=uuid.uuid4(),
            on_mark=lambda _: None,
            register_input=room.register_input("write the docs"),
        )
        await _until(lambda: seat_b in runtime.tasks)

        order: list[str] = []
        # 座位 B 的旧读取还在读：stop 必须等它读完。
        reading_b = runtime.tasks[seat_b]
        reading_b.cancel()

        async def drain_b() -> None:
            await asyncio.sleep(0.05)
            order.append("pollB-done")

        runtime.tasks[seat_b] = asyncio.create_task(drain_b())

        hold = asyncio.Event()
        real_lock_fn = attachments.lock
        gated = _GatedLock(real_lock_fn(seat_a), hold)
        attachments.lock = lambda seat: gated if seat == seat_a else real_lock_fn(seat)
        new_poll = None
        try:
            stop = asyncio.create_task(runtime.stop_listening())
            await asyncio.sleep(0)  # stop 快照（含旧 A/旧 B），在 seat A 锁前等待
            assert gated.acquisitions == 1

            # 新 attach 完成整个 swap：不同会话、同一 seat。
            resumed = dataclasses.replace(live_a, conversation="resumed")
            attached = await runtime._attach(resumed)
            assert attached.attachment != live_a.attachment
            # 恢复后 _listen 起的新读取。
            new_poll = asyncio.create_task(asyncio.sleep(30))
            runtime.tasks[seat_a] = new_poll

            hold.set()
            await stop
            order.append("stopped")

            assert attachments.current(seat_a) == attached.attachment, (
                "旧 stop 清新 registry"
            )
            assert runtime.live[seat_a] is attached, "新 maps 被旧 stop 清了"
            assert not new_poll.done(), "旧 stop 等/取消了新读取"

            # 身份相同的座位 B：旧读取被等完，stop 才算完。
            assert order.index("pollB-done") < order.index("stopped")
            assert seat_b not in runtime.live
            assert attachments.current(seat_b) is None
        finally:
            attachments.lock = real_lock_fn
            if new_poll is not None:
                new_poll.cancel()
            attachments.note(seat_a, None)
            attachments.note(seat_b, None)
    finally:
        await room.close()


@pytest.mark.anyio
async def test_a_same_handle_attach_is_refused_when_ownership_is_closed():
    """ownership 关闭窗口里，同 handle 的幂等快路不能放行（FB-56）：快路在锁内、
    先校 owns_sessions 再决定。窗口中 recover 沿同 handle 回来必须吃
    DeviceOffline、maps 原样；窗口过后同 handle 幂等返回、什么都不动。"""
    room = Room(Scripted(_works_on_it))
    runtime = room.runtime
    try:
        await room.send("fix the login page")
        await _until(lambda: room.receipts == ["fix the login page"])
        seat = (room.topic, "cheese")
        old = runtime.live[seat]
        reading = runtime.tasks[seat]

        runtime.bind_owns_sessions(lambda: SimpleNamespace(owns_sessions=False))
        try:
            with pytest.raises(DeviceOffline):
                await runtime._attach(old)
            assert runtime.live[seat] is old, "拒绝动了已注册的东西"
            assert runtime.tasks[seat] is reading
            assert attachments.current(seat) == old.attachment
        finally:
            runtime.bind_owns_sessions(None)

        # 窗口过后：同一会话幂等返回，不 detach、不换注册。
        assert await runtime._attach(old) is old
        assert runtime.tasks[seat] is reading
        assert attachments.current(seat) == old.attachment
    finally:
        await room.close()
