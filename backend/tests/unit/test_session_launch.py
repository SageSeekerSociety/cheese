"""一块屏幕上什么时候可以接着用原来那个 claude，什么时候必须重开。

The policy lives above the transport seam because every clause of it is about
Claude Code — it reports through hooks, it reads its wiring once at exec, it
draws an input box before it will take typing — and none is about tmux or
about a link to someone's laptop. A transport supplies the six verbs; this is
the order they go in.
"""

import pytest

from app.domain.agent.harness.claude_code import (
    LaunchSpec,
    ensure_claude,
    session_launch,
)

pytestmark = pytest.mark.anyio

LAUNCH = LaunchSpec(command="claude", env={}, files=())


class _Host:
    """A screen that records what was done to it, in order."""

    def __init__(self, *, exists: bool, deaf: bool = False, comes_up: bool = True):
        self._exists = exists
        self._deaf = deaf
        self._comes_up = comes_up
        self.did: list[str] = []

    async def session_exists(self, screen):
        return self._exists

    async def session_deaf(self, screen):
        return self._deaf

    async def retire_session(self, screen):
        self.did.append("retire")

    async def start_session(self, screen, launch):
        self.did.append(f"start:{launch.command}")

    async def reclaim_session(self, screen):
        self.did.append("reclaim")

    async def capture_session(self, screen):
        self.did.append("look")
        return "❯ " if self._comes_up else "still booting"


async def test_a_live_session_is_reused_because_it_is_the_conversation():
    """Restarting throws away everything the topic said to it. A session that
    can still report is worth keeping even when it is mid-render."""
    host = _Host(exists=True)
    assert await ensure_claude(host, "screen", LAUNCH) is True
    assert host.did == ["reclaim", "look"]


async def test_a_session_that_can_no_longer_report_is_replaced_not_reused():
    """The worst state is a claude that works perfectly and tells nobody: its
    turns run to the ceiling having been observed doing nothing. Losing the
    conversation is the cheaper half of that trade — and the retirement has to
    come first, so the topic hears about it before a fresh session appears."""
    host = _Host(exists=True, deaf=True)
    assert await ensure_claude(host, "screen", LAUNCH) is True
    assert host.did == ["retire", "start:claude", "look"]


async def test_no_session_is_simply_started():
    host = _Host(exists=False)
    assert await ensure_claude(host, "screen", LAUNCH) is True
    assert host.did == ["start:claude", "look"]
    # Nothing was retired: there was nothing there, and retiring a session that
    # does not exist would announce a lost conversation to a topic that has none.
    assert "retire" not in host.did


async def test_a_screen_that_never_shows_its_input_box_is_not_ready(monkeypatch):
    """Typing before the box paints loses the prompt into a boot-time modal, so
    "started" is not "ready" — the caller has to be able to tell them apart."""
    monkeypatch.setattr(session_launch, "READY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(session_launch, "READY_POLL_S", 0.01)
    host = _Host(exists=False, comes_up=False)
    assert await ensure_claude(host, "screen", LAUNCH) is False
    assert host.did[0] == "start:claude"


def test_the_launch_names_the_files_claude_reads_before_it_starts():
    """All three are read exactly once, at exec: the hook wiring that is this
    harness's only sense organ, the first-launch gates (without which the
    onboarding dialog eats the first prompt), and the system prompt."""
    launch = session_launch.build_session_launch(
        config_dir="/sessions/x", workdir="/topics/t", system_prompt="你是芝士。"
    )
    planted = {f.name: f.content for f in launch.files}
    assert set(planted) == {"settings.json", ".claude.json", "cheese-system-prompt.md"}
    assert planted["cheese-system-prompt.md"] == "你是芝士。"
    # The config dir isolates one claude from another on a machine; the harness
    # tag is how a runtime tells its own sessions from another harness's after a
    # restart, on a machine that hosts both.
    assert launch.env == {
        "CLAUDE_CONFIG_DIR": "/sessions/x",
        "CHEESE_HARNESS": "claude-code",
    }
    assert "--append-system-prompt-file /sessions/x/cheese-system-prompt.md" in (
        launch.command
    )


def test_a_withdrawn_system_prompt_leaves_no_stale_one_behind():
    """The file is written even when empty, and the flag is dropped. A topic
    whose prompt was taken away must not keep serving the old one to its next
    fresh session."""
    launch = session_launch.build_session_launch(
        config_dir="/sessions/x", workdir="/topics/t", system_prompt=""
    )
    planted = {f.name: f.content for f in launch.files}
    assert planted["cheese-system-prompt.md"] == ""
    assert "--append-system-prompt-file" not in launch.command
