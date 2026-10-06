"""DeviceChannel: opening, reusing and retiring a room's screen on a machine.

What runs in the screen is the harness's runner; the channel asks it things
through the hub (``call_executor``), and every other step — the launcher, the
per-turn files, the tunnel probe, the release — is an ``exec`` on the machine.
``FakeHub`` answers both the way a connector with a live runner behind it does.
"""

import asyncio
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import settings
from app.domain.agent import device_provider, machine_launcher, screen_identity
from app.domain.agent.device_hub import DeviceCallError, HubScreen
from app.domain.agent.device_provider import (
    DeviceChannel,
    device_home_dir,
    device_store_dir,
)
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.harness.channel import SESSION_TOKEN_TTL_S, ScreenSetupError
from app.domain.agent.harness.claude_code.device_launch import DEVICE_TUNNEL_PROBE
from app.domain.agent.harness.claude_code.remote_execution import (
    release as resident_release,
)
from app.domain.agent.harness.claude_code.session_launch import ClaudeLaunch
from app.domain.agent.place import seat_dir, seat_key


@pytest.fixture(autouse=True)
def _no_device_identity(monkeypatch):
    """The backend addresses this device at the base it was built with."""

    async def public_base(self, _device_id):
        return self._public_base

    monkeypatch.setattr(DeviceChannel, "_device_api_base", public_base)


def _release_step(stdin: str) -> str:
    """Which resident-release function a `python3 -` exec runs."""
    return stdin.rsplit("\nprint(json.dumps(", 1)[1].split("(", 1)[0]


class FakeHub:
    """A connector with one machine, whose screens run a Claude Code runner.

    ``ping`` is what the runner answers (or the exception the call raises), the
    tunnel probe answers ``tunnel``, and the resident-release steps answer from
    ``release``. Every open gets a fresh sid, so a reopen is distinguishable
    from a reassert."""

    def __init__(self) -> None:
        self.opened: list[HubScreen] = []
        self.envs: list[dict | None] = []
        self.reasserted: list[str] = []
        # The environment each reassertion carried, by sid.
        self.reasserted_envs: dict[str, dict | None] = {}
        self.closed: list[str] = []
        self.execs: list[tuple[list, str | None]] = []
        self.asked: list[tuple[str, dict]] = []  # (method, params) to the runner
        self.ping: dict | BaseException = {"alive": True, "working": False}
        self.tunnel = ("up", 0)
        self.probed_dirs: list[str] = []
        self.release = {"stage": {"changed": True}, "acknowledge": {}}
        # The release the machine's helpers are on, as its marker names it.
        self.marker = ""
        # A fresh launch installs the current release, as the launcher does.
        self.launch_writes_marker = False
        # Controls the running session never completes.
        self.refused: set[str] = set()
        self._sid = 0

    def online_device_ids(self) -> list[str]:
        return ["dev1"]

    async def list_screens(self, device_id):
        return []

    def is_online(self, device_id: str) -> bool:
        return device_id == "dev1"

    def all_online_screens(self) -> list[HubScreen]:
        return list(self.opened)

    def device_name(self, device_id: str) -> str:
        return "andy 的笔记本" if device_id == "dev1" else device_id

    def last_seen_age(self, device_id: str) -> float | None:
        return 3.0 if device_id == "dev1" else None

    async def open_screen(self, device_id, command, **kw) -> HubScreen:
        if self.launch_writes_marker and self._sid:
            self.marker = resident_release.digest(resident_release.sources())
        self._sid += 1
        screen = HubScreen(
            sid=f"s{self._sid}",
            device_id=device_id,
            command=command,
            token="tok",
            agent_user_id=kw["agent_user_id"],
            agent_handle=kw["agent_handle"],
            project_id=kw["project_id"],
            topic_id=kw["topic_id"],
        )
        self.opened.append(screen)
        self.envs.append(kw.get("env"))
        return screen

    async def reassert_screen(self, screen: HubScreen, *, command, env=None) -> None:
        screen.command = command
        self.reasserted.append(screen.sid)
        self.reasserted_envs[screen.sid] = env

    def update_screen(self, sid: str, **values) -> HubScreen:
        screen = next(screen for screen in self.opened if screen.sid == sid)
        for name, value in values.items():
            if value is not None or name in {"resource_id", "execution_target"}:
                setattr(screen, name, value)
        return screen

    async def close_screen(self, device_id, sid) -> bool:
        self.closed.append(sid)
        self.opened = [s for s in self.opened if s.sid != sid]
        return True

    async def exec(
        self, device_id, argv, *, cwd=None, env=None, timeout=60, stdin=None
    ) -> dict:
        self.execs.append((argv, stdin))
        if env and "CHEESE_TUNNEL_PROBE_DIR" in env:
            self.probed_dirs.append(env["CHEESE_TUNNEL_PROBE_DIR"])
            verdict, code = self.tunnel
            return {"stdout": verdict, "stderr": "", "exit": code}
        if argv[:2] == ["sh", "-c"] and "release-ready" in argv[2]:
            return {"stdout": self.marker, "stderr": "", "exit": 0}
        if argv == ["python3", "-"]:
            answer = self.release[_release_step(stdin or "")]
            return {"stdout": json.dumps(answer), "stderr": "", "exit": 0}
        return {"stdout": "", "stderr": "", "exit": 0, "truncated": False}

    async def call_executor(self, device_id, state, method, params, timeout=None):
        self.asked.append((method, params))
        if method == "ping":
            if isinstance(self.ping, BaseException):
                raise self.ping
            return dict(self.ping)
        if method == "command":
            return {"is_error": False}
        subtype = params["request"]["subtype"]
        if subtype in self.refused:
            return {"subtype": "error", "error": "not completed"}
        response = (
            {"mcpServers": [{"name": "native", "status": "connected"}]}
            if subtype == "mcp_status"
            else {}
        )
        return {"subtype": "success", "response": response}

    def commands(self) -> list[str]:
        return [params["text"] for method, params in self.asked if method == "command"]

    def controls(self) -> list[str]:
        return [
            params["request"]["subtype"]
            for method, params in self.asked
            if method == "control"
        ]


class ShellHub(FakeHub):
    """A hub whose `sh -c` execs really run, with `$HOME` at ``home``.

    The per-turn files are written by the machine's own shell — a `umask`, a
    temp file and a rename — so a test about WHICH file a turn touches has to
    let that shell run rather than answer it with a canned stdout.
    """

    def __init__(self, home):
        super().__init__()
        self.home = home

    async def exec(self, device_id, argv, *, env=None, stdin=None, **kwargs):
        if argv[:2] != ["sh", "-c"] or (env or {}).get("CHEESE_TUNNEL_PROBE_DIR"):
            return await super().exec(device_id, argv, env=env, stdin=stdin, **kwargs)
        self.execs.append((argv, stdin))
        result = subprocess.run(
            argv,
            input=stdin,
            text=True,
            capture_output=True,
            env={**os.environ, "HOME": str(self.home), **(env or {})},
            timeout=10,
        )
        return {
            "exit": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }


def _room(hub, **fixed):
    """One room on ``hub``: its channel, and a call that ensures its screen."""
    channel = DeviceChannel(hub=hub, public_base="http://cheese.test")
    arguments = dict(
        device_id="dev1",
        agent_user_id=1,
        agent_handle="cheese",
        project_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        token="tok",
        env=None,
        launch=ClaudeLaunch(system_prompt=""),
    )
    arguments.update(fixed)

    async def ensure(**changes) -> HubScreen:
        return await channel._ensure_screen(**{**arguments, **changes})

    return SimpleNamespace(channel=channel, arguments=arguments, ensure=ensure)


def _retired(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("device_screen_retired")
    ]


# --- recovery ---------------------------------------------------------------


async def test_screen_inventory_failure_does_not_skip_the_next_device():
    visited = set()

    class Hub(FakeHub):
        async def list_screens(self, device_id):
            visited.add(device_id)
            if device_id == "broken":
                raise DeviceCallError("dial unix: no such file")
            return []

    project, first, second = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    channel = DeviceChannel(hub=Hub())
    restored = await channel.restore_screens(
        [
            (project, first, "broken"),
            (project, second, "healthy"),
        ]
    )
    assert visited == {"broken", "healthy"}
    assert restored == [(project, first, None, None), (project, second, None, None)]


