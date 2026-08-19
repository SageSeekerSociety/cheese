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
from app.domain.agent.harness import SessionRef
from app.domain.agent.hook_events import HookRouter
from app.domain.agent.hooks_substrate import ActivityTracker
from app.domain.agent.service import (
    AgentMessage,
    AgentResult,
    AgentSessionInfo,
    AgentToolUse,
)
from app.domain.agent.tmux_provider import TmuxHooksProvider, TmuxScreen, pane_ready


def test_pane_ready_detects_prompt_box():
    assert pane_ready("some boot noise\n│ > type here          ❯ │\n") is True
    assert pane_ready("Welcome to Claude Code\nloading...\n") is False


# --- prompt-delivery verification (the 2026-08-16 paste-loop / swallowed-Enter
# family): matching must survive soft-wrap, box borders, CJK widths and the
# `[Pasted text …]` placeholder. Mirrors the cheeselet's norm()/bodyInComposer.

_CJK_PROMPT = (
    "【平台】以下是平台自动发出的指令，不是任何人手打的：请检查当前工作台状态并汇报。"
)


def test_prompt_snippet_flattens_and_caps():
    from app.domain.agent.tmux_provider import prompt_snippet

    assert prompt_snippet("hello world\nmore") == "helloworld"
    # Leading blank lines are skipped; the cap is 24 flattened chars.
    assert prompt_snippet("\n  \n" + _CJK_PROMPT) == _CJK_PROMPT.replace(" ", "")[:24]
    assert prompt_snippet("   \n\t\n") == ""


def test_composer_holds_body_across_soft_wrap_and_borders():
    """A 46-column pane shows at most ~21 CJK chars per bordered row — the
    24-char anchor can never sit on one row, only in the flattened region."""
    from app.domain.agent.tmux_provider import composer_holds_body, prompt_snippet

    snippet = prompt_snippet(_CJK_PROMPT)
    capture = (
        "some scrollback\n"
        "╭──────────────────────────────────────────╮\n"
        "│ ❯ 【平台】以下是平台自动发出的指令，不是任 │\n"
        "│ 何人手打的：请检查当前工作台状态并汇报。 │\n"
        "╰──────────────────────────────────────────╯\n"
    )
    assert composer_holds_body(capture, snippet) is True
    # An empty composer must not count, and neither must a different body.
    assert composer_holds_body("junk\n│ ❯  │\n", snippet) is False
    assert composer_holds_body("junk\n│ ❯ 完全不同的内容 │\n", snippet) is False
    # No input box painted at all → nothing can be verified.
    assert composer_holds_body("still booting", snippet) is False


def test_composer_holds_body_accepts_the_pasted_text_placeholder():
    """Large pastes render as `[Pasted text #N +N lines]` instead of the body
    (claude-session-driver #20) — the widget proves delivery just the same."""
    from app.domain.agent.tmux_provider import composer_holds_body

    capture = "history\n│ ❯ [Pasted text #1 +11 lines] │\n"
    assert composer_holds_body(capture, "anysnippet") is True


class _FakeScreenControl:
    """Control client + pane model in one: a paste puts the body into the
    composer (unless configured to drop it), an Enter clears it (unless
    configured to swallow it) — the two silent failures _send_prompt exists to
    catch."""

    def __init__(
        self,
        drop_pastes: int = 0,
        swallow_enters: int = 0,
        initial_composer: str = "",
    ) -> None:
        self.composer = initial_composer
        self.body = ""
        self.pastes = 0
        self.enters = 0
        self.kills = 0
        self._drop_pastes = drop_pastes
        self._swallow_enters = swallow_enters

    async def pane_dead(self) -> bool:
        return False

    async def send(self, *args: str):
        from types import SimpleNamespace

        if "paste-buffer" in args:
            self.pastes += 1
            if self.pastes > self._drop_pastes:
                self.composer = self.body
        elif "C-u" in args:
            self.kills += 1
            self.composer = ""
        elif "Enter" in args:
            self.enters += 1
            if self.enters > self._swallow_enters:
                self.composer = ""
        return SimpleNamespace(ok=True, error=None)

    def capture(self) -> str:
        return f"scrollback\n│ ❯ {self.composer} │\n"


