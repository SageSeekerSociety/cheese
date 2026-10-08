"""A sandboxed session's network, limits and system calls (#2320), and the
code outside its sandbox that reads what the session can write.

The sandbox's network and limits are set up by root (`sandbox_host.py`
through `sudo -n`), so the tests that start one need Linux, bubblewrap and a
passwordless sudo, as CI's runner has; elsewhere they are skipped. They change
the machine's iptables rules and cgroups the way a Cloud machine's are
changed, and CI turns them on (`CHEESE_TEST_SANDBOX_HOST`) rather than every
Linux checkout that happens to allow sudo. The rest holds anywhere.
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.agent import environment_runner
from app.domain.agent import resource_cleanup as cleanup
from app.domain.agent.harness.claude_code.remote_execution import (
    bootstrap,
    runtime,
    sandbox_host,
)
from app.domain.agent.harness.claude_code.remote_execution.launch import payload_for

HELPER = Path(sandbox_host.__file__)
RUNNER = Path(environment_runner.__file__)


def _sandbox_host_runs() -> bool:
    if os.environ.get("CHEESE_TEST_SANDBOX_HOST") != "1":
        return False
    if not sys.platform.startswith("linux") or shutil.which("bwrap") is None:
        return False
    return subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0


with_sandbox_host = pytest.mark.skipif(
    not _sandbox_host_runs(),
    reason="needs Linux, bubblewrap and sudo -n (CHEESE_TEST_SANDBOX_HOST=1)",
)


class _Answer(BaseHTTPRequestHandler):
    def do_GET(self):
        body = self.server.body  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


@pytest.fixture
def machine_service():
    """A server on the machine's loopback answering `body`, by port."""
    servers = []

    def serve(body: bytes) -> int:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Answer)
        server.body = body  # type: ignore[attr-defined]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_address[1]

    yield serve
    for server in servers:
        server.shutdown()


class Room:
    """One sandboxed session's executor on this machine, started by the
    install the backend sends."""

    def __init__(self, owner: Path, api: str, capsys, env: dict | None = None):
        self.project, self.resource = uuid.uuid4(), uuid.uuid4()
        self.home = owner / ".cheese/home" / str(self.project) / str(self.resource)
        self.state = self.home / ".cheese/executor"
        payload = payload_for(
            self.project,
            self.resource,
            {"CHEESE_API": api, "CHEESE_TOKEN": "test", **(env or {})},
            sandbox=True,
            platform_machine=True,
        )
        try:
            bootstrap.configure(payload)
        except RuntimeError:
            log = self.home / ".cheese/executor-bootstrap.log"
            pytest.fail(log.read_text() if log.exists() else "no executor log")
        self.release = Path(json.loads(capsys.readouterr().out)["release"])

    def bash(self, command: str) -> dict:
        answer = runtime.request(
            self.state,
            "invoke",
            {"id": uuid.uuid4().hex, "tool": "Bash", "args": {"command": command}},
        )
        assert "error" not in answer, answer
        return answer["value"]

    def python(self, program: str) -> str:
        return self.bash(f"python3 - <<'PROGRAM'\n{program}\nPROGRAM")["stdout"]

    def address(self) -> str:
        return self.python(
            "import socket; s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
            "s.connect(('192.0.2.1', 9)); print(s.getsockname()[0])"
        ).strip()

    def teardown(self):
        cleanup.stop_executor(self.home, str(self.resource))


@pytest.fixture
def rooms(tmp_path, monkeypatch, capsys):
    owner = tmp_path / "owner"
    monkeypatch.setenv("HOME", str(owner))
    monkeypatch.setattr(bootstrap, "binary", lambda *_: sys.executable)
    started: list[Room] = []

    def start(api: str, env: dict | None = None) -> Room:
        room = Room(owner, api, capsys, env)
        started.append(room)
        return room

    yield start
    for room in started:
        room.teardown()


# The sandbox's firewall drops what it refuses rather than rejecting it, so each
# dial expected to be unreachable lasts its whole timeout. What a dial expects to
# reach is a service on this machine's loopback, which answers in milliseconds.
DIAL = """
import socket, urllib.request
def dial(host, port):
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=1) as r:
            return r.read().decode()
    except Exception as error:
        return "unreachable"
"""


