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
    assert "mcp__native__cheese_*" in current["permissions"]["allow"]
    assert "cheese_.*" in current["hooks"]["PreToolUse"][0]["matcher"]
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


def test_release_bundles_locked_fuse_adapter_with_license():
    sources = release.sources()
    assert "class ForwardedProject" in sources["forwarded_fs.py"]
    assert "Permission to use, copy, modify, and distribute" in sources["fuse.py"]


def test_skill_reload_receipt_is_distinct_from_plugin_reload(tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("")
    offsets = {str(transcript): 0}
    with transcript.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "local_command",
                    "content": (
                        "<local-command-stdout>Reloaded skills: 2 skills available "
                        "(1 added)</local-command-stdout>"
                    ),
                }
            )
            + "\n"
        )
    assert release.skills_reloaded(offsets)
    assert not release.reloaded(offsets)


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
@pytest.mark.parametrize("connected", [False, True])
async def test_release_acknowledgement_requires_connection(
    tmp_path, monkeypatch, connected
):
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

    commands = []

    class Control:
        async def current(self, topic):
            return {"id": "session", "status": "active"}

        async def enqueue(self, sid, payload, actor):
            commands.append(payload["request"]["subtype"])

        async def result(self, *args):
            if not connected:
                return {"response": {"subtype": "error", "error": "disconnected"}}
            value = {}
            if commands[-1] == "mcp_status":
                value = {
                    "mcpServers": [
                        {
                            "name": "native",
                            "status": (
                                "pending"
                                if commands.count("mcp_status") == 1
                                else "connected"
                            ),
                        }
                    ]
                }
            return {"response": {"subtype": "success", "response": value}}

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
    if connected:
        await channel._refresh_resident(screen, str(tmp_path), {})
        assert (
            config / "remote-execution/release-ready"
        ).read_text() == release.digest(release.sources())
        assert commands == ["mcp_reconnect", "mcp_status", "mcp_status"]
    else:
        with pytest.raises(ScreenSetupError, match="mcp_reconnect"):
            await channel._refresh_resident(screen, str(tmp_path), {})
        assert not (config / "remote-execution/release-ready").exists()


@pytest.mark.anyio
@pytest.mark.parametrize("changed", [False, True])
async def test_forwarded_context_reloads_skills_only_for_a_new_generation(
    tmp_path, monkeypatch, changed
):
    config = tmp_path / ".claude"
    transcript = config / "projects/work/session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("")
    prompts = []

    class Hub:
        async def exec(self, device, command, *, stdin=None, timeout):
            if stdin == "apply_forwarded_context":
                return {
                    "exit": 0,
                    "stdout": json.dumps(
                        {"changed": changed, **({"offsets": {}} if changed else {})}
                    ),
                }
            assert stdin == "wait_skills_reloaded"
            return {"exit": 0, "stdout": "true"}

    async def send_prompt(screen, prompt):
        prompts.append(prompt)
        with transcript.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "system",
                        "subtype": "local_command",
                        "content": (
                            "<local-command-stdout>Reloaded skills: 2 skills "
                            "available</local-command-stdout>"
                        ),
                    }
                )
                + "\n"
            )

    channel = object.__new__(DeviceChannel)
    channel._hub = Hub()
    monkeypatch.setattr(release, "script", lambda function, *args: function)
    monkeypatch.setattr(channel, "send_prompt", send_prompt)
    screen = HubScreen("screen", "device", [], "token", 1, "agent")
    await channel._refresh_forwarded_context(
        screen,
        str(tmp_path),
        {"context_tree": {"generation": "new", "entries": {}}},
    )
    assert prompts == (["/reload-skills"] if changed else [])


def test_forwarded_context_replaces_mirror_files_with_links(tmp_path, monkeypatch):
    config = tmp_path / ".claude"
    directory = config / "remote-session"
    helpers = config / "remote-execution"
    workspace = tmp_path / "old-workspace"
    hidden = directory / "forwarded-project"
    skill = workspace / ".claude/skills/check/SKILL.md"
    remote_skill = hidden / ".claude/skills/check/SKILL.md"
    skill.parent.mkdir(parents=True)
    remote_skill.parent.mkdir(parents=True)
    helpers.mkdir(parents=True)
    (workspace / "CLAUDE.md").write_text("mirrored body")
    skill.write_text("mirrored skill")
    (hidden / "CLAUDE.md").write_text("forwarded body")
    remote_skill.write_text("forwarded skill")
    target_path = directory / "execution.json"
    target_path.write_text(
        json.dumps(
            {
                "kind": "device",
                "central_workspace": str(workspace),
                "central_config": str(config),
                "helper": ["python3", "client.py"],
                "target_file": str(target_path),
            }
        )
    )
    (directory / "context-manifest.json").write_text(
        json.dumps(["CLAUDE.md", ".claude/skills/check/SKILL.md"])
    )
    tree = {
        "generation": "new",
        "entries": {
            "CLAUDE.md": {
                "kind": "file",
                "mode": 0o444,
                "mtime_ns": 1,
                "size": 14,
                "nlink": 1,
            },
            ".claude": {
                "kind": "directory",
                "mode": 0o555,
                "mtime_ns": 1,
                "size": 0,
                "nlink": 2,
            },
            ".claude/skills": {
                "kind": "directory",
                "mode": 0o555,
                "mtime_ns": 1,
                "size": 0,
                "nlink": 2,
            },
            ".claude/skills/check": {
                "kind": "directory",
                "mode": 0o555,
                "mtime_ns": 1,
                "size": 0,
                "nlink": 2,
            },
            ".claude/skills/check/SKILL.md": {
                "kind": "file",
                "mode": 0o444,
                "mtime_ns": 1,
                "size": 15,
                "nlink": 1,
            },
        },
    }
    monkeypatch.setattr(release.os.path, "ismount", lambda path: path == hidden)
    result = release.apply_forwarded_context(
        str(tmp_path), {"kind": "device", "context_tree": tree}
    )
    assert result["changed"]
    assert (workspace / "CLAUDE.md").is_symlink()
    assert (workspace / "CLAUDE.md").read_text() == "forwarded body"
    assert (workspace / ".claude/skills").is_symlink()
    assert (
        workspace / ".claude/skills/check/SKILL.md"
    ).read_text() == "forwarded skill"
    assert not (directory / "context-manifest.json").exists()
    backup = helpers / "release-backups/forwarded-context/legacy/context-manifest.json"
    assert json.loads(backup.read_text()) == [
        "CLAUDE.md",
        ".claude/skills/check/SKILL.md",
    ]
    files_backup = backup.parent / "files"
    assert (files_backup / "CLAUDE.md").read_text() == "mirrored body"
    assert (
        files_backup / ".claude/skills/check/SKILL.md"
    ).read_text() == "mirrored skill"
    retry = release.apply_forwarded_context(
        str(tmp_path), {"kind": "device", "context_tree": tree}
    )
    assert retry["changed"]
    (directory / "context-generation").write_text("new")
    unchanged = release.apply_forwarded_context(
        str(tmp_path),
        {"kind": "device", "token": "rotated", "context_tree": tree},
    )
    assert unchanged == {"changed": False}
    assert json.loads(target_path.read_text())["token"] == "rotated"