@pytest.fixture
def _fast_settle(monkeypatch):
    monkeypatch.setattr(tp, "_PASTE_SETTLE_S", 0.05)
    monkeypatch.setattr(tp, "_ENTER_SETTLE_S", 0.05)
    monkeypatch.setattr(tp, "_SETTLE_POLL_S", 0.01)


# A box hosts a whole room, so a screen is (container, session) — every helper
# below needs one, and the session name is what tells two topics apart inside
# the same box.
_BOX = TmuxScreen("topic-box", "cheese-ab12cd34")


def _aio(value):
    """Wrap a value as an awaitable, for monkeypatching an async method."""

    async def _coro(*_args, **_kwargs):
        return value

    return _coro()


def _topic_env(**overrides) -> dict[str, str]:
    """The minimum per-session env `_ensure_session` reads off its argument."""
    return {
        "CLAUDE_CONFIG_DIR": "/sessions/ab12cd34",
        "CHEESE_WORKDIR": "/topics/topic_ab12cd34",
        "CHEESE_PORT_SLOT": "0",
        "CHEESE_TOPIC": str(uuid.uuid4()),
        **overrides,
    }


def _wire(monkeypatch, provider, screen: _FakeScreenControl) -> None:
    async def fake_control(_screen):
        return screen

    async def fake_docker(*args: str, stdin: bytes | None = None):
        if "load-buffer" in args and stdin is not None:
            screen.body = stdin.decode()
        if "capture-pane" in args:
            return 0, screen.capture(), ""
        return 0, "", ""

    monkeypatch.setattr(provider, "_control", fake_control)
    monkeypatch.setattr(tp, "_docker", fake_docker)


@pytest.mark.anyio
async def test_send_prompt_resends_a_swallowed_enter(_fast_settle, monkeypatch):
    """Claude Code swallows an Enter that rides too close to the paste
    (claude-session-driver #20) — the prompt then sat in the composer until the
    25s undelivered verdict. The sender must see the composer still holding the
    body and nudge Enter again; it must NOT re-paste (that duplicates)."""
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())
    screen = _FakeScreenControl(swallow_enters=1)
    _wire(monkeypatch, provider, screen)

    await provider._send_prompt(_BOX, "帮我看下这个问题")

    assert screen.pastes == 1, "re-pasting duplicates the prompt"
    assert screen.enters == 2, "the swallowed Enter was never re-sent"
    assert screen.composer == ""


@pytest.mark.anyio
async def test_send_prompt_fails_loud_when_the_paste_never_lands(
    _fast_settle, monkeypatch
):
    """Every paste dropped (the #430 fire-and-forget shape): bounded re-pastes,
    then a clean error — never an Enter fired at a composer that visibly never
    received the body, and never a silent 25s wait."""
    from app.domain.agent.hooks_substrate import ScreenSetupError

    provider = TmuxHooksProvider(image="img:test", router=HookRouter())
    screen = _FakeScreenControl(drop_pastes=999)
    _wire(monkeypatch, provider, screen)

    with pytest.raises(ScreenSetupError, match="没有出现在输入框"):
        await provider._send_prompt(_BOX, "帮我看下这个问题")

    assert screen.pastes == 1 + tp._MAX_REPASTES
    assert screen.enters == 0, "an Enter was fired at a body-less composer"


