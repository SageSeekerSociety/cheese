"""ComputePool: which backend a turn lands on — one per harness, over the one
executor channel, whatever machine the session works on."""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.domain.agent.compute import ComputePool
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.harness import SessionRef
from app.domain.delivery.input_identity import InputRegistrar


@pytest.mark.anyio
async def test_switch_parks_previous_harness_before_routing_mid_turn_input():
    native = _FakeBackend("device")
    codex = _FakeBackend("device", "codex")
    calls = []
    native.holds = lambda topic_id, agent_handle=None: True
    codex.holds = lambda topic_id, agent_handle=None: True
    native.interrupt = AsyncMock(side_effect=lambda ref: calls.append("interrupt"))
    native.close = AsyncMock(side_effect=lambda ref: calls.append("close"))
    native.steer = AsyncMock(return_value=True)
    codex.steer = AsyncMock(return_value=True)
    pool = ComputePool([native, codex])
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), harness="claude-code")
    await pool.activate(session, codex)
    assert calls == ["interrupt", "close"]
    register_input = AsyncMock()
    assert await pool.steer(
        session.topic_id, "follow up", register_input=register_input
    )
    native.steer.assert_not_awaited()
    codex.steer.assert_awaited_once_with(
        session.topic_id,
        "follow up",
        images=None,
        register_input=register_input,
        agent_handle=None,
        owes_reply=False,
    )


@pytest.mark.anyio
async def test_activate_parks_only_the_same_seats_previous_harness():
    """Another agent's session in the same room is a different conversation:
    taking a seat must not evict the neighbour."""
    native = _FakeBackend("device")
    codex = _FakeBackend("device", "codex")
    native.interrupt = AsyncMock()
    native.close = AsyncMock()
    # native holds agent-a's session in the room; agent-b's turn activates codex.
    native.holds = lambda topic_id, agent_handle=None: (
        agent_handle
        in (
            None,
            "agent-a",
        )
    )
    pool = ComputePool([native, codex])
    topic = uuid.uuid4()
    await pool.activate(
        SessionRef(uuid.uuid4(), topic, "agent-b", harness="claude-code"), codex
    )
    native.interrupt.assert_not_awaited()
    native.close.assert_not_awaited()
    # The same agent switching harness is still parked.
    await pool.activate(
        SessionRef(uuid.uuid4(), topic, "agent-a", harness="claude-code"), codex
    )
    native.interrupt.assert_awaited_once()
    native.close.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "gone",
    [
        DeviceCallError(
            "dial unix /tmp/cheese-execution-1000-x.sock: connect: no such file "
            "or directory"
        ),
        DeviceOffline("device went offline"),
    ],
)
async def test_a_seat_whose_previous_runner_is_gone_is_taken_anyway(gone):
    """A runner that let its idle session go cannot be told to stop. The seat
    still changes hands: the new turn starts, and a later message goes to it."""
    native = _FakeBackend("device")
    pi = _FakeBackend("device", "pi")
    native.holds = lambda topic_id, agent_handle=None: True
    native.interrupt = AsyncMock(side_effect=gone)
    native.close = AsyncMock()
    native.steer = AsyncMock(return_value=True)
    pi.steer = AsyncMock(return_value=True)
    pool = ComputePool([native, pi])
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), harness="pi")

    await pool.activate(session, pi)

    native.close.assert_awaited_once()
    assert await pool.steer(session.topic_id, "follow up", register_input=AsyncMock())
    native.steer.assert_not_awaited()
    pi.steer.assert_awaited_once()


def test_words_for_a_room_never_go_to_a_guessed_working_seat():
    """With two teammates at work in one room, words that name neither are for
    nobody: putting them into one of the two sessions is a guess."""
    topic = uuid.uuid4()
    native = _FakeBackend("device")
    codex = _FakeBackend("device", "codex")
    native.working_seats = lambda topic_id, agent_handle=None: [
        (h, a)
        for h, a in [("cheese", "cheese-a1")]
        if topic_id == topic and agent_handle in (None, h, a)
    ]
    codex.working_seats = lambda topic_id, agent_handle=None: [
        (h, a)
        for h, a in [("reviewer", "cheese-b2")]
        if topic_id == topic and agent_handle in (None, h, a)
    ]
    pool = ComputePool([native, codex])

    assert pool.working_seat(topic) is None
    assert pool.working_seat(topic, "cheese-b2") == ("reviewer", "cheese-b2")
    assert pool.working_seat(topic, "cheese") == ("cheese", "cheese-a1")
    assert pool.working_seat(topic, "somebody-else") is None
    assert pool.working_seat(uuid.uuid4()) is None


