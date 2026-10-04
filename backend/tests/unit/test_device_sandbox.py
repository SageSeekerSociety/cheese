"""A session's isolated environment on a machine a person enrolled (#2320 step
2), and the code outside it that reads what the session can write.

The tests that start a sandbox need Linux with bubblewrap and unprivileged
user namespaces, as CI's runner has, or macOS with `sandbox-exec`; elsewhere
they are skipped. The rest holds anywhere.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from app.domain.agent import environment_runner
from app.domain.agent import resource_cleanup as cleanup
from app.domain.agent.harness.claude_code.remote_execution import bootstrap, runtime
from app.domain.agent.harness.claude_code.remote_execution.launch import payload_for

RUNNER = Path(environment_runner.__file__)


def _bubblewrap_runs() -> bool:
    if not sys.platform.startswith("linux") or shutil.which("bwrap") is None:
        return False
    probe = subprocess.run(
        ["bwrap", "--unshare-user", "--unshare-pid", "--ro-bind", "/", "/"]
        + ["--dev", "/dev", "--proc", "/proc", "true"],
        capture_output=True,
    )
    return probe.returncode == 0


sandboxed = pytest.mark.skipif(
    not _bubblewrap_runs(), reason="needs bubblewrap with user namespaces (Linux)"
)
linux = pytest.mark.skipif(sys.platform != "linux", reason="Linux only")
seatbelt = pytest.mark.skipif(
    sys.platform != "darwin" or not os.access(bootstrap.SANDBOX_EXEC, os.X_OK),
    reason="needs macOS sandbox-exec",
)


def _wait(condition, what, timeout=15):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _payload(project, resource, *, sandbox, environment=None):
    env = {"CHEESE_API": "http://127.0.0.1:1", "CHEESE_TOKEN": "test"}
    if environment is not None:
        env["CHEESE_ENVIRONMENT"] = json.dumps(environment)
    # A machine a person enrolled: the platform installs nothing there.
    return payload_for(project, resource, env, sandbox=sandbox, platform_machine=False)


@pytest.fixture
def owner(tmp_path, monkeypatch):
    """A person's home on their own machine, the connector running as them."""
    home = tmp_path / "owner"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(bootstrap, "binary", lambda *_: sys.executable)
    return home


def _refusal(payload, capsys) -> str:
    with pytest.raises(SystemExit) as exited:
        bootstrap.configure(payload)
    assert exited.value.code == bootstrap.SANDBOX_UNAVAILABLE_EXIT
    return capsys.readouterr().err


@linux
def test_an_isolated_room_this_machine_cannot_isolate_is_refused_and_nothing_runs(
    owner, tmp_path, monkeypatch, capsys
):
    """No bubblewrap on Linux: the room is refused with what to do about it,
    nothing is installed with the owner's sudo, and nothing runs over the
    whole machine instead."""
    tools = tmp_path / "tools"
    tools.mkdir()
    asked = tmp_path / "sudo-was-run"
    (tools / "sudo").write_text(f"#!/bin/sh\ntouch {asked}\nexit 1\n")
    (tools / "sudo").chmod(0o755)
    monkeypatch.setenv("PATH", str(tools))
    project, resource = uuid.uuid4(), uuid.uuid4()

    said = _refusal(_payload(project, resource, sandbox=True), capsys)

    assert "bubblewrap is not installed" in said
    assert "sudo apt install bubblewrap" in said
    assert "full machine access" in said
    assert not asked.exists()
    state = owner / ".cheese/home" / str(project) / str(resource) / ".cheese/executor"
    assert not Path(runtime.socket_path(state)).exists()
    assert not (owner / ".cheese/sandboxes").exists()


@linux
def test_a_machine_that_allows_no_user_namespaces_is_told_what_to_change(
    owner, tmp_path, monkeypatch, capsys
):
    """Bubblewrap makes the sandbox in a user namespace of its own; where the
    machine forbids that (Ubuntu's AppArmor restriction among others) the
    room is refused with bubblewrap's own words and what to change."""
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "bwrap").write_text(
        "#!/bin/sh\necho 'bwrap: setting up uid map: Permission denied' >&2\nexit 1\n"
    )
    (tools / "bwrap").chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}:{os.environ['PATH']}")

    said = _refusal(_payload(uuid.uuid4(), uuid.uuid4(), sandbox=True), capsys)

    assert "setting up uid map: Permission denied" in said
    assert "user namespaces" in said
    assert "full machine access" in said
    assert not (owner / ".cheese/sandboxes").exists()