@pytest.mark.anyio
async def test_send_prompt_clears_a_poisoned_composer_before_pasting(
    _fast_settle, monkeypatch
):
    """A composer already stacked with widgets from FAILED earlier sends (44
    deep in prod, 2026-08-17) must be cleared before every paste attempt.
    Without the Ctrl+U, the OLD widget makes the paste-verify pass even when
    this turn's paste was dropped — and the Enter then submits pure garbage."""
    from app.domain.agent.hooks_substrate import ScreenSetupError

    provider = TmuxHooksProvider(image="img:test", router=HookRouter())
    screen = _FakeScreenControl(
        drop_pastes=999,
        initial_composer="[Pasted text #9 +40 lines][Pasted text #10 +42 lines]",
    )
    _wire(monkeypatch, provider, screen)

    with pytest.raises(ScreenSetupError, match="没有出现在输入框"):
        await provider._send_prompt(_BOX, "帮我看下这个问题")

    assert screen.kills == 1 + tp._MAX_REPASTES, "no Ctrl+U before each paste"
    assert screen.enters == 0, "the old garbage widget was verified as this paste"


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
        _BOX,
        None,
        session_env=_topic_env(),
        resume_session_id=sid,
        session_dir=str(tmp_path),
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
        _BOX,
        None,
        session_env=_topic_env(),
        resume_session_id=sid,
        session_dir=str(tmp_path),
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
    await provider._ensure_session(
        _BOX, None, session_env=_topic_env(), session_dir=str(tmp_path)
    )

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
    monkeypatch.setattr(tp.ws, "sessions_root", lambda p: tmp_path)
    monkeypatch.setattr(tp.ws, "topic_worktree", lambda p, t: tmp_path / "work")
    monkeypatch.setattr(
        TmuxHooksProvider, "_room_id", lambda self, project_id, topic_id: _aio(topic_id)
    )
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
    async def fake_send(screen, prompt: str) -> None:
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
    # Stop closes only the run marker; the screen subscription stays live.
    assert router.push(topic_key, {"x": 1}) is True
    await provider._close_topic(topic_id)
    assert router.push(topic_key, {"x": 2}) is False


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

    task = await provider._start_activity_monitor(_BOX, tracker)
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
    loop = asyncio.get_event_loop()
    tracker = ActivityTracker(last_at=loop.time())
    provider._activity[tp._screen_for(topic_id)] = tracker

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


def test_subscription_session_token_lives_for_the_session_not_one_hour(monkeypatch):
    """The container's CLAUDE_CODE_OAUTH_TOKEN is read once at claude start and
    never hot-refreshed (env is read at process start; the tmux session is reused
    across turns — see ContainerSubscription). A 1h token expires under the
    still-running process and the metering proxy then 407s every later turn, so
    its exp must span the session — matching the CHEESE_TOKEN minted beside it,
    not the per-turn default."""
    import time

    from app.core.config import settings
    from app.core.sandbox_auth import scoped_token_claims
    from app.domain.agent.hooks_substrate import SESSION_TOKEN_TTL_S

    monkeypatch.setattr(settings, "subscription_enabled", True)
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())
    env = provider._topic_env(
        project_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        token="hook-token",
        port_slot=0,
        env=None,
        memory_scope=None,
        owner=None,
        turn_id=None,
    )

    claims = scoped_token_claims(env["CLAUDE_CODE_OAUTH_TOKEN"])
    assert claims is not None
    remaining = claims["exp"] - int(time.time())
    assert remaining > 7 * 24 * 3600  # rules out the 3600s per-turn default
    assert SESSION_TOKEN_TTL_S - 300 < remaining <= SESSION_TOKEN_TTL_S + 5


@pytest.mark.anyio
async def test_drop_control_also_drops_the_container_subscription():
    router = HookRouter()
    provider = TmuxHooksProvider(image="img:test", router=router)
    project_id, topic_id = uuid.uuid4(), uuid.uuid4()
    subscription = await provider.ensure_subscription(project_id, topic_id)
    provider._live[topic_id] = _BOX

    await provider.drop_control(_BOX)

    assert subscription.consumer_task is not None
    assert subscription.consumer_task.done()
    assert router.push(str(topic_id), {"hook_event_name": "Stop"}) is False


@pytest.mark.anyio
async def test_restart_recovery_subscribes_running_topic_containers(monkeypatch):
    project_id, topic_id = uuid.uuid4(), uuid.uuid4()
    calls: list[tuple[str, ...]] = []

    async def fake_docker(*args: str, stdin=None):
        calls.append(args)
        if args and args[0] == "ps":
            return 0, "topic-box\n", ""
        if args and args[0] == "inspect":
            return 0, f"CHEESE_PROJECT={project_id}\n", ""
        if "list-sessions" in args:
            return 0, f"cheese-{topic_id.hex[:8]}\n", ""
        if "show-environment" in args:
            return 0, f"CHEESE_TOPIC={topic_id}\n", ""
        return 1, "", "unexpected"

    monkeypatch.setattr(tp, "_docker", fake_docker)
    router = HookRouter()
    provider = TmuxHooksProvider(image="img:test", router=router)

    recovered = await provider.recover_subscriptions()

    assert len(recovered) == 1
    assert recovered[0].project_id == project_id
    assert recovered[0].topic_id == topic_id
    assert not recovered[0].ready.is_set()
    assert provider._live[topic_id] == tp.TmuxScreen(
        "topic-box", f"cheese-{topic_id.hex[:8]}"
    )
    assert calls[0][0] == "ps"

    recovered[0].ready.set()
    router.push(str(topic_id), {"hook_event_name": "PostToolUse"})
    await recovered[0].sink.queue.join()
    await provider._close_topic(topic_id)


