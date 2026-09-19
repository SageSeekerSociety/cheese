import errno
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.domain.agent import remote_control
from app.domain.agent.device_hub import HubScreen
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.agent.harness.claude_code.remote_execution import release


def test_staged_release_preserves_context_and_waits_for_reload(tmp_path):
    config = tmp_path / ".claude"
    config.mkdir(exist_ok=True)
    platform_dir = tmp_path / ".cheese"
    directory = platform_dir / "remote-session"
    helpers = platform_dir / "remote-execution"
    directory.mkdir(parents=True)
    helpers.mkdir()
    original_target = json.dumps(
        {
            "kind": "device",
            "token": "existing-scoped-token",
            "helper": [sys.executable, str(helpers / "client.py")],
        }
    )
    (directory / "execution.json").write_text(original_target)
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
    assert (directory / "execution.json").read_text() == original_target
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


def test_a_forwarded_view_whose_server_died_is_released_not_read_as_empty(
    tmp_path, monkeypatch
):
    """A FUSE mount outlives the process serving it. The directory stays occupied
    and every stat on it fails with ENOTCONN, which `os.path.ismount` swallows —
    so the answer it gives for a dead mount is the answer it gives for an empty
    directory. That is how a killed run leaves a mountpoint nothing ever clears:
    the next run's cleanup sees "nothing mounted" and skips it, a fresh mount onto
    it fails, and anything that merely walks the directory blocks there.
    """
    dead = tmp_path / "forwarded-project"
    dead.mkdir()
    occupied = {dead}
    real_lstat = release.os.lstat

    def lstat(path, *args, **kwargs):
        if Path(path) in occupied:
            raise OSError(errno.ENOTCONN, "Transport endpoint is not connected")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(release.os, "lstat", lstat)
    monkeypatch.setattr(release.shutil, "which", lambda name: "/bin/" + name)
    calls = []

    def unmount(argv, **_kwargs):
        calls.append(argv)
        occupied.discard(Path(argv[-1]))

    monkeypatch.setattr(release.subprocess, "run", unmount)

    assert release.os.path.ismount(dead) is False  # what every caller used to see
    assert release.mount_state(dead) == release.MOUNT_DEAD
    assert release.release_mount(dead) is True
    assert calls == [["/bin/fusermount3", "-u", str(dead)]]


def test_staged_release_unmounts_only_the_forwarded_view_before_replacement(
    tmp_path, monkeypatch
):
    config = tmp_path / ".claude"
    config.mkdir(exist_ok=True)
    platform_dir = tmp_path / ".cheese"
    directory = platform_dir / "remote-session"
    helpers = platform_dir / "remote-execution"
    directory.mkdir(parents=True)
    helpers.mkdir()
    (directory / "execution.json").write_text(
        json.dumps({"kind": "device", "central_workspace": str(tmp_path / "room")})
    )
    (config / "settings.json").write_text("{}")
    forwarded = directory / "forwarded-project"
    forwarded.mkdir()
    calls = []
    mounted = {forwarded}
    monkeypatch.setattr(release.os.path, "ismount", lambda path: Path(path) in mounted)
    monkeypatch.setattr(release.shutil, "which", lambda name: "/bin/" + name)

    def unmount(argv, **_kwargs):
        calls.append(argv)
        mounted.discard(Path(argv[-1]))

    monkeypatch.setattr(release.subprocess, "run", unmount)
    release.stage(str(tmp_path), {"client.py": "new", "proxy.js": "new"})
    # One plain unmount, and no lazy follow-up: the lazy flag detaches a mount a
    # reader may still hold, so it is only ever reached when the plain one failed.
    assert calls == [["/bin/fusermount3", "-u", str(forwarded)]]


