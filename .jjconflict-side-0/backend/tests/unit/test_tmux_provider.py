"""TmuxHooksProvider with docker/tmux mocked out.

Drives a full run_turn without a real container: `_docker` is stubbed to canned
results, workspace paths point at tmp, and the hooks that would arrive over HTTP
are pushed into the router the moment the prompt is "sent". Asserts the provider
yields SessionInfo → ToolUse → Message → Result in order.
"""

import asyncio
import contextlib
import uuid

import pytest

from app.domain.agent import tmux_provider as tp
from app.domain.agent.hook_events import HookRouter
from app.domain.agent.hooks_substrate import ActivityTracker
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


def test_resume_ready_only_when_transcript_present(tmp_path):
    from app.domain.agent import clone

    sid = "cloned-session-id"
    # No transcript yet → do NOT resume (ordinary fresh topic stays fresh).
    assert tp._resume_ready(str(tmp_path), sid) is False
    # Write the forked transcript where the mount would hold it → resume.
    # (Any slug counts — reads key off the session id, not the cwd.)
    f = clone.transcript_file(tmp_path, sid, cwd="/topics/topic_ab12cd34")
    f.parent.mkdir(parents=True)
    f.write_text("{}", encoding="utf-8")
    assert tp._resume_ready(str(tmp_path), sid) is True


class _FakeRun:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


# The `docker port` call moved into workspace.published_endpoint (one parse
# shared by the terminal and 运行环境预览), so the seam to stub is the one that
# module runs — tmux_provider no longer imports subprocess at all.
def test_ttyd_endpoint_parses_docker_port(monkeypatch):
    monkeypatch.setattr(tp.ws, "sandbox_available", lambda: True)
    monkeypatch.setattr(
        tp.ws.subprocess, "run", lambda *a, **k: _FakeRun(0, "127.0.0.1:55011\n")
    )
    assert tp.ttyd_endpoint(uuid.uuid4()) == "127.0.0.1:55011"


def test_ttyd_endpoint_none_when_container_down(monkeypatch):
    monkeypatch.setattr(tp.ws, "sandbox_available", lambda: True)
    # docker port on a missing/unpublished container → non-zero rc, empty stdout.
    monkeypatch.setattr(tp.ws.subprocess, "run", lambda *a, **k: _FakeRun(1, ""))
    assert tp.ttyd_endpoint(uuid.uuid4()) is None


def test_ttyd_endpoint_none_without_docker(monkeypatch):
    monkeypatch.setattr(tp.ws, "sandbox_available", lambda: False)
    assert tp.ttyd_endpoint(uuid.uuid4()) is None


@pytest.mark.anyio
async def test_ensure_session_resumes_cloned_transcript(monkeypatch, tmp_path):
    from app.domain.agent import clone

    calls: list[tuple[str, ...]] = []

    async def fake_docker(*args: str, stdin=None):
        calls.append(args)
        if "has-session" in args:
            return 1, "", ""  # no live session → create one
        return 0, "", ""

    monkeypatch.setattr(tp, "_docker", fake_docker)
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())
    sid = "cloned-sid"

    # No transcript → fresh session (no --resume).
    await provider._ensure_session(
        "box", None, resume_session_id=sid, session_dir=str(tmp_path)
    )
    new_session = next(c for c in calls if "new-session" in c)
    assert "--resume" not in " ".join(new_session)

    # Write the cloned transcript → next session creation resumes it. A legacy
    # /work-slug transcript must count too (pre-project-mount sessions).
    f = clone.transcript_file(tmp_path, sid, cwd=clone.LEGACY_CONTAINER_CWD)
    f.parent.mkdir(parents=True)
    f.write_text("{}", encoding="utf-8")
    calls.clear()
    await provider._ensure_session(
        "box", None, resume_session_id=sid, session_dir=str(tmp_path)
    )
    new_session = next(c for c in calls if "new-session" in c)
    assert f"--resume {sid}" in " ".join(new_session)


async def test_ensure_session_denies_the_unanswerable_ask_tool(monkeypatch, tmp_path):
    """AskUserQuestion draws its picker inside the pane, where nobody can answer
    it — the turn then hangs. The launch line must deny it (cheese ask is the
    platform's way to ask), and --dangerously-skip-permissions must not be able
    to wave it through."""
    calls: list[tuple[str, ...]] = []

    async def fake_docker(*args: str, stdin=None):
        calls.append(args)
        return (1, "", "") if "has-session" in args else (0, "", "")

    monkeypatch.setattr(tp, "_docker", fake_docker)
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())
    await provider._ensure_session("box", None, session_dir=str(tmp_path))

    new_session = " ".join(next(c for c in calls if "new-session" in c))
    assert "--disallowedTools AskUserQuestion" in new_session


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
    monkeypatch.setattr(tp.ws, "session_dir", lambda p, t: tmp_path / "session")
    monkeypatch.setattr(tp.ws, "topic_worktree", lambda p, t: tmp_path / "work")
    (tmp_path / "session").mkdir()
    (tmp_path / "work").mkdir()
    return sent


@pytest.mark.anyio
async def test_run_turn_streams_hook_events_in_order(_stub_env, monkeypatch):
    router = HookRouter()
    provider = TmuxHooksProvider(
        image="img:test", router=router, idle_suspect_s=5, hard_ceiling_s=5
    )
    project_id, topic_id = uuid.uuid4(), uuid.uuid4()
    topic_key = str(topic_id)

    # The moment the prompt is injected, deliver the hooks the container would
    # POST back (in the real system they arrive over /sandbox/hooks).
    async def fake_send(name: str, prompt: str) -> None:
        _stub_env["prompt"] = prompt
        for hook in [
            {"hook_event_name": "SessionStart", "session_id": "sess-1"},
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "echo hi"},
            },
            {"hook_event_name": "PostToolUse", "tool_name": "Bash"},
            {"hook_event_name": "MessageDisplay", "delta": "在看了"},
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "搞定",
                "session_id": "sess-1",
            },
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