# --- one box per room (容器按房间分配) ---------------------------------------
#
# The invariant these guard: two topics of one room share a CONTAINER and share
# NOTHING that says which topic they are. The second half is the dangerous one —
# a tmux session seeds its environment from the server's frozen global, so
# anything not written with `-e` silently makes the second topic act as the
# first (measured on the device backend, see device_launch.py).


class _FakeBox:
    """A docker+tmux stand-in that remembers what each session was created with,
    so a test can ask "what is topic B's claude actually running on?"."""

    def __init__(self) -> None:
        self.created: list[list[str]] = []  # `docker run` argv per container
        self.sessions: dict[tuple[str, str], dict[str, str]] = {}
        self.containers: set[str] = set()
        self.mounts: dict[str, list[str]] = {}

    async def docker(self, *args: str, stdin: bytes | None = None):
        args = tuple(args)
        if args[0] == "run":
            name = args[args.index("--name") + 1]
            self.containers.add(name)
            self.created.append(list(args))
            self.mounts[name] = [
                args[i + 1].replace(":", "->", 1).removesuffix(":ro")
                for i, token in enumerate(args)
                if token == "-v"
            ]
            return 0, "", ""
        if args[0] == "rm":
            # Destroying the box destroys every session in it. A fake that kept
            # them would answer `has-session` yes on a box that was just
            # recreated, sending the caller down the deaf-session path and
            # announcing a spurious "token" rebuild on top of the real one.
            name = args[-1]
            self.containers.discard(name)
            self.mounts.pop(name, None)
            for key in [k for k in self.sessions if k[0] == name]:
                del self.sessions[key]
            return 0, "", ""
        if args[0] == "inspect" and "{{.Config.Image}}" in args:
            return (0, "img:test", "") if args[-1] in self.containers else (1, "", "")
        if args[0] == "inspect" and "{{.State.Running}}" in args:
            return 0, "true", ""
        if args[0] == "inspect" and ".Mounts" in " ".join(args):
            # Answer the CLI-mount freshness probe from what this box was
            # ACTUALLY created with — a fake that always looks stale would
            # rebuild the box on every turn and quietly defeat the point of
            # every assertion below.
            return 0, "\n".join(self.mounts.get(args[-1], [])), ""
        if args[0] == "inspect":
            return 0, "", ""
        if args[0] != "exec":
            return 0, "", ""
        container = args[2] if args[1] == "-d" else args[1]
        rest = list(args[3:] if args[1] == "-d" else args[2:])
        if "has-session" in rest:
            session = rest[rest.index("-t") + 1]
            return (0, "", "") if (container, session) in self.sessions else (1, "", "")
        if "new-session" in rest:
            session = rest[rest.index("-s") + 1]
            env = {}
            for i, token in enumerate(rest):
                if token == "-e":
                    key, _, value = rest[i + 1].partition("=")
                    env[key] = value
            self.sessions[(container, session)] = env
            return 0, "", ""
        if "list-sessions" in rest:
            live = [s for c, s in self.sessions if c == container]
            return 0, "\n".join(live) + "\n", ""
        if "show-environment" in rest:
            session = rest[rest.index("-t") + 1]
            key = rest[-1]
            value = self.sessions.get((container, session), {}).get(key)
            return (
                (0, f"{key}={value}\n", "") if value is not None else (0, "-" + key, "")
            )
        if "capture-pane" in rest:
            return 0, "❯ ", ""
        return 0, "", ""


_announced: list = []