def test_staged_release_keeps_a_forwarded_view_used_as_the_native_cwd(
    tmp_path, monkeypatch
):
    config = tmp_path / ".claude"
    config.mkdir(exist_ok=True)
    platform_dir = tmp_path / ".cheese"
    directory = platform_dir / "remote-session"
    helpers = platform_dir / "remote-execution"
    forwarded = directory / "forwarded-project"
    directory.mkdir(parents=True)
    helpers.mkdir()
    (directory / "execution.json").write_text(
        json.dumps({"kind": "device", "central_workspace": str(forwarded)})
    )
    (config / "settings.json").write_text("{}")
    monkeypatch.setattr(release.os.path, "ismount", lambda path: True)

    def unexpected(*args, **kwargs):
        raise AssertionError("a native cwd mount cannot be normally unmounted")

    monkeypatch.setattr(release.subprocess, "run", unexpected)
    result = release.stage(str(tmp_path), {"client.py": "new", "proxy.js": "new"})
    assert result["changed"]


def test_staged_release_only_removes_the_managed_context_hook(tmp_path):
    config = tmp_path / ".claude"
    config.mkdir(exist_ok=True)
    platform_dir = tmp_path / ".cheese"
    directory = platform_dir / "remote-session"
    helpers = platform_dir / "remote-execution"
    directory.mkdir(parents=True)
    helpers.mkdir()
    target_path = directory / "execution.json"
    helper = [sys.executable, str(helpers / "client.py")]
    target_path.write_text(json.dumps({"kind": "device", "helper": helper}))
    managed = {
        "type": "command",
        "command": helper[0],
        "args": [str(helpers / "context_service.py"), str(target_path)],
    }
    custom = {
        "type": "command",
        "command": "audit-context_service.py",
        "args": ["custom"],
    }
    settings = {
        "hooks": {
            event: [
                {"matcher": "startup", "hooks": [managed, custom], "once": True},
                {"hooks": [managed]},
            ]
            for event in ("SessionStart", "UserPromptSubmit")
        }
    }
    (config / "settings.json").write_text(json.dumps(settings))
    sources = {
        "client.py": "new client",
        "executor_transport.py": "companion",
        "proxy.js": "const target = __EXECUTION_CONFIG__;",
    }

    release.stage(str(tmp_path), sources)

    current = json.loads((config / "settings.json").read_text())
    for event in ("SessionStart", "UserPromptSubmit"):
        assert current["hooks"][event] == [
            {"matcher": "startup", "hooks": [custom], "once": True}
        ]


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
        tmp_path / ".cheese/remote-execution/release-ready"
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
    config.mkdir(exist_ok=True)
    platform_dir = tmp_path / ".cheese"
    (platform_dir / "remote-session").mkdir(parents=True)
    (platform_dir / "remote-execution").mkdir()
    (platform_dir / "remote-session/execution.json").write_text("{}")
    (config / "settings.json").write_text("{}")
    client = platform_dir / "remote-execution/client.py"
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
    config.mkdir(exist_ok=True)
    platform_dir = tmp_path / ".cheese"
    (platform_dir / "remote-session").mkdir(parents=True)
    (platform_dir / "remote-session/execution.json").write_text("{}")
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
        async def current(self, topic, agent_handle=None):
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
        assert await channel._refresh_resident(screen, str(tmp_path), {}) is True
        assert (
            platform_dir / "remote-execution/release-ready"
        ).read_text() == release.digest(release.sources())
        assert commands == ["mcp_reconnect", "mcp_status", "mcp_status"]
    else:
        with pytest.raises(ScreenSetupError, match="mcp_reconnect"):
            await channel._refresh_resident(screen, str(tmp_path), {})
        assert not (platform_dir / "remote-execution/release-ready").exists()


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
    config.mkdir(exist_ok=True)
    platform_dir = tmp_path / ".cheese"
    directory = platform_dir / "remote-session"
    helpers = platform_dir / "remote-execution"
    workspace = tmp_path / "old-workspace"
    hidden = directory / "forwarded-project"
    skill = workspace / ".claude/skills/check/SKILL.md"
    remote_skill = hidden / ".claude/skills/check/SKILL.md"
    skill.parent.mkdir(parents=True)
    remote_skill.parent.mkdir(parents=True)
    helpers.mkdir(parents=True)
    (config / "CLAUDE.md").write_text("old aggregated instructions")
    (config / "skills").symlink_to(workspace / ".claude/skills")
    (workspace / "CLAUDE.md").write_text("mirrored body")
    skill.write_text("mirrored skill")
    (hidden / "CLAUDE.md").write_text("forwarded body")
    (hidden / "docs").mkdir()
    (hidden / "docs/more.md").write_text("forwarded import")
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
            "docs": {
                "kind": "directory",
                "mode": 0o555,
                "mtime_ns": 1,
                "size": 0,
                "nlink": 2,
            },
            "docs/more.md": {
                "kind": "file",
                "mode": 0o444,
                "mtime_ns": 1,
                "size": 16,
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
    assert (config / "CLAUDE.md").read_text() == f"@{hidden / 'CLAUDE.md'}\n"
    user_backup = (
        helpers
        / "release-backups/forwarded-context/user-source"
        / hashlib.sha256(str(config).encode()).hexdigest()[:16]
    )
    assert (user_backup / "CLAUDE.md").read_text() == "old aggregated instructions"
    assert (user_backup / "skills.symlink").read_text() == str(
        workspace / ".claude/skills"
    )
    assert not (config / "docs").exists()
    assert (config / "skills/check/SKILL.md").read_text() == "forwarded skill"
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


def test_forwarded_context_rejects_unsupported_paths_before_mutation(tmp_path):
    config = tmp_path / ".claude"
    config.mkdir(exist_ok=True)
    platform_dir = tmp_path / ".cheese"
    directory = platform_dir / "remote-session"
    workspace = tmp_path / "workspace"
    mirrored = workspace / "CLAUDE.md"
    directory.mkdir(parents=True)
    workspace.mkdir()
    mirrored.write_text("retained mirror")
    target_path = directory / "execution.json"
    original_target = json.dumps(
        {
            "kind": "device",
            "token": "original",
            "central_workspace": str(workspace),
            "central_config": str(config),
            "helper": ["python3", "client.py"],
            "target_file": str(target_path),
        }
    )
    target_path.write_text(original_target)
    (directory / "context-generation").write_text("old")
    (directory / "context-tree.json").write_text('{"generation":"old"}')
    (directory / "context-manifest.json").write_text(json.dumps(["CLAUDE.md"]))
    tree = {
        "generation": "new",
        "entries": {},
        "unsupported_imports": ["~/private.md"],
        "unsupported_paths": ["/outside/rule.md"],
    }

    with pytest.raises(
        RuntimeError,
        match="Project context leaves the forwarded project boundary",
    ):
        release.apply_forwarded_context(
            str(tmp_path),
            {"kind": "device", "token": "rotated", "context_tree": tree},
        )

    assert target_path.read_text() == original_target
    assert (directory / "context-generation").read_text() == "old"
    assert (directory / "context-tree.json").read_text() == '{"generation":"old"}'
    assert (directory / "context-manifest.json").exists()
    assert mirrored.read_text() == "retained mirror"


def test_unchanged_forwarded_generation_remounts_after_helper_release(
    tmp_path, monkeypatch
):
    config = tmp_path / ".claude"
    config.mkdir(exist_ok=True)
    platform_dir = tmp_path / ".cheese"
    directory = platform_dir / "remote-session"
    helpers = platform_dir / "remote-execution"
    workspace = tmp_path / "workspace"
    directory.mkdir(parents=True)
    helpers.mkdir()
    workspace.mkdir()
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
    (directory / "context-generation").write_text("same")
    (directory / "context-tree.json").write_text(
        json.dumps({"generation": "same", "entries": {}})
    )
    mounted = False

    def ismount(path):
        return mounted and Path(path) == directory / "forwarded-project"

    class Process:
        def __init__(self, argv, **kwargs):
            nonlocal mounted
            assert argv[1:] == [
                str(helpers / "forwarded_fs.py"),
                str(target_path),
                str(directory / "forwarded-project"),
            ]
            mounted = True

    monkeypatch.setattr(release.os.path, "ismount", ismount)
    monkeypatch.setattr(release.subprocess, "Popen", Process)
    result = release.apply_forwarded_context(
        str(tmp_path),
        {"kind": "device", "context_tree": {"generation": "same", "entries": {}}},
    )
    assert result == {"changed": True, "offsets": {}}
    assert mounted
    assert json.loads(target_path.read_text())["token_file"] == str(
        directory / "execution.token"
    )
