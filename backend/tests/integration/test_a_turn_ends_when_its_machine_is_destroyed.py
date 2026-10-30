"""A machine the platform destroys takes the turn that was running on it with
it; a machine that merely stopped answering takes nothing.

The difference is who said so. The platform destroys a sandbox itself, and the
runner that would have reported the turn's end goes with it — reachability is a
lease on that runner, never an exit (``harness/driven/lifecycle``). So the
destruction is the one place that can record the turn is over, and it does:
``machine.lifecycle.close_turns_of_a_destroyed_machine``. Without it nothing
ever closes the interval, and the room reports the teammate at work for as long
as the row exists — the incident of 2026-10-23, where one turn sat open for 21
hours after its sandbox was released.

A machine out of reach is NOT that. It may be asleep, offline, or still being
prepared, and the platform has no authority to end anything on it; its turns
stay open and ``death_evidence.unknown_row`` keeps them so (FB-56 legacy③).
That rule is what the second test here holds, and the change that closes
destroyed machines must not touch it.

The turns are not all a destroyed machine was given. A person's message added
into a working session (a mid-turn addition, `input_id` is not `work_id`) is no
turn's own work, so closing the turns never reaches it: once the turn it joined
has stopped, it holds its seat for good, and every later message to that
teammate is turned away without a session ever opening — eleven of them in the
incident of 2026-10-23. The third test holds the rule that the destruction ends
those too: it is the last moment anything can know they will never be read.
"""

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.api import deps as session_turn_deps
from app.api.deps import get_chat_service
from app.domain.agent import death_evidence, turn_inputs
from app.domain.agent.chat import ChatService
from app.domain.agent.models import AgentTurn, SessionState
from app.domain.agent_session.models import AgentSession
from app.domain.delivery.input_holds import seat_has_unfinished_input
from app.domain.delivery.models import NativeInput
from app.domain.machine.lifecycle import SandboxLifecycle
from app.domain.machine.models import CloudHost, MachineStatus, SandboxHome
from app.main import app
from tests.conftest import StubChannel, stub_compute
from tests.integration.conftest import (
    open_task,
    post_message,
    post_project,
    session_auth_headers,
)


class KeepsWorking(StubChannel):
    """A session that takes every input and never finishes the turn — the
    session of the incident, parked on a long command when its machine was
    taken away."""

    def emit_turn(self, topic_id, prompt, reply, *, agent=None):
        del reply
        self.starts(topic_id, agent=agent)
        self.acknowledges(topic_id, prompt, agent=agent)
        self.uses(topic_id, "Bash", agent=agent, command="sleep 600")


class Recycled(StubChannel):
    """The same machine, not answering at all: its connector is not attached,
    so ``hub.is_online`` is false and nothing at its address is asked."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[str] = []

        def refuse(device_id: str) -> bool:
            self.asked.append(device_id)
            return False

        self.host._hub.is_online = refuse


class Destroys:
    """The host's connector, answering the one action a destroy sends: it ran,
    and the home is gone."""

    def __init__(self) -> None:
        self.actions: list[str] = []

    async def exec(self, device_id, argv, *, stdin=None, timeout=60, **_):
        self.actions.append(device_id)
        return {"exit": 0, "stdout": "{}\n"}


def _backend(client, channel: StubChannel) -> ChatService:
    """One backend process's sessions, reached through ``channel``."""
    return ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/destroyed-machine-ws",
        compute=stub_compute(channel),
    )


def _until(predicate, what: str) -> None:
    deadline = time.monotonic() + 15
    while not predicate():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def _turns(client, conversation: uuid.UUID) -> list[AgentTurn]:
    async def read() -> list[AgentTurn]:
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(AgentTurn).where(AgentTurn.conversation_id == conversation)
                )
            )

    return client.portal.call(read)


def _session_state(client, conversation: uuid.UUID) -> SessionState | None:
    async def read() -> SessionState | None:
        async with client.test_factory() as session:
            return await session.scalar(
                select(SessionState).where(SessionState.conversation_id == conversation)
            )

    return client.portal.call(read)