class Room:
    """One session's executor on this machine, started by the install the
    backend sends."""

    def __init__(self, owner: Path, capsys, *, sandbox=True, environment=None):
        self.project, self.resource = uuid.uuid4(), uuid.uuid4()
        self.home = owner / ".cheese/home" / str(self.project) / str(self.resource)
        self.state = self.home / ".cheese/executor"
        try:
            bootstrap.configure(
                _payload(
                    self.project,
                    self.resource,
                    sandbox=sandbox,
                    environment=environment,
                )
            )
        except RuntimeError:
            log = self.home / ".cheese/executor-bootstrap.log"
            pytest.fail(log.read_text() if log.exists() else "no executor log")
        self.started = json.loads(capsys.readouterr().out)
        self.release = Path(self.started["release"])

    def bash(self, command: str) -> str:
        answer = runtime.request(
            self.state,
            "invoke",
            {"id": uuid.uuid4().hex, "tool": "Bash", "args": {"command": command}},
        )
        assert "error" not in answer, answer
        return answer["value"]["stdout"].strip()

    def stop(self):
        cleanup.stop_executor(self.home, str(self.resource))


@pytest.fixture
def rooms(owner, capsys):
    started: list[Room] = []

    def start(**options) -> Room:
        room = Room(owner, capsys, **options)
        started.append(room)
        return room

    yield start
    for room in started:
        room.stop()


@sandboxed
def test_a_session_on_a_persons_machine_sees_the_system_and_none_of_their_home(
    owner, rooms
):
    """The machine's own tools work; what the person keeps in their home —
    their keys, their tools under ~/.local/bin, ~/.cargo, ~/.nvm — is not
    there, and neither is anything mounted under /mnt or /media."""
    (owner / ".ssh").mkdir()
    (owner / ".ssh/id_ed25519").write_text("the owner's key")
    for tool in (".local/bin/mytool", ".cargo/bin/mytool", ".nvm/bin/mytool"):
        path = owner / tool
        path.parent.mkdir(parents=True)
        path.write_text("#!/bin/sh\necho ran\n")
        path.chmod(0o755)

    room = rooms()

    assert room.bash("git --version").startswith("git version")
    assert room.bash("python3 -c 'print(6 * 7)'") == "42"
    for hidden in (".ssh/id_ed25519", ".local/bin/mytool", ".cargo", ".nvm"):
        assert room.bash(f'ls "{owner / hidden}" >/dev/null 2>&1 || echo hidden') == (
            "hidden"
        ), hidden
    path = f"{owner}/.local/bin:{owner}/.cargo/bin:$PATH"
    assert room.bash(f'PATH="{path}"; command -v mytool || echo missing') == "missing"
    for mount in bootstrap.HIDDEN_MOUNTS:
        if Path(mount).is_dir():
            assert room.bash(f'ls -A "{mount}" | wc -l') == "0", mount
    # Its own home is there, and writable.
    assert room.bash('touch "$HOME/mine" && echo ok') == "ok"
    assert (room.home / "mine").exists()


@sandboxed
def test_the_teardown_ends_everything_the_sandbox_started(rooms):
    """A session's processes are its sandbox's descendants, whatever pid files
    it wrote: the teardown ends them all with the sandbox, reading only what
    the install recorded."""
    room = rooms()
    marker = f"sleep {31000 + os.getpid() % 1000}"
    room.bash(f"setsid nohup {marker} >/dev/null 2>&1 & echo started")

    def running() -> bool:
        listing = subprocess.run(["ps", "-eo", "args="], capture_output=True, text=True)
        return any(line.strip() == marker for line in listing.stdout.splitlines())

    _wait(running, "the session's process never started")
    sandbox = environment_runner.sandbox_process(room.home)
    assert sandbox is not None

    room.stop()

    _wait(lambda: not running(), "the session's process outlived its sandbox")
    assert environment_runner.sandbox_process(room.home) is None
    with pytest.raises(OSError):
        runtime.request(room.state, "ping")


