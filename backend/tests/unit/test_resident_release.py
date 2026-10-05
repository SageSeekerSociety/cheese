import errno
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.domain.agent import place
from app.domain.agent.device_hub import HubScreen
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.agent.harness.claude_code.remote_execution import release

# Every release carries the platform tool table; `stage` reads it.
CHEESE = release.sources()["cheese.py"]


def test_staged_release_preserves_context_and_is_acknowledged_once(tmp_path):
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
    (directory / "settings.json").write_text(json.dumps(settings))
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
        "cheese.py": CHEESE,
    }
    staged = release.stage(str(tmp_path), sources)
    assert staged == {"changed": True, "version": release.digest(sources)}
    assert not (directory / "release-ready").exists()
    current = json.loads((directory / "settings.json").read_text())
    assert current["customSetting"] is True
    assert current["permissions"]["deny"] == ["Bash(rm *)"]
    assert (
        current["hooks"]["PreToolUse"][0]["hooks"]
        == settings["hooks"]["PreToolUse"][0]["hooks"]
    )
    # Every platform tool is allowed by name, those without a `cheese_` prefix
    # included.
    for name in ("chat_send", "todo_write", "cheese_task", "platform_request"):
        assert "mcp__native__" + name in current["permissions"]["allow"]
    # A policy hook is the build's to run, on every tool, as written.
    assert current["hooks"]["PreToolUse"] == settings["hooks"]["PreToolUse"]
    assert (
        helpers / "release-backups" / staged["version"] / "client.py"
    ).read_text() == "old client"
    assert (directory / "execution.json").read_text() == original_target
    assert transcript.read_text() == history
    release.acknowledge(str(tmp_path), staged["version"])
    old_stat = (helpers / "client.py").stat()
    assert not release.stage(str(tmp_path), sources)["changed"]
    assert (helpers / "client.py").stat().st_mtime_ns == old_stat.st_mtime_ns


def test_releasing_one_seat_keeps_the_other_seats_hooks(tmp_path):
    config = tmp_path / ".claude"
    config.mkdir()
    base = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "base"}]}]}}
    (config / "settings.json").write_text(json.dumps(base))
    first = tmp_path / ".cheese/seats/first/remote-session"
    second = tmp_path / ".cheese/seats/second/remote-session"
    for directory, name in ((first, "first"), (second, "second")):
        directory.mkdir(parents=True)
        (directory / "execution.json").write_text("{}")
        (directory / "settings.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {"hooks": [{"type": "command", "command": name}]}
                        ]
                    }
                }
            )
        )
    sources = {
        "client.py": "new",
        "proxy.js": "__EXECUTION_CONFIG__",
        "cheese.py": CHEESE,
    }

    first_stage = release.stage(str(tmp_path), sources, seat=str(first.parent))
    original = (first / "settings.json").read_text()
    release.acknowledge(str(tmp_path), first_stage["version"], seat=str(first.parent))
    second_stage = release.stage(str(tmp_path), sources, seat=str(second.parent))

    assert first_stage["changed"]
    assert second_stage["changed"]
    assert not release.stage(str(tmp_path), sources, seat=str(first.parent))["changed"]
    first_client = first.parent / "remote-execution/client.py"
    second_client = second.parent / "remote-execution/client.py"
    assert first_client.read_text() == second_client.read_text() == "new"
    second_client.write_text("other seat changed")
    assert not release.stage(str(tmp_path), sources, seat=str(first.parent))["changed"]
    assert first_client.read_text() == "new"
    assert (first / "settings.json").read_text() == original
    assert json.loads((config / "settings.json").read_text()) == base
    updated = json.loads((second / "settings.json").read_text())
    assert updated["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "second"
    assert "mcp__native__invoke" in updated["permissions"]["allow"]


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
    (directory / "settings.json").write_text("{}")
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
    release.stage(
        str(tmp_path), {"client.py": "new", "proxy.js": "new", "cheese.py": CHEESE}
    )
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
    (directory / "settings.json").write_text("{}")
    monkeypatch.setattr(release.os.path, "ismount", lambda path: True)

    def unexpected(*args, **kwargs):
        raise AssertionError("a native cwd mount cannot be normally unmounted")

    monkeypatch.setattr(release.subprocess, "run", unexpected)
    result = release.stage(
        str(tmp_path), {"client.py": "new", "proxy.js": "new", "cheese.py": CHEESE}
    )
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
    (directory / "settings.json").write_text(json.dumps(settings))
    sources = {
        "client.py": "new client",
        "executor_transport.py": "companion",
        "proxy.js": "const target = __EXECUTION_CONFIG__;",
        "cheese.py": CHEESE,
    }

    release.stage(str(tmp_path), sources)

    current = json.loads((directory / "settings.json").read_text())
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
    assert (tmp_path / ".cheese/remote-session/release-ready").read_text() == "released"


def test_release_bundles_locked_fuse_adapter_with_license():
    sources = release.sources()
    assert "class ForwardedProject" in sources["forwarded_fs.py"]
    assert "Permission to use, copy, modify, and distribute" in sources["fuse.py"]


def test_active_turn_blocks_changes_until_completion(tmp_path):
    config = tmp_path / ".claude"
    config.mkdir(exist_ok=True)
    platform_dir = tmp_path / ".cheese"
    (platform_dir / "remote-session").mkdir(parents=True)
    (platform_dir / "remote-execution").mkdir()
    (platform_dir / "remote-session/execution.json").write_text("{}")
    (platform_dir / "remote-session/settings.json").write_text("{}")
    client = platform_dir / "remote-execution/client.py"
    client.write_text("previous")
    transcript = config / "projects/work/session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "work"}}) + "\n"
    )
    sources = {
        "client.py": "released",
        "proxy.js": "__EXECUTION_CONFIG__",
        "cheese.py": CHEESE,
    }
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