def _backdate(client, conversation: uuid.UUID, opened_at: datetime) -> None:
    """Make the turn as old as the incident's: every deadline the sweep reads is
    the row's age, and the board's clock is the session's last report."""

    async def write() -> None:
        async with client.test_factory() as session:
            await session.execute(
                update(AgentTurn)
                .where(AgentTurn.conversation_id == conversation)
                .values(started_at=opened_at, delivered_at=opened_at)
            )
            await session.execute(
                update(SessionState)
                .where(SessionState.conversation_id == conversation)
                .values(changed_at=opened_at)
            )
            await session.commit()

    client.portal.call(write)


@contextmanager
def _working_task(client) -> Iterator[tuple[KeepsWorking, dict, str, uuid.UUID]]:
    """A task whose teammate is in the middle of its first turn, delivered, and
    whose runner has reported it at work — with this backend's sessions
    installed for the test, and taken down again however it ends."""
    data = post_project(client, {"name": "Destroyed"}, owner="alice").json()["data"]
    room = data["root_topic_id"]
    channel = KeepsWorking()
    app.dependency_overrides[get_chat_service] = lambda: _backend(client, channel)
    try:
        task = open_task(client, room, "验证一下", owner="alice")
        task_id = uuid.UUID(task["id"])
        post_message(client, task["id"], "alice", {"content": "动手吧"})
        _until(
            lambda: any(key[0] == task_id for key in channel.sessions),
            "the owner's first word never reached the task's teammate",
        )
        _until(
            lambda: any(
                turn.delivered_at is not None and turn.stopped_at is None
                for turn in _turns(client, task_id)
            ),
            "the turn never opened delivered and stayed open",
        )
        _until(
            lambda: (
                (_session_state(client, task_id) or None) is not None
                and _session_state(client, task_id).worker_status == "running"
            ),
            "the runner never reported the session at work",
        )
        yield channel, task, room, task_id
    finally:
        app.dependency_overrides.pop(get_chat_service, None)


def _seat_and_session(client, task_id: uuid.UUID) -> tuple[str, str, str]:
    """The harness, the native conversation the machine held, and the seat the
    ledger names inputs by — the three strings an input row is written under."""

    async def read() -> tuple[str, str, str]:
        async with client.test_factory() as session:
            placed = await session.scalar(
                select(AgentSession).where(AgentSession.conversation_id == task_id)
            )
            seat = await session.scalar(
                select(SessionState).where(SessionState.conversation_id == task_id)
            )
            assert placed is not None and placed.resume_token, "会话还没有 native id"
            assert seat is not None, "座位没有状态行"
            return placed.harness, placed.resume_token, seat.agent_handle

    return client.portal.call(read)


def _leave_an_unread_input(
    client,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    *,
    harness: str,
    native: str,
    seat: str,
) -> uuid.UUID:
    """One input added to a turn that ended 25 minutes ago, and never read.

    A mid-turn addition: its ``input_id`` is not its ``work_id``, so it is not
    its own work's, no turn's close accounts for it, and that soon after its
    work ended ``unread_input_with_over_work`` does not free it yet. Long enough
    ago, though, for the session to count as idle, which is what lets the idle
    sweep destroy the machine. What it holds is the seat."""

    async def write() -> uuid.UUID:
        async with client.test_factory() as session:
            (running,) = list(
                await session.scalars(
                    select(AgentTurn).where(
                        AgentTurn.conversation_id == task_id,
                        AgentTurn.stopped_at.is_(None),
                    )
                )
            )
            joined = AgentTurn(
                id=uuid.uuid4(),
                conversation_id=task_id,
                continuation_id=running.continuation_id,
                author="alice",
                content="这一句没人读到",
                started_at=running.started_at,
                delivered_at=running.delivered_at,
                stopped_at=datetime.now(UTC) - timedelta(minutes=25),
                session_id=running.session_id,
                agent_handle=running.agent_handle,
            )
            session.add(joined)
            session.add(
                NativeInput(
                    project_id=project_id,
                    conversation_id=task_id,
                    recipient_handle=seat,
                    harness=harness,
                    native_session_id=native,
                    input_id=uuid.uuid4(),
                    work_id=joined.id,
                    registered_at=datetime.now(UTC),
                )
            )
            await session.commit()
            return joined.id

    return client.portal.call(write)


