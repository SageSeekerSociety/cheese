"""A machine is recycled under a turn that is still open, and nothing ever
collects that turn — the room goes on saying its teammate is working.

THIS FILE IS A REPRODUCTION, NOT A GUARD. It pins the behaviour the platform
has today, and its assertions are meant to be INVERTED by the change that gives
the platform a way to close a turn whose machine is gone (task「机器没了轮次
不收」). Every assertion here goes red the moment that change lands, and that is
the point: it is the "before" half of that fix's 修前失败/修后通过 pair.

The chain it reconstructs, each step being one of the judgements the platform
uses to decide whether a still-open turn is dead:

1. a turn interval is open and its prompt was delivered (``AgentTurn.stopped_at
   IS NULL``, ``delivered_at`` set, ``session_id`` naming its conversation);
2. its session's runner last said it was running (``SessionState.worker_status
   == "running"``), and nothing can ever correct that: the report that a turn
   ended only ever comes from that runner, and reachability is a lease, never an
   exit (``harness/driven/lifecycle.py``);
3. the machine is *unreachable* — which is not the same as the machine answering
   that its runner is gone. ``session_host/starting.attach`` asks
   ``hub.is_online`` first and answers ``None`` for an offline host without
   asking anything, so no terminal answer is ever recorded and
   ``death_evidence.row_is_dead`` keeps saying "unknown";
4. a recover round over that machine re-attaches zero sessions and marks nothing
   dead;
5. the orphan sweep runs, repeatedly, with the turn older than every deadline the
   platform has (30 minutes of silence, 2 hours of ``STALE_TURN``, 21 hours of
   nothing at all) — and collects nothing, because a row the process cannot
   judge is not a dead row (``death_evidence.unknown_row``, FB-56 legacy③).

What a person then sees is the incident of 2026-10-23: the turn open, the board
still saying the teammate is working, the clock counting from the last thing the
machine ever reported. The test prints that scene, so the phenomenon can be read
without reading the assertions.

``test_a_turn_is_collected_when_the_machine_answers_its_executor_is_gone`` runs
the same construction with the one thing changed — the machine answers — and
collects it. The pair shows the sweep is not broken: it is waiting for an answer
a recycled machine can never give.

When the fix lands, flip the assertions in the first test (a collected turn,
``worker_status`` no longer "running", the room no longer "moving") and this file
becomes that fix's regression guard.
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
from app.domain.machine.lifecycle import SandboxLifecycle
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
    so ``hub.is_online`` is false and nothing at its address is asked.

    This is NOT "the machine answered that its executor is gone" — that answer
    is the one the platform accepts as a death, and the control test below
    supplies it. An unreachable machine is the case the lifecycle protocol
    leaves open on purpose, and the case a recycled sandbox lands in.
    """

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[str] = []

        # The hub is the only thing standing between the platform and the
        # machine: offline, ``starting.attach`` returns before it looks.
        def refuse(device_id: str) -> bool:
            self.asked.append(device_id)
            return False

        self.host._hub.is_online = refuse


