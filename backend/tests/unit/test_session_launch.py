"""claude 开机前要在盘上看到什么，以及它的 settings 长什么样。

Every clause here is about Claude Code — which files it reads exactly once at
exec, which hooks still act on a session the runner observes from its stdout,
which tool no user here can answer — and none is about a transport.
"""

import hashlib
import os
import subprocess
from pathlib import Path

from app.domain.agent.harness.claude_code import device_launch
from app.domain.agent.harness.claude_code.session_launch import (
    ClaudeLaunch,
    session_settings,
)
from app.domain.agent.harness.launch import MachinePlace
from app.domain.agent.place import seat_dir
from app.domain.agent.skills import native_skill_files
from app.domain.project_skill.service import session_skill_bundle

STATE = "$HOME/.cheese/harness/p/r/claude-code/deadbeef"


def _seat(home) -> Path:
    """How these tests reach the seat the launch writes into.

    Nothing here names a teammate — `launch_holes` with no `seat` — so it is
    the seat an empty handle names.
    """
    return Path(seat_dir(str(home)))


def _seed_bundle_cache(real_home) -> None:
    """Put this project's skill bundle where a machine that has already fetched
    it keeps it, so configure installs the skills without a platform to ask.

    Configure no longer carries the skills inline: it looks them up by digest in
    the machine cache and fetches only on a miss (device_launch). Seeding the
    cache is how a test stands in for the fetch — the bytes are the very ones
    the digest names, so the sha256 check the launcher runs passes."""
    bundle = session_skill_bundle(None)
    digest = hashlib.sha256(bundle).hexdigest()
    cache = Path(real_home) / ".cheese/skill-bundles" / f"{digest}.json.gz"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(bundle)


def _configure(home, system_prompt: str) -> None:
    """The configure hole, run the way the launcher runs it: HOME is the
    session's home by then.

    ``REAL_HOME`` is the machine owner's home, which the skeleton sets — the
    skill cache lives there, keyed by content digest (device_launch). The tests
    use one directory for both; what matters is that configure finds a machine
    cache to unpack from."""
    _seed_bundle_cache(home)
    holes = device_launch.launch_holes(state=STATE, system_prompt=system_prompt)
    subprocess.run(
        ["sh"],
        # On stdin, the way the device gets it (`_ship_launcher` writes a file):
        # the skills alone are past what one argv string may hold.
        input="set -e\n" + holes.configure,
        text=True,
        env={
            "HOME": str(home),
            "REAL_HOME": str(home),
            "PATH": "/usr/bin:/bin",
        },
        check=True,
        capture_output=True,
    )


def test_the_launch_writes_the_files_claude_reads_before_it_starts(tmp_path):
    """Each is read exactly once, at exec: the settings, the WebFetch transport
    it preloads, and the platform's own skills — all in the seat's config dir
    — plus the system prompt, in the seat that owns this session,
    because one teammate's prompt is not another's."""
    _configure(tmp_path, "你是芝士。")

    config = _seat(tmp_path) / ".claude"
    planted = {
        str(path.relative_to(config)) for path in config.rglob("*") if path.is_file()
    }
    # The skills are named by the module that ships them rather than listed
    # here, because a skill is a directory: `documents` carries the scripts that
    # do the editing and the reference files they are explained in, and a test
    # that named only SKILL.md would pass while the script never left the
    # building.
    assert planted == {
        "webfetch_transport.cjs",
        *native_skill_files(),
        # What this seat was shipped, which the next launch removes the
        # retired ones against. Claude never reads it.
        "skills/.cheese-platform-skills",
    }
    assert (_seat(tmp_path) / "remote-session/base-settings.json").is_file()
    assert (config / "projects").resolve() == (tmp_path / ".claude/projects").resolve()
    assert (_seat(tmp_path) / "cheese-system-prompt.md").read_text() == "你是芝士。\n"
    for name, content in native_skill_files().items():
        assert (config / name).read_text() == content, name


def test_a_withdrawn_system_prompt_leaves_no_stale_one_behind(tmp_path):
    """The file is written even when empty, and the flag is guarded on it being
    non-empty. A topic whose prompt was taken away must not keep serving the old
    one to its next fresh session."""
    _configure(tmp_path, "old prompt")
    _configure(tmp_path, "")

    assert (_seat(tmp_path) / "cheese-system-prompt.md").read_text() == ""
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