def _inputs(client, task_id: uuid.UUID) -> int:
    """How many inputs the platform has handed this conversation's session."""

    async def read() -> int:
        async with client.test_factory() as session:
            return len(
                list(
                    await session.scalars(
                        select(NativeInput.input_id).where(
                            NativeInput.conversation_id == task_id
                        )
                    )
                )
            )

    return client.portal.call(read)


def _seat_is_held(client, task_id: uuid.UUID, seat: str) -> bool:
    async def read() -> bool:
        async with client.test_factory() as session:
            return await seat_has_unfinished_input(session, task_id, seat)

    return client.portal.call(read)


def _sandbox_home(client, task: dict, task_id: uuid.UUID) -> uuid.UUID:
    """A sandbox home of this session's on a host — the row a destroy names.

    Idle for 20 minutes: past the standard sandbox's ten, which is what lets
    the sweep take it (``machine.lifecycle._idle_since``)."""

    async def write() -> uuid.UUID:
        async with client.test_factory() as session:
            host = CloudHost(
                machine_id=1,
                customer_id=1,
                account_id=1,
                offering_id=1,
                hostname="host-a",
                login_user="cheese",
                cores=1,
                memory_mb=1024,
                disk_gb=10,
                status=MachineStatus.running,
            )
            session.add(host)
            await session.flush()
            placed = await session.scalar(
                select(AgentSession).where(AgentSession.conversation_id == task_id)
            )
            assert placed is not None, "the task's session was never placed"
            home = SandboxHome(
                host_id=host.id,
                spec="standard",
                project_id=uuid.UUID(task["project_id"]),
                topic_id=task_id,
                room_resource_id=str(task_id),
                resource_id=str(task_id),
                session_id=placed.id,
                active_at=datetime.now(UTC) - timedelta(minutes=20),
            )
            session.add(home)
            await session.commit()
            return home.id

    return client.portal.call(write)


def _destroy(client, home_id: uuid.UUID, task_id: uuid.UUID, hub) -> bool:
    """What the idle sweep does to one home it has picked."""

    async def go() -> bool:
        async with client.test_factory() as session:
            return await SandboxLifecycle(session, hub=hub)._destroy(
                home_id, task_id, "host-a", timedelta(minutes=20)
            )

    return client.portal.call(go)


def test_destroying_the_machine_ends_the_turn_that_was_running_on_it(client):
    """平台自己销毁一台机器时，它上面那一轮就此结束：轮次关掉、会话不再报在干活。

    这一轮早就过了机器「还算它在干活」的两小时线，所以回收照常发生；差别只在
    销毁那一步现在会记下「这轮随机器没了」——平台是唯一能说这句话的一方。
    """
    with _working_task(client) as (channel, task, room, task_id):
        opened_at = datetime.now(UTC) - timedelta(hours=21, minutes=24)
        _backdate(client, task_id, opened_at)
        (turn,) = [t for t in _turns(client, task_id) if t.stopped_at is None]
        hub = Destroys()

        assert _destroy(client, _sandbox_home(client, task, task_id), task_id, hub)
        assert hub.actions == ["host-a"], "the host was never asked to destroy it"

        after = _turns(client, task_id)[0]
        assert after.stopped_at is not None, "这一轮还开着"
        assert _session_state(client, task_id).worker_status == "idle", (
            "会话还在报在干活"
        )
        board = client.get(
            f"/topics?project_id={task['project_id']}",
            headers=session_auth_headers("alice"),
        ).json()["data"]
        (room_row,) = [item for item in board["data"] if item["id"] == room]
        assert room_row["moving"] is False, "房间还显示在干活"

        print(
            "\n".join(
                [
                    "",
                    "===== 机器被平台销毁之后 =====",
                    f"轮次 {turn.id}",
                    f"  开于            {opened_at.isoformat()}（21 小时 24 分前）",
                    f"  stopped_at      {after.stopped_at.isoformat()}  ← 随机器一起结束",
                    "执行器所在机器    被平台销毁（销毁那一步记下了这一轮的死）",
                    f"会话状态          {_session_state(client, task_id).worker_status}",
                    "房间看板          moving=False（计时停下）",
                    "===== 结束 =====",
                ]
            )
        )