RECORD_STATUS = """
import json, os, subprocess, sys, runpy
runner = runpy.run_path(sys.argv[1])
child = subprocess.Popen(["sleep", "300"], start_new_session=True)
runner["write_json"](
    __import__("pathlib").Path.home() / ".cheese-environment/status.json",
    {
        "state": "preparing",
        "pid": child.pid,
        "pid_namespace": runner["pid_namespace"](),
        "process_identity": runner["process_identity"](child.pid),
    },
)
print(child.pid)
"""


@sandboxed
def test_a_cancel_reaches_the_sessions_own_process_by_its_sandbox(rooms):
    """The cancel runs outside the sandbox and signals what the session's
    status names only when it is one of the sandbox's processes: here it is,
    so it ends."""
    room = rooms()
    room.bash('mkdir -p "$HOME/.cheese-environment"')
    program = room.home / "record.py"
    program.write_text(RECORD_STATUS)
    inside = room.bash(
        f'python3 "{program}" "{room.release / "cheese-environment.py"}"'
    )
    assert room.bash(f"kill -0 {inside} && echo alive") == "alive"

    cancelled = _run_runner(room.home, "cancel", room.release / "cheese-environment.py")

    assert cancelled.returncode == 0, cancelled.stderr
    _wait(
        lambda: (
            room.bash(f"kill -0 {inside} 2>/dev/null && echo alive || echo gone")
            == "gone"
        ),
        "the session's own process was not cancelled",
    )


def _sandboxed_home(owner: Path, release: Path) -> Path:
    """A room's home on a machine that recorded it as sandboxed, started from
    `release`."""
    project, resource = str(uuid.uuid4()), str(uuid.uuid4())
    home = owner / ".cheese/home" / project / resource
    (home / ".cheese").mkdir(parents=True)
    marker = owner / ".cheese/sandboxes" / project / resource
    marker.parent.mkdir(parents=True)
    marker.write_text(str(release))
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
def test_a_status_naming_a_process_the_sandbox_did_not_start_signals_nothing(
    tmp_path, action
):
    """A sandboxed room writes its own environment status. One naming a
    process of the machine's that its sandbox did not start gets that process
    no signal from the reset or the cancel run outside the sandbox."""
    home = _sandboxed_home(tmp_path, tmp_path / "release")
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
        # And a record naming it the sandbox, which the room cannot write but
        # which must not be enough on its own either: a sandbox's own first
        # process is not one of the room's.
        record = tmp_path / ".cheese/sandboxes" / home.parent.name / home.name
        Path(str(record) + ".process").write_text(
            json.dumps(
                {
                    "pid": bystander.pid,
                    "identity": environment_runner.process_identity(bystander.pid),
                }
            )
        )

        _run_runner(home, action)

        assert bystander.poll() is None
    finally:
        bystander.send_signal(signal.SIGKILL)
        bystander.wait()


def test_pruning_a_skill_removes_the_links_in_it_and_nothing_they_name(tmp_path):
    """A skill the room's record says was planted and is no longer shipped is
    removed with everything in it. What the room put there as links goes as
    links: the directories and files they name are the owner's and stay."""
    home = tmp_path / "home"
    skills = home / ".claude/skills"
    stale = skills / "stale"
    (stale / "nested").mkdir(parents=True)
    (stale / "nested/file").write_text("planted")
    owners = tmp_path / "owners"
    (owners / "documents").mkdir(parents=True)
    (owners / "documents/report").write_text("the owner's")
    (stale / "linked-directory").symlink_to(owners / "documents")
    (stale / "nested/linked-file").symlink_to(owners / "documents/report")
    (skills / "kept").mkdir()
    (skills / "linked-skill").symlink_to(owners)
    (skills / ".cheese-project-skills.json").write_text(
        json.dumps(["stale", "kept", "linked-skill"])
    )

    bootstrap.prune_project_skills(home, ["kept"])

    assert not stale.exists() and not stale.is_symlink()
    assert not (skills / "linked-skill").is_symlink()
    assert (skills / "kept").is_dir()
    assert (owners / "documents/report").read_text() == "the owner's"
    assert json.loads((skills / ".cheese-project-skills.json").read_text()) == ["kept"]


