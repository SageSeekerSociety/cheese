"""ComputePool: which machine a turn lands on (design §3 / review R2)."""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.domain.agent.compute import ComputePool
from app.domain.agent.harness import SessionRef


@pytest.mark.anyio
async def test_switch_parks_previous_harness_before_routing_mid_turn_input():
    native = _FakeBackend("device")
    codex = _FakeBackend("device", "codex")
    calls = []
    native.holds = lambda topic_id: True
    codex.holds = lambda topic_id: True
    native.interrupt = AsyncMock(side_effect=lambda ref: calls.append("interrupt"))
    native.close = AsyncMock(side_effect=lambda ref: calls.append("close"))
    native.deliver = AsyncMock(return_value=True)
    codex.deliver = AsyncMock(return_value=True)
    pool = ComputePool([native, codex], "device")
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), harness="claude-code")
    with pytest.raises(RuntimeError, match="multiple live harnesses"):
        await pool.deliver(session.topic_id, "ambiguous")
    await pool.activate(session, codex)
    assert calls == ["interrupt", "close"]
    assert await pool.deliver(session.topic_id, "follow up")
    native.deliver.assert_not_awaited()
    codex.deliver.assert_awaited_once_with(session.topic_id, "follow up")


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
    provisions_machine = False

    def __init__(self, name: str, harness: str = "claude-code"):
        self.name = name
        self.harness = harness

    def available(self) -> bool:
        return True

    async def ensure(self, session, opening, *, work_id=None):
        return None

    async def send(self, session, message, opening, *, work_id, on_mark, images=None):
        return True

    def backlog(self, session):
        return _EmptyBacklog()

    async def deliver(self, topic_id, text, images=None):
        return False

    async def interrupt(self, session):
        return True

    async def close(self, session):
        return None

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        return None

    def bind_events(self, consumer) -> None:
        return None

    def bind_activity(self, consumer) -> None:
        return None

    def bind_receipts(self, consumer) -> None:
        return None

    def bind_unread_probe(self, probe) -> None:
        self.unread_probe = probe

    def bind_reachability(self, consumer) -> None:
        return None

    def holds(self, topic_id: uuid.UUID) -> bool:
        return False

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
    from unittest.mock import AsyncMock

    from app.core.config import settings
    from app.domain.agent.cloud_provider import CloudChannel
    from app.domain.agent.compute import build_compute_pool
    from app.domain.agent.market import compute_default_name

    def _cloud() -> CloudChannel:
        return CloudChannel(
            configured=True,
            ensure_topic_cloud=AsyncMock(),
            read_topic_cloud=AsyncMock(),
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
    from unittest.mock import AsyncMock

    from app.domain.agent.cloud_provider import CloudChannel
    from app.domain.agent.compute import build_compute_pool

    cloud = CloudChannel(
        configured=False,
        ensure_topic_cloud=AsyncMock(),
        read_topic_cloud=AsyncMock(),
    )
    pool = build_compute_pool(cloud_channel=cloud)

    backend = pool.select(provider_id="cloud")
    assert pool.has("cloud")
    assert backend.channel.channel.executor is cloud
    # Registration is independent of readiness. Chat needs its session host;
    # Cloud hands are acquired by a tool, never by turn admission.
    assert backend.available() is False
    assert backend.provisions_machine is False
    assert backend.deferred_work is True


def _register_pi(monkeypatch):
    """把 pi 写回注册表，就为这一条测试。

    它今天答不出四条硬性要求，所以不在 `HARNESSES` 里，池子也就不挂它（结论
    43）。下面两条钉的是另一件事——**一旦它答得出，挂在哪几条通道上由什么判据
    说**——那条判据是活代码，不能因为今天没有骨架走到它就没人守。
    """
    from app.domain.agent.harness import HARNESSES, PI, Harness, SubagentRequirement

    monkeypatch.setitem(
        HARNESSES,
        PI,
        Harness(
            PI,
            "pi",
            subagents=dict.fromkeys(SubagentRequirement, "本条测试里假定它答得出"),
        ),
    )


def test_the_pool_runs_only_the_harnesses_the_registry_lists():
    """答不出四条的骨架不在注册表里，也就不在池子里（结论 43）。

    「不在注册表里」如果只是功能矩阵上少一列，它在运行时就还是活的：挂进池子的
    backend 会被 `recover_sessions` 恢复、被 `bind_events` 交上房间侧的持久化、在
    没有 owner 的时候被 `deliver` 按 `holds()` 找到。所以这条收缩要在装配那一步
    可判。
    """
    from app.domain.agent.compute import build_compute_pool
    from app.domain.agent.harness import CLAUDE_CODE, CODEX, HARNESSES, PI

    pool = build_compute_pool()

    assert set(HARNESSES) == {CLAUDE_CODE}
    for machine in pool.machines():
        assert pool.select(provider_id=machine, harness=CODEX) is None
        assert pool.select(provider_id=machine, harness=PI) is None
        assert pool.select(provider_id=machine, harness=CLAUDE_CODE) is not None


def test_pi_is_wired_onto_the_places_whose_hands_are_the_session_machine(monkeypatch):
    """pi 挂在哪几条通道上，由地点的能力位说。

    pi 的进程和它的工作区在同一台机器上——没有第二台机器要指派，也没有执行器要把
    工具转过去。所以断言的是真实构造下的那一份池：两条进得了池的通道手都在会话机
    上，两条都挂 pi；而每一条被 ``CentralChannel`` 包出来的 backend 手在执行机上，
    一条 pi 都没有。

    这一条只看这份池的内容；判据换没换形状由下一条钉。
    """
    from unittest.mock import AsyncMock

    from app.domain.agent.cloud_provider import CloudChannel
    from app.domain.agent.compute import build_compute_pool
    from app.domain.agent.harness import CLAUDE_CODE, PI

    _register_pi(monkeypatch)
    cloud = CloudChannel(
        configured=True,
        ensure_topic_cloud=AsyncMock(),
        read_topic_cloud=AsyncMock(),
    )
    pool = build_compute_pool(cloud_channel=cloud)

    assert pool.select(provider_id="device", harness=PI) is not None
    assert pool.select(provider_id="cloud", harness=PI) is not None
    # 包出来的那两个 backend 手在执行机上，所以它们身上挂的是要转一程的骨架。
    for provider in ("device", "cloud"):
        wrapped = pool.select(provider_id=provider, harness=CLAUDE_CODE)
        assert wrapped is not None
        assert wrapped.channel.channel.capabilities() == frozenset()


def test_a_place_whose_hands_are_elsewhere_gets_no_pi(monkeypatch):
    """负向对照：把判据换回 ``isinstance(c, DeviceChannel)``，这一条红。

    ``hands_here = False`` 的通道进这个池，说的是「会话进程在一台机器上，工具要再
    跳一程到另一台」——pi 把进程和工作区放在同一台机器上，挂不住。能力位看的是这
    个事实，所以它不挂；``isinstance`` 看的是类，而这条通道照样是 ``DeviceChannel``
    （今天进得了这个池的都是），所以它会挂上一个跑不起来的 backend。

    今天池里恰好没有这样一条通道，这正是为什么它要在这里被造出来：判据换没换形
    状，是可以脱开「今天池里装了什么」单独钉住的。
    """
    from app.domain.agent.compute import build_compute_pool
    from app.domain.agent.device_provider import DeviceChannel
    from app.domain.agent.harness import CLAUDE_CODE, PI

    _register_pi(monkeypatch)

    class Elsewhere(DeviceChannel):
        name = "elsewhere"
        hands_here = False

    pool = build_compute_pool(cloud_channel=Elsewhere())

    assert pool.select(provider_id="elsewhere", harness=CLAUDE_CODE) is not None
    assert pool.select(provider_id="elsewhere", harness=PI) is None
    # 而手在会话机上的那一条照旧挂着 pi——排除的是这一条通道，不是 pi 这个骨架。
    assert pool.select(provider_id="device", harness=PI) is not None


def test_resolve_compute_id_uses_room_then_explicit_project_default():
    from app.domain.agent.chat import _resolve_compute_id
    from app.domain.agent.compute_configs import ComputeChoice, ProjectComputeConfigs

    configs = ProjectComputeConfigs(
        default=ComputeChoice(name="Lab", profile="device", device_id="lab")
    )
    values = {"compute_configs": configs.model_dump()}
    assert _resolve_compute_id(values, "cloud") == "cloud"
    assert _resolve_compute_id(values) == "device"