async def test_central_recovery_restores_actual_screen_and_close_reaches_device(
    monkeypatch,
):
    from unittest.mock import AsyncMock

    from app.domain.agent.device_hub import DeviceHub
    from app.domain.identity.services import IdentityService

    project_id, topic_id, resource_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    room = SimpleNamespace(id=topic_id, project_id=project_id, resource_id=resource_id)
    metadata = {
        "sid": "survivor",
        "screen": "birth-token",
        "command": ["claude"],
        "env": {
            "CHEESE_PROJECT": str(project_id),
            "CHEESE_TOPIC": str(topic_id),
            "CHEESE_RESOURCE_ID": str(resource_id),
            "CHEESE_TOKEN_EXPIRES": "1234567890",
            "CHEESE_AGENT_CONFIG": "original-config",
            "CHEESE_EXECUTION_TARGET": '{"device_id":"executor"}',
        },
    }
    hub = DeviceHub()
    sent = []
    retired = {
        **metadata,
        "sid": "retired",
        "screen": "retired-token",
        "env": {**metadata["env"], "CHEESE_RESOURCE_ID": str(uuid.uuid4())},
    }

    class Transport:
        async def send_json(self, msg):
            sent.append(msg)
            if msg["t"] in ("session.list", "session.close"):
                await hub.on_device_message(
                    "center",
                    {
                        "t": "session.result",
                        "id": msg["id"],
                        "value": [metadata, retired]
                        if msg["t"] == "session.list"
                        else None,
                    },
                )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def commit(self):
            pass

    async def agent(self, topic):
        return SimpleNamespace(id=1, username="agent")

    monkeypatch.setattr(IdentityService, "ensure_room_agent_user", agent)
    monkeypatch.setattr(
        "app.domain.topic.services.TopicService.get", AsyncMock(return_value=room)
    )
    await hub.attach_device("center", Transport())
    # The screens the session core adopts after a restart (its Claude Code
    # driver), for each place a session was found at.
    channel = DeviceChannel(hub=hub, session_factory=Session)
    await channel.restore_screens([(project_id, topic_id, "center")])
    screen = hub.screen("survivor")
    assert screen is not None
    assert screen.resource_id == resource_id
    assert screen.credential_expires == 1234567890
    assert screen.agent_configuration == "original-config"
    assert screen.execution_target == {"device_id": "executor"}
    # A retired generation stays registered, so its cleanup can still reach it.
    assert hub.screen("retired") is not None
    assert {item.sid for item in hub.all_online_screens()} == {"survivor", "retired"}
    assert not any(msg["t"] == "session.create" for msg in sent)
    assert await hub.close_screen("center", screen.sid)
    assert hub.screen("survivor") is None
    assert sent[-1]["t"] == "session.close"


# --- reuse: a live screen is reasserted, never trusted, never relaunched -----


async def test_second_turn_reasserts_the_screen_instead_of_trusting_the_registry():
    """The hub's registry outlives what the device actually runs (a connector
    restart kills its sessions; a create sent on a dying transport was never
    delivered). So a later turn re-sends the screen's adopt-create — idempotent
    on a live session, a respawn for a lost one — rather than trust it."""
    hub = FakeHub()
    room = _room(hub)

    first = await room.ensure()
    second = await room.ensure()

    assert second is first
    assert [s.sid for s in hub.opened] == [first.sid]  # the room keeps ONE screen …
    assert hub.reasserted == [first.sid]  # … re-asserted on reuse
    assert hub.closed == []


async def test_reuse_checks_and_launcher_transfer_do_not_wait_for_each_other(
    monkeypatch,
):
    hub = FakeHub()
    room = _room(hub)
    screen = await room.ensure()
    entered = set()
    ready = asyncio.Event()

    async def operation(name, result):
        entered.add(name)
        if len(entered) == 3:
            ready.set()
        await ready.wait()
        assert hub.reasserted == []
        return result

    async def runner(_device_id, _state, method, params=None):
        assert method == "ping"
        return await operation("ping", {"alive": True})

    async def tunnel(_screen, _home):
        return await operation("tunnel", False)

    async def refresh(*_arguments, **_keywords):
        return await operation("files", None)

    monkeypatch.setattr(room.channel, "_runner", runner)
    monkeypatch.setattr(room.channel, "_tunnel_helper_is_down", tunnel)
    monkeypatch.setattr(room.channel, "_refresh_screen_files", refresh)
    reused = await asyncio.wait_for(room.ensure(), timeout=2)
    assert reused is screen
    assert entered == {"ping", "tunnel", "files"}
    assert hub.reasserted == [screen.sid]


async def test_a_live_screen_is_not_sent_the_launcher_again():
    """The first turn writes the launcher; a reused screen never runs it, so
    the second turn ships only the per-turn files and points the adopt at the
    file already on the machine. A screen found dead gets a fresh launcher
    before it is reopened."""
    hub = FakeHub()
    room = _room(hub, launch=ClaudeLaunch(system_prompt="a prompt the launcher embeds"))

    def shipped(execs):
        return [stdin for _argv, stdin in execs if stdin is not None]

    screen = await room.ensure()
    assert len(shipped(hub.execs)) == 1
    assert "a prompt the launcher embeds" in shipped(hub.execs)[0]
    written = re.search(r'cat > "([^"]+)"', hub.execs[0][0][-1]).group(1)
    reused = await room.ensure()
    assert reused is screen
    assert len(shipped(hub.execs)) == 1
    # The adopt points the screen at the file the first turn wrote.
    assert screen.command == ["bash", "-lc", f'exec bash "{written}"']

    hub.ping = {"alive": False}
    replacement = await room.ensure()
    assert replacement is not screen and hub.closed == [screen.sid]
    assert len(shipped(hub.execs)) == 2


async def test_a_reused_screen_gets_its_token_rotated_and_its_harness_config_left_alone(
    tmp_path,
):
    """A live session reads its forwarded-fs token from a file, so a reused
    turn rewrites that file in place — as the machine's own shell runs it — and
    touches nothing of the harness's own config under the same home."""

    hub = ShellHub(tmp_path)
    hub.release["stage"] = {"changed": False}
    room = _room(
        hub, env={"CHEESE_EXECUTION_TARGET": json.dumps({"device_id": "executor"})}
    )
    screen = await room.ensure(token="first")
    home = Path(
        device_home_dir(
            room.arguments["project_id"], room.arguments["topic_id"]
        ).replace("$HOME", str(tmp_path))
    )
    # The token lives in THIS seat's directory, the room's other teammates
    # having one of their own (`place.seat_dir`).
    token = (
        Path(seat_dir(str(home), room.arguments["agent_handle"]))
        / "remote-session/execution.token"
    )
    assert token.read_text() == "first"
    settings_path = home / ".claude/settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text('{"keep":true}')

    reused = await room.ensure(token="rotated")

    assert reused is screen and len(hub.opened) == 1
    assert hub.reasserted == [screen.sid]
    assert token.read_text() == "rotated"
    assert token.stat().st_mode & 0o777 == 0o600
    assert settings_path.read_text() == '{"keep":true}'


def test_release_marker_is_read_from_the_requested_seat(tmp_path):
    home = tmp_path / "room"
    for name in ("first", "second"):
        session = Path(seat_dir(str(home), name)) / "remote-session"
        session.mkdir(parents=True)
        (session / "release-ready").write_text(name)

    for name in ("first", "second"):
        command, env = DeviceChannel._screen_file_refresh(
            str(home), release_state={}, execution_token=None, seat=name
        )
        result = subprocess.run(
            ["sh", "-c", command], capture_output=True, text=True, env=env
        )
        assert result.returncode == 0
        assert result.stdout == name


async def test_a_turn_rewrites_only_its_own_seat_s_execution_token(tmp_path):
    """一个话题两个座位，各写各的凭据文件（docs/manual/dev/turn.md #seats-session）。

    The file this replaces lived at the ROOM's level and was rewritten by every
    turn of every teammate, so opening a second teammate's turn swapped the
    token the first one's running session authenticated with: its next call was
    refused with 「Execution credential does not own this session」, its runner
    socket vanished and the turn it was in the middle of was declared dead
    (2026-09-29 05:39).
    """
    hub = ShellHub(tmp_path)
    hub.release["stage"] = {"changed": False}
    room = _room(
        hub, env={"CHEESE_EXECUTION_TARGET": json.dumps({"device_id": "executor"})}
    )

    mine_screen = await room.ensure(agent_handle="cheese-a", token="a1")
    home = Path(
        device_home_dir(
            room.arguments["project_id"], room.arguments["topic_id"]
        ).replace("$HOME", str(tmp_path))
    )
    mine = Path(seat_dir(str(home), "cheese-a")) / "remote-session/execution.token"
    theirs = Path(seat_dir(str(home), "cheese-b")) / "remote-session/execution.token"
    assert mine.read_text() == "a1" and not theirs.exists()

    theirs_screen = await room.ensure(agent_handle="cheese-b", token="b1")
    assert theirs_screen.sid != mine_screen.sid
    assert theirs.read_text() == "b1"
    assert mine.read_text() == "a1", "开第二位队友的轮次换掉了第一位正在用的凭据"

    await room.ensure(agent_handle="cheese-a", token="a2")
    assert mine.read_text() == "a2"
    assert theirs.read_text() == "b1"


async def test_a_task_s_session_leaves_its_room_s_session_its_own_files(tmp_path):
    """One teammate in a room and in a task of that room: two conversations on
    one machine, under the room's home. Opening the task's must not change the
    credential or the launcher the room's session runs on.

    On dev (2026-10-05) the task's start rewrote both: from then on every call
    the room's agent made was refused as working another conversation, and the
    task's launcher came up on the room's state and never answered.
    """
    hub = ShellHub(tmp_path)
    hub.release["stage"] = {"changed": False}
    project, room_id, task_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    room = _room(
        hub,
        project_id=project,
        env={
            "CHEESE_EXECUTION_TARGET": json.dumps({"device_id": "executor"}),
            "CHEESE_RESOURCE_ID": str(room_id),
        },
    )
    home = Path(device_home_dir(project, room_id).replace("$HOME", str(tmp_path)))

    def launcher(screen: HubScreen) -> Path:
        written = re.search(r'exec bash "([^"]+)"', screen.command[-1]).group(1)
        return Path(written.replace("$HOME", str(tmp_path)))

    room_screen = await room.ensure(topic_id=room_id, token="room")
    room_token = Path(seat_dir(str(home), "cheese")) / "remote-session/execution.token"
    room_launcher = launcher(room_screen).read_text()

    task_screen = await room.ensure(
        topic_id=task_id, seat=seat_key("cheese", task_id), token="task"
    )

    assert task_screen.sid != room_screen.sid
    assert room_token.read_text() == "room", "开任务的会话换掉了房间会话的凭据"
    assert launcher(room_screen).read_text() == room_launcher
    assert launcher(task_screen) != launcher(room_screen)


