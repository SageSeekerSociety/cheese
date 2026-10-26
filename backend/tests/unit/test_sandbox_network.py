"""A sandboxed session's network, limits and system calls (#2320), and the
code outside its sandbox that reads what the session can write.

The sandbox's network and limits are set up by root (`sandbox_host.py`
through `sudo -n`), so the tests that start one need Linux, bubblewrap and a
passwordless sudo, as CI's runner has; elsewhere they are skipped. They change
the machine's iptables rules, cgroups and project quotas the way a Cloud
machine's are changed, and CI turns them on (`CHEESE_TEST_SANDBOX_HOST`)
rather than every Linux checkout that happens to allow sudo. The homes are
made on an XFS filesystem with project quotas, as a platform host's data disk
is (`CHEESE_TEST_SANDBOX_DISK`, a directory on it). The rest holds anywhere.
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
import time
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
from tests.support import bubblewrap, sandbox_disk

HELPER = Path(sandbox_host.__file__)
RUNNER = Path(environment_runner.__file__)


def _sandbox_host_runs() -> bool:
    if os.environ.get("CHEESE_TEST_SANDBOX_HOST") != "1":
        return False
    if not sandbox_disk.available():
        return False
    if not sys.platform.startswith("linux") or shutil.which("bwrap") is None:
        return False
    return subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0


with_sandbox_host = pytest.mark.skipif(
    not _sandbox_host_runs(),
    reason="needs Linux, bubblewrap, sudo -n (CHEESE_TEST_SANDBOX_HOST=1) and a "
    "directory with project quotas (CHEESE_TEST_SANDBOX_DISK)",
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
        self.env = {"CHEESE_API": api, "CHEESE_TOKEN": "test", **(env or {})}
        self.capsys = capsys
        payload = self.payload()
        try:
            bootstrap.configure(payload)
        except RuntimeError:
            log = self.home / ".cheese/executor-bootstrap.log"
            pytest.fail(log.read_text() if log.exists() else "no executor log")
        self.release = Path(json.loads(capsys.readouterr().out)["release"])

    def payload(self) -> dict:
        """What the backend sends to install or prepare this room."""
        return payload_for(
            self.project,
            self.resource,
            self.env,
            sandbox=True,
            platform_machine=True,
        )

    def install_again(self, payload: dict | None = None) -> dict:
        """The install, run again as the next tool call runs it: what it said."""
        self.capsys.readouterr()
        bootstrap.configure(payload or self.payload())
        return json.loads(self.capsys.readouterr().out.strip().splitlines()[-1])

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
    with sandbox_disk.owner(tmp_path) as owner:
        yield from _rooms(owner, monkeypatch, capsys)


def _rooms(owner, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(owner))
    bubblewrap.place(owner)
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


@pytest.fixture
def ahead_of_the_sandboxes():
    """Puts a chain that accepts everything at the top of the machine's
    FORWARD, ahead of the sandboxes' own, as Docker's restart puts its rules
    there; taken out again afterwards."""
    iptables = ["sudo", "-n", f"{sandbox_host.TOOLS}/xtables-nft-multi", "iptables"]
    chain = "CHEESE-TEST-AHEAD"

    def insert():
        subprocess.run([*iptables, "-N", chain], check=True)
        subprocess.run([*iptables, "-A", chain, "-j", "ACCEPT"], check=True)
        subprocess.run([*iptables, "-I", "FORWARD", "1", "-j", chain], check=True)

    yield insert
    subprocess.run([*iptables, "-D", "FORWARD", "-j", chain], capture_output=True)
    subprocess.run([*iptables, "-F", chain], capture_output=True)
    subprocess.run([*iptables, "-X", chain], capture_output=True)


@with_sandbox_host
def test_sandboxes_stay_apart_after_another_firewall_goes_first(
    rooms, machine_service, ahead_of_the_sandboxes
):
    """A rule something else put ahead of the sandboxes' own, as Docker does
    each time its daemon starts, decides for no sandbox once the next one
    starts: two sandboxes still cannot reach each other."""
    api = f"http://127.0.0.1:{machine_service(b'backend')}"
    first = rooms(api)
    first.bash(
        "setsid nohup python3 -m http.server 8000 >/dev/null 2>&1 </dev/null &"
        " for i in $(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/8000) 2>/dev/null"
        " && break; sleep 0.1; done"
    )
    assert first.python(DIAL + "print(dial('127.0.0.1', 8000))").strip() != (
        "unreachable"
    )
    ahead_of_the_sandboxes()

    second = rooms(api)

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


@pytest.fixture
def small_disk(monkeypatch):
    """A sandbox may hold a gigabyte and two thousand files."""
    monkeypatch.setattr(settings, "cloud_sandbox_disk_budget_gb", 1)
    monkeypatch.setattr(settings, "cloud_sandbox_inode_budget", 2000)


MAKE_FILES = """
import os
made = 0
try:
    while True:
        os.mkdir(f"d{made}")
        made += 1