@pytest.fixture
def _room_box(monkeypatch, tmp_path):
    box = _FakeBox()
    _announced.clear()
    monkeypatch.setattr(tp, "_docker", box.docker)
    monkeypatch.setattr(tp.ws, "sandbox_available", lambda: True)
    monkeypatch.setattr(tp.ws, "sessions_root", lambda p: tmp_path / "sessions")
    monkeypatch.setattr(
        tp.ws, "session_dir", lambda p, t: tmp_path / "sessions" / t.hex[:8]
    )
    monkeypatch.setattr(tp.ws, "topic_worktree", lambda p, t: tmp_path / t.hex[:8])

    async def _announce(topic_id, cause="image"):
        _announced.append((topic_id, cause))

    monkeypatch.setattr(tp, "warn_container_rebuilt", _announce)
    (tmp_path / "sessions").mkdir()
    return box


async def _bring_up(provider, box, project_id, topic_id, room_id, token="tok"):
    """Run only the setup half of a turn and hand back the screen it produced."""
    monkey = getattr(provider, "_room_id")  # noqa: B009 — documents the seam
    assert monkey is not None
    return await provider._ensure_ready(
        project_id=project_id,
        topic_id=topic_id,
        token=token,
        model=None,
        env=None,
        memory_scope=None,
        owner=None,
        turn_id=None,
        resume_session_id=None,
        system_prompt="",
        precheck=None,
    )


@pytest.mark.anyio
async def test_a_rooms_topics_share_one_container(_room_box, monkeypatch):
    """验收 #1: the母话题 and a task split out of it run in the SAME box —
    otherwise "containers are never reaped" grows the box count without bound."""
    box = _room_box
    project_id, room_id, task_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rooms = {room_id: room_id, task_id: room_id}
    monkeypatch.setattr(
        TmuxHooksProvider, "_room_id", lambda self, p, t: _aio(rooms[t])
    )
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())

    room_screen = await _bring_up(provider, box, project_id, room_id, room_id)
    task_screen = await _bring_up(provider, box, project_id, task_id, room_id)

    assert room_screen.container == task_screen.container
    assert len(box.containers) == 1, "the task built a second box"
    assert len(box.created) == 1, "the task rebuilt the room's box out from under it"
    # ...and they are NOT the same screen: one session each, or the second topic
    # would be typing into the first one's claude.
    assert room_screen.session != task_screen.session
    assert len(box.sessions) == 2


@pytest.mark.anyio
async def test_two_topics_in_one_box_never_share_identity(_room_box, monkeypatch):
    """验收 #2/#3: each session carries its OWN topic id, hook token, hook URL,
    config dir and cwd. Nothing here may be inherited — a tmux session seeds
    from the server's frozen global env, so an unset key means "act as whoever
    started this box's tmux server first"."""
    box = _room_box
    project_id, room_id, task_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rooms = {room_id: room_id, task_id: room_id}
    monkeypatch.setattr(
        TmuxHooksProvider, "_room_id", lambda self, p, t: _aio(rooms[t])
    )
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())

    await _bring_up(provider, box, project_id, room_id, room_id, token="room-token")
    await _bring_up(provider, box, project_id, task_id, room_id, token="task-token")

    room_env = box.sessions[
        (tp.ws.tmux_container_name(room_id), tp.ws.tmux_session_name(room_id))
    ]
    task_env = box.sessions[
        (tp.ws.tmux_container_name(room_id), tp.ws.tmux_session_name(task_id))
    ]

    assert room_env["CHEESE_TOPIC"] == str(room_id)
    assert task_env["CHEESE_TOPIC"] == str(task_id)
    assert room_env["CHEESE_TOKEN"] == "room-token"
    assert task_env["CHEESE_TOKEN"] == "task-token"
    assert str(task_id) in task_env["CHEESE_HOOK_URL"]
    assert str(room_id) not in task_env["CHEESE_HOOK_URL"]
    # 验收 #3: each session's cwd and claude state are its own.
    assert room_env["CHEESE_WORKDIR"] != task_env["CHEESE_WORKDIR"]
    assert task_env["CHEESE_WORKDIR"] == tp.ws.sandbox_topic_workdir(task_id)
    assert task_env["CLAUDE_CONFIG_DIR"] == tp.ws.sandbox_session_dir(task_id)
    assert room_env["CLAUDE_CONFIG_DIR"] != task_env["CLAUDE_CONFIG_DIR"]
    # The spool and await logs follow the config dir, or the backend reads one
    # topic's events out of another's directory.
    assert task_env["CHEESE_HOOK_SPOOL"].startswith(task_env["CLAUDE_CONFIG_DIR"])
    assert task_env["CHEESE_AWAIT_LOGS"].startswith(task_env["CLAUDE_CONFIG_DIR"])
    # 分身身份 travels per session too.
    assert room_env["CHEESE_AUTHOR"] != task_env["CHEESE_AUTHOR"]


