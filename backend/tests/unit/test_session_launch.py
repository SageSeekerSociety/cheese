"""claude 开机前要在盘上看到什么，以及它的 settings 长什么样。

Every clause here is about Claude Code — which files it reads exactly once at
exec, which hooks still act on a session the runner observes from its stdout,
which tool no user here can answer — and none is about a transport.
"""

import os
import subprocess

from app.domain.agent.harness.claude_code import device_launch
from app.domain.agent.harness.claude_code.session_launch import (
    ClaudeLaunch,
    session_settings,
)
from app.domain.agent.harness.launch import MachinePlace
from app.domain.agent.skills import native_skill_files

STATE = "$HOME/.cheese/harness/p/r/claude-code/deadbeef"


def _configure(home, system_prompt: str) -> None:
    """The configure hole, run the way the launcher runs it: HOME is the
    session's home by then."""
    holes = device_launch.launch_holes(state=STATE, system_prompt=system_prompt)
    subprocess.run(
        ["sh", "-c", "set -e\n" + holes.configure],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        check=True,
        capture_output=True,
    )


def test_the_launch_writes_the_files_claude_reads_before_it_starts(tmp_path):
    """Each is read exactly once, at exec: the settings, the system prompt, the
    WebFetch transport it preloads, and the platform's own skills."""
    _configure(tmp_path, "你是芝士。")

    config = tmp_path / ".claude"
    planted = {
        str(path.relative_to(config)) for path in config.rglob("*") if path.is_file()
    }
    # The skills are named by the module that ships them rather than listed
    # here, because a skill is a directory: `documents` carries the scripts that
    # do the editing and the reference files they are explained in, and a test
    # that named only SKILL.md would pass while the script never left the
    # building.
    assert planted == {
        "settings.json",
        "cheese-system-prompt.md",
        "webfetch_transport.cjs",
        *native_skill_files(),
    }
    assert (config / "cheese-system-prompt.md").read_text() == "你是芝士。\n"
    for name, content in native_skill_files().items():
        assert (config / name).read_text() == content + "\n", name


def test_a_withdrawn_system_prompt_leaves_no_stale_one_behind(tmp_path):
    """The file is written even when empty, and the flag is guarded on it being
    non-empty. A topic whose prompt was taken away must not keep serving the old
    one to its next fresh session."""
    _configure(tmp_path, "old prompt")
    _configure(tmp_path, "")

    assert (tmp_path / ".claude/cheese-system-prompt.md").read_text() == ""
    prepare = device_launch.launch_holes(state=STATE).prepare
    assert '[ -s "$CHEESE_SP" ] && CLAUDE="$CLAUDE --append-system-prompt-file' in (
        prepare
    )


def test_the_settings_deny_unreachable_prompt_ui_and_allow_webfetch():
    """The build's question tool is unreachable from a room; repaired WebFetch
    remains available."""
    denied = session_settings()["permissions"]["deny"]
    assert "AskUserQuestion" in denied
    assert "WebFetch" not in denied


def test_the_settings_sync_teammate_definitions_on_start_and_prompt():
    """发现层：会话启动和每个提示都刷新一次队友分身定义文件 —— 主 agent 在
    Agent 工具的可用清单里读到可指定谁（模型范围 = 项目 AI 队友，闸在准入）。"""
    hooks = session_settings()["hooks"]
    for event in ("SessionStart", "UserPromptSubmit"):
        commands = [
            hook["command"]
            for group in hooks[event]
            for hook in group["hooks"]
            if hook["type"] == "command"
        ]
        assert commands == ["cheese sync-agents || true"]


def test_sync_agents_hook_does_not_block_prompts_on_an_older_cli(tmp_path):
    command = session_settings()["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    old_cli = tmp_path / "cheese"
    old_cli.write_text("#!/bin/sh\nexit 2\n")
    old_cli.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    assert subprocess.run(command, shell=True, env=env, check=False).returncode == 0


def test_the_plan_offers_its_resume_and_keeps_its_state_where_the_machine_said():
    place = MachinePlace(
        home="$HOME/.cheese/home/P/R",
        workdir="$HOME/.cheese/work/P/R",
        store="",
        state="$HOME/.cheese/harness/P/R/claude-code/abc",
        api_base="",
        project_id="P",
        topic_id="T",
        agent_handle="ops",
    )
    launch = ClaudeLaunch(system_prompt="x", resume_session_id="sess-1").on(place)

    assert launch.env["CHEESE_RESUME_SESSION"] == "sess-1"
    assert "CLAUDE_STATE='$HOME/.cheese/harness/P/R/claude-code/abc'" in (
        launch.prepare
    )
    fresh = ClaudeLaunch(system_prompt="x").on(place)
    assert "CHEESE_RESUME_SESSION" not in fresh.env