except OSError as error:
    print(made, error.errno)
"""


@with_sandbox_host
def test_a_sandbox_is_refused_writes_past_its_disk_and_its_neighbour_is_not(
    rooms, machine_service, small_disk
):
    api = f"http://127.0.0.1:{machine_service(b'backend')}"
    full, neighbour = rooms(api), rooms(api)

    written = full.bash("fallocate -l 1100M big; echo exit=$?; rm -f big")
    made, error = full.python(MAKE_FILES).split()

    assert "No space left on device" in written["stdout"] + written["stderr"]
    assert "exit=1" in written["stdout"]
    assert int(made) < 2000 and int(error) == 28  # ENOSPC
    assert neighbour.bash("fallocate -l 100M f && echo ok")["stdout"].strip() == "ok"
    assert neighbour.bash("mkdir d{1..500} && echo ok")["stdout"].strip() == "ok"


# Every way a session might take its files out of its quota from inside: set
# a file's project, take the inheritance off its home, or move or link a file
# into its project's store, whose project is another.
ESCAPES = """
import errno, fcntl, os, struct
GET, SET, INHERIT = 0x801C581F, 0x401C5820, 0x200
def change(path, project=None, inherit=True):
    fd = os.open(path, os.O_RDONLY)
    try:
        fields = list(struct.unpack("IIIII8s", fcntl.ioctl(fd, GET, bytes(28))))
        if project is not None:
            fields[3] = project
        if not inherit:
            fields[0] &= ~INHERIT
        fcntl.ioctl(fd, SET, struct.pack("IIIII8s", *fields))
        return "changed"
    except OSError as error:
        return errno.errorcode[error.errno]
    finally:
        os.close(fd)
def moved(call, *paths):
    try:
        call(*paths)
        return "moved"
    except OSError as error:
        return errno.errorcode[error.errno]
open("mine", "w").write("x")
store = os.environ["CHEESE_STORE"]
print(
    change("mine", project=0),
    change(os.environ["HOME"], inherit=False),
    moved(os.link, "mine", os.path.join(store, "linked")),
    moved(os.rename, "mine", os.path.join(store, "renamed")),
)
"""


@with_sandbox_host
def test_a_session_cannot_take_its_files_out_of_its_disk_limit(rooms, machine_service):
    room = rooms(f"http://127.0.0.1:{machine_service(b'backend')}")

    assert room.python(ESCAPES).split() == ["EINVAL", "EINVAL", "EXDEV", "EXDEV"]


@with_sandbox_host
def test_a_sandbox_that_filled_its_disk_still_answers_and_says_so(
    rooms, machine_service, small_disk
):
    room = rooms(f"http://127.0.0.1:{machine_service(b'backend')}")
    # Its limit is 953 MiB: most of it at once, the rest written to the end.
    room.bash("fallocate -l 900M most; head -c 100M /dev/zero > big")

    result = room.bash("echo more >> big; echo exit=$?")

    assert "No space left on device" in result["stdout"] + result["stderr"]
    assert "exit=1" in result["stdout"]
    assert runtime.request(room.state, "ping")


def fill(room: "Room") -> None:
    """The room's disk, 953 MiB here, filled to the last block."""
    room.bash("fallocate -l 900M most; head -c 100M /dev/zero > big")