def _backend(client, channel: StubChannel) -> ChatService:
    """One backend process's sessions, reached through ``channel``."""
    return ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/recycled-machine-ws",
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
    the row's age, and the board's clock is the session's last report.

    The prompt was delivered when the turn opened, so the delivery stamp moves
    with it — one more thing the machine reported and nothing can correct.
    """

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
    data = post_project(client, {"name": "Recycled"}, owner="alice").json()["data"]
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


def _hand_over_to(
    client, channel: StubChannel, *, online: bool
) -> tuple[StubChannel, ChatService]:
    """The next backend process, over the same machine: the old one's coroutine
    dies with it and the open interval stays, which is what a deploy — or a
    recycled sandbox — leaves behind.

    The machine's table still lists the session either way. ``online`` is the
    whole difference between the two tests: whether the hub can reach the
    machine at all.
    """
    client.portal.call(session_turn_deps.get_work_runner().let_go)
    client.portal.call(channel.runtime.stop_listening)
    after = StubChannel() if online else Recycled()
    after.root = channel.root
    after.sessions = dict(channel.sessions)
    for session in after.sessions.values():
        session.channel = after
    return after, _backend(client, after)


def _open_turns(client, service: ChatService) -> dict:
    async def read() -> dict:
        return await turn_inputs.open_turns(service._sessions)

    return client.portal.call(read)


def _machine_counts_as_work(
    client, conversation: uuid.UUID, handle: str, at: datetime
) -> bool:
    """Whether the machine would keep this open turn as its session at work —
    the judgement that decides whether it may be released
    (``machine.lifecycle.SandboxLifecycle.at_work``)."""

    async def ask() -> bool:
        async with client.test_factory() as session:
            return await SandboxLifecycle(session).at_work(conversation, handle, at)

    return client.portal.call(ask)


def test_a_recycled_machines_turn_is_never_collected(client):
    """机器被回收后，那一轮永远收不掉：轮次还开着、会话还报着在干活、房间还在
    显示「正在工作」。

    每一步都断言一次，对应模块开头那条链里的一步。最后打印一份现场，让人不读
    代码也看得见现象。
    """
    with _working_task(client) as (channel, task, room, task_id):
        runner = session_turn_deps.get_work_runner()
        opened_at = datetime.now(UTC) - timedelta(hours=21, minutes=24)
        _backdate(client, task_id, opened_at)
        (turn,) = [t for t in _turns(client, task_id) if t.stopped_at is None]

        # 1. 行开着、已送达、带着自己那条会话的名字 —— 这是「一轮还开着」的定义。
        assert turn.delivered_at is not None, "the prompt never got through"
        assert turn.session_id, "the row names no conversation to judge it by"

        # 2. runner 最后说的是「在跑」；它已经不在了，没人能更正这句话。
        assert _session_state(client, task_id).worker_status == "running"

        # 2b. 机器这一侧：这一轮开着，但只在开着的头 2 小时内算它的活在干
        #     （`STALE_TURN`）。过了这条线，机器该被回收照样被回收 —— 回收不是
        #     这个缺陷造成的，它只是把缺陷露出来的那一步。
        assert _machine_counts_as_work(
            client, task_id, turn.agent_handle, opened_at + timedelta(hours=1)
        ), "an hour in, the machine still counts this turn as work"
        assert not _machine_counts_as_work(
            client, task_id, turn.agent_handle, datetime.now(UTC)
        ), "past STALE_TURN the machine no longer counts it as work"

        # 3. 机器被回收：平台连问都问不到它（is_online 为假），所以它永远给不出
        #    「执行器没了」那个唯一被认的答案。
        recycled, replaced = _hand_over_to(client, channel, online=False)

        # 4. recover 一轮：机器上的会话一个也没接回来 —— 没有任何终止证据。
        #    （「问不出来」是平台自己的说法：`attach` 在 `is_online` 为假时直接
        #    返回，连一次调用都没有发出去，所以这里也断言它确实什么都没问。）
        assert client.portal.call(replaced.recover_sessions) == 0
        assert recycled.asked, "the platform never even tried the machine"
        # 死亡证据问的是「行自己那张票有没有被权威作废」，所以这里就用行自己的
        # 席位去问 —— 答案是没有，因为那台机器根本没能说出一句话。
        assert (
            replaced.row_is_dead(task_id, turn.agent_handle, turn.session_id) is False
        )

        # 5. 清扫一轮又一轮，每一次都收掉 0 轮。年龄已经远远过了平台「开着的轮次
        #    只保护 2 小时」那条线，也没用。
        swept = [client.portal.call(runner.resume_orphans, replaced) for _ in range(3)]
        assert swept == [0, 0, 0], "the sweep collected a turn the machine never denied"

        # 拦住清扫的那条规矩，点名断言：不是「不在活跃表」也不是「30 分钟没输出」
        # 挡住了它（两条都成立），是「判不出来的一律算未知，未知保持打开」。
        assert (
            death_evidence.unknown_row(replaced, _open_turns(client, replaced)[turn.id])
            is True
        )

        # 现象：轮次还开着，会话还报在干活，房间看板还显示在「往前走」。
        assert _turns(client, task_id)[0].stopped_at is None
        assert _session_state(client, task_id).worker_status == "running"
        board = client.get(
            f"/topics?project_id={task['project_id']}",
            headers=session_auth_headers("alice"),
        ).json()["data"]
        (room_row,) = [item for item in board["data"] if item["id"] == room]
        assert room_row["moving"] is True, "the room is no longer shown at work"

        print(
            "\n".join(
                [
                    "",
                    "===== 现场：机器没了，轮次还开着 =====",
                    f"轮次 {turn.id}",
                    f"  开于            {opened_at.isoformat()}（21 小时 24 分前）",
                    f"  stopped_at      {turn.stopped_at}  ← 一直没被收掉",
                    f"  delivered_at    {turn.delivered_at.isoformat()}（提示确实送到了）",
                    f"  session_id      {turn.session_id}",
                    f"会话状态          {_session_state(client, task_id).worker_status}"
                    "  ← 房间一直报「正在工作」",
                    f"  计时起点        {opened_at.isoformat()}",
                    "机器是否算它在干活  开着的头 2 小时内算，之后不算（STALE_TURN）",
                    "执行器所在机器    不可达（is_online=False，平台连问都没有问）",
                    "recover 一轮      接回 0 个会话，终止证据 0 条",
                    f"清扫 3 轮         收掉 {swept} 轮",
                    "房间看板          moving=True（侧栏/看板仍显示在干活）",
                    "===== 结束 =====",
                ]
            )
        )


def test_a_turn_is_collected_when_the_machine_answers_its_executor_is_gone(client):
    """同样的构造，只把「机器问不出来」换成「机器答了：执行器没了」。

    正对照：清扫本身是好的，它收得掉——它只是永远在等一个回收后的机器不可能
    再说出来的答案。两条并列，差别只有一样：机器答不答。

    这里声明死亡用的席位取自轮次那一行自己的 ``agent_handle``：那是死亡证据拿
    去比对的那个值（``death_evidence.row_is_dead``），机器按会话的名字说话时说的
    就是它。
    """
    with _working_task(client) as (channel, _task, _room, task_id):
        runner = session_turn_deps.get_work_runner()
        (turn,) = [t for t in _turns(client, task_id) if t.stopped_at is None]
        seat = next(agent for (topic, agent) in channel.sessions if topic == task_id)

        # 机器还在（问得到它），但它上面已经没有这条会话了，而且它按会话的名字
        # 说出了这一点 —— 平台认得的那唯一一种终止答案。
        answered, replaced = _hand_over_to(client, channel, online=True)
        answered.sessions.pop((task_id, seat), None)
        answered.report_gone(task_id, turn.agent_handle, turn.session_id)

        client.portal.call(replaced.recover_sessions)
        assert replaced.row_is_dead(task_id, turn.agent_handle, turn.session_id) is True
        client.portal.call(runner.resume_orphans, replaced)

        assert _turns(client, task_id)[0].stopped_at is not None, "收掉了"