@pytest.mark.parametrize(
    ("ping", "retired"),
    [
        pytest.param({"alive": True}, False, id="alive"),
        pytest.param({"alive": False}, True, id="says-it-is-gone"),
        pytest.param(DeviceCallError("dial unix: refused"), True, id="no-runner"),
        pytest.param(TimeoutError(), False, id="timed-out"),
        pytest.param(httpx.ConnectError("reset"), False, id="link-lost"),
    ],
)
async def test_only_the_machine_saying_so_retires_a_reused_session(
    caplog, ping, retired
):
    """Retiring a screen ends the `claude` behind it and whatever it was doing.
    So it takes the machine's word: a runner that says the session is gone, or
    no runner to ask at all. A timeout or a lost link says nothing about the
    session and leaves it working."""
    hub = FakeHub()
    room = _room(hub)
    first = await room.ensure()
    hub.ping = ping

    with caplog.at_level("INFO"):
        second = await room.ensure()

    if retired:
        assert second is not first and hub.closed == [first.sid]
        assert hub.reasserted == []
        assert [s.sid for s in hub.opened] == [second.sid]
        (line,) = _retired(caplog)
        assert "reason=session_not_alive" in line
    else:
        assert second is first and hub.closed == []
        assert hub.reasserted == [first.sid]
        assert _retired(caplog) == []


async def test_a_retired_screen_says_which_gate_decided(caplog):
    """A room whose screen is retired meets a `claude` seconds old: its platform
    tools are briefly not listed and its next turn is seconds slower. Reading the
    connector's access log afterwards shows the close and never the why, and a
    release that replaces the deciding backend takes even that away — so the reason
    is written where it is read, at the moment the gate fires."""
    hub = FakeHub()
    room = _room(hub)
    await room.ensure()
    hub.ping = {"alive": False}

    with caplog.at_level("INFO"):
        await room.ensure()

    (line,) = _retired(caplog)
    assert f"topic={room.arguments['topic_id']}" in line
    assert "sid=s1" in line
    assert "reason=session_not_alive" in line


# --- the tunnel helper -------------------------------------------------------


async def test_a_reused_screen_whose_tunnel_helper_died_is_relaunched(
    monkeypatch, caplog
):
    """`claude` dials a machine-local tunnel helper it was handed at startup and
    never re-reads. That helper is brought up ONLY by the launcher's prefix, and a
    reused screen is reasserted rather than relaunched — so when the helper dies
    under a still-running `claude`, nothing on either side restores it. The reuse
    gate retires such a screen so a FRESH launch runs the prefix again."""
    monkeypatch.setattr(
        settings, "subscription_tunnel_url", "wss://gateway.example/llm/tunnel"
    )
    hub = FakeHub()
    hub.tunnel = ("down", 0)
    room = _room(hub)
    first = await room.ensure()

    with caplog.at_level("INFO"):
        second = await room.ensure()

    assert hub.reasserted == []  # never reasserted onto the dead helper …
    assert hub.closed == [first.sid]  # … the screen was retired …
    assert [s.sid for s in hub.opened] == [second.sid]  # … and relaunched fresh
    (line,) = _retired(caplog)
    assert "reason=tunnel_helper_down" in line
    # The probe reads the port from THIS SEAT's own directory, so concurrent
    # seats (and rooms) on one machine are judged independently rather than
    # sharing one verdict.
    assert hub.probed_dirs == [
        seat_dir(
            device_home_dir(room.arguments["project_id"], room.arguments["topic_id"]),
            room.arguments["agent_handle"],
        )
    ]


@pytest.mark.parametrize(
    ("verdict", "exit_code"),
    [("up", 0), ("unknown", 0), ("down", 1), ("", 0)],
)
async def test_only_an_explicit_down_retires_a_screen(monkeypatch, verdict, exit_code):
    """The gate throws away a live screen and the work in flight behind it, so it
    fires only on proof. A helper that is up, a box with no /proc or no awk
    (`unknown`), a probe that failed to run (non-zero exit), and an empty answer
    must all leave the screen alone."""
    monkeypatch.setattr(
        settings, "subscription_tunnel_url", "wss://gateway.example/llm/tunnel"
    )
    hub = FakeHub()
    hub.tunnel = (verdict, exit_code)
    room = _room(hub)
    first = await room.ensure()

    assert await room.ensure() is first
    assert hub.closed == []
    assert hub.reasserted == [first.sid]


async def test_no_tunnel_deployment_pays_nothing_for_the_gate(monkeypatch):
    """Where no tunnel is configured the device dials the meter directly and there
    is no helper to lose, so the gate must not cost an extra round trip to every
    box on every turn."""
    monkeypatch.setattr(settings, "subscription_tunnel_url", "")
    hub = FakeHub()
    hub.tunnel = ("down", 0)
    room = _room(hub)
    await room.ensure()
    await room.ensure()

    assert hub.probed_dirs == []  # never asked
    assert hub.closed == []  # and nothing retired on a verdict it never got


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_the_tunnel_probe_reads_a_real_listening_socket(tmp_path, host: str):
    """The probe is a shell script parsing /proc/net/tcp, which is exactly the kind
    of thing that passes review and is wrong on the box. Run it for real: against
    the port a seat's port file records, it must say `up` while the recorded
    helper pid (this test) is listening there, `down` once nothing holds it, and
    `down` when the port is held by a process other than the recorded helper —
    the listener another seat's helper becomes once the kernel hands it a dead
    helper's port."""
    import socket

    if not os.access("/proc/net/tcp", os.R_OK):
        pytest.skip("no readable /proc/net/tcp on this platform")
    if host == "::1" and not socket.has_ipv6:
        pytest.skip("IPv6 is unavailable on this platform")
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    port_file = _seat_dir(tmp_path) / "cheese-tunnel.port"
    pid_file = port_file.with_name("cheese-tunnel.pid")
    port_file.parent.mkdir(parents=True)

    def verdict(port: int, helper: int) -> str:
        port_file.write_text(f"{port}\n")
        pid_file.write_text(f"{helper}\n")
        return _tunnel_probe(tmp_path)

    stranger = subprocess.Popen(["sleep", "30"])
    try:
        with socket.socket(family) as live:
            live.bind((host, 0))
            live.listen(1)
            listening = live.getsockname()[1]
            assert verdict(listening, os.getpid()) == "up"
            assert verdict(listening, stranger.pid) == "down"
    finally:
        stranger.kill()
        stranger.wait()

    with socket.socket(family) as probe:  # bound, then released → nothing listening
        probe.bind((host, 0))
        free = probe.getsockname()[1]
    assert verdict(free, os.getpid()) == "down"


# The seat name the launcher derives from the handle under test — the sha256
# prefix `place.seat_name` gives a `cheese-…` handle, spelled opaquely here.
_TUNNEL_PROBE_SEAT = "46dd0e26bf01"


def _seat_dir(machine_home: Path, seat: str = _TUNNEL_PROBE_SEAT) -> Path:
    """Where a launch leaves one seat's helper files."""
    return machine_home / "room" / ".cheese" / "seats" / seat


def _tunnel_probe(machine_home: Path, seat: str = _TUNNEL_PROBE_SEAT) -> str:
    """The probe as the backend runs it: the seat's directory named with the
    literal `$HOME` placeholder the backend leaves in it, resolved against the
    machine's own HOME. Per seat, because a room may seat several agents and
    each has a helper of its own."""
    return subprocess.run(
        ["sh", "-c", DEVICE_TUNNEL_PROBE],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HOME": str(machine_home),
            "CHEESE_TUNNEL_PROBE_DIR": f"$HOME/room/.cheese/seats/{seat}",
        },
        timeout=30,
    ).stdout.strip()


@pytest.mark.parametrize("content", [None, "", "not-a-port\n"])
def test_a_room_without_a_readable_port_file_is_unknown_not_down(tmp_path, content):
    """Only the machine knows the helper's port, and it knows it through this
    file. No file (a room launched before it existed, a home being rebuilt) or a
    file that is not a number is no evidence the helper died — `down` here would
    throw away a working screen on a probe that never looked at a port."""
    if content is not None:
        port_file = _seat_dir(tmp_path) / "cheese-tunnel.port"
        port_file.parent.mkdir(parents=True)
        port_file.write_text(content)

    assert _tunnel_probe(tmp_path) == "unknown"


def test_a_room_without_a_recorded_helper_pid_is_unknown_not_down(tmp_path):
    """Whose socket the port is can only be told from the pid the helper was
    started as. Without that record the probe has no evidence either way."""
    port_file = _seat_dir(tmp_path) / "cheese-tunnel.port"
    port_file.parent.mkdir(parents=True)
    port_file.write_text("40000\n")

    assert _tunnel_probe(tmp_path) == "unknown"


# --- the resident release: new helpers into a running session ---------------


def _executor_room(hub, *, target: dict | None = None):
    return _room(
        hub,
        env={
            "CHEESE_EXECUTION_TARGET": json.dumps(target or {"device_id": "executor"})
        },
        launch=ClaudeLaunch(system_prompt="", resume_session_id="conversation"),
    )