@with_sandbox_host
def test_a_room_whose_disk_is_full_is_prepared_again_and_told_so(
    rooms, machine_service, small_disk
):
    room = rooms(f"http://127.0.0.1:{machine_service(b'backend')}")
    fill(room)
    # A skill the next install writes into the home, which the disk has no
    # room for (the preview token it once rewrote there is now empty: a room
    # with its platform socket holds no credential).
    payload = room.payload()
    payload["skills"] = {"skills/notes/SKILL.md": "x" * 65536}

    installed = room.install_again(payload)

    assert installed["disk_full_mb"] < 64
    assert room.bash("echo ok")["stdout"].strip() == "ok"


@with_sandbox_host
def test_a_command_that_fails_on_a_full_disk_says_the_disk_is_full(
    rooms, machine_service, small_disk
):
    room = rooms(f"http://127.0.0.1:{machine_service(b'backend')}")
    fill(room)

    result = room.bash("echo more >> big")
    # The same command through the shell a session's own build runs it in
    # (`shell_prefix`), which reads the command's end from the executor.
    shell = {"subtype": "shell", "command_id": "full-disk"}
    runtime.request(
        room.state,
        "control",
        {
            **shell,
            "operation": "start",
            "kind": "sh",
            "body": "echo more >> big",
            "cwd": str(room.home / "room"),
            "env": {},
            "merge": True,
            "stdin": None,
        },
    )
    deadline = time.monotonic() + 30
    while "exit" not in (
        read := runtime.request(
            room.state,
            "control",
            {**shell, "operation": "read", "out": 0, "err": 0, "wait": 1},
        )
    ):
        assert time.monotonic() < deadline

    assert "disk full:" in result["stdout"] and "MB free" in result["stdout"]
    assert read["exit"] != 0 and "disk full:" in read["disk"]