class _FakeBackend:
    """Minimal backend stand-in for routing tests (v4 会话级选择): what the
    pool asks of a room's sessions on one machine pool."""

    embeds_images = True
    controls: tuple[str, ...] = ()

    def __init__(self, name: str, harness: str = "claude-code"):
        self.name = name
        self.harness = harness

    def available(self) -> bool:
        return True

    async def send(self, session, message, *, register_input: InputRegistrar, **_):
        return True

    async def steer(
        self, topic_id, text, images=None, *, register_input: InputRegistrar, **kwargs
    ):
        return False

    async def interrupt(self, session):
        return True

    async def close(self, session):
        return None

    def report_to(self, reader, *, unread, memory) -> None:
        self.unread_probe = unread

    async def memory(self, session, request):
        # 这个 double 的会话不存记忆文件：「这里没有」而不是「失败了」。
        return None

    def holds(self, topic_id: uuid.UUID, agent_handle=None) -> bool:
        return False

    def work_in_flight(self, topic_id: uuid.UUID, agent_handle=None):
        return None

    async def recover(self, device_id=None):
        return []

    async def stop_listening(self) -> None:
        return None

    async def replay(self, session):
        return None

    hard_ceiling_s = 900.0


def test_a_pool_without_the_deployments_first_harness_is_refused():
    """A deployment whose first preference the pool cannot run is a
    misconfigured one, refused when the pool is built rather than at the first
    turn that needs it."""
    with pytest.raises(ValueError):
        ComputePool([])


def test_one_backend_per_harness_whatever_machine_the_room_is_on():
    """Which machine a session works on is its own environment, settled when it
    needs hands. The backend a turn lands on depends on the harness alone, and
    every one of them runs its session on the session host and defers its work
    to the machine."""
    from app.domain.agent.compute import build_compute_pool
    from app.domain.agent.harness import CLAUDE_CODE

    pool = build_compute_pool()

    backend = pool.select(harness=CLAUDE_CODE)
    assert backend is not None
    assert backend.deferred_work is True
    assert pool.choose({})[1] is pool.select(harness=pool.choose({})[0])


def test_the_pool_runs_only_the_harnesses_the_registry_lists():
    """答不出四条的骨架不在注册表里，也就不在池子里（结论 43）。

    「不在注册表里」如果只是功能矩阵上少一列，它在运行时就还是活的：挂进池子的
    backend 会被 `recover_sessions` 恢复、被 `report_to` 交上房间的耳朵、在
    没有 owner 的时候被 `steer` 按 `holds()` 找到。所以这条收缩要在装配那一步
    可判。
    """
    from app.domain.agent.compute import build_compute_pool
    from app.domain.agent.harness import CLAUDE_CODE, CODEX, HARNESSES, PI

    pool = build_compute_pool()

    assert set(HARNESSES) == {CLAUDE_CODE, PI}
    assert pool.select(harness=CODEX) is None
    assert pool.select(harness=CLAUDE_CODE) is not None


def test_pi_takes_the_machine_only_for_work():
    """pi's session runs on the session host and reaches the session's machine
    for its work (#1106), as the other harnesses' do: a turn on it does not
    wait for a machine to start."""
    from app.domain.agent.compute import build_compute_pool
    from app.domain.agent.harness import PI

    backend = build_compute_pool().select(harness=PI)
    assert backend is not None
    assert backend.deferred_work is True


def test_a_room_runs_a_harness_that_hands_tools_over(monkeypatch):
    """要远端执行的场景不会派给没声明它的骨架，哪怕项目指定了它。

    会话跑在中心机、工具交给执行机。拿掉 pi 的这一项声明，挑出来的就是下一个
    声明了的；声明在，pi 照旧被挑中。
    """
    from app.core.config import settings
    from app.domain.agent import harness as harness_module
    from app.domain.agent.compute import build_compute_pool
    from app.domain.agent.harness import (
        CLAUDE_CODE,
        HARNESS_SETTING,
        HARNESSES,
        PI,
        Harness,
    )

    monkeypatch.setattr(settings, "agent_harnesses", [CLAUDE_CODE, PI])
    project = {HARNESS_SETTING: PI}

    def chosen() -> str:
        return build_compute_pool().choose(project)[0]

    assert chosen() == PI
    pi = HARNESSES[PI]
    monkeypatch.setitem(harness_module.HARNESSES, PI, Harness(pi.name, pi.label))
    assert chosen() == CLAUDE_CODE


def test_a_harness_that_cannot_hand_tools_over_is_not_put_behind_the_central_host(
    monkeypatch,
):
    """会话在中心机、手在执行机的那条路，只挂声明了远端执行的骨架。

    把 Claude Code 的这一项声明拿掉，它就不挂：一个答不出「工具交给执行机」的骨架
    挂在那里，跑起来是在中心机上碰项目文件。
    """
    from app.core.config import settings
    from app.domain.agent import harness as harness_module
    from app.domain.agent.compute import build_compute_pool
    from app.domain.agent.harness import CLAUDE_CODE, HARNESSES, PI, Harness

    claude = HARNESSES[CLAUDE_CODE]
    monkeypatch.setitem(
        harness_module.HARNESSES,
        CLAUDE_CODE,
        Harness(claude.name, claude.label),
    )
    monkeypatch.setattr(settings, "agent_harnesses", [PI])
    pool = build_compute_pool()

    assert pool.select(harness=CLAUDE_CODE) is None
    assert pool.select(harness=PI) is not None
