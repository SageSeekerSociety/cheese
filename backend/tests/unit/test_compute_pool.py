"""ComputePool: which machine a turn lands on (design §3 / review R2)."""

import uuid

import pytest

from app.domain.agent.compute import ComputePool


class _FakeProvider:
    """Minimal provider stand-in for routing tests (v4 会话级选择).

    Answers the whole ``AgentRuntime`` contract because the pool checks for it
    at construction: a backend that runs no harness cannot be registered, so a
    double that skipped half the contract would be testing a pool nobody can
    build.
    """

    harness = "claude-code"
    embeds_images = True
    provisions_machine = False

    def __init__(self, name: str):
        self.name = name

    def available(self) -> bool:
        return True

    async def ensure(self, session, opening, *, work_id=None):
        return None

    async def send(self, session, message, opening, *, work_id, on_mark, images=None):
        return True

    def read(self, session, *, since=None):
        return []

    def cursor(self, session):
        return None

    def acknowledge(self, session, *, through):
        return None

    async def deliver(self, topic_id, text, images=None):
        return False

    async def interrupt(self, session):
        return True

    async def close(self, session):
        return None

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        return None


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


def _two_provider_pool() -> ComputePool:
    return ComputePool(
        [_FakeProvider("tmux-hooks"), _FakeProvider("device")], "tmux-hooks"
    )


def test_select_routes_to_the_named_provider():
    pool = _two_provider_pool()
    assert pool.select(provider_id="device").name == "device"
    assert pool.select(provider_id="tmux-hooks").name == "tmux-hooks"


def test_select_falls_back_to_default_for_unknown_or_none():
    pool = _two_provider_pool()
    # None (topic/project chose nothing) → the pool default.
    assert pool.select(provider_id=None).name == "tmux-hooks"
    assert pool.select().name == "tmux-hooks"
    # A stored id that isn't deployed here (e.g. a pool that was retired) must
    # never break a turn — it degrades to the default, not an error.
    assert pool.select(provider_id="gpu").name == "tmux-hooks"


def test_pool_select_returns_available_default():
    pool = _two_provider_pool()
    provider = pool.select()
    assert provider is pool.default()
    assert provider.available() is True


def test_build_pool_registers_device_alongside_the_local_box():
    """A topic can route to the user's own machine, and the default stays local
    (device is opt-in per topic)."""
    from app.domain.agent.compute import build_compute_pool

    pool = build_compute_pool()
    assert pool.has("tmux-hooks")
    assert pool.has("device")
    assert pool.default().name == "tmux-hooks"
    assert pool.select(provider_id="device").name == "device"


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


def test_device_backend_still_offers_a_local_transport(monkeypatch):
    # Defaulting every turn to someone else's machine must not leave a topic
    # unable to route back to a local one.
    from app.core.config import settings
    from app.domain.agent.compute import build_compute_pool

    monkeypatch.setattr(settings, "agent_backend", "device")
    pool = build_compute_pool()

    assert pool.default().name == "device"
    assert pool.has("tmux-hooks")


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