@pytest.mark.anyio
async def test_the_container_environment_holds_nothing_per_topic(
    _room_box, monkeypatch
):
    """The box's own env is fixed at creation and a room keeps gaining tasks, so
    a per-topic key baked in there would be frozen — and WRONG — for every topic
    that joins later. It must carry the room-wide wiring only."""
    box = _room_box
    project_id, room_id, task_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rooms = {room_id: room_id, task_id: room_id}
    monkeypatch.setattr(
        TmuxHooksProvider, "_room_id", lambda self, p, t: _aio(rooms[t])
    )
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())

    await _bring_up(provider, box, project_id, task_id, room_id, token="task-token")

    argv = box.created[0]
    container_env = {}
    for i, token in enumerate(argv):
        if token == "-e":
            key, _, value = argv[i + 1].partition("=")
            container_env[key] = value
    for leaked in (
        "CHEESE_TOPIC",
        "CHEESE_TOKEN",
        "CHEESE_HOOK_URL",
        "CHEESE_AUTHOR",
        "CLAUDE_CONFIG_DIR",
        "CHEESE_HOOK_SPOOL",
        "CHEESE_AWAIT_LOGS",
    ):
        assert leaked not in container_env, f"{leaked} is per-topic, not per-room"
    assert container_env["CHEESE_ROOM"] == str(room_id)
    assert container_env["CHEESE_PROJECT"] == str(project_id)
    # The sessions TREE is mounted, not one topic's dir — a box cannot gain a
    # mount later, and the tasks that will need one do not exist yet.
    assert f"{tmp_mount(argv)}:{tp.ws.SANDBOX_SESSIONS_ROOT}" in argv
    # The box's own workdir is the TREE, never a topic's directory in it.
    # `docker run -w` creates a missing workdir, and this one is inside a bind
    # mount — naming a topic would plant an empty dir on the host exactly where
    # that topic's jj workspace has to go, and `jj workspace add` refuses a path
    # that already exists. A room that has never taken a turn is the live case.
    assert argv[argv.index("-w") + 1] == tp.ws.SANDBOX_TOPICS_ROOT
    for session_env in box.sessions.values():
        assert session_env["CHEESE_WORKDIR"].startswith(
            f"{tp.ws.SANDBOX_TOPICS_ROOT}/"
        ), "a session still has to land in its own worktree"


def tmp_mount(argv: list[str]) -> str:
    """The host side of the sessions mount in a `docker run` argv."""
    for i, token in enumerate(argv):
        if token == "-v" and argv[i + 1].endswith(tp.ws.SANDBOX_SESSIONS_ROOT):
            return argv[i + 1].split(":")[0]
    raise AssertionError("no sessions mount in the container argv")


@pytest.mark.anyio
async def test_each_topic_gets_its_own_published_port_slot(_room_box, monkeypatch):
    """运行环境预览 and 现场终端 are per topic, but published ports are fixed at
    container creation — so the box publishes a block up front and each session
    is handed a distinct slot out of it."""
    box = _room_box
    project_id, room_id, task_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rooms = {room_id: room_id, task_id: room_id}
    monkeypatch.setattr(
        TmuxHooksProvider, "_room_id", lambda self, p, t: _aio(rooms[t])
    )
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())

    await _bring_up(provider, box, project_id, room_id, room_id)
    await _bring_up(provider, box, project_id, task_id, room_id)

    slots = {
        env["CHEESE_PORT_SLOT"]: env["CHEESE_APP_PORT"] for env in box.sessions.values()
    }
    assert len(slots) == 2, "two topics were handed the same preview port"
    argv = box.created[0]
    published = {argv[i + 1] for i, t in enumerate(argv) if t == "-p"}
    for slot in range(tp.ws.port_slots()):
        assert f"127.0.0.1:0:{tp.ws.app_port_for_slot(slot)}" in published
        assert f"127.0.0.1:0:{tp.ws.ttyd_port_for_slot(slot)}" in published