# --- turn 活跃度检测 (2026-08-09): TmuxHooksProvider's activity-detection seam.


@pytest.mark.anyio
async def test_confirm_alive_reflects_pane_dead(monkeypatch):
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())

    class _FakeControl:
        def __init__(self, dead: bool) -> None:
            self._dead = dead

        async def pane_dead(self) -> bool:
            return self._dead

    async def control_alive(_name: str):
        return _FakeControl(False)

    monkeypatch.setattr(provider, "_control", control_alive)
    assert await provider._confirm_alive("box") is True

    async def control_dead(_name: str):
        return _FakeControl(True)

    monkeypatch.setattr(provider, "_control", control_dead)
    assert await provider._confirm_alive("box") is False


@pytest.mark.anyio
async def test_confirm_alive_treats_a_probe_failure_as_alive(monkeypatch):
    """A control-connection hiccup while probing isn't proof of death — mirrors
    `_send_prompt` treating a SEND failure (not a probe failure) as fatal."""
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())

    async def boom(_name: str):
        raise RuntimeError("control connection dropped")

    monkeypatch.setattr(provider, "_control", boom)
    assert await provider._confirm_alive("box") is True


@pytest.mark.anyio
async def test_activity_monitor_touches_tracker_on_pane_change(monkeypatch):
    """The background poller must count a CHANGED capture-pane as activity —
    the tmux backend's answer to "no hook, but the pane is clearly busy"; an
    unchanged pane must NOT keep touching the tracker."""
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())
    monkeypatch.setattr(provider, "_ACTIVITY_POLL_S", 0.01)
    outputs = ["frame-1", "frame-1", "frame-2", "frame-2", "frame-2"]
    calls = {"i": 0}

    async def fake_docker(*args: str, stdin=None):
        i = min(calls["i"], len(outputs) - 1)
        calls["i"] += 1
        return 0, outputs[i], ""

    monkeypatch.setattr(tp, "_docker", fake_docker)
    tracker = ActivityTracker(last_at=0.0)

    task = await provider._start_activity_monitor("box", tracker)
    assert task is not None
    try:
        await asyncio.sleep(0.08)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # The unchanged→changed transition touched it off the initial sentinel.
    assert tracker.last_at > 0.0


@pytest.mark.anyio
async def test_activity_status_none_when_no_turn_is_monitored():
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())
    assert provider.activity_status(uuid.uuid4()) is None


@pytest.mark.anyio
async def test_activity_status_reports_idle_and_suspect_state(monkeypatch):
    """`cheese status` reads this while a turn is running (turn 活跃度检测) — it
    must reflect a currently-monitored turn's idle time and, once idle-suspect
    trips, how long it's been suspected."""
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())
    topic_id = uuid.uuid4()
    name = tp._tmux_container_name(topic_id)
    loop = asyncio.get_event_loop()
    tracker = ActivityTracker(last_at=loop.time())
    provider._activity[name] = tracker

    status = provider.activity_status(topic_id)
    assert status is not None
    assert status["idle_for_s"] >= 0
    assert status["suspect_since_s_ago"] is None

    tracker.suspect_since = loop.time() - 5
    status = provider.activity_status(topic_id)
    assert status["suspect_since_s_ago"] >= 5


def test_env_stamp_ignores_per_turn_values_but_tracks_the_model_route():
    """The stamp decides whether a long-lived container is still valid. It must
    change when model routing changes and NOT change per turn, or every turn
    would rebuild the box."""
    from app.domain.agent.tmux_provider import _env_stamp

    base = {
        "ANTHROPIC_BASE_URL": "http://gw/api/llm",
        "ANTHROPIC_AUTH_TOKEN": "tok-1",
        "CLAUDE_MODEL": "opus",
        "CHEESE_API": "http://backend/api",
        "CHEESE_TURN": "turn-aaa",
    }

    same_turn_later = {**base, "CHEESE_TURN": "turn-bbb"}
    assert _env_stamp(base) == _env_stamp(same_turn_later)

    rerouted = {**base, "ANTHROPIC_BASE_URL": "http://other/api/llm"}
    assert _env_stamp(base) != _env_stamp(rerouted)

    remodelled = {**base, "CLAUDE_MODEL": "sonnet"}
    assert _env_stamp(base) != _env_stamp(remodelled)


def test_env_stamp_does_not_leak_the_credential():
    from app.domain.agent.tmux_provider import _env_stamp

    stamp = _env_stamp({"ANTHROPIC_AUTH_TOKEN": "sk-super-secret-value"})
    assert "sk-super-secret-value" not in stamp
    assert len(stamp) == 16


def test_an_unstamped_container_is_not_treated_as_drifted():
    """Rebuilding kills the tmux session, and that session is the topic's
    conversational continuity. A container from before the stamp existed says
    nothing about its route, so it must not be torn down on suspicion —
    otherwise shipping the stamp resets every live topic's memory."""
    from app.domain.agent.tmux_provider import env_stamp_drifted

    assert not env_stamp_drifted("", "abc123")


def test_a_differing_stamp_is_drift():
    from app.domain.agent.tmux_provider import env_stamp_drifted

    assert env_stamp_drifted("old456", "abc123")
    assert not env_stamp_drifted("abc123", "abc123")