async def test_a_reused_screen_a_release_cannot_reach_is_relaunched(caplog):
    """A release reaches a running `claude` only through its runner. One that
    cannot be asked right now keeps the process on the old release whatever the
    turn does, so failing the turn would fail every retry too. A fresh launch
    starts on the current release, so the turn gets a new screen instead."""
    hub = FakeHub()
    room = _executor_room(hub)
    first = await room.ensure()
    hub.ping = TimeoutError()

    with caplog.at_level("INFO"):
        replacement = await room.ensure()

    assert hub.closed == [first.sid]
    assert replacement is not first and hub.all_online_screens() == [replacement]
    assert hub.reasserted == []
    assert hub.commands() == [] and hub.controls() == []
    (line,) = _retired(caplog)
    assert "reason=resident_release_unreachable" in line
    # The new process is offered the room's conversation, as the first one was.
    assert (hub.envs[-1] or {}).get("CHEESE_RESUME_SESSION") == "conversation"


async def test_a_reused_screen_is_released_in_place_through_its_runner():
    """With the runner answering, the running `claude` is brought to the
    current release in place and keeps serving the room: the plugin reloads,
    and the MCP server carrying the tools reconnects and is waited on."""
    hub = FakeHub()
    room = _executor_room(hub)
    first = await room.ensure()

    reused = await room.ensure()

    assert reused is first and hub.closed == []
    assert hub.reasserted == [first.sid]
    assert hub.commands() == ["/reload-plugins"]
    assert hub.controls() == ["mcp_reconnect", "mcp_status"]
    steps = [
        _release_step(stdin) for argv, stdin in hub.execs if argv == ["python3", "-"]
    ]
    assert steps == ["stage", "acknowledge"]
    for method, params in hub.asked:
        if method == "control":
            assert params["request"]["serverName"] == "native"


async def test_a_session_already_on_this_release_is_not_reloaded():
    hub = FakeHub()
    hub.release["stage"] = {"changed": False}
    room = _executor_room(hub)
    first = await room.ensure()

    assert await room.ensure() is first
    assert hub.commands() == [] and hub.controls() == []


async def test_a_release_that_fails_in_place_relaunches_the_screen(caplog):
    """The running process never completes the reconnect that would load the
    new helpers. Measured on dev 2026-09-24: one room failed ten turns in a row
    on exactly that. A fresh launch starts on the current release, so this turn
    gets one — and the turn after reuses it rather than trying again."""
    hub = FakeHub()
    hub.refused = {"mcp_reconnect"}
    hub.launch_writes_marker = True
    room = _executor_room(hub)
    first = await room.ensure()

    with caplog.at_level("INFO"):
        replacement = await room.ensure()
        again = await room.ensure()

    assert hub.closed == [first.sid]
    assert replacement is not first and again is replacement
    assert hub.all_online_screens() == [replacement]
    assert hub.reasserted == [replacement.sid]
    (line,) = _retired(caplog)
    assert "reason=resident_release_failed" in line
    # Only the first screen was asked to release in place.
    assert hub.controls() == ["mcp_reconnect"]
    assert (hub.envs[-1] or {}).get("CHEESE_RESUME_SESSION") == "conversation"


async def test_a_release_refused_by_a_busy_conversation_waits_for_a_later_turn(
    caplog,
):
    """Helpers are not replaced under a conversation that is still running.
    That is a reason to wait, not a failed turn: this turn runs on the release
    the process has, and the next turn tries again."""
    hub = FakeHub()
    hub.release["stage"] = {"changed": False, "busy": True}
    room = _executor_room(hub)
    first = await room.ensure()

    with caplog.at_level("INFO"):
        reused = await room.ensure()
        hub.release["stage"] = {"changed": True}
        await room.ensure()

    assert reused is first and hub.closed == []
    assert hub.reasserted == [first.sid, first.sid]
    assert _retired(caplog) == []
    steps = [
        _release_step(stdin) for argv, stdin in hub.execs if argv == ["python3", "-"]
    ]
    # Refused on the first attempt, applied on the next.
    assert steps == ["stage", "stage", "acknowledge"]
    assert hub.commands() == ["/reload-plugins"]


@pytest.mark.parametrize("busy", [{"working": True}, {"tasks": {"id": "running"}}])
async def test_a_busy_seat_never_stages_its_release_even_if_another_transcript_is_idle(
    busy,
):
    hub = FakeHub()
    room = _executor_room(hub)
    first = await room.ensure()
    hub.ping = {"alive": True, **busy}

    assert await room.ensure() is first
    assert not [stdin for argv, stdin in hub.execs if argv == ["python3", "-"]]
    assert hub.commands() == []


async def test_a_deferred_room_is_released_without_a_context_tree():
    """A room that has not leased a work machine yet carries a `deferred`
    target, and no target carries a context tree: the machine's own transport
    fetches the tree at launch and again when a lease arrives."""
    hub = FakeHub()
    topic_id = uuid.uuid4()
    target = {
        "kind": "deferred",
        "resource_id": str(topic_id),
        "session_id": str(uuid.uuid4()),
        "lease_path": f"/topics/{topic_id}/sessions/s/work-lease",
        "setup_env": {},
        "workspace": "/unavailable-project",
        "mcp_servers": [],
    }
    room = _executor_room(hub, target=target)
    first = await room.ensure(topic_id=topic_id)

    reused = await room.ensure(topic_id=topic_id)

    assert reused is first and hub.closed == []
    assert hub.reasserted == [first.sid]
    steps = [
        _release_step(stdin) for argv, stdin in hub.execs if argv == ["python3", "-"]
    ]
    assert steps == ["stage", "acknowledge"]
    assert reused.execution_target == target


@pytest.mark.parametrize("gone", [True, False])
async def test_a_room_whose_platform_tools_went_away_gets_them_back(gone):
    """Asked after a turn that published nothing: the session is told to
    reconnect the server carrying the room's tools only when it reports that
    server gone, and True — the message is re-delivered — only then."""

    class ToolsGone(FakeHub):
        def __init__(self) -> None:
            super().__init__()
            self.native = ["failed", "connected"] if gone else ["connected"]

        async def call_executor(self, device_id, state, method, params, timeout=None):
            if method == "control" and params["request"]["subtype"] == "mcp_status":
                self.asked.append((method, params))
                status = self.native.pop(0) if len(self.native) > 1 else self.native[0]
                return {
                    "subtype": "success",
                    "response": {"mcpServers": [{"name": "native", "status": status}]},
                }
            return await super().call_executor(device_id, state, method, params)

    hub = ToolsGone()
    room = _room(hub)
    await room.ensure()

    recovered = await room.channel.recover_native_tools(room.arguments["topic_id"])

    assert recovered is gone
    assert hub.controls() == (
        ["mcp_status", "mcp_reconnect", "mcp_status"] if gone else ["mcp_status"]
    )


async def test_a_room_with_no_screen_here_is_not_resent():
    hub = FakeHub()
    room = _room(hub)

    assert await room.channel.recover_native_tools(uuid.uuid4()) is False
    assert hub.asked == []


# --- the launcher -------------------------------------------------------------


async def test_launch_script_ships_as_a_file_never_as_tmux_argv():
    """The frozen cli hands the screen command to `tmux new-session`, whose packed
    command tops out around 16KB — a launcher carrying the assembled system prompt
    blows through that and every spawn dies with `command too long`. So the
    script travels over the link's `exec` (stdin → a per-topic file) and the
    session command stays a short runner, no matter how large the prompt is."""
    hub = FakeHub()
    room = _room(hub, launch=ClaudeLaunch(system_prompt="x" * 100_000))
    topic_id = room.arguments["topic_id"]

    await room.ensure()

    (argv, stdin) = next((a, s) for a, s in hub.execs if s and "x" * 1000 in s)
    assert f"$HOME/.cheese/launch/{topic_id}" in argv[-1]
    command = hub.opened[0].command
    assert sum(len(part) for part in command) < 1024
    assert f"$HOME/.cheese/launch/{topic_id}" in command[-1]


