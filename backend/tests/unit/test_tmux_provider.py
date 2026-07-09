"""TmuxHooksProvider with docker/tmux mocked out.

Drives a full run_turn without a real container: `_docker` is stubbed to canned
results, workspace paths point at tmp, and the hooks that would arrive over HTTP
are pushed into the router the moment the prompt is "sent". Asserts the provider
yields SessionInfo → ToolUse → Message → Result in order.
"""

import uuid

import pytest

from app.domain.agent import tmux_provider as tp
from app.domain.agent.hook_events import HookRouter
from app.domain.agent.service import (
    AgentMessage,
    AgentResult,
    AgentSessionInfo,
    AgentToolUse,
)
from app.domain.agent.tmux_provider import TmuxHooksProvider, pane_ready


def test_pane_ready_detects_prompt_box():
    assert pane_ready("some boot noise\n│ > type here          ❯ │\n") is True
    assert pane_ready("Welcome to Claude Code\nloading...\n") is False


class _FakeRun:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def test_ttyd_endpoint_parses_docker_port(monkeypatch):
    monkeypatch.setattr(tp.ws, "sandbox_available", lambda: True)
    monkeypatch.setattr(
        tp.subprocess, "run", lambda *a, **k: _FakeRun(0, "127.0.0.1:55011\n")
    )
    assert tp.ttyd_endpoint(uuid.uuid4()) == "127.0.0.1:55011"


def test_ttyd_endpoint_none_when_container_down(monkeypatch):
    monkeypatch.setattr(tp.ws, "sandbox_available", lambda: True)
    # docker port on a missing/unpublished container → non-zero rc, empty stdout.
    monkeypatch.setattr(tp.subprocess, "run", lambda *a, **k: _FakeRun(1, ""))
    assert tp.ttyd_endpoint(uuid.uuid4()) is None


def test_ttyd_endpoint_none_without_docker(monkeypatch):
    monkeypatch.setattr(tp.ws, "sandbox_available", lambda: False)
    assert tp.ttyd_endpoint(uuid.uuid4()) is None


@pytest.fixture
def _stub_env(monkeypatch, tmp_path):
    """Fake docker + workspace so the provider needs no real container."""
    sent: dict[str, str] = {}

    async def fake_docker(*args: str, stdin: bytes | None = None):
        # inspect image → "not found" so _ensure_container creates a fresh box.
        if args[:2] == ("inspect", "-f") and "{{.Config.Image}}" in args:
            return 1, "", "no such object"
        if args[:2] == ("inspect", "-f") and "{{.State.Running}}" in args:
            return 0, "true", ""
        # capture-pane → a ready pane (has the ❯ box).
        if "capture-pane" in args:
            return 0, "❯ ", ""
        # has-session → missing (rc 1) so a session gets created.
        if "has-session" in args:
            return 1, "", ""
        if "load-buffer" in args and stdin is not None:
            sent["prompt"] = stdin.decode()
        return 0, "", ""

    monkeypatch.setattr(tp, "_docker", fake_docker)
    monkeypatch.setattr(tp.ws, "sandbox_available", lambda: True)
    monkeypatch.setattr(
        tp.ws, "session_dir", lambda p, t: tmp_path / "session"
    )
    monkeypatch.setattr(
        tp.ws, "topic_worktree", lambda p, t: tmp_path / "work"
    )
    (tmp_path / "session").mkdir()
    (tmp_path / "work").mkdir()
    return sent


@pytest.mark.anyio
async def test_run_turn_streams_hook_events_in_order(_stub_env, monkeypatch):
    router = HookRouter()
    provider = TmuxHooksProvider(image="img:test", router=router, turn_timeout_s=5)
    project_id, topic_id = uuid.uuid4(), uuid.uuid4()
    topic_key = str(topic_id)

    # The moment the prompt is injected, deliver the hooks the container would
    # POST back (in the real system they arrive over /sandbox/hooks).
    async def fake_send(name: str, prompt: str) -> None:
        _stub_env["prompt"] = prompt
        for hook in [
            {"hook_event_name": "SessionStart", "session_id": "sess-1"},
            {"hook_event_name": "PreToolUse", "tool_name": "Bash",
             "tool_input": {"command": "echo hi"}},
            {"hook_event_name": "PostToolUse", "tool_name": "Bash"},
            {"hook_event_name": "MessageDisplay", "delta": "在看了"},
            {"hook_event_name": "Stop", "last_assistant_message": "搞定",
             "session_id": "sess-1"},
        ]:
            router.push(topic_key, hook)

    monkeypatch.setattr(provider, "_send_prompt", fake_send)

    events = [
        e
        async for e in provider.run_turn(
            project_id=project_id,
            topic_id=topic_id,
            prompt="帮我看下",
            system_prompt="sys",
            resume_session_id=None,
        )
    ]

    assert isinstance(events[0], AgentSessionInfo) and events[0].session_id == "sess-1"
    assert isinstance(events[1], AgentToolUse) and events[1].name == "Bash"
    assert isinstance(events[2], AgentMessage) and events[2].text == "在看了"
    assert isinstance(events[3], AgentResult) and events[3].text == "搞定"
    assert events[3].is_error is False
    assert _stub_env["prompt"] == "帮我看下"
    # The queue is released after the turn (next push finds no listener).
    assert router.push(topic_key, {"x": 1}) is False


@pytest.mark.anyio
async def test_run_turn_without_docker_yields_error_result(monkeypatch):
    monkeypatch.setattr(tp.ws, "sandbox_available", lambda: False)
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())
    events = [
        e
        async for e in provider.run_turn(
            project_id=uuid.uuid4(),
            topic_id=uuid.uuid4(),
            prompt="x",
            system_prompt="s",
            resume_session_id=None,
        )
    ]
    assert len(events) == 1
    assert isinstance(events[0], AgentResult) and events[0].is_error is True


@pytest.mark.anyio
async def test_run_turn_not_ready_yields_error(_stub_env, monkeypatch):
    # capture-pane never shows the ❯ box → handshake times out fast.
    async def never_ready(*args: str, stdin: bytes | None = None):
        if "capture-pane" in args:
            return 0, "still booting", ""
        if args[:2] == ("inspect", "-f") and "{{.Config.Image}}" in args:
            return 1, "", ""
        if "has-session" in args:
            return 1, "", ""
        return 0, "", ""

    monkeypatch.setattr(tp, "_docker", never_ready)
    monkeypatch.setattr(tp, "_READY_TIMEOUT_S", 0.5)
    monkeypatch.setattr(tp, "_READY_POLL_S", 0.1)
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())
    events = [
        e
        async for e in provider.run_turn(
            project_id=uuid.uuid4(),
            topic_id=uuid.uuid4(),
            prompt="x",
            system_prompt="s",
            resume_session_id=None,
        )
    ]
    assert isinstance(events[-1], AgentResult) and events[-1].is_error is True
    assert "未就绪" in events[-1].text