@pytest.mark.anyio
async def test_a_deaf_session_is_restarted_without_touching_the_box(
    _room_box, monkeypatch
):
    """验收 #4's remaining half: a session whose token no longer verifies can
    never report anything again, so it is rebuilt — but ONLY it. Rebuilding the
    box would take the room's other topics down with it, which is exactly what
    the old container-level check did."""
    box = _room_box
    project_id, room_id, task_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rooms = {room_id: room_id, task_id: room_id}
    monkeypatch.setattr(
        TmuxHooksProvider, "_room_id", lambda self, p, t: _aio(rooms[t])
    )
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())
    await _bring_up(provider, box, project_id, room_id, room_id)
    await _bring_up(provider, box, project_id, task_id, room_id, token="stale")
    container = tp.ws.tmux_container_name(room_id)
    room_session = tp.ws.tmux_session_name(room_id)
    box.sessions[(container, room_session)]["marker"] = "original"

    # Only the task's token is dead.
    async def dead_only_for_the_task(screen):
        return screen.session == tp.ws.tmux_session_name(task_id)

    monkeypatch.setattr(provider, "_hook_token_dead", dead_only_for_the_task)
    boxes_before = len(box.created)
    await _bring_up(provider, box, project_id, task_id, room_id, token="fresh")

    assert len(box.created) == boxes_before, "the whole box was rebuilt"
    assert box.sessions[(container, room_session)]["marker"] == "original", (
        "the sibling's session was destroyed"
    )
    assert (
        box.sessions[(container, tp.ws.tmux_session_name(task_id))]["CHEESE_TOKEN"]
        == "fresh"
    )
    # Losing the conversation must be said out loud, and attributed to the topic
    # that actually lost it — not to the room.
    assert _announced == [(task_id, "token")]


@pytest.mark.anyio
async def test_rebuilding_a_box_is_announced_to_every_topic_in_it(
    _room_box, monkeypatch
):
    """A box serves a whole room, so destroying it ends EVERY topic's
    conversation in it. Telling only the topic whose turn noticed leaves the
    siblings' agents restarted with no memory and nothing in their rooms saying
    why — the same silence the token-rebuild notice was added to fix, just
    moved from one topic to the rest of the room."""
    box = _room_box
    project_id, room_id, task_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rooms = {room_id: room_id, task_id: room_id}
    monkeypatch.setattr(
        TmuxHooksProvider, "_room_id", lambda self, p, t: _aio(rooms[t])
    )
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())
    await _bring_up(provider, box, project_id, room_id, room_id)
    await _bring_up(provider, box, project_id, task_id, room_id)
    _announced.clear()

    # The image moved under the running box — a whole-box rebuild.
    provider._image = "img:newer"
    await _bring_up(provider, box, project_id, room_id, room_id)

    told = {topic for topic, _ in _announced}
    assert told == {room_id, task_id}, "a sibling lost its session in silence"
    assert {cause for _, cause in _announced} == {"image"}


# --- interrupt: take the work away without saying anything -------------------


@pytest.mark.anyio
async def test_interrupt_presses_escape_on_the_live_screen(monkeypatch):
    """Escape is what stops a claude mid-generation — the key a person watching
    would press, down the channel that already carries this backend's typing.

    Weaker than killing the session, deliberately: what the platform wants when
    it decides a turn should not continue is the work stopped, not the
    conversation destroyed."""
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())
    screen = _FakeScreenControl()
    sent: list[tuple] = []

    async def fake_control(_screen):
        return screen

    original_send = screen.send

    async def recording_send(*args: str):
        sent.append(args)
        return await original_send(*args)

    screen.send = recording_send  # type: ignore[method-assign]
    monkeypatch.setattr(provider, "_control", fake_control)
    session = SessionRef(uuid.uuid4(), uuid.uuid4())
    provider._live[session.topic_id] = _BOX

    assert await provider.interrupt(session) is True
    assert any("Escape" in args for args in sent)


@pytest.mark.anyio
async def test_interrupt_without_a_live_screen_says_so(monkeypatch):
    """No screen, nothing to stop. False rather than a raise: the caller is
    deciding what to do about work it believes is stuck, and an exception there
    would take out whatever else it was in the middle of."""
    provider = TmuxHooksProvider(image="img:test", router=HookRouter())
    assert await provider.interrupt(SessionRef(uuid.uuid4(), uuid.uuid4())) is False
