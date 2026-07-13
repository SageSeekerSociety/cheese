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


def test_resolve_compute_id_topic_wins_then_project_sticky():
    from app.domain.agent.chat import _resolve_compute_id

    # Topic's own选择 wins over the project sticky.
    assert (
        _resolve_compute_id({"compute_profile": "local-docker"}, "remote-cheesed")
        == "remote-cheesed"
    )
    # No topic选择 → project sticky.
    assert _resolve_compute_id({"compute_profile": "remote-cheesed"}, None) == (
        "remote-cheesed"
    )
    # Neither → None (pool default).
    assert _resolve_compute_id({}, None) is None
    assert _resolve_compute_id(None, None) is None
