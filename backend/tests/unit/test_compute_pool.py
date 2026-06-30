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