def _machine_address() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]


@with_sandbox_host
def test_a_sandbox_reaches_the_backend_on_loopback_and_nothing_else_here(
    rooms, machine_service
):
    """A machine whose backend is a loopback forward is reached from the
    sandbox at the same address; nothing else the machine listens on is, by
    any of its addresses, and the sandbox has no IPv6 to go round that."""
    api = machine_service(b"backend")
    other = machine_service(b"something of the machine's")
    room = rooms(f"http://127.0.0.1:{api}")
    gateway = room.bash("ip route | awk '/default/ {print $3}'")["stdout"].strip()

    answers = json.loads(
        room.python(
            DIAL
            + "import json; print(json.dumps(["
            + f"dial('127.0.0.1', {api}), dial('127.0.0.1', {other}),"
            + f"dial('{gateway}', {other}), dial('{_machine_address()}', {other})]))"
        )
    )

    assert answers == ["backend", "unreachable", "unreachable", "unreachable"]
    assert room.bash("cat /proc/net/if_inet6 | wc -l")["stdout"].strip() == "0"


@with_sandbox_host
def test_a_sandbox_reaches_the_site_by_its_name_on_the_machines_forward(
    rooms, machine_service
):
    """A machine that answers the deployment's site on a loopback forward
    has its sandboxes resolve the site's name there, on the site's own port,
    so a browser in the room keeps the public origin and never leaves the
    machine for it. Other names still resolve as the machine resolves them."""
    api = machine_service(b"backend")
    site = machine_service(b"site")
    room = rooms(
        f"http://127.0.0.1:{api}", {"CHEESE_SITE_FORWARD": f"site.invalid:{site}"}
    )

    answers = json.loads(
        room.python(
            DIAL
            + "import json; print(json.dumps(["
            + f"dial('site.invalid', 443), dial('127.0.0.1', {site}),"
            + "dial('127.0.0.1', 443), socket.gethostbyname('localhost')]))"
        )
    )

    assert answers == ["site", "unreachable", "unreachable", "127.0.0.1"]


@with_sandbox_host
def test_sandboxes_cannot_reach_each_other(rooms, machine_service):
    api = f"http://127.0.0.1:{machine_service(b'backend')}"
    first, second = rooms(api), rooms(api)
    first.bash(
        "setsid nohup python3 -m http.server 8000 >/dev/null 2>&1 </dev/null &"
        " for i in $(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/8000) 2>/dev/null"
        " && break; sleep 0.1; done"
    )
    assert first.python(DIAL + "print(dial('127.0.0.1', 8000))").strip() != (
        "unreachable"
    )

    assert second.python(DIAL + f"print(dial('{first.address()}', 8000))").strip() == (
        "unreachable"
    )


@with_sandbox_host
def test_a_sandbox_over_its_memory_is_killed_and_the_machine_is_not(
    rooms, machine_service, monkeypatch
):
    monkeypatch.setattr(settings, "cloud_sandbox_memory_mb", 256)
    room = rooms(f"http://127.0.0.1:{machine_service(b'backend')}")

    result = room.bash(
        "python3 -c 'b = bytearray(512 * 1024 * 1024)\n"
        "for i in range(0, len(b), 4096): b[i] = 1'; echo exit=$?"
    )

    assert "exit=137" in result["stdout"], result
    assert runtime.request(room.state, "ping")


