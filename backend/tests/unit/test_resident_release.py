import errno
import json
import subprocess
import sys
import time
import uuid
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


def test_a_skill_reload_receipt_is_not_a_plugin_reload(tmp_path):
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
    assert release.stage(str(tmp_path), sources) == {
        "changed": False,
        "busy": True,
        "version": release.digest(sources),
    }
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
    topic_id = uuid.uuid4()

    class Control:
        async def current(self, topic, agent_handle=None):
            assert topic == str(topic_id)
            # A screen is launched as one named agent and records it, so this is
            # the control session to look for — no room is asked, and nothing
            # re-resolves it behind the screen's back.
            assert agent_handle == "agent"
            return {"id": "session", "status": "active", "last_seen": time.time()}

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
    screen = HubScreen(
        "screen",
        "device",
        [],
        "token",
        1,
        "agent",
        topic_id=topic_id,
    )
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
