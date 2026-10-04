"""ComputePool: which machine a turn lands on (design §3 / review R2)."""

import uuid
from types import SimpleNamespace
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
    native.deliver = AsyncMock(return_value=True)
    codex.deliver = AsyncMock(return_value=True)
    pool = ComputePool([native, codex], "device")
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), harness="claude-code")
    await pool.activate(session, codex)
    assert calls == ["interrupt", "close"]
    register_input = AsyncMock()
    assert await pool.deliver(
        session.topic_id, "follow up", register_input=register_input
    )
    native.deliver.assert_not_awaited()
    codex.deliver.assert_awaited_once_with(
        session.topic_id,
        "follow up",
        images=None,
        register_input=register_input,
        expected_work_id=None,
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
    pool = ComputePool([native, codex], "device")
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
    native.deliver = AsyncMock(return_value=True)
    pi.deliver = AsyncMock(return_value=True)
    pool = ComputePool([native, pi], "device")
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), harness="pi")

    await pool.activate(session, pi)

    native.close.assert_awaited_once()
    assert await pool.deliver(session.topic_id, "follow up", register_input=AsyncMock())
    native.deliver.assert_not_awaited()
    pi.deliver.assert_awaited_once()


class _EmptyBacklog:
    """A session that said nothing while nobody was listening."""

    def unread(self):
        return []

    def assemble(self, entry):
        return []

    def unfinished(self):
        return set()

    def give_up(self):
        return []

    def landed(self, *, through):
        return None

    def forget(self, *, older_than_s):
        return None


class _FakeBackend:
    """Minimal backend stand-in for routing tests (v4 会话级选择).

    Answers the whole ``AgentRuntime`` contract because the pool checks for it
    at construction: a backend that runs no harness cannot be registered, so a
    double that skipped half the contract would be testing a pool nobody can
    build.
    """

    embeds_images = True
    # 会话不存记忆文件（下面的 `memory()` 答 None），和它答的那条契约一致。
    keeps_memory = False

    def __init__(self, name: str, harness: str = "claude-code"):
        self.name = name
        self.harness = harness

    def available(self) -> bool:
        return True

    async def ensure(self, session, opening, *, work_id=None):
        return None

    async def send(
        self,
        session,
        message,
        opening,
        *,
        work_id,
        on_mark,
        register_input: InputRegistrar,
        **kwargs,
    ):
        return True

    def backlog(self, session):
        return _EmptyBacklog()

    async def deliver(
        self, topic_id, text, images=None, *, register_input: InputRegistrar, **kwargs
    ):
        return False

    async def interrupt(self, session):
        return True

    async def close(self, session):
        return None

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        return None

    def bind_reader(self, reader) -> None:
        return None

    def bind_unread_probe(self, probe) -> None:
        self.unread_probe = probe

    def bind_memory(self, consumer) -> None:
        return None

    async def memory(self, topic_id, request):
        # 这个 double 的会话不存记忆文件：「这里没有」而不是「失败了」。
        return None

    async def ask_origin(self, project_id, topic_id, agent_handle):
        # Routing doubles have no native conversation to authenticate.
        return None

    def holds(self, topic_id: uuid.UUID, agent_handle=None) -> bool:
        return False

    def work_in_flight(self, topic_id: uuid.UUID, agent_handle=None):
        return None

    async def recover(self, device_id=None):
        return []

    async def stop_listening(self) -> None:
        return None

    async def replay(self, session, *, known_texts):
        return None

    hard_ceiling_s = 900.0

    async def run_turn(self, **kwargs):
        return
        yield  # pragma: no cover — an async generator that yields nothing


def test_pool_rejects_unknown_default():
    with pytest.raises(ValueError):
        ComputePool([], "missing")


def test_pool_rejects_a_backend_that_runs_no_harness():
    """Caught at wiring time, not at turn time. A provider missing half the
    contract used to be discovered by a turn that quietly did nothing."""

    class _NotARuntime:
        name = "hollow"
        embeds_images = True

        def available(self) -> bool:
            return True

    with pytest.raises(TypeError):
        ComputePool([_NotARuntime()], "hollow")


def _two_machine_pool() -> ComputePool:
    return ComputePool([_FakeBackend("cloud"), _FakeBackend("device")], "cloud")


def test_select_routes_to_the_named_machine():
    pool = _two_machine_pool()
    assert pool.select(provider_id="device").name == "device"
    assert pool.select(provider_id="cloud").name == "cloud"


