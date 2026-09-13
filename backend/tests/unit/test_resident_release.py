import json
import subprocess
import sys

import pytest

from app.domain.agent import remote_control
from app.domain.agent.device_hub import HubScreen
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.agent.harness.claude_code.remote_execution import release


def test_staged_release_preserves_context_and_waits_for_reload(tmp_path):
    config = tmp_path / ".claude"
    directory = config / "remote-session"
    helpers = config / "remote-execution"
    directory.mkdir(parents=True)
    helpers.mkdir()
    (directory / "execution.json").write_text('{"token":"existing-scoped-token"}')
    settings = {
        "customSetting": True,
        "permissions": {"deny": ["Bash(rm *)"]},
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "^(?!mcp__native__invoke$).*(?:.*)",
                    "hooks": [{"type": "command", "command": "policy-deny"}],
                }
            ]
        },
    }
    (config / "settings.json").write_text(json.dumps(settings))
    (helpers / "client.py").write_text("old client")
    transcript = config / "projects/work/session.jsonl"
    transcript.parent.mkdir(parents=True)
    old_record = {"type": "user", "message": {"content": "retained history"}}
    history = (
        json.dumps(old_record)
        + "\n"
        + json.dumps(
            {
                "type": "assistant",
                "message": {"stop_reason": "end_turn"},
            }
        )
        + "\n"
    )
    transcript.write_text(history)
    sources = {
        "client.py": "new client",
        "executor_transport.py": "companion",
        "proxy.js": "const target = __EXECUTION_CONFIG__;",
    }
    staged = release.stage(str(tmp_path), sources)
    assert staged["changed"]
    assert not release.reloaded(staged["offsets"])
    assert not (helpers / "release-ready").exists()
    current = json.loads((config / "settings.json").read_text())
    assert current["customSetting"] is True
    assert current["permissions"]["deny"] == ["Bash(rm *)"]
    assert (
        current["hooks"]["PreToolUse"][0]["hooks"]
        == settings["hooks"]["PreToolUse"][0]["hooks"]
    )
    assert "mcp__native__chat_send" in current["permissions"]["allow"]
    assert (
        helpers / "release-backups" / staged["version"] / "client.py"
    ).read_text() == "old client"
    assert (
        directory / "execution.json"
    ).read_text() == '{"token":"existing-scoped-token"}'
    assert transcript.read_text() == history
    with transcript.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "local_command",
                    "content": (
                        "<local-command-stdout>Reloaded: 1 plugin"
                        "</local-command-stdout>"
                    ),
                }
            )
            + "\n"
        )
    assert release.reloaded(staged["offsets"])
    release.acknowledge(str(tmp_path), staged["version"])
    old_stat = (helpers / "client.py").stat()
    assert not release.stage(str(tmp_path), sources)["changed"]
    assert (helpers / "client.py").stat().st_mtime_ns == old_stat.st_mtime_ns


def test_emitted_release_runs_without_backend_imports(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-"],
        input=release.script("acknowledge", str(tmp_path), "released"),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) is None
    assert (
        tmp_path / ".claude/remote-execution/release-ready"
    ).read_text() == "released"


def test_active_turn_blocks_changes_until_completion(tmp_path):
    config = tmp_path / ".claude"
    (config / "remote-session").mkdir(parents=True)
    (config / "remote-execution").mkdir()
    (config / "remote-session/execution.json").write_text("{}")
    (config / "settings.json").write_text("{}")
    client = config / "remote-execution/client.py"
    client.write_text("previous")
    transcript = config / "projects/work/session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "work"}}) + "\n"
    )
    sources = {"client.py": "released", "proxy.js": "__EXECUTION_CONFIG__"}
    with pytest.raises(RuntimeError, match="must finish"):
        release.stage(str(tmp_path), sources)
    assert client.read_text() == "previous"
    with transcript.open("a") as stream:
        stream.write(
            json.dumps({"type": "assistant", "message": {"stop_reason": "end_turn"}})
            + "\n"
        )
    assert release.stage(str(tmp_path), sources)["changed"]


@pytest.mark.anyio
async def test_failed_reconnect_keeps_release_unacknowledged(tmp_path, monkeypatch):
    config = tmp_path / ".claude"
    (config / "remote-session").mkdir(parents=True)
    (config / "remote-session/execution.json").write_text("{}")
    (config / "settings.json").write_text("{}")
    transcript = config / "projects/work/session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("")
    monkeypatch.setattr(
        release,
        "sources",
        lambda: {
            "client.py": "released",
            "proxy.js": "__EXECUTION_CONFIG__",
        },
    )

    class Control:
        async def current(self, topic):
            return {"id": "session", "status": "active"}

        async def enqueue(self, *args):
            pass

        async def result(self, *args):
            return {"response": {"subtype": "error", "error": "disconnected"}}

    class Hub:
        async def exec(self, device, command, *, stdin, timeout):
            result = subprocess.run(
                [sys.executable, "-I", "-"],
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "exit": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

    async def reload(screen, prompt):
        with transcript.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "system",
                        "subtype": "local_command",
                        "content": "<local-command-stdout>Reloaded: 1 plugin",
                    }
                )
                + "\n"
            )

    monkeypatch.setattr(remote_control, "store", Control)
    channel = object.__new__(DeviceChannel)
    channel._hub = Hub()
    monkeypatch.setattr(channel, "send_prompt", reload)
    screen = HubScreen("screen", "device", [], "token", 1, "agent")
    with pytest.raises(ScreenSetupError, match="mcp_reconnect"):
        await channel._refresh_resident(screen, str(tmp_path), {})
    assert not (config / "remote-execution/release-ready").exists()