async def test_launcher_transfer_rotates_forwarded_token_without_an_extra_exec(
    tmp_path,
):
    class LocalHub:
        def __init__(self):
            self.calls = 0

        async def exec(self, device_id, argv, *, stdin, env, timeout):
            self.calls += 1
            result = subprocess.run(
                argv,
                input=stdin,
                text=True,
                capture_output=True,
                env={**os.environ, "HOME": str(tmp_path), **(env or {})},
                timeout=timeout,
            )
            return {
                "exit": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

    hub = LocalHub()
    provider = DeviceChannel(hub=hub)
    topic = uuid.uuid4()
    home = tmp_path / "room-home"
    # The token is the SEAT's (`place.seat_dir`): the session that reads it is
    # one teammate's, and a room-mate writing here is what used to swap a
    # running turn's credential for its own.
    seat = Path(seat_dir(str(home), "cheese"))
    for value in ("first", "rotated"):
        await provider._ship_launcher(
            "device",
            topic,
            ["bash", "-lc", "printf launcher"],
            str(home),
            execution_token=value,
            seat="cheese",
        )
        token = seat / "remote-session/execution.token"
        assert token.read_text() == value
        assert token.stat().st_mode & 0o777 == 0o600
    assert hub.calls == 2


async def test_a_connector_that_never_answers_the_launcher_is_named_in_the_error():
    """The room used to read 「device 后端启动失败：TimeoutError」 — no step, no
    machine, nothing about the link (2026-08-29, machine 477). The line has to
    say which step, which machine, and what the connector looked like."""

    class SilentHub(FakeHub):
        async def exec(self, device_id, argv, *, stdin=None, **kw) -> dict:
            self.execs.append((argv, stdin))
            if stdin is not None:  # the launcher ship is the one exec with input
                raise TimeoutError
            return {"stdout": "", "stderr": "", "exit": 0, "truncated": False}

    hub = SilentHub()
    with pytest.raises(ScreenSetupError) as raised:
        await _room(hub).ensure()

    text = str(raised.value)
    assert "写启动脚本" in text, text
    assert "andy 的笔记本" in text and "dev1" in text, text
    assert "没有应答" in text and "最近一帧是 3 秒前" in text, text
    assert "TimeoutError" not in text, "the exception class is not a reason"
    assert not hub.opened, "no screen is opened on a machine that did not answer"


async def test_no_online_device_is_a_clean_error():
    async def resolver(_project_id, _topic_id):
        return None  # nothing online / bound

    channel = DeviceChannel(hub=FakeHub(), device_resolver=resolver)
    with pytest.raises(ScreenSetupError):
        await channel.precheck(
            SessionRef(uuid.uuid4(), uuid.uuid4(), harness="claude-code"),
            needs_place=True,
        )


# --- where a room lives on the machine ---------------------------------------


def test_rooms_never_share_a_home_but_always_share_their_project_store():
    """The two halves of the same layout, stated together because they pull
    opposite ways and both are load-bearing.

    A room's HOME must be its own — the session's config, transcripts and
    credentials live in it, and two rooms sharing one would share a
    conversation. The packages installed INTO that home must not be: every room
    of a project installs the same lockfile, so a per-room store means a
    physical copy of the same dependency tree per room, which is how 220
    worktrees of one project came to hold 236GB.
    """
    project = uuid.uuid4()
    other = uuid.uuid4()
    room_a, room_b = uuid.uuid4(), uuid.uuid4()

    assert device_home_dir(project, room_a) != device_home_dir(project, room_b)
    assert device_store_dir(project) == device_store_dir(project)
    assert device_store_dir(project) != device_store_dir(other)

    # And the store is not inside either room, or retiring one would take it.
    for room in (room_a, room_b):
        assert not device_store_dir(project).startswith(device_home_dir(project, room))


def test_every_device_work_dir_is_a_topic_scratch_dir():
    pid = uuid.uuid4()
    tid = uuid.uuid4()
    prov = DeviceChannel(hub=FakeHub())
    assert prov._work_dir(pid, tid) == f"$HOME/.cheese/home/{pid}/{tid}/room"


async def test_concurrent_topics_never_share_a_device_home():
    """Two topics of one project get two DIFFERENT device homes, and each
    screen is started pointed at its own."""
    hub = FakeHub()
    project_id = uuid.uuid4()
    topic_a, topic_b = uuid.uuid4(), uuid.uuid4()

    for topic_id in (topic_a, topic_b):
        await _room(hub, project_id=project_id, topic_id=topic_id).ensure()

    homes = [env["CHEESE_HOME"] for env in hub.envs]
    assert str(topic_a) in homes[0]
    assert str(topic_b) in homes[1]
    assert homes[0] != homes[1]


@pytest.mark.parametrize("direct", [False, True])
async def test_every_machine_facing_url_is_the_base_plus_a_route_that_exists(
    monkeypatch, direct
):
    """Each URL handed to a device must be `{public_base}/<a real backend path>`.

    That is the contract `settings.connector_public_base` states — the base maps
    1:1 onto the backend ROOT — and it is a contract precisely because nothing
    downstream reports a violation: an extra path segment makes the CLI 404 with
    「话题不存在」, which looks like the agent doing nothing. It broke exactly
    that way once, when an extra `/api` turned `<origin>/api` into
    `<origin>/api/api`. So the assertion asks the live router whether the
    remainder is a path this app actually serves.
    """
    from starlette.routing import Match

    from app.main import app

    def served(path: str) -> bool:
        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "root_path": "",
            "headers": [],
        }
        # PARTIAL = right path, wrong method — the path exists, which is the
        # only thing under test here.
        return any(r.matches(scope)[0] is not Match.NONE for r in app.routes)

    # The production shape: behind the gateway the base carries the `/api` mount.
    base = "http://127.0.0.1:18080" if direct else "http://cheese.test/api"
    hub = FakeHub()
    room = _room(hub)

    async def api_base(_device_id):
        return base

    monkeypatch.setattr(room.channel, "_device_api_base", api_base)
    await room.ensure()

    env = hub.envs[0] or {}
    assert env["CHEESE_API"] == base
    for key, url in env.items():
        if url.startswith(base) and url != base:
            remainder = url[len(base) :]
            assert served(remainder), (
                f"{key}={url} leaves {remainder!r}, which this app does not serve"
            )


async def test_every_device_gets_the_context_for_task_repository_lookup():
    """The CLI resolves the forge remote from authenticated task metadata."""
    hub = FakeHub()
    room = _room(hub)
    await room.ensure()

    env = hub.envs[0] or {}
    assert env["CHEESE_API"] == "http://cheese.test"
    assert env["CHEESE_PROJECT"] == str(room.arguments["project_id"])
    assert env["CHEESE_TOKEN"] == "tok"
    assert "CHEESE_GIT_REMOTE" not in env
    assert "CHEESE_GIT_BRANCH" not in env
    assert env["CHEESE_TOPIC"] == str(room.arguments["topic_id"])


def test_a_device_is_warned_about_a_box_local_model_endpoint(caplog):
    """Routing the box's turns through the local gateway is what makes spend
    visible — but the same URL means nothing on a machine elsewhere, and the
    failure there is a connection error with no hint why."""
    import logging

    from app.domain.agent.device_provider import (
        _warn_if_model_endpoint_is_box_local,
    )

    with caplog.at_level(logging.ERROR, logger="app.domain.agent.device_provider"):
        _warn_if_model_endpoint_is_box_local(
            {"HTTPS_PROXY": "http://cheese:tok@172.17.0.1:8444"}, "machine-1"
        )
    assert any("only resolves on the backend" in r.getMessage() for r in caplog.records)
    assert any("HTTPS_PROXY" in r.getMessage() for r in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="app.domain.agent.device_provider"):
        _warn_if_model_endpoint_is_box_local(
            {"HTTPS_PROXY": "http://cheese:tok@meter.example:8444"},
            "machine-1",
        )
    assert not caplog.records, "a reachable endpoint must not be flagged"


# --- subscription parity (#325 G2): device turns ride the metering proxy --------
# A device screen gets the one supply there is: fake credential + proxy CA +
# scoped session token, no ANTHROPIC_BASE_URL, no gateway model pin. The
# regression this guards is dev shipping device screens with
# CLAUDE_MODEL=deepseek-chat — users thought they were talking to Claude and
# were not.


def _subscription_settings(monkeypatch, tmp_path) -> str:
    """Point the backend at a readable proxy CA whose text a test can assert on."""
    ca = "-----BEGIN CERTIFICATE-----\nMETERCA\n-----END CERTIFICATE-----\n"
    ca_path = tmp_path / "proxy-ca.pem"
    ca_path.write_text(ca)
    monkeypatch.setattr(settings, "subscription_ca_backend_path", str(ca_path))
    monkeypatch.setattr(settings, "subscription_proxy_host", "172.17.0.1")
    monkeypatch.setattr(settings, "subscription_device_proxy_host", "")
    monkeypatch.setattr(settings, "subscription_proxy_connect_port", 8444)
    return ca


async def _subscription_screen(
    env: dict | None = None,
    model: str | None = None,
) -> tuple[FakeHub, dict, uuid.UUID, uuid.UUID]:
    hub = FakeHub()
    room = _room(
        hub,
        token="hook-token",
        env=env,
        launch=ClaudeLaunch(system_prompt="", model=model),
    )
    await room.ensure()
    return (
        hub,
        hub.envs[0] or {},
        room.arguments["project_id"],
        room.arguments["topic_id"],
    )


async def test_the_launch_environment_is_the_same_whatever_model_is_bound(
    monkeypatch, tmp_path
):
    """启动环境里没有模型。绑定变了，启动环境一个键都不变。"""
    _subscription_settings(monkeypatch, tmp_path)

    _, unbound, _, _ = await _subscription_screen()
    _, subscription_bound, _, _ = await _subscription_screen(model="opus")
    _, pool_bound, _, _ = await _subscription_screen(model="glm-5.2")

    assert set(unbound) == set(subscription_bound) == set(pool_bound)
    for env in (unbound, subscription_bound, pool_bound):
        # CHEESE_MODEL_PROXY says the meter accepts model hosts; it names none.
        assert [k for k in env if "MODEL" in k] == ["CHEESE_MODEL_PROXY"]
        assert "CLAUDE_MODEL" not in env
        assert "ANTHROPIC_BASE_URL" not in env
        assert env["HTTPS_PROXY"]


def test_a_machine_launch_script_never_names_a_model():
    """The env is not the only channel: `claude --model` would put the same
    second declaration on the command line."""
    from app.domain.agent.harness.claude_code.device_launch import launch_holes

    machine = launch_holes(state="$HOME/.cheese/state", system_prompt="你是芝士。")

    assert not [k for k in machine.env if "MODEL" in k], machine.env
    script = "".join(
        (
            machine.command,
            machine.contract,
            machine.configure,
            machine.credentials,
            machine.prepare,
        )
    )
    assert "--model" not in script
    assert "CLAUDE_MODEL" not in script


