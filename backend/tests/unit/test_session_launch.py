"""claude 开机前要在盘上看到什么，以及 hooks 接线长什么样。

Every clause here is about Claude Code — which files it reads exactly once at
exec, which hooks are its sense organs, which tool no user here can answer —
and none is about a transport. A channel takes the resulting ``LaunchSpec`` and
does its own delivery with it.
"""

import json

import pytest

from app.domain.agent.harness.claude_code import hooks_settings, session_launch

pytestmark = pytest.mark.anyio


def test_the_launch_names_the_files_claude_reads_before_it_starts():
    """Each is read exactly once, at exec: the hook wiring that is this
    harness's only sense organ, the first-launch gates (without which the
    onboarding dialog eats the first prompt), the system prompt, and the
    platform's own skills."""
    launch = session_launch.build_session_launch(
        config_dir="/sessions/x", workdir="/topics/t", system_prompt="你是芝士。"
    )
    planted = {f.name: f.content for f in launch.files}
    assert set(planted) == {
        "settings.json",
        ".claude.json",
        "cheese-system-prompt.md",
        "webfetch_transport.cjs",
        "skills/cheese-docs/SKILL.md",
        "skills/documents/SKILL.md",
    }
    assert planted["cheese-system-prompt.md"] == "你是芝士。"
    assert json.loads(planted["settings.json"])["enableArtifact"] is False
    # The config dir isolates one claude from another on a machine; the harness
    # tag is how a runtime tells its own sessions from another harness's after a
    # restart, on a machine that hosts both.
    assert launch.env == {
        "CLAUDE_CONFIG_DIR": "/sessions/x",
        "CHEESE_HARNESS": "claude-code",
        "BUN_OPTIONS": "--preload=/sessions/x/webfetch_transport.cjs",
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


def test_hooks_settings_wire_every_perception_hook_to_the_forwarder():
    s = hooks_settings()
    assert s["skipDangerousModePermissionPrompt"] is True
    names = (
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "MessageDisplay",
        # A subagent is a second worker inside the same session. Without these
        # two the room gets its tool calls mixed into the session's own stream
        # with nothing saying whose they are, and never gets what it concluded.
        "SubagentStart",
        "SubagentStop",
        "Stop",
    )
    for event in names:
        entry = s["hooks"][event][0]
        assert entry["hooks"][0] == {"type": "command", "command": "cheese-hook"}


def test_a_subagent_finishing_does_not_hand_the_tree_back():
    """`extra_stop`（cheese-sync）是「这一轮完了，把机器上的活推回去」。分身停下
    不是轮次停下——会话还在干，往往紧接着再派一个。挂上去就会一轮推好几次，
    而且每次推的都是一棵还没写完的树。"""
    s = hooks_settings(["cheese-sync"])
    assert s["hooks"]["SubagentStop"][0]["hooks"] == [
        {"type": "command", "command": "cheese-hook"}
    ]


def test_hooks_settings_deny_unreachable_prompt_ui_and_allow_webfetch():
    """The terminal picker is unreachable; repaired WebFetch remains available."""
    denied = hooks_settings()["permissions"]["deny"]
    assert "AskUserQuestion" in denied
    assert "WebFetch" not in denied