@with_sandbox_host
def test_the_sandboxes_together_leave_the_machine_memory_of_its_own(
    rooms, machine_service
):
    rooms(f"http://127.0.0.1:{machine_service(b'backend')}")
    meminfo = Path("/proc/meminfo").read_text()
    total = int(meminfo.split("MemTotal:")[1].split()[0]) * 1024

    held = int((sandbox_host.CGROUP / "memory.max").read_text())

    assert held <= total - max(1 << 30, total // 4)


@pytest.mark.parametrize(("total_gib", "kept_gib"), [(4, 1), (8, 2), (16, 4), (2, 1)])
def test_a_machine_keeps_a_quarter_of_its_memory_and_at_least_a_gib(
    total_gib, kept_gib
):
    total = total_gib << 30

    assert total - sandbox_host.sandboxes_memory(total) == kept_gib << 30


@with_sandbox_host
def test_a_sandbox_may_not_trace_nest_namespaces_or_use_io_uring(
    rooms, machine_service
):
    room = rooms(f"http://127.0.0.1:{machine_service(b'backend')}")

    errors = room.python(
        "import ctypes, os\n"
        "libc = ctypes.CDLL(None, use_errno=True)\n"
        "def errno(call):\n"
        "    ctypes.set_errno(0); call(); return ctypes.get_errno()\n"
        "nr = {'x86_64': 425, 'aarch64': 425}[os.uname().machine]\n"
        "print(errno(lambda: libc.ptrace(0, 0, 0, 0)),"
        " errno(lambda: libc.unshare(0x10000000)),"
        " errno(lambda: libc.syscall(nr, 8, 0)))"
    ).split()

    assert errors == ["1", "1", "38"]
    assert room.bash("echo ok")["stdout"].strip() == "ok"


@with_sandbox_host
def test_the_teardown_ends_everything_a_sandbox_started(rooms, machine_service):
    room = rooms(f"http://127.0.0.1:{machine_service(b'backend')}")
    room.bash("setsid nohup sleep 600 >/dev/null 2>&1 </dev/null &")
    group = sandbox_host.CGROUP / str(room.resource)
    assert group.is_dir()

    room.teardown()

    assert not group.exists()
    with pytest.raises(OSError):
        runtime.request(room.state, "ping")


def _sandboxed_home(owner: Path, release: Path | None = None) -> Path:
    """A room's home on a machine that recorded it as sandboxed, started from
    `release` (one that does not exist when not given)."""
    project, resource = str(uuid.uuid4()), str(uuid.uuid4())
    home = owner / ".cheese/home" / project / resource
    (home / ".cheese").mkdir(parents=True)
    marker = owner / ".cheese/sandboxes" / project / resource
    marker.parent.mkdir(parents=True)
    marker.write_text(str(release or owner / "release"))
    return home


def _run_runner(
    home: Path, action: str, runner: Path = RUNNER
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(runner), action],
        env={**os.environ, "HOME": str(home), "CHEESE_SANDBOXED": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("action", ["reset", "cancel"])
def test_a_sandboxed_rooms_status_cannot_have_a_process_outside_it_signalled(
    tmp_path, action
):
    """A sandboxed room writes its own environment status. One naming a
    process of the machine's — here a process that is not in the room's
    sandbox — gets that process no signal from the reset or the cancel run
    outside the sandbox."""
    home = _sandboxed_home(tmp_path)
    bystander = subprocess.Popen(["sleep", "60"])
    try:
        directory = home / ".cheese-environment"
        directory.mkdir()
        environment_runner.write_json(
            directory / "status.json",
            {
                "state": "ready" if action == "reset" else "preparing",
                "pid": bystander.pid,
                "pid_namespace": environment_runner.pid_namespace(),
                "process_identity": environment_runner.process_identity(bystander.pid),
            },
        )

        _run_runner(home, action)

        assert bystander.poll() is None
    finally:
        bystander.send_signal(signal.SIGKILL)
        bystander.wait()


def test_an_environment_log_is_read_from_the_rooms_own_directory_only(tmp_path):
    """The status a room writes names the log to show with it. A name that
    leads out of the room's environment directory, or a link there, shows
    nothing of what is outside."""
    credential = tmp_path / "config.json"
    credential.write_text("the machine identity")
    directory = tmp_path / "environment"
    directory.mkdir()
    (directory / "linked.log").symlink_to(credential)
    for name in (str(credential), "../config.json", "linked.log"):
        environment_runner.write_json(
            directory / "status.json", {"state": "failed", "log_file": name}
        )

        status = environment_runner.read_status(directory)

        assert "the machine identity" not in json.dumps(status), name


def _unsandboxed_install(tmp_path, monkeypatch):
    owner = tmp_path / "owner"
    monkeypatch.setenv("HOME", str(owner))
    monkeypatch.setattr(bootstrap, "binary", lambda *_: sys.executable)
    project, resource = uuid.uuid4(), uuid.uuid4()
    home = owner / ".cheese/home" / str(project) / str(resource)
    (home / ".cheese").mkdir(parents=True)
    (home / ".claude").mkdir()
    outside = owner / "outside"
    outside.mkdir()
    payload = payload_for(
        project,
        resource,
        {"CHEESE_API": "http://127.0.0.1:1", "CHEESE_TOKEN": "test"},
        sandbox=False,
        platform_machine=False,
    )
    return home, outside, payload


def test_the_install_plants_no_skill_through_a_link_in_the_room(tmp_path, monkeypatch):
    """The install writes the room's skills, and removes the ones no longer
    shipped, from outside its sandbox. A link the room left where they go
    reaches none of the owner's files: nothing is written there, and nothing
    the link's target holds is removed for a skill the room's own record
    names."""
    home, outside, payload = _unsandboxed_install(tmp_path, monkeypatch)
    (outside / "keep").mkdir()
    (outside / "keep/file").write_text("the owner's")
    (outside / ".cheese-project-skills.json").write_text(json.dumps(["keep"]))
    (home / ".claude/skills").symlink_to(outside)

    with pytest.raises(OSError):
        bootstrap.configure(payload)

    assert (outside / "keep/file").read_text() == "the owner's"
    assert sorted(path.name for path in outside.iterdir()) == [
        ".cheese-project-skills.json",
        "keep",
    ]


def test_the_install_writes_the_preview_token_into_the_room_not_through_a_link(
    tmp_path, monkeypatch, capsys
):
    home, outside, payload = _unsandboxed_install(tmp_path, monkeypatch)
    token = home / ".cheese/cheese-preview.token"
    token.symlink_to(outside / "authorized_keys")

    bootstrap.configure(payload)
    state = Path(json.loads(capsys.readouterr().out)["state"])
    try:
        assert not (outside / "authorized_keys").exists()
        assert not token.is_symlink() and token.read_text() == "test"
    finally:
        stopped = subprocess.run(
            [sys.executable, str(runtime.__file__), "stop", "--state", str(state)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert stopped.returncode == 0, stopped.stderr


@pytest.mark.parametrize(
    "linked", [".cheese/executor", ".cheese/remote-execution", ".cheese-environment"]
)
def test_the_install_writes_no_executor_state_through_a_link_in_the_room(
    tmp_path, monkeypatch, linked
):
    """The install writes the executor's config and environment, the marker
    naming the room's executor, the release entrypoints and the environment
    config into the room's home from outside its sandbox. A directory the room
    replaced with a link to another room's gets none of it, and no executor
    starts."""
    home, outside, _ = _unsandboxed_install(tmp_path, monkeypatch)
    environment = {
        "revision": "linked",
        "variables": {},
        "setup_script": "true",
        "startup_script": "",
    }
    payload = payload_for(
        uuid.UUID(home.parent.name),
        uuid.UUID(home.name),
        {
            "CHEESE_API": "http://127.0.0.1:1",
            "CHEESE_TOKEN": "test",
            "CHEESE_ENVIRONMENT": json.dumps(environment),
        },
        sandbox=False,
        platform_machine=False,
    )
    (outside / "config.json").write_text("another room's")
    (home / linked).symlink_to(outside)

    with pytest.raises(OSError):
        bootstrap.configure(payload)

    assert sorted(path.name for path in outside.iterdir()) == ["config.json"]
    assert (outside / "config.json").read_text() == "another room's"


@pytest.fixture
def neighbour(tmp_path, monkeypatch, capsys):
    """Another room's executor running on the machine, and a sandboxed room
    whose executor state is a link to it."""
    home, _, payload = _unsandboxed_install(tmp_path, monkeypatch)
    bootstrap.configure(payload)
    started = json.loads(capsys.readouterr().out)
    state, release = Path(started["state"]), Path(started["release"])
    # Started from the release the teardown takes its programs from.
    room = _sandboxed_home(tmp_path / "owner", release)
    (room / ".cheese/executor").symlink_to(state)
    # As the room's own install wrote it, naming the room: the room can.
    (room / ".cheese/execution-owner.json").write_text(
        json.dumps({"resource": room.name})
    )
    yield room, state, release
    subprocess.run(
        [sys.executable, str(runtime.__file__), "stop", "--state", str(state)],
        capture_output=True,
        timeout=60,
    )


def test_a_sandboxed_rooms_teardown_stops_no_other_rooms_executor(neighbour):
    room, state, _ = neighbour

    cleanup.stop_executor(room, room.name)

    assert runtime.request(state, "ping")
    # Nor does it keep the room open behind it: its teardown goes on.
    cleanup.check_no_writers([room])


def test_a_sandboxed_rooms_reset_stops_no_other_rooms_executor(neighbour):
    room, state, release = neighbour

    # The runner as the machine runs it, beside the runtime it stops with.
    _run_runner(room, "reset", release / "cheese-environment.py")

    assert runtime.request(state, "ping")


def test_a_sandboxed_rooms_terminal_marker_ends_no_terminal(tmp_path, monkeypatch):
    """The teardown would close the terminal a room's marker names and signal
    its panes. A sandboxed room has no terminal of the platform's and writes
    the marker itself, so for one the teardown reads none."""
    if shutil.which("tmux") is None:
        pytest.skip("needs tmux")
    monkeypatch.setenv("HOME", str(tmp_path))
    home = _sandboxed_home(tmp_path)
    work = home.parent / "work"
    checksum = subprocess.run(
        ["cksum"], input=str(home / "room").encode(), capture_output=True, check=True
    )
    name = "cheese_" + checksum.stdout.decode().split()[0]
    # Short, as a socket path has to be.
    with tempfile.TemporaryDirectory(dir="/tmp") as sockets:
        sock = str(Path(sockets) / "t")
        terminal = ["tmux", "-S", sock]
        subprocess.run([*terminal, "new", "-d", "-s", name, "sleep 60"], check=True)
        try:
            marker = home / ".cheese/environment-session.json"
            marker.write_text(json.dumps([sock, name]))

            with open(tmp_path / "lock", "w") as lock:
                cleanup.request_exit(home, work, lock.fileno())

            alive = subprocess.run([*terminal, "has-session", "-t", "=" + name])
            assert alive.returncode == 0
        finally:
            subprocess.run([*terminal, "kill-server"], capture_output=True)


@pytest.mark.parametrize(
    "argv",
    [
        ["up", "../etc", "100", "--memory-mb", "64"],
        ["up", str(uuid.uuid4()), "1;id"],
        ["down", "*"],
        ["exec", str(uuid.uuid4())],
        ["up", str(uuid.uuid4()), "100", "--site", "70000"],
    ],
)
def test_the_sandbox_helper_refuses_what_it_does_not_take(argv):
    result = subprocess.run(
        [sys.executable, str(HELPER), *argv], capture_output=True, text=True
    )
    assert result.returncode == 2, result.stderr


def test_the_sandbox_helper_runs_only_for_a_user_through_sudo():
    name = str(uuid.uuid4())
    result = subprocess.run(
        [sys.executable, str(HELPER), "down", name],
        env={**os.environ, "SUDO_UID": str(os.getuid())},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2 and "sudo" in result.stderr


def test_the_machines_rules_are_written_once_for_every_sandbox():
    """Rules name no sandbox, so a second sandbox finds them in place and
    writes nothing; a flushed chain is noticed and written again."""
    wanted = sandbox_host.rules(["10.53.0.1"], [18080])
    first = sandbox_host.restore(wanted, "")
    assert "-I FORWARD 1 -j CHEESE-SBX-FWD" in first
    assert "-A CHEESE-SBX-FWD -i chs+ -d 10.53.0.1 -p udp --dport 53 -j ACCEPT" in first
    assert "-A CHEESE-SBX-FWD -i chs+ -d 192.168.0.0/16 -j DROP" in first
    assert first.index("--dport 53 -j ACCEPT") < first.index("10.0.0.0/8 -j DROP")
    saved = _as_saved(first)

    assert sandbox_host.restore(wanted, saved) == ""
    flushed = "\n".join(
        line for line in saved.splitlines() if not line.startswith("-A CHEESE-SBX-FWD")
    )
    assert sandbox_host.restore(wanted, flushed) != ""


def _as_saved(restored: str) -> str:
    """What `iptables-save` prints after `restored` was applied to empty
    tables: the jumps appended to their built-in chains."""
    lines = []
    for line in restored.splitlines():
        if line.startswith("-I "):
            _, parent, _, *rest = line.split()
            line = " ".join(["-A", parent, *rest])
        lines.append(line)
    return "\n".join(lines)
