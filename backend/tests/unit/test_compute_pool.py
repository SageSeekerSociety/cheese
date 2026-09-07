"""ComputePool: which machine a turn lands on (design §3 / review R2)."""

import uuid

import pytest

from app.domain.agent.compute import ComputePool


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

    def holds(self, topic_id: uuid.UUID) -> bool:
        return False

    async def recover(self, device_id=None):
        return []

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
    assert backend.channel is cloud
    # Unconfigured Cloud is registered but not runnable, and it is the one
    # backend the turn path must wait for a machine on.
    assert backend.available() is False
    assert backend.provisions_machine is True


def test_resolve_compute_id_topic_then_project_then_team_default():
    from app.domain.agent.chat import _resolve_compute_id

    # Topic's own选择 wins over the project sticky.
    assert (
        _resolve_compute_id({"compute_profile": "cloud"}, "device", "cloud") == "device"
    )
    # No topic选择 → project sticky, before the team's default.
    assert _resolve_compute_id({"compute_profile": "device"}, None, "cloud") == "device"
    # A fresh project starts from the team's default.
    assert _resolve_compute_id({}, None, "device") == "device"
    assert _resolve_compute_id(None, None, "device") == "device"
    # No choice at any layer → None (pool default).
    assert _resolve_compute_id({}, None, None) is None
    assert _resolve_compute_id(None, None, None) is None
