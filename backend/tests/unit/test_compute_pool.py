"""ComputePool / LocalDockerProvider (design §3 / review R2)."""

import uuid

import pytest

from app.domain.agent.compute import ComputePool, LocalDockerProvider
from app.domain.agent.service import AgentResult


class _RecordingAgent:
    """Captures the kwargs run_turn forwards to the SDK."""

    def __init__(self):
        self.calls: list[dict] = []

    async def stream_reply(self, **kwargs):
        self.calls.append(kwargs)
        yield AgentResult(text="ok", session_id="s1")


def _provider(
    tmp_path, *, sandbox_enabled: bool
) -> tuple[LocalDockerProvider, _RecordingAgent]:
    agent = _RecordingAgent()
    p = LocalDockerProvider(
        agent=agent, workspace_root=str(tmp_path), sandbox_enabled=sandbox_enabled
    )
    return p, agent


@pytest.mark.anyio
async def test_degraded_turn_has_no_sandbox_and_uses_workspace_cwd(tmp_path):
    p, agent = _provider(tmp_path, sandbox_enabled=False)
    assert p.sandboxed() is False
    pid = uuid.uuid4()
    events = [
        e
        async for e in p.run_turn(
            project_id=pid,
            topic_id=uuid.uuid4(),
            prompt="hi",
            system_prompt="sys",
            resume_session_id=None,
            model="glm-5.2",
            env={"ANTHROPIC_AUTH_TOKEN": "k"},
        )
    ]
    assert isinstance(events[0], AgentResult)
    call = agent.calls[0]
    assert call["sandbox"] is None  # no Docker → degraded
    assert str(pid) in call["cwd"]  # cwd = the project's workspace dir
    assert call["model"] == "glm-5.2"
    assert call["env"] == {"ANTHROPIC_AUTH_TOKEN": "k"}


@pytest.mark.anyio
async def test_checkpoint_noops_without_sandbox(tmp_path):
    p, _ = _provider(tmp_path, sandbox_enabled=False)
    # Must not raise / must not touch git when there is no sandbox.
    p.checkpoint(uuid.uuid4(), uuid.uuid4())


def test_pool_select_returns_available_default(tmp_path):
    agent = _RecordingAgent()
    pool = ComputePool.local(
        agent=agent, workspace_root=str(tmp_path), sandbox_enabled=False
    )
    provider = pool.select()
    assert provider is pool.default()
    assert provider.available() is True
    assert provider.name == "local-docker"


def test_pool_rejects_unknown_default():
    with pytest.raises(ValueError):
        ComputePool([], "missing")


class _FakeProvider:
    """Minimal provider stand-in for routing tests (v4 会话级选择)."""

    def __init__(self, name: str):
        self.name = name

    def available(self) -> bool:
        return True


def _two_provider_pool() -> ComputePool:
    local = _FakeProvider("local-docker")
    remote = _FakeProvider("remote-cheesed")
    return ComputePool([local, remote], "local-docker")


def test_select_routes_to_the_named_provider():
    pool = _two_provider_pool()
    assert pool.select(provider_id="remote-cheesed").name == "remote-cheesed"
    assert pool.select(provider_id="local-docker").name == "local-docker"


def test_select_falls_back_to_default_for_unknown_or_none():
    pool = _two_provider_pool()
    # None (topic/project chose nothing) → the pool default.
    assert pool.select(provider_id=None).name == "local-docker"
    assert pool.select().name == "local-docker"
    # A stored id that isn't deployed here (e.g. "gpu") must never break a turn —
    # it degrades to the default, not an error.
    assert pool.select(provider_id="gpu").name == "local-docker"


def test_build_pool_registers_device_alongside_local():
    # In the default (sdk) deployment the self-hosted device pool rides alongside
    # local-docker, so a topic can route to the user's own machine — but the
    # default stays local-docker (device is opt-in per topic).
    from app.domain.agent.compute import build_compute_pool

    pool = build_compute_pool(_RecordingAgent())
    assert pool.has("local-docker")
    assert pool.has("device")
    assert pool.default().name == "local-docker"
    assert pool.select(provider_id="device").name == "device"


def test_build_pool_registers_the_concrete_cloud_provider():
    from unittest.mock import AsyncMock

    from app.domain.agent.cloud_provider import CloudProvider
    from app.domain.agent.compute import build_compute_pool

    cloud = CloudProvider(
        configured=False,
        ensure_topic_cloud=AsyncMock(),
        read_topic_cloud=AsyncMock(),
    )
    pool = build_compute_pool(_RecordingAgent(), cloud_provider=cloud)

    assert pool.has("cloud")
    assert pool.select(provider_id="cloud") is cloud
    assert cloud.available() is False


def test_resolve_compute_id_topic_then_project_then_team_default():
    from app.domain.agent.chat import _resolve_compute_id

    # Topic's own选择 wins over the project sticky.
    assert (
        _resolve_compute_id(
            {"compute_profile": "local-docker"}, "remote-cheesed", "device"
        )
        == "remote-cheesed"
    )
    # No topic选择 → project sticky, before the team's default.
    assert (
        _resolve_compute_id({"compute_profile": "remote-cheesed"}, None, "device")
        == "remote-cheesed"
    )
    # A fresh project starts from the team's default.
    assert _resolve_compute_id({}, None, "device") == "device"
    assert _resolve_compute_id(None, None, "device") == "device"
    # No choice at any layer → None (pool default).
    assert _resolve_compute_id({}, None, None) is None
    assert _resolve_compute_id(None, None, None) is None


def test_tmux_keeps_the_remote_transport_in_the_pool(monkeypatch):
    """The convergence end state must be reachable.

    fusion-design §8.6 settles on ONE turn flow with two thin transports — tmux
    locally, device remotely, a topic choosing per turn. Returning a
    single-provider pool for `tmux` dropped the remote one, so the only way to
    have device compute at all was to run the pre-convergence SDK path locally.
    """
    from app.core.config import settings
    from app.domain.agent.compute import build_compute_pool

    monkeypatch.setattr(settings, "agent_backend", "tmux")
    pool = build_compute_pool(_RecordingAgent())

    assert pool.has("tmux-hooks")
    assert pool.has("device"), "the remote transport must survive picking a local one"
    assert not pool.has("local-docker"), "exactly one local transport, not both"
    assert pool.default().name == "tmux-hooks"


def test_device_backend_still_offers_a_local_transport(monkeypatch):
    # Defaulting every turn to someone else's machine must not leave a topic
    # unable to route back to a local one.
    from app.core.config import settings
    from app.domain.agent.compute import build_compute_pool

    monkeypatch.setattr(settings, "agent_backend", "device")
    pool = build_compute_pool(_RecordingAgent())

    assert pool.default().name == "device"
    assert pool.has("local-docker")