async def test_subscription_screen_env_has_no_gateway_and_no_real_credential(
    monkeypatch, tmp_path
):
    from app.core.sandbox_auth import scoped_token_claims

    monkeypatch.setattr(settings, "anthropic_auth_token", "UPSTREAM-PROVIDER-KEY")
    _subscription_settings(monkeypatch, tmp_path)
    _hub, env, project, topic = await _subscription_screen()

    # No BASE_URL (it flips the CLI into API-key mode), no gateway key, no
    # deepseek/gateway model pin — the exact env dev observed is impossible.
    assert "ANTHROPIC_BASE_URL" not in env
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert [k for k in env if "MODEL" in k] == ["CHEESE_MODEL_PROXY"]
    assert env["CHEESE_MODEL_PROXY"] == "1"
    assert "UPSTREAM-PROVIDER-KEY" not in repr(env)
    # What the backend hands over is a scoped cheese token the proxy can
    # verify; the Claude login is the host's own and never travels from here.
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    claims = scoped_token_claims(env["CHEESE_CONNECT_TOKEN"])
    assert claims is not None
    assert claims["p"] == str(project) and claims["t"] == str(topic)
    assert env["CHEESE_CONNECT_TOKEN"] != env["CHEESE_TOKEN"]


async def test_subscription_screen_reaches_the_meter_by_connect_proxy(
    monkeypatch, tmp_path
):
    """A bare device process has no --add-host, so the capture is HTTPS_PROXY at
    the meter's CONNECT listener, with the scoped token as the proxy password —
    and NO_PROXY keeps the platform's own wiring (git, CLI) out of it."""
    _subscription_settings(monkeypatch, tmp_path)
    _hub, env, _project, _topic = await _subscription_screen()

    token = env["CHEESE_CONNECT_TOKEN"]
    assert env["HTTPS_PROXY"] == f"http://cheese:{token}@172.17.0.1:8444"
    for key in ("NO_PROXY", "no_proxy"):
        assert "cheese.test" in env[key]
        assert "localhost" in env[key]


async def test_subscription_ca_travels_in_the_launcher_not_as_a_host_path(
    monkeypatch, tmp_path
):
    """The backend's CA path means nothing on the device. The CA BYTES ride the
    shipped launch script, which writes them under the screen's isolated home
    and exports NODE_EXTRA_CA_CERTS itself."""
    ca = _subscription_settings(monkeypatch, tmp_path)
    hub, _env, _project, _topic = await _subscription_screen()

    script = next(s for _a, s in hub.execs if s and "METERCA" in s)
    assert ca.strip() in script
    assert 'export NODE_EXTRA_CA_CERTS="$HOME/.claude/proxy-ca.pem"' in script


async def test_subscription_proxy_token_lives_for_the_session_not_one_hour(
    monkeypatch, tmp_path
):
    """The proxy token is baked into the bare process's env (the HTTPS_PROXY
    CONNECT password), read ONCE at launch and never hot-refreshed while the
    screen is reused across turns. A 1h token
    therefore expires under a still-running agent and the metering proxy 407s
    every later turn. Its exp must span the session."""
    from app.core.sandbox_auth import scoped_token_claims

    _subscription_settings(monkeypatch, tmp_path)
    _hub, env, _project, _topic = await _subscription_screen()

    # The CONNECT credential …
    token = env["CHEESE_CONNECT_TOKEN"]
    assert f"cheese:{token}@" in env["HTTPS_PROXY"]
    claims = scoped_token_claims(token)
    assert claims is not None
    remaining = claims["exp"] - int(time.time())
    assert remaining > 7 * 24 * 3600  # rules out the 3600s per-turn default
    assert SESSION_TOKEN_TTL_S - 300 < remaining <= SESSION_TOKEN_TTL_S + 5
    assert env["NODE_EXTRA_CA_CERTS"] == "$HOME/.claude/proxy-ca.pem"


async def test_subscription_drops_gateway_pins_a_caller_env_carries(
    monkeypatch, tmp_path
):
    """The caller's env is the gateway shape (BASE_URL + model pins). Any of it
    surviving flips the CLI into API-key mode or pins a model the subscription
    does not serve — dropped, not overridden."""
    _subscription_settings(monkeypatch, tmp_path)
    _hub, env, _p, _t = await _subscription_screen(
        env={
            "ANTHROPIC_BASE_URL": "http://cheese.test/llm",
            "CLAUDE_MODEL": "deepseek-chat",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-chat",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-chat",
            "SOME_OTHER": "kept",
        },
    )
    assert "ANTHROPIC_BASE_URL" not in env
    assert "deepseek" not in repr(env)
    assert env["SOME_OTHER"] == "kept"


async def test_the_session_credential_carries_no_model_either(monkeypatch, tmp_path):
    """凭据里签一个型号，等于把启动那一刻的选择带到每一次准入 —— 同样是第二份
    声明，而且是准入唯一读得到的那一份，它会压过卡上的绑定。"""
    from app.core.sandbox_auth import scoped_token_claims

    _subscription_settings(monkeypatch, tmp_path)
    _hub, env, _project, _topic = await _subscription_screen(model="glm-5.2")
    claims = scoped_token_claims(env["CHEESE_CONNECT_TOKEN"])
    assert claims is not None
    assert "m" not in claims
    assert "glm-5.2" not in repr(claims)


async def test_subscription_without_a_readable_ca_fails_loud_not_into_the_gateway(
    monkeypatch, tmp_path
):
    """Falling back to the gateway would silently swap the model — the failure
    #325 G2 exists to kill. A half-configured deployment must say what to fix."""
    _subscription_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "subscription_ca_backend_path", "")

    with pytest.raises(ScreenSetupError, match="SUBSCRIPTION_CA_BACKEND_PATH"):
        await _subscription_screen()


async def test_a_tunnel_screen_is_handed_no_loopback_port(monkeypatch, tmp_path):
    """The helper's port is the machine's: the kernel picks a free one when the
    helper binds, and the launcher exports what it got. A port named here would be
    a guess about another machine's free ports — the guess that put two rooms on
    one helper, where the second room's `claude` spent the first room's
    credential."""
    _subscription_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(
        settings, "subscription_tunnel_url", "wss://gateway.example/llm/tunnel"
    )

    _hub, env, _project, _topic = await _subscription_screen()

    assert env["CHEESE_TUNNEL_URL"] == "wss://gateway.example/llm/tunnel"
    assert "HTTPS_PROXY" not in env
    assert not any("//127.0.0.1:" in value for value in env.values())
    # The meter's allowlist still applies to the tunnelled route.
    assert env["CHEESE_MODEL_PROXY"] == "1"


@pytest.mark.parametrize("cloud", [True, False])
async def test_a_cloud_machine_tunnels_through_its_loopback_forward(
    monkeypatch, tmp_path, cloud
):
    """A MicroCloud guest is isolated from private networks, so a cloud machine
    whose backend is the loopback forward must find the model tunnel there too,
    not at the deployment's private gateway. A device someone enrolled keeps
    the configured URL."""
    import ipaddress
    from urllib.parse import urlsplit

    from app.domain.agent import machine_address
    from app.domain.device.supply import Supply

    _subscription_settings(monkeypatch, tmp_path)
    configured = "wss://gateway.internal.example/api/llm/tunnel"
    monkeypatch.setattr(settings, "subscription_tunnel_url", configured)
    monkeypatch.setattr(settings, "agent_session_device_id", None)
    machine = SimpleNamespace(
        supply=Supply.cloud if cloud else Supply.self_hosted,
        cloud_control_private=cloud,
    )

    class Session:
        async def get(self, _row, _device_id):
            return machine

    async def api_base(self, device_id):
        return await machine_address.device_api_base(
            Session(), device_id, self._public_base
        )

    monkeypatch.setattr(DeviceChannel, "_device_api_base", api_base)
    _hub, env, _project, _topic = await _subscription_screen()

    tunnel = urlsplit(env["CHEESE_TUNNEL_URL"])
    if cloud:
        api = urlsplit(env["CHEESE_API"])
        assert ipaddress.ip_address(tunnel.hostname).is_loopback
        assert (tunnel.hostname, tunnel.port) == (api.hostname, api.port)
        assert (tunnel.scheme, tunnel.path) == ("ws", "/llm/tunnel")
    else:
        assert env["CHEESE_TUNNEL_URL"] == configured
    assert "HTTPS_PROXY" not in env


def test_every_backend_in_the_deployed_pool_gets_the_same_liveness_policy():
    """How long a turn may talk without working, how long an input may sit
    unread, and the ceiling over both are decided once for the deployment. A
    transport that drifted from another would end the same turn at a different
    moment depending on where it ran."""
    from app.domain.agent.compute import build_compute_pool

    pool = build_compute_pool()
    for name in pool.machines():
        backend = pool.select(provider_id=name)
        assert backend.no_progress_s == settings.agent_no_progress_s
        assert backend.unread_grace_s == settings.agent_unread_grace_s
        assert backend.hard_ceiling_s == settings.agent_turn_hard_ceiling_s


# --- credential freshness is part of the reuse decision (#388 缺陷二) ----------
# A bare `claude` reads its credential once and never re-reads it, and a reused
# screen is only reasserted, never relaunched — so a live process on a dead
# credential is 401/407'd every turn while its runner reports it healthy. The
# backend stamps the credential's expiry; it gates reuse too.


