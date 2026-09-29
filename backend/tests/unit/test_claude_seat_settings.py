"""One teammate's Claude hooks must keep pointing at that teammate's tools."""

import json
import sys

import pytest

from app.domain.agent.harness.claude_code.remote_execution import client


@pytest.mark.skipif(sys.platform != "linux", reason="the session host is Linux")
def test_starting_another_seat_does_not_replace_the_first_seats_hooks(tmp_path):
    home = tmp_path / "room"
    config = home / ".claude"
    config.mkdir(parents=True)
    base = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}}
    (config / "settings.json").write_text(json.dumps(base))
    workspace = tmp_path / "work"
    (workspace / ".git").mkdir(parents=True)
    claude = tmp_path / "claude"
    claude.write_text(f'#!/bin/sh\necho "{client.PINNED_VERSION} (Claude Code)"\n')
    claude.chmod(0o755)

    def start(seat):
        directory = home / ".cheese/seats" / seat / "remote-session"
        launch = client.prepare(
            directory,
            {"kind": "unavailable", "workspace": str(workspace), "mcp_servers": []},
            claude=str(claude),
            base_settings=base,
            home_override=home,
            config_override=config,
            workspace_override=workspace,
        )
        return directory, launch

    first, first_launch = start("first")
    second, second_launch = start("second")

    assert json.loads((config / "settings.json").read_text()) == base
    for directory, launch, other in (
        (first, first_launch, second),
        (second, second_launch, first),
    ):
        command = launch["command"]
        settings = directory / "settings.json"
        assert command[command.index("--setting-sources") + 1] == ""
        assert command[command.index("--settings") + 1] == str(settings)
        hooks = json.loads(settings.read_text())["hooks"]["PreToolUse"]
        guard = next(
            hook
            for group in hooks
            for hook in group["hooks"]
            if "guard" in hook["command"]
        )
        assert str(directory / "execution.json") in guard["command"]
        assert str(other / "execution.json") not in guard["command"]