def test_one_seats_busy_transcript_does_not_delay_an_idle_seats_release(tmp_path):
    config = tmp_path / ".claude/projects/work"
    config.mkdir(parents=True)
    idle = config / "idle.jsonl"
    idle.write_text(
        json.dumps({"type": "user", "message": {"content": "done"}})
        + "\n"
        + json.dumps({"type": "assistant", "message": {"stop_reason": "end_turn"}})
        + "\n"
    )
    busy = config / "busy.jsonl"
    busy.write_text(json.dumps({"type": "user", "message": {"content": "work"}}) + "\n")
    sources = {
        "client.py": "released",
        "proxy.js": "__EXECUTION_CONFIG__",
        "cheese.py": CHEESE,
    }
    for name in ("idle", "busy"):
        directory = tmp_path / ".cheese/seats" / name / "remote-session"
        directory.mkdir(parents=True)
        (directory / "execution.json").write_text("{}")
        (directory / "settings.json").write_text("{}")

    idle_seat = tmp_path / ".cheese/seats/idle"
    busy_seat = tmp_path / ".cheese/seats/busy"
    assert release.stage(str(tmp_path), sources, str(idle_seat), "idle")["changed"]
    assert release.stage(str(tmp_path), sources, str(busy_seat), "busy")["busy"]
    assert (idle_seat / "remote-execution/client.py").read_text() == "released"
    assert not (busy_seat / "remote-execution/client.py").exists()


class _Runner:
    """A hub whose `call_executor` answers the way the screen's runner does.

    `command` is a slash command run to its `result`; `control` is a control
    request answered with the build's `control_response`. Every call is kept in
    order, which is the thing under test: the release is acknowledged only after
    the session has taken it.
    """

    def __init__(self, *, command_error=False, control_error=False):
        self.calls = []
        self.command_error = command_error
        self.control_error = control_error

    async def exec(self, device, command, *, stdin=None, timeout):
        result = subprocess.run(
            [sys.executable, "-I", "-"],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        self.calls.append(("exec", json.loads(result.stdout or "null")))
        return {
            "exit": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    async def call_executor(self, device, state, method, params, timeout):
        assert state == "$HOME/.cheese/harness/state"
        if method == "command":
            self.calls.append(("command", params["text"]))
            return {"result": "", "is_error": self.command_error}
        assert method == "control"
        subtype = params["request"]["subtype"]
        assert params["request"]["serverName"] == "native"
        self.calls.append(("control", subtype))
        if self.control_error:
            return {"subtype": "error", "error": "disconnected"}
        response = {}
        if subtype == "mcp_status":
            polled = sum(call == ("control", "mcp_status") for call in self.calls)
            response = {
                "mcpServers": [
                    {
                        "name": "native",
                        "status": "pending" if polled == 1 else "connected",
                    }
                ]
            }
        return {"subtype": "success", "request_id": "r", "response": response}


def _released_home(tmp_path, monkeypatch):
    config = tmp_path / ".claude"
    config.mkdir(exist_ok=True)
    # The screen's own seat, not the room: a release is that session's target
    # and that session's plugin.
    session = (
        Path(place.seat_dir(str(tmp_path), SCREEN.agent_handle)) / "remote-session"
    )
    session.mkdir(parents=True)
    (session / "execution.json").write_text("{}")
    (session / "settings.json").write_text("{}")
    monkeypatch.setattr(
        release,
        "sources",
        lambda: {
            "client.py": "released",
            "proxy.js": "__EXECUTION_CONFIG__",
            "cheese.py": CHEESE,
        },
    )
    return session / "release-ready"


def _channel(hub):
    channel = object.__new__(DeviceChannel)
    channel._hub = hub
    return channel


SCREEN = HubScreen("screen", "device", [], "token", 1, "agent")
STATE = "$HOME/.cheese/harness/state"


@pytest.mark.anyio
async def test_a_release_is_acknowledged_once_the_session_has_reconnected(
    tmp_path, monkeypatch
):
    ready = _released_home(tmp_path, monkeypatch)
    hub = _Runner()

    assert await _channel(hub)._refresh_resident(
        SCREEN, str(tmp_path), STATE, {}, seat=SCREEN.agent_handle
    )

    assert [call for call in hub.calls if call[0] != "exec"] == [
        ("command", "/reload-plugins"),
        ("control", "mcp_reconnect"),
        ("control", "mcp_status"),
        ("control", "mcp_status"),
    ]
    # Staged first, acknowledged last.
    assert hub.calls[0][0] == "exec" and hub.calls[-1] == ("exec", None)
    assert ready.read_text() == release.digest(release.sources())


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("hub", "failed"),
    [
        (_Runner(command_error=True), "/reload-plugins"),
        (_Runner(control_error=True), "mcp_reconnect"),
    ],
)
async def test_a_release_the_session_did_not_take_is_not_acknowledged(
    tmp_path, monkeypatch, hub, failed
):
    ready = _released_home(tmp_path, monkeypatch)

    with pytest.raises(ScreenSetupError, match=failed):
        await _channel(hub)._refresh_resident(
            SCREEN, str(tmp_path), STATE, {}, seat=SCREEN.agent_handle
        )

    assert not ready.exists()


@pytest.mark.anyio
async def test_a_release_already_in_place_asks_the_session_nothing(
    tmp_path, monkeypatch
):
    _released_home(tmp_path, monkeypatch)
    hub = _Runner()
    version = release.digest(release.sources())

    assert not await _channel(hub)._refresh_resident(
        SCREEN, str(tmp_path), STATE, {"version": version}, seat=SCREEN.agent_handle
    )
    assert hub.calls == []