def test_a_machine_can_offer_more_than_one_harness():
    """The two axes are two questions. A registry keyed by machine alone could
    not hold a second harness at all — both backends would answer to the same
    name and one would silently replace the other."""
    pool = ComputePool(
        [_FakeBackend("cloud"), _FakeBackend("cloud", harness="pi")],
        "cloud",
    )
    assert pool.machines() == {"cloud"}
    assert pool.select(provider_id="cloud", harness="pi").harness == "pi"
    assert pool.select(provider_id="cloud").harness == "claude-code"


def test_a_harness_this_deployment_does_not_run_is_refused_not_substituted():
    """Running something else would answer as an agent nobody configured. The
    machine falls back; what runs on it never does."""
    pool = _two_machine_pool()
    assert pool.select(provider_id="cloud", harness="pi") is None


def test_select_falls_back_to_default_for_unknown_or_none():
    pool = _two_machine_pool()
    # None (topic/project chose nothing) → the pool default.
    assert pool.select(provider_id=None).name == "cloud"
    assert pool.select().name == "cloud"
    # A stored id that isn't deployed here (e.g. a pool that was retired) must
    # never break a turn — it degrades to the default, not an error.
    assert pool.select(provider_id="gpu").name == "cloud"


def test_pool_select_returns_available_default():
    pool = _two_machine_pool()
    provider = pool.select()
    assert provider is pool.default()
    assert provider.available() is True


def test_the_pool_holds_only_machines_the_catalogue_offers():
    """A turn can only land on something a person could have picked. The
    platform's own container host was in this pool and in no catalogue, so it was
    where every unconfigured topic ran (#358)."""
    from app.domain.agent.compute import build_compute_pool
    from app.domain.agent.market import COMPUTE_CLOUD, COMPUTE_DEVICE

    pool = build_compute_pool()
    assert pool.machines() <= {COMPUTE_DEVICE, COMPUTE_CLOUD}
    assert pool.has(COMPUTE_DEVICE)


def test_an_unconfigured_turn_lands_on_the_pool_the_catalogue_marks_default(
    monkeypatch,
):
    """The two answers are one function now. They used not to be, and the gap was
    invisible: the picker said Cloud while the turn ran in a container here.

    Checked in BOTH deployment shapes, because the answer moves between them and
    only one of the two could be got right by accident."""

    from app.core.config import settings
    from app.domain.agent.cloud_provider import CloudChannel
    from app.domain.agent.compute import build_compute_pool
    from app.domain.agent.market import compute_default_name

    def _cloud() -> CloudChannel:
        return CloudChannel(
            configured=True,
        )

    monkeypatch.setattr(settings, "microcloud_base_url", "")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "")
    pool = build_compute_pool(cloud_channel=_cloud())
    assert pool.select(provider_id=None).name == compute_default_name()

    monkeypatch.setattr(settings, "microcloud_base_url", "https://microcloud.example")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "secret")
    pool = build_compute_pool(cloud_channel=_cloud())
    assert pool.select(provider_id=None).name == compute_default_name()


def test_the_default_never_names_a_machine_the_pool_does_not_hold(monkeypatch):
    """A default the pool cannot resolve is refused at construction, so it would
    take the whole deployment down at startup. Preferring Cloud must therefore
    never outvote what was actually wired in."""
    from app.core.config import settings
    from app.domain.agent.compute import build_compute_pool

    monkeypatch.setattr(settings, "microcloud_base_url", "https://microcloud.example")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "secret")

    pool = build_compute_pool()  # no Cloud channel handed in

    assert pool.default().name in pool.machines()


def test_build_pool_registers_the_concrete_cloud_channel():

    from app.domain.agent.cloud_provider import CloudChannel
    from app.domain.agent.compute import build_compute_pool

    cloud = CloudChannel(
        configured=False,
    )
    pool = build_compute_pool(cloud_channel=cloud)

    backend = pool.select(provider_id="cloud")
    assert pool.has("cloud")
    assert backend.channel.channel.executor is cloud
    # Registration is independent of readiness. Chat needs its session host;
    # Cloud hands are acquired by a tool, never by turn admission.
    assert backend.available() is False
    assert backend.deferred_work is True