@sandboxed
def test_a_room_given_full_machine_access_after_running_isolated_runs_outside_it(
    owner, rooms, capsys
):
    """The owner gives an isolated room the whole machine: its next install
    replaces the sandboxed executor with one over the machine, and nothing
    out here goes on treating the room as sandboxed."""
    room = rooms()
    assert environment_runner.sandbox_process(room.home) is not None
    assert room.bash(f'ls "{owner}/.cheese/sandboxes" 2>/dev/null || echo hidden') == (
        "hidden"
    )

    bootstrap.configure(_payload(room.project, room.resource, sandbox=False))
    capsys.readouterr()

    assert (
        room.bash(f'ls -d "{owner}/.cheese/sandboxes"') == f"{owner}/.cheese/sandboxes"
    )
    assert cleanup.sandbox_release(room.home) is None
    assert environment_runner.sandbox_process(room.home) is None


def _within(path: str, directory: Path) -> bool:
    return Path(os.path.realpath(path)).is_relative_to(os.path.realpath(directory))


@seatbelt
def test_a_session_on_a_mac_sees_its_own_home_and_none_of_the_persons(owner, rooms):
    """The Mac's own tools work. The person's keys, the connector's credential
    and another room are unreadable; the session writes its own home and its
    own temporary directory, and nothing else of the machine."""
    key = owner / ".ssh/id_ed25519"
    credential = owner / "Library/Application Support/cheese/config.json"
    for secret in (key, credential):
        secret.parent.mkdir(parents=True)
        secret.write_text("the owner's secret")

    room, other = rooms(), rooms()

    assert room.bash("git --version").startswith("git version")
    assert room.bash("python3 -c 'print(6 * 7)'") == "42"
    for hidden in (key, credential, other.home):
        probe = f'cat "{hidden}" || ls "{hidden}"'
        assert room.bash(f"({probe}) >/dev/null 2>&1 || echo hidden") == "hidden", (
            hidden
        )
    assert room.bash('touch "$HOME/mine" && echo ok') == "ok"
    assert (room.home / "mine").exists()
    for outside in (
        owner / "outside",
        Path("/tmp") / f"cheese-test-{uuid.uuid4().hex}",
    ):
        assert (
            room.bash(f'touch "{outside}" 2>/dev/null && echo wrote || echo refused')
            == "refused"
        )
        assert not outside.exists()
    made = room.bash("python3 -c 'import tempfile; print(tempfile.mkdtemp())'")
    assert _within(made, room.home), made


@seatbelt
def test_a_session_on_a_mac_signals_and_dials_nothing_of_the_persons(
    owner, tmp_path, rooms
):
    """macOS has no pid namespace: the sandbox itself keeps the session from
    signalling the person's processes and from dialling their Unix sockets,
    a terminal multiplexer's among them, which would run commands outside."""
    import socket

    sleeper = subprocess.Popen(["sleep", "300"])
    import tempfile

    listening = socket.socket(socket.AF_UNIX)
    # A short path: a Unix socket's is at most 104 bytes on macOS.
    address = Path(tempfile.mkdtemp(dir="/tmp")) / "s.sock"
    listening.bind(str(address))
    listening.listen(1)
    try:
        room = rooms()
        assert (
            room.bash(
                f"kill -0 {sleeper.pid} 2>/dev/null && echo reached || echo refused"
            )
            == "refused"
        )
        dial = (
            "import socket; s = socket.socket(socket.AF_UNIX)\n"
            "try:\n"
            f"    s.connect({str(address)!r}); print('reached')\n"
            "except OSError:\n"
            "    print('refused')"
        )
        program = room.home / "dial.py"
        program.write_text(dial)
        assert room.bash(f'python3 "{program}"') == "refused"
        assert sleeper.poll() is None
    finally:
        sleeper.kill()
        listening.close()


@seatbelt
def test_teardown_git_on_a_mac_runs_inside_the_rooms_directories(owner, rooms):
    """Git runs the programs a checkout's config names; for a sandboxed room
    it runs sandboxed, so what the room put there writes nowhere outside."""
    room = rooms()
    checkout = room.home / "room"
    escaped = owner / "escaped"
    hook = room.home / "monitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch '{escaped}'\n")
    hook.chmod(0o755)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "core.fsmonitor", str(hook)], check=True
    )

    cleanup.git(["status", "--porcelain"], checkout, room.home)

    assert not escaped.exists()