async def test_each_launch_ships_a_fresh_now_based_token_expiry():
    """The device screen env carries ``CHEESE_TOKEN_EXPIRES`` — the expiry the
    launcher stamps against the session it creates, so a later launch retires a
    session whose baked credential has DIED instead of adopting the corpse. It is
    minted from NOW: strictly in the future and no further out than a session."""
    hub = FakeHub()
    before = int(time.time())
    await _room(hub).ensure()

    raw = (hub.envs[0] or {}).get("CHEESE_TOKEN_EXPIRES")
    assert raw is not None, "the launch env must carry the credential expiry"
    assert before < int(raw) <= int(time.time()) + SESSION_TOKEN_TTL_S + 5


async def test_a_reused_screen_whose_birth_credential_expired_is_retired_not_adopted():
    hub = FakeHub()
    room = _room(hub)

    first = await room.ensure()
    assert first.sid == "s1"
    assert isinstance(first.credential_expires, int)
    assert first.credential_expires > int(time.time())

    # The credential this screen was BORN with has since died.
    first.credential_expires = int(time.time()) - 1
    second = await room.ensure()

    assert hub.closed == ["s1"]
    assert hub.reasserted == []
    assert second.sid == "s2"
    assert second.credential_expires > int(time.time())


async def test_a_reused_screen_with_a_live_credential_is_adopted():
    hub = FakeHub()
    room = _room(hub)

    first = await room.ensure()
    second = await room.ensure()

    assert second is first
    assert hub.reasserted == ["s1"]
    assert hub.closed == []
    assert [s.sid for s in hub.opened] == ["s1"]


def test_topic_credential_expiry_reads_the_live_screens_stamp():
    """The credential expiry of a topic's LIVE device screen, or None when it
    has none online / unrecorded."""
    from app.domain.agent.device_provider import topic_credential_expiry

    tid = uuid.uuid4()

    class Hub:
        def __init__(self, screens: dict, online: set) -> None:
            self._screens = screens
            self._online = online

        def screens_for_topic(self, topic_id):
            return list(self._screens.get(topic_id, []))

        def is_online(self, device_id):
            return device_id in self._online

    def _screen_with(device_id: str, exp: int | None) -> HubScreen:
        s = HubScreen(
            sid="s",
            device_id=device_id,
            command=[],
            token="t",
            agent_user_id=1,
            agent_handle="a",
            topic_id=tid,
        )
        s.credential_expires = exp
        return s

    hub = Hub({tid: [_screen_with("dev1", 12345)]}, {"dev1"})
    assert topic_credential_expiry(tid, hub=hub) == 12345  # type: ignore[arg-type]
    assert topic_credential_expiry(tid, hub=Hub({}, set())) is None  # type: ignore[arg-type]
    hub_off = Hub({tid: [_screen_with("devX", 999)]}, set())
    assert topic_credential_expiry(tid, hub=hub_off) is None  # type: ignore[arg-type]
    hub_none = Hub({tid: [_screen_with("dev1", None)]}, {"dev1"})
    assert topic_credential_expiry(tid, hub=hub_none) is None  # type: ignore[arg-type]


# --- what a live session was started with -------------------------------------


async def test_agent_config_change_replaces_screen_at_next_launch(caplog):
    hub = FakeHub()
    room = _room(hub, launch=ClaudeLaunch(system_prompt="", model="requested-model"))

    first = await room.ensure(env={"CHEESE_AGENT_CONFIG": "original"})
    assert await room.ensure(env={"CHEESE_AGENT_CONFIG": "original"}) is first
    with caplog.at_level("INFO"):
        second = await room.ensure(env={"CHEESE_AGENT_CONFIG": "edited"})

    assert second.sid != first.sid
    assert hub.closed == [first.sid]
    assert second.agent_configuration != first.agent_configuration
    (line,) = _retired(caplog)
    assert "reason=agent_configuration_changed" in line
    # And the machine is told what it is, so a backend that restarts can
    # ask the connector rather than guess.
    assert hub.envs[-1]["CHEESE_AGENT_CONFIG"] == second.agent_configuration


async def test_config_change_waits_for_every_task_the_session_is_running(caplog):
    """Closing the session ends what it is doing — a workflow, a subagent, a
    command it put in the background — so the relaunch waits for the runner to
    report nothing running. Meanwhile the room's turns run on the session it
    has, whichever harness the new launch would start."""
    hub = FakeHub()
    room = _room(hub)
    first = await room.ensure(env={"CHEESE_AGENT_CONFIG": "original"})

    with caplog.at_level("INFO"):
        for task_type in ("local_workflow", "local_agent", "local_bash"):
            hub.ping = {"alive": True, "working": False, "tasks": {"t": task_type}}
            assert await room.ensure(env={"CHEESE_AGENT_CONFIG": "edited"}) is first
        assert (
            await room.ensure(
                env={"CHEESE_AGENT_CONFIG": "edited"},
                launch=ClaudeLaunch(system_prompt="another prompt"),
            )
            is first
        )
    assert hub.closed == [] and hub.opened == [first]
    assert _retired(caplog) == []

    hub.ping = {"alive": True, "working": False, "tasks": {}}
    second = await room.ensure(env={"CHEESE_AGENT_CONFIG": "edited"})
    assert second.sid != first.sid
    assert hub.closed == [first.sid]


async def test_config_change_does_not_cut_a_turn_short():
    """A session can be in a turn of its own when the room's next one arrives:
    a background task finished and woke it."""
    hub = FakeHub()
    room = _room(hub)
    first = await room.ensure(env={"CHEESE_AGENT_CONFIG": "original"})
    hub.ping = {"alive": True, "working": True, "tasks": {}}

    assert await room.ensure(env={"CHEESE_AGENT_CONFIG": "edited"}) is first
    assert hub.closed == []

    hub.ping = {"alive": True, "working": False, "tasks": {}}
    assert (await room.ensure(env={"CHEESE_AGENT_CONFIG": "edited"})).sid != first.sid


async def test_a_session_left_running_still_says_what_it_was_started_with():
    """A relaunch put off by a running task stays owed. The machine keeps the
    identity a session reports, and a respawn under the same sid runs the
    launcher already on disk, so the running session must go on reporting
    the launch it came from — or a backend restart would read it as current
    and never replace it."""
    hub = FakeHub()
    room = _room(hub)
    first = await room.ensure(env={"CHEESE_AGENT_CONFIG": "original"})
    hub.ping = {"alive": True, "working": False, "tasks": {"b": "local_bash"}}

    await room.ensure(env={"CHEESE_AGENT_CONFIG": "edited"})

    env = hub.reasserted_envs[first.sid] or {}
    assert env["CHEESE_AGENT_CONFIG"] == first.agent_configuration


async def test_a_config_change_on_a_session_nobody_can_ask_replaces_it():
    """A runner that cannot be asked has nothing left running to lose."""
    hub = FakeHub()
    room = _room(hub)
    first = await room.ensure(env={"CHEESE_AGENT_CONFIG": "original"})
    hub.ping = DeviceCallError("dial unix: refused")

    second = await room.ensure(env={"CHEESE_AGENT_CONFIG": "edited"})

    assert second.sid != first.sid
    assert hub.closed == [first.sid]


async def test_a_changed_harness_argv_replaces_the_screen_that_has_the_old_one():
    """A running CLI holds the argv it was started with, and nothing can hand it
    new ones — so a change to what the backend would start today has to close it.
    Handing the room's work to an executor is decided by the harness out of what
    the room is, not by the caller's config hash."""
    hub = FakeHub()
    room = _room(hub, env={"CHEESE_AGENT_CONFIG": "unchanged"})

    first = await room.ensure()
    assert await room.ensure() is first

    second = await room.ensure(
        env={
            "CHEESE_AGENT_CONFIG": "unchanged",
            "CHEESE_EXECUTION_TARGET": json.dumps({"machine": "m1", "workdir": "/w"}),
        }
    )

    assert second.sid != first.sid
    assert hub.closed == [first.sid]


async def test_a_screen_installed_under_another_root_is_not_reused(monkeypatch):
    """What makes a move of the platform's own directory reach the rooms already
    running. Their files are at the old place and their processes are pointed
    there; a deploy that changed the root and reused them would leave each room
    half under each."""
    hub = FakeHub()
    room = _room(hub, env={"CHEESE_AGENT_CONFIG": "unchanged"})

    first = await room.ensure()
    monkeypatch.setattr(screen_identity, "footprint_root", lambda: ".somewhere-else")

    assert (await room.ensure()).sid != first.sid
    assert hub.closed == [first.sid]


def _deploy_helpers(monkeypatch, **changed: str) -> None:
    """A deploy whose remote-execution helpers differ from what is running."""
    current = resident_release.sources()
    monkeypatch.setattr(resident_release, "sources", lambda: {**current, **changed})


async def test_a_session_started_with_other_launch_only_helpers_is_relaunched(
    monkeypatch, caplog
):
    """`client.prepare` writes the shell prefix, the launch environment, the
    argv and the MCP config once, when the session starts, and nothing writes
    them again: a release that swaps `client.py` on disk leaves the session
    running the prefix it was born with. So a new `client.py` is a new launch.
    The replacement is offered the room's conversation, which the runner
    resumes when the transcript is on disk (test_resume_across_screens)."""
    hub = FakeHub()
    room = _executor_room(hub)
    first = await room.ensure()
    assert await room.ensure() is first
    _deploy_helpers(
        monkeypatch,
        **{"client.py": resident_release.sources()["client.py"] + "\n# next\n"},
    )

    with caplog.at_level("INFO"):
        replacement = await room.ensure()
        again = await room.ensure()

    assert replacement.sid != first.sid and again is replacement
    assert hub.closed == [first.sid]
    (line,) = _retired(caplog)
    assert "reason=agent_configuration_changed" in line
    assert (hub.envs[-1] or {}).get("CHEESE_RESUME_SESSION") == "conversation"


