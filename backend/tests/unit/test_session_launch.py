"""claude 开机前要在盘上看到什么，以及 hooks 接线长什么样。

Every clause here is about Claude Code — which files it reads exactly once at
exec, which hooks are its sense organs, which tool no user here can answer —
and none is about a transport. A channel takes the resulting ``LaunchSpec`` and
does its own delivery with it.
"""

import json
import os
import subprocess

import pytest

from app.domain.agent.harness.claude_code import hooks_settings, session_launch
from app.domain.agent.skills import native_skill_files

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
    # The skills are named by the module that ships them rather than listed
    # here, because a skill is a directory: `documents` carries the scripts that
    # do the editing and the reference files they are explained in, and a test
    # that named only SKILL.md would pass while the script never left the
    # building.
    assert set(planted) == {
        "settings.json",
        ".claude.json",
        "cheese-system-prompt.md",
        "webfetch_transport.cjs",
        "skills/cheese-docs/SKILL.md",
        *native_skill_files(),
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


def test_hooks_settings_sync_teammate_definitions_on_start_and_prompt():
    """发现层：会话启动和每个提示都刷新一次队友分身定义文件 —— 主 agent 在
    Agent 工具的可用清单里读到可指定谁（模型范围 = 项目 AI 队友，闸在准入）。"""
    hooks = hooks_settings()["hooks"]
    for event in ("SessionStart", "UserPromptSubmit"):
        commands = [
            hook["command"]
            for group in hooks[event]
            for hook in group["hooks"]
            if hook["type"] == "command"
        ]
        assert "cheese sync-agents || true" in commands
    # 转发器仍然排在最前 —— 感知一个不能少。
    assert hooks["SessionStart"][0]["hooks"][0]["command"] == "cheese-hook"


def test_sync_agents_hook_does_not_block_prompts_on_an_older_cli(tmp_path):
    command = hooks_settings()["hooks"]["UserPromptSubmit"][1]["hooks"][0]["command"]
    old_cli = tmp_path / "cheese"
    old_cli.write_text("#!/bin/sh\nexit 2\n")
    old_cli.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    assert subprocess.run(command, shell=True, env=env, check=False).returncode == 0