def test_a_machine_out_of_reach_leaves_the_turn_open(client):
    """机器只是够不着、平台并没有销毁它：这一轮照旧开着，房间照旧报在干活。

    这是那条老规矩，动它会把断网、休眠、还在准备的机器误判成死。新加的那句话
    只在平台亲手销毁时才有，这里断言它一分都没有越界。
    """
    with _working_task(client) as (channel, task, room, task_id):
        runner = session_turn_deps.get_work_runner()
        opened_at = datetime.now(UTC) - timedelta(hours=21, minutes=24)
        _backdate(client, task_id, opened_at)
        (turn,) = [t for t in _turns(client, task_id) if t.stopped_at is None]
        assert turn.delivered_at is not None, "the prompt never got through"
        assert turn.session_id, "the row names no conversation to judge it by"
        assert _session_state(client, task_id).worker_status == "running"

        # 机器没了，但没人销毁过它：后端换代，新进程一个会话也接不回来。
        client.portal.call(runner.let_go)
        client.portal.call(channel.runtime.stop_listening)
        gone = Recycled()
        gone.root = channel.root
        gone.sessions = dict(channel.sessions)
        for session in gone.sessions.values():
            session.channel = gone
        replaced = _backend(client, gone)

        assert client.portal.call(replaced.recover_sessions) == 0
        assert gone.asked, "the platform never even asked whether the machine is up"
        assert (
            replaced.row_is_dead(task_id, turn.agent_handle, turn.session_id) is False
        )

        swept = [client.portal.call(runner.resume_orphans, replaced) for _ in range(3)]
        assert swept == [0, 0, 0], "the sweep collected a turn nobody said had died"

        async def open_rows() -> dict:
            return await turn_inputs.open_turns(replaced._sessions)

        assert death_evidence.unknown_row(
            replaced, client.portal.call(open_rows)[turn.id]
        )

        assert _turns(client, task_id)[0].stopped_at is None
        assert _session_state(client, task_id).worker_status == "running"
        board = client.get(
            f"/topics?project_id={task['project_id']}",
            headers=session_auth_headers("alice"),
        ).json()["data"]
        (room_row,) = [item for item in board["data"] if item["id"] == room]
        assert room_row["moving"] is True, "the room stopped counting it as work"


def test_destroying_the_machine_frees_the_seat_its_unread_inputs_held(client):
    """平台销毁机器时，这台机器上所有没被读过的输入一并结掉，不只还开着那一轮里的。

    座位被一条没人读过的输入按住，再来的消息就一条也递不进去——平台把它当作「上一条
    还没读完」，不写进会话，也不给它开一轮（2026-10-23 那次 11 条消息停在门外、
    会话看起来像丢了，就是这么来的）。那条输入是中途插进正在跑的那一轮的，它自己
    不是一轮；那一轮停下之后，关轮次的那条路再也不会提到它。机器被销毁，是平台最后
    一个能说「它再也不会被读到」的时刻，它得把这句话说全。
    """
    with _working_task(client) as (channel, task, room, task_id):
        harness, native, seat = _seat_and_session(client, task_id)
        _backdate(client, task_id, datetime.now(UTC) - timedelta(hours=21, minutes=24))
        _leave_an_unread_input(
            client,
            uuid.UUID(task["project_id"]),
            task_id,
            harness=harness,
            native=native,
            seat=seat,
        )
        assert _seat_is_held(client, task_id, seat), (
            "一条没人读过的输入没有按住座位，用例的处境没造出来"
        )

        assert _destroy(
            client, _sandbox_home(client, task, task_id), task_id, Destroys()
        )

        assert not _seat_is_held(client, task_id, seat), (
            "这台机器上那条没人读的输入，销毁之后还在按着座位"
        )
        handed = _inputs(client, task_id)
        post_message(client, task["id"], "alice", {"content": "还在吗"})
        _until(
            lambda: _inputs(client, task_id) > handed,
            "座位还按着：下一条消息没能交给会话",
        )