def test_the_pool_runs_only_the_harnesses_the_registry_lists():
    """答不出四条的骨架不在注册表里，也就不在池子里（结论 43）。

    「不在注册表里」如果只是功能矩阵上少一列，它在运行时就还是活的：挂进池子的
    backend 会被 `recover_sessions` 恢复、被 `bind_reader` 交上房间的耳朵、在
    没有 owner 的时候被 `deliver` 按 `holds()` 找到。所以这条收缩要在装配那一步
    可判。
    """
    from app.domain.agent.compute import build_compute_pool
    from app.domain.agent.harness import CLAUDE_CODE, CODEX, HARNESSES, PI

    pool = build_compute_pool()

    assert set(HARNESSES) == {CLAUDE_CODE, PI}
    for machine in pool.machines():
        assert pool.select(provider_id=machine, harness=CODEX) is None
        assert pool.select(provider_id=machine, harness=CLAUDE_CODE) is not None


def test_pi_runs_on_every_machine_and_takes_it_only_for_work():
    """pi's session runs on the session host and reaches the room's machine
    for its work (#1106), as the other harnesses' do: it is on every machine
    in the pool, and a turn on it does not wait for a machine to start."""

    from app.domain.agent.cloud_provider import CloudChannel
    from app.domain.agent.compute import build_compute_pool
    from app.domain.agent.harness import PI

    cloud = CloudChannel(
        configured=True,
    )
    pool = build_compute_pool(cloud_channel=cloud)

    for provider in ("device", "cloud"):
        backend = pool.select(provider_id=provider, harness=PI)
        assert backend is not None
        assert backend.deferred_work is True


def test_a_room_runs_a_harness_that_hands_tools_over(monkeypatch):
    """要远端执行的场景不会派给没声明它的骨架，哪怕项目指定了它。

    每台机器上，会话跑在中心机、工具交给执行机。拿掉 pi 的这一项声明，挑出来的
    就是下一个声明了的；声明在，pi 照旧被挑中。
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
        harness_on,
    )

    monkeypatch.setattr(settings, "agent_harnesses", [CLAUDE_CODE, PI])
    project = {HARNESS_SETTING: PI}

    def chosen() -> str | None:
        pool = build_compute_pool()
        return harness_on(
            project,
            lambda name: pool.select(provider_id="device", harness=name) is not None,
        )

    assert chosen() == PI
    pi = HARNESSES[PI]
    monkeypatch.setitem(
        harness_module.HARNESSES, PI, Harness(pi.name, pi.label, subagents=pi.subagents)
    )
    assert chosen() == CLAUDE_CODE


def test_a_harness_that_cannot_hand_tools_over_is_not_put_behind_the_central_host(
    monkeypatch,
):
    """会话在中心机、手在执行机的那条路，只挂声明了远端执行的骨架。

    把 Claude Code 的这一项声明拿掉，它就哪台机器都不挂：一个答不出「工具交给执行
    机」的骨架挂在那里，跑起来是在中心机上碰项目文件。
    """
    from app.core.config import settings
    from app.domain.agent import harness as harness_module
    from app.domain.agent.compute import build_compute_pool
    from app.domain.agent.harness import CLAUDE_CODE, HARNESSES, PI, Harness

    claude = HARNESSES[CLAUDE_CODE]
    monkeypatch.setitem(
        harness_module.HARNESSES,
        CLAUDE_CODE,
        Harness(claude.name, claude.label, subagents=claude.subagents),
    )
    monkeypatch.setattr(settings, "agent_harnesses", [PI])
    pool = build_compute_pool()

    for machine in pool.machines():
        assert pool.select(provider_id=machine, harness=CLAUDE_CODE) is None
    assert pool.select(provider_id="device", harness=PI) is not None


def test_resolve_compute_id_uses_room_then_explicit_project_default():
    from app.domain.agent.compute_configs import ComputeChoice, ProjectComputeConfigs
    from app.domain.agent.work_policy import resolve_compute_id

    configs = ProjectComputeConfigs(
        default=ComputeChoice(name="Lab", profile="device", device_id="lab")
    )
    values = {"compute_configs": configs.model_dump()}
    cloud = ComputeChoice(name="Cloud", profile="cloud")
    room = SimpleNamespace(compute_config=cloud.model_dump())
    fresh = SimpleNamespace(compute_config=None)
    assert resolve_compute_id(values, room) == "cloud"
    assert resolve_compute_id(values, fresh) == "device"
    assert resolve_compute_id(values) == "device"