async def test_a_helper_the_release_does_not_know_is_a_new_launch(monkeypatch):
    """A helper added later is written by the launcher, and nothing in a
    running session reads it again until someone shows a release can."""
    hub = FakeHub()
    room = _executor_room(hub)
    first = await room.ensure()
    _deploy_helpers(monkeypatch, **{"shell_view.py": "VIEW = 1\n"})

    assert (await room.ensure()).sid != first.sid
    assert hub.closed == [first.sid]


async def test_a_helper_the_release_reloads_is_released_in_place(monkeypatch):
    """The plugin module is loaded again by `/reload-plugins`, so a change to
    it reaches the running session without ending it."""
    hub = FakeHub()
    room = _executor_room(hub)
    first = await room.ensure()
    _deploy_helpers(
        monkeypatch,
        **{"proxy.js": resident_release.sources()["proxy.js"] + "\n// next\n"},
    )

    assert await room.ensure() is first
    assert hub.closed == []
    assert hub.commands() == ["/reload-plugins"]


async def test_a_launch_only_change_waits_for_a_background_command(monkeypatch):
    """A command the session put in the background dies with it."""
    hub = FakeHub()
    room = _executor_room(hub)
    first = await room.ensure()
    _deploy_helpers(
        monkeypatch,
        **{"client.py": resident_release.sources()["client.py"] + "\n# next\n"},
    )
    hub.ping = {"alive": True, "working": False, "tasks": {"bash-1": "local_bash"}}

    assert await room.ensure() is first
    assert hub.closed == []

    hub.ping = {"alive": True, "working": False, "tasks": {}}
    assert (await room.ensure()).sid != first.sid
    assert hub.closed == [first.sid]


async def test_a_deferred_relaunch_does_not_release_helpers_into_the_old_session(
    monkeypatch,
):
    """A busy old process keeps the helper files it was started with."""
    hub = FakeHub()
    room = _executor_room(hub)
    first = await room.ensure()
    _deploy_helpers(
        monkeypatch,
        **{"client.py": resident_release.sources()["client.py"] + "\n# next\n"},
    )
    hub.ping = {"alive": True, "working": True, "tasks": {}}
    hub.execs.clear()

    assert await room.ensure() is first

    assert hub.closed == []
    assert not any(
        argv == ["python3", "-"] and _release_step(stdin or "") == "stage"
        for argv, stdin in hub.execs
    )


# --- seats: a room's teammates have one session each -------------------------


class _MachineHub(FakeHub):
    """A machine whose screens run the launcher file each of them names.

    The one thing ``FakeHub`` does not model: a screen runs the file at the path
    its command names, ONCE, at birth — and that file says which state directory
    its runner keeps (``CLAUDE_STATE``). So a call addressed to a state no living
    screen runs is refused the way a connector refuses it: the socket is not
    there. That is what a turn whose handle points at another seat's state gets,
    and it is why this model is what the seat tests below need.
    """

    def __init__(self) -> None:
        super().__init__()
        self.scripts: dict[str, str] = {}
        self.running: dict[str, str] = {}
        self.screen_state: dict[str, str] = {}

    async def exec(
        self, device_id, argv, *, cwd=None, env=None, timeout=60, stdin=None
    ) -> dict:
        result = await super().exec(
            device_id, argv, cwd=cwd, env=env, timeout=timeout, stdin=stdin
        )
        if stdin:
            for path in re.findall(r'cat > "([^"]+)"', " ".join(argv)):
                self.scripts[path] = stdin
        return result

    async def open_screen(self, device_id, command, **kw) -> HubScreen:
        screen = await super().open_screen(device_id, command, **kw)
        state = _launcher_state(self.scripts.get(_launched_file(command), ""))
        if state:
            self.running[state] = screen.sid
            self.screen_state[screen.sid] = state
        return screen

    async def close_screen(self, device_id, sid) -> bool:
        self.running = {
            state: holder for state, holder in self.running.items() if holder != sid
        }
        self.screen_state.pop(sid, None)
        return await super().close_screen(device_id, sid)

    async def call_executor(self, device_id, state, method, params, timeout=None):
        if state not in self.running:
            raise DeviceCallError(
                "dial unix /tmp/cheese-execution-1000-"
                f"{uuid.uuid4().hex[:24]}.sock: connect: no such file or directory"
            )
        return await super().call_executor(device_id, state, method, params, timeout)


def _launched_file(command: list[str]) -> str:
    """Which launcher file a screen runs: what its command execs."""
    return command[2].split('"')[1]


def _launcher_state(script: str) -> str:
    """Where the runner a launcher starts keeps its state."""
    found = re.search(r"^CLAUDE_STATE=(.*)$", script, re.MULTILINE)
    return found.group(1).strip().strip("'") if found else ""


def _seat_state(room, seat: str) -> str:
    """The state directory this seat's runner keeps, as the connector resolves it."""
    return machine_launcher.state_dir(
        room.arguments["project_id"], room.arguments["topic_id"], CLAUDE_CODE, seat
    )


async def test_a_room_mate_starting_a_turn_leaves_the_other_session_running():
    """一个话题一个容器，会话按座位分开（docs/manual/dev/turn.md #seats-session）：
    两位队友在同一台机器上各有一个会话，开一位的不会停掉另一位正在跑的。

    What this looks like when it is wrong is the other teammate: its turn dies
    with 「Claude Code session process exited」, and the turn that closed it holds
    a handle to its OWN state, where no runner was ever started — so every call
    it makes is refused with `no such file or directory`.
    """
    machine = _MachineHub()
    room = _room(machine, agent_handle="cheese-a")

    first = await room.ensure()
    second = await room.ensure(agent_handle="cheese-b")

    assert machine.closed == [], "开第二位队友的会话时关掉了第一位正在跑的会话"
    assert (first.agent_handle, second.agent_handle) == ("cheese-a", "cheese-b")
    assert first.sid != second.sid
    for seat, screen in (("cheese-a", first), ("cheese-b", second)):
        state = _seat_state(room, seat)
        # This seat's session runs on its own state, and that is the socket its
        # next call — the one that answers the turn — is dialled at.
        assert machine.screen_state[screen.sid] == state
        assert await room.channel._runner("dev1", state, "ping") is not None


async def test_a_teammate_reuses_its_own_screen_and_not_the_room_mates():
    """第二位队友的下一轮续的是自己的会话，不是同房间另一位的那块屏幕。"""
    machine = _MachineHub()
    room = _room(machine, agent_handle="cheese-a")

    mine = await room.ensure()
    theirs = await room.ensure(agent_handle="cheese-b")

    again = await room.ensure(agent_handle="cheese-b")

    assert again.sid == theirs.sid, "第二位队友的下一轮又开了一个会话"
    assert machine.reasserted[-1] == theirs.sid
    assert mine.sid not in machine.closed
    assert len(machine.opened) == 2


async def test_recovery_adopts_each_seat_s_screen_as_its_own(monkeypatch):
    """后端重启后每个座位接回自己那个会话（#seats-session）：机器上活着的两块屏幕
    各按自己的座位认领——认成同一个座位，两位队友就会去抢对方那块。"""
    from unittest.mock import AsyncMock

    from app.domain.agent.device_hub import DeviceHub
    from app.domain.identity.services import IdentityService

    project_id, topic_id, resource_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    room = SimpleNamespace(id=topic_id, project_id=project_id, resource_id=resource_id)
    metadata = {
        "sid": "seat-a",
        "screen": "token-a",
        "command": ["claude"],
        "env": {
            "CHEESE_PROJECT": str(project_id),
            "CHEESE_TOPIC": str(topic_id),
            "CHEESE_RESOURCE_ID": str(resource_id),
            "CHEESE_AUTHOR": "cheese-a",
        },
    }
    other = {
        **metadata,
        "sid": "seat-b",
        "screen": "token-b",
        "env": {**metadata["env"], "CHEESE_AUTHOR": "cheese-b"},
    }
    hub = DeviceHub()
    sent = []

    class Transport:
        async def send_json(self, msg):
            sent.append(msg)
            if msg["t"] == "session.list":
                await hub.on_device_message(
                    "center",
                    {
                        "t": "session.result",
                        "id": msg["id"],
                        "value": [metadata, other],
                    },
                )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def commit(self):
            pass

    async def room_agent(self, topic):
        # The roster's default seat answers for the room; the room-mate is the
        # one the adoption has to tell apart from it.
        return SimpleNamespace(id=1, username="cheese-a")

    async def user(session, handle):
        return SimpleNamespace(id=2, username=handle)

    monkeypatch.setattr(IdentityService, "ensure_room_agent_user", room_agent)
    monkeypatch.setattr(device_provider, "user_by_handle", user)
    monkeypatch.setattr(
        "app.domain.topic.services.TopicService.get", AsyncMock(return_value=room)
    )
    await hub.attach_device("center", Transport())
    channel = DeviceChannel(hub=hub, session_factory=Session)
    await channel.restore_screens([(project_id, topic_id, "center")])

    adopted = {screen.sid: screen for screen in hub.all_online_screens()}
    assert set(adopted) == {"seat-a", "seat-b"}
    assert adopted["seat-a"].agent_handle == "cheese-a"
    assert adopted["seat-b"].agent_handle == "cheese-b"
    assert adopted["seat-b"].agent_user_id == 2