def full_release_store(room: "Room"):
    """The machine's release store with no room for another release."""
    store = Path(os.environ["HOME"]) / ".cheese/executor-releases"
    mount = subprocess.run(
        ["findmnt", "-n", "-o", "TARGET", "--target", str(store)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    project = str(4_000_000 + os.getpid() % 1000)

    def quota(command: str) -> None:
        subprocess.run(
            ["sudo", "-n", "xfs_quota", "-x", "-c", command, mount], check=True
        )

    quota(f"project -s -p {store} {project}")
    quota(f"limit -p bhard=1m {project}")
    return lambda: quota(f"limit -p bhard=0 {project}")


def newer(payload: dict) -> dict:
    """The same payload from a later release."""
    payload["files"]["remote-execution/newer.txt"] = "bmV3ZXI="
    if "file_names" in payload:
        payload["file_names"] = [*payload["file_names"], "remote-execution/newer.txt"]
    return payload


@with_sandbox_host
def test_an_upgrade_with_no_room_for_its_release_keeps_the_running_executor(
    rooms, machine_service
):
    room = rooms(f"http://127.0.0.1:{machine_service(b'backend')}")
    lift = full_release_store(room)
    try:
        answer = room.install_again(newer(room.payload()))
    finally:
        lift()

    assert answer["upgrade_pending"] is True
    assert answer["release"] == str(room.release)
    assert room.bash("echo ok")["stdout"].strip() == "ok"


@with_sandbox_host
def test_no_full_disk_keeps_an_executor_running_otherwise_than_it_is_asked(
    rooms, machine_service
):
    """Sandboxed now, asked to run with other limits: no room to stage the
    release that would run it so is no reason to keep it as it is."""
    room = rooms(f"http://127.0.0.1:{machine_service(b'backend')}")
    payload = newer(room.payload())
    payload["sandbox"] = {**payload["sandbox"], "memory_mb": 512}
    lift = full_release_store(room)
    try:
        with pytest.raises(OSError):
            room.install_again(payload)
    finally:
        lift()


@with_sandbox_host
def test_a_new_sandbox_is_not_charged_for_a_removed_ones_leftovers(
    rooms, machine_service, small_disk
):
    """A sandbox taken down leaves its home until its teardown deletes it,
    which can wait on whatever still holds it. A sandbox started meanwhile
    has its whole limit, not what the leftovers leave of it."""
    api = f"http://127.0.0.1:{machine_service(b'backend')}"
    gone = rooms(api)
    gone.bash("fallocate -l 800M left")
    subprocess.run(
        ["sudo", "-n", bootstrap.SANDBOX_HOST, "down", str(gone.resource)], check=True
    )

    fresh = rooms(api)

    assert fresh.bash("fallocate -l 800M mine && echo ok")["stdout"].strip() == "ok"


@with_sandbox_host
def test_no_sandbox_runs_where_its_home_has_no_disk_limit(
    tmp_path, monkeypatch, capsys
):
    owner = tmp_path / "owner"
    monkeypatch.setenv("HOME", str(owner))
    bubblewrap.place(owner)
    monkeypatch.setattr(bootstrap, "binary", lambda *_: sys.executable)
    payload = payload_for(
        uuid.uuid4(),
        uuid.uuid4(),
        {"CHEESE_API": "http://127.0.0.1:9", "CHEESE_TOKEN": "test"},
        sandbox=True,
        platform_machine=True,
    )

    with pytest.raises(RuntimeError, match="project quotas"):
        bootstrap.configure(payload)


@with_sandbox_host
def test_an_idle_sandbox_gives_back_its_page_cache(rooms, machine_service):
    room = rooms(f"http://127.0.0.1:{machine_service(b'backend')}")
    room.bash("head -c 300M /dev/urandom > blob && cat blob > /dev/null")
    group = sandbox_host.CGROUP / str(room.resource)
    held = int((group / "memory.current").read_text())

    answer = subprocess.run(
        ["sudo", "-n", bootstrap.SANDBOX_HOST, "reclaim", str(room.resource)],
        capture_output=True,
        text=True,
    )

    assert answer.returncode == 0, answer.stderr
    assert int((group / "memory.current").read_text()) < held - (200 << 20)
    assert room.bash("echo ok")["stdout"].strip() == "ok"


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
    bubblewrap.place(owner)
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
        # Written in place, and empty: a room with its platform socket holds no
        # credential (the preview helper asks the socket per connection).
        assert not token.is_symlink() and token.read_text() == ""
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
    bubblewrap.place(tmp_path)
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


def test_the_sandbox_helper_takes_only_the_home_of_the_sandbox_it_starts():
    name, project = str(uuid.uuid4()), str(uuid.uuid4())
    limits = ["--memory-mb", "64", "--swap-mb", "0", "--cpus", "1", "--pids", "16"]
    limits += ["--disk-mib", "64", "--inodes", "1000"]
    for home in (
        f"/h/.cheese/home/{project}/{uuid.uuid4()}",
        f"/h/.cheese/home/{project}/../{project}/{name}",
        f"/h/.cheese/home/not-a-project/{name}",
        f"h/.cheese/home/{project}/{name}",
        f"/h/.cheese/home/{project}/{name}/",
    ):
        result = subprocess.run(
            [sys.executable, str(HELPER), "up", name, "100", "--home", home, *limits],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2 and "is not a sandbox's home" in result.stderr


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
    saved = _applied("", first)
    assert _first_rule(saved, "filter", "FORWARD") == "-A FORWARD -j CHEESE-SBX-FWD"
    assert "-A CHEESE-SBX-FWD -i chs+ -d 10.53.0.1 -p udp --dport 53 -j ACCEPT" in saved
    assert "-A CHEESE-SBX-FWD -i chs+ -d 192.168.0.0/16 -j DROP" in saved
    assert saved.index("--dport 53 -j ACCEPT") < saved.index("10.0.0.0/8 -j DROP")

    assert sandbox_host.restore(wanted, saved) == ""
    flushed = "\n".join(
        line for line in saved.splitlines() if not line.startswith("-A CHEESE-SBX-FWD")
    )
    assert sandbox_host.restore(wanted, flushed) != ""


# What Docker's daemon puts at the top of FORWARD and POSTROUTING each time it
# starts, ahead of whatever was there.
DOCKER_AHEAD = {
    ("filter", "FORWARD"): [
        "-A FORWARD -j DOCKER-USER",
        "-A FORWARD -j DOCKER-FORWARD",
    ],
    ("nat", "POSTROUTING"): [
        "-A POSTROUTING -s 172.17.0.0/16 ! -o docker0 -j MASQUERADE"
    ],
}


def test_the_sandbox_rules_come_first_again_after_docker_restarts():
    """Docker restarting puts its rules ahead of ours; the next sandbox's
    start puts ours back on top, once, and leaves Docker's in place."""
    wanted = sandbox_host.rules(["10.53.0.1"], [18080])
    saved = _applied("", sandbox_host.restore(wanted, ""))
    for (table, _), lines in DOCKER_AHEAD.items():
        saved = _insert_first(saved, table, lines)

    again = _applied(saved, sandbox_host.restore(wanted, saved))

    for (table, chain), ours in (
        (("filter", "FORWARD"), "CHEESE-SBX-FWD"),
        (("filter", "INPUT"), "CHEESE-SBX-IN"),
        (("nat", "PREROUTING"), "CHEESE-SBX-PRE"),
        (("nat", "POSTROUTING"), "CHEESE-SBX-POST"),
    ):
        rules = _rules(again, table, chain)
        assert rules[0] == f"-A {chain} -j {ours}"
        assert rules.count(f"-A {chain} -j {ours}") == 1
        assert set(DOCKER_AHEAD.get((table, chain), [])) <= set(rules[1:])
    assert sandbox_host.restore(wanted, again) == ""


def _rules(saved: str, table: str, chain: str) -> list[str]:
    found, current = [], None
    for line in saved.splitlines():
        if line.startswith("*"):
            current = line[1:]
        elif current == table and line.startswith(f"-A {chain} "):
            found.append(line)
    return found


def _first_rule(saved: str, table: str, chain: str) -> str | None:
    return next(iter(_rules(saved, table, chain)), None)


def _tables(saved: str) -> dict[str, dict[str, list[str]]]:
    tables: dict[str, dict[str, list[str]]] = {}
    current = None
    for line in saved.splitlines():
        if line.startswith("*"):
            current = tables.setdefault(line[1:], {})
        elif line.startswith("-A ") and current is not None:
            current.setdefault(line.split()[1], []).append(line)
    return tables


def _saved(tables: dict[str, dict[str, list[str]]]) -> str:
    """`iptables-save`'s shape: per table, every rule in chain order."""
    lines = []
    for table, chains in tables.items():
        lines.append(f"*{table}")
        for rules in chains.values():
            lines += rules
        lines.append("COMMIT")
    return "\n".join([*lines, ""])


def _insert_first(saved: str, table: str, lines: list[str]) -> str:
    tables = _tables(saved)
    for line in reversed(lines):
        chain = line.split()[1]
        tables.setdefault(table, {}).setdefault(chain, []).insert(0, line)
    return _saved(tables)


def _applied(saved: str, restored: str) -> str:
    """What `iptables-save` prints after `iptables-restore --noflush` applied
    `restored` to tables holding `saved`: a declared chain is emptied, `-A`
    appends, `-I <chain> 1` inserts at the top, `-D` removes the first equal
    rule."""
    tables = _tables(saved)
    table = None
    for line in restored.splitlines():
        if line.startswith("*"):
            table = tables.setdefault(line[1:], {})
        elif line.startswith(":") and table is not None:
            table[line[1:].split()[0]] = []
        elif table is not None and line[:3] in ("-A ", "-I ", "-D "):
            verb, chain, *rest = line.split()
            rules = table.setdefault(chain, [])
            if verb == "-I":
                assert rest[0] == "1"
                rules.insert(0, " ".join(["-A", chain, *rest[1:]]))
            elif verb == "-D":
                rules.remove(" ".join(["-A", chain, *rest]))
            else:
                rules.append(line)
    return _saved(tables)
