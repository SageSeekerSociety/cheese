"""A session's executor in a sandbox of its own (#2320), and what reaches it
from outside: the connector through its socket, the platform when it stops it,
the teardown and the environment reset, which run outside every sandbox.

The sandbox itself is bubblewrap, so those tests run where bubblewrap does —
Linux with unprivileged user namespaces — and are skipped elsewhere. The rest
holds on any machine the executor runs on.
"""

import json
import os
import shutil
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
from tests.support import executor_release

RUNTIME = Path(runtime.__file__)


def _bubblewrap_runs() -> bool:
    # A sandbox's network and limits are set up as root (`sandbox_host.py`),
    # which only a machine that opted in lets a test do: CI's runner.
    if os.environ.get("CHEESE_TEST_SANDBOX_HOST") != "1":
        return False
    if not sys.platform.startswith("linux") or shutil.which("bwrap") is None:
        return False
    probe = subprocess.run(
        ["bwrap", "--unshare-user", "--unshare-pid", "--ro-bind", "/", "/"]
        + ["--dev", "/dev", "--proc", "/proc", "true"],
        capture_output=True,
    )
    return probe.returncode == 0


sandboxed = pytest.mark.skipif(
    not _bubblewrap_runs(),
    reason="needs bubblewrap, user namespaces and sudo (CHEESE_TEST_SANDBOX_HOST=1)",
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


def _stop(state: Path, runtime_file: Path = RUNTIME) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(runtime_file), "stop", "--state", str(state)],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_every_socket_an_executor_binds_fits_a_unix_socket_address(tmp_path):
    """A Unix socket's path is at most 104 bytes on macOS (108 on Linux). The
    executor's live in a directory of their own now, which a sandbox is shown,
    so the room's state path must not reach their length — and a uid can run
    to ten digits."""
    home = tmp_path / ".cheese/home" / str(uuid.uuid4()) / str(uuid.uuid4())
    started = executor_release.install(home / ".cheese")
    state = home / ".cheese/executor"
    work = home / "room"
    work.mkdir(parents=True)
    subprocess.run(
        [sys.executable, str(started), "start", "--state", str(state)],
        input=json.dumps({"workspace": str(work), "env": {}}),
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )
    try:
        directory = Path(runtime.socket_directory(state))
        # The executor's own, and its CLI worker's, which comes up after it.
        _wait(lambda: len(os.listdir(directory)) >= 2, "the CLI socket never came")
        bound = [directory / name for name in os.listdir(directory)]
        assert runtime.request(state, "ping")["workspace"] == str(work)
        widest = len(str(os.getuid()))
        for path in (*bound, Path(runtime.socket_path(state))):
            assert len(str(path).encode()) - widest + 10 < 104, path
    finally:
        assert _stop(state, started).returncode == 0


# What an executor started before stopping was a request answers: `ping` with
# the pid it was told to name, anything else as an unknown method.
_PREVIOUS_EXECUTOR = """
import fcntl, json, os, signal, socket, sys
from pathlib import Path

state = Path(sys.argv[-1])
held = open(state / "service.lock", "a")
fcntl.flock(held, fcntl.LOCK_EX)
address = os.environ["ADDRESS"]
named = int(os.environ.get("NAMED") or os.getpid())


def leave(*_):
    os.unlink(address)
    sys.exit(0)


signal.signal(signal.SIGTERM, leave)
server = socket.socket(socket.AF_UNIX)
server.bind(address)
server.listen()
while True:
    connection, _ = server.accept()
    with connection, connection.makefile("rwb") as stream:
        method = json.loads(stream.readline())["method"]
        answer = (
            {"result": {"pid": named}}
            if method == "ping"
            else {"error": "Unknown executor method"}
        )
        stream.write(json.dumps(answer).encode() + b"\\n")
        stream.flush()
"""


def _previous_executor(tmp_path, named=None, through_link=False):
    state = tmp_path / "executor"
    state.mkdir()
    if through_link:
        # A home reached through a link, as the platform spells it.
        (tmp_path / "linked").symlink_to(tmp_path)
        state = tmp_path / "linked/executor"
    program = tmp_path / "previous/runtime.py"
    program.parent.mkdir()
    program.write_text(_PREVIOUS_EXECUTOR)
    address = runtime.socket_path(state)
    process = subprocess.Popen(
        [sys.executable, str(program), "serve", "--state", str(state)],
        env={**os.environ, "ADDRESS": address, "NAMED": str(named or "")},
    )
    _wait(lambda: Path(address).exists(), "the previous executor never listened")
    return state, process


@pytest.mark.parametrize("through_link", [False, True])
def test_an_executor_from_before_stop_requests_is_still_stopped(tmp_path, through_link):
    """Every running executor was started by an earlier release, which stopped
    on SIGTERM and does not know `shutdown`. The stop that replaces it has to
    take those down too, or no room could be upgraded off them — including
    one whose state path the platform spelled through a link."""
    state, process = _previous_executor(tmp_path, through_link=through_link)
    try:
        stopped = _stop(state)
        assert stopped.returncode == 0, stopped.stderr
        assert process.wait(timeout=10) == 0
    finally:
        process.kill()


def test_a_stop_signals_no_process_but_the_rooms_own_service(tmp_path):
    """What answers on a room's socket is not proof of who is behind it: a
    sandboxed room can put anything there. A pid it names is signalled only
    when that process is the service for this room's state."""
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    state, process = _previous_executor(tmp_path, named=bystander.pid)
    try:
        stopped = _stop(state)
        assert stopped.returncode != 0
        assert "did not stop" in stopped.stderr
        assert bystander.poll() is None
    finally:
        for child in (process, bystander):
            child.kill()


def test_a_stop_reaches_a_service_named_through_a_state_descriptor(tmp_path):
    """A sandboxed room reaches its executor's state through an open directory
    descriptor (`bootstrap.executor_state`), so `stop` is told `/proc/self/fd/N`
    while the service's own command line carries the path it resolved to in
    `start`. The fallback that runs when a service refuses `shutdown` has to
    recognize both spellings, or such a room can never stop an executor from an
    earlier release."""
    state, process = _previous_executor(tmp_path)
    descriptor = os.open(state, os.O_RDONLY)
    try:
        stopped = subprocess.run(
            [
                sys.executable,
                str(RUNTIME),
                "stop",
                "--state",
                f"/proc/self/fd/{descriptor}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            pass_fds=(descriptor,),
        )
        assert stopped.returncode == 0, stopped.stderr
        assert process.wait(timeout=10) == 0
    finally:
        os.close(descriptor)
        process.kill()


@pytest.mark.parametrize("sandbox", [True, False])
def test_the_teardown_runs_a_sandboxed_rooms_programs_from_its_release(
    tmp_path, monkeypatch, sandbox
):
    """The teardown runs outside every sandbox. A sandboxed room can rewrite
    its own installation, so for one its programs come from the release it was
    started from, which no room can write; an unsandboxed room's installation
    is the platform's as it always was."""
    monkeypatch.setenv("HOME", str(tmp_path))
    project, resource = str(uuid.uuid4()), str(uuid.uuid4())
    home = tmp_path / ".cheese/home" / project / resource
    installed = home / ".cheese"
    (installed / "executor").mkdir(parents=True)
    (installed / "execution-owner.json").write_text(json.dumps({"resource": resource}))
    ran = tmp_path / "the-room-program-ran"
    room_program = installed / "remote-execution/runtime.py"
    room_program.parent.mkdir()
    room_program.write_text(
        f"from pathlib import Path\nPath({str(ran)!r}).touch()\n"
        "def socket_path(state):\n    return str(state / 'absent')\n"
    )
    if sandbox:
        release = tmp_path / ".cheese/executor-releases" / ("0" * 64)
        (release / "remote-execution").mkdir(parents=True)
        shutil.copy(RUNTIME, release / "remote-execution/runtime.py")
        marker = tmp_path / ".cheese/sandboxes" / project / resource
        marker.parent.mkdir(parents=True)
        marker.write_text(str(release))

    cleanup.stop_executor(home, resource)

    assert ran.exists() is not sandbox


def test_the_install_writes_through_no_link_a_room_left_in_its_home(
    tmp_path, monkeypatch
):
    """The install runs outside every sandbox, and a sandboxed room writes its
    own home. A link it leaves where the install keeps its log must not carry
    the executor's output into the owner's files."""
    owner = tmp_path / "owner"
    monkeypatch.setenv("HOME", str(owner))
    monkeypatch.setattr(bootstrap, "binary", lambda *_: sys.executable)
    project, resource = uuid.uuid4(), uuid.uuid4()
    platform = owner / ".cheese/home" / str(project) / str(resource) / ".cheese"
    platform.mkdir(parents=True)
    keys = owner / ".ssh/authorized_keys"
    keys.parent.mkdir()
    keys.write_text("ssh-ed25519 AAAA owner\n")
    (platform / "executor-bootstrap.log").symlink_to(keys)
    payload = payload_for(
        project,
        resource,
        {"CHEESE_API": "http://127.0.0.1:1", "CHEESE_TOKEN": "test"},
        sandbox=False,
    )

    with pytest.raises(OSError):
        bootstrap.configure(payload)

    assert keys.read_text() == "ssh-ed25519 AAAA owner\n"


def _configure(payload, home):
    """The install, with the executor's own log in the failure if it fails."""
    try:
        bootstrap.configure(payload)
    except RuntimeError:
        log = home / ".cheese/executor-bootstrap.log"
        pytest.fail(log.read_text() if log.exists() else "no executor log")


def _run_git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.mark.parametrize(
    "sandbox", [pytest.param(True, marks=sandboxed), pytest.param(False)]
)
def test_git_in_a_sandboxed_rooms_checkout_runs_in_a_sandbox_too(
    tmp_path, monkeypatch, sandbox
):
    """Git runs what a checkout's config names — `core.fsmonitor` on every
    `git status` — and a sandboxed room writes its checkout's config. So the
    teardown's own git, in that room's checkouts, sees none of the owner's
    files."""
    monkeypatch.setenv("HOME", str(tmp_path))
    project, resource = str(uuid.uuid4()), str(uuid.uuid4())
    home = tmp_path / ".cheese/home" / project / resource
    checkout = home / "room"
    checkout.mkdir(parents=True)
    _run_git("init", "-q", cwd=checkout)
    (checkout / "notes").write_text("committed")
    _run_git("add", "notes", cwd=checkout)
    _run_git(
        "-c", "user.name=a", "-c", "user.email=a@a", "commit", "-qm", "1", cwd=checkout
    )
    escaped = tmp_path / "written-outside-the-sandbox"
    _run_git("config", "core.fsmonitor", f"touch {escaped}; false", cwd=checkout)
    if sandbox:
        marker = tmp_path / ".cheese/sandboxes" / project / resource
        marker.parent.mkdir(parents=True)
        marker.write_text(str(tmp_path / "release"))

    with pytest.raises(RuntimeError):
        # The commit is on no remote; what matters is what ran on the way.
        cleanup.check_resource_publication(home, home.parent / "absent")

    assert escaped.exists() is not sandbox


@sandboxed
def test_a_sandboxed_session_reaches_its_own_room_and_nothing_else(
    tmp_path, monkeypatch, capsys
):
    owner = tmp_path / "owner"
    monkeypatch.setenv("HOME", str(owner))
    monkeypatch.setattr(bootstrap, "binary", lambda *_: sys.executable)
    project, resource = uuid.uuid4(), uuid.uuid4()
    home = owner / ".cheese/home" / str(project) / str(resource)
    neighbour = owner / ".cheese/home" / str(project) / str(uuid.uuid4())
    neighbour.mkdir(parents=True)
    (neighbour / "notes").write_text("another session's")
    credential = owner / ".config/cheese/config.json"
    credential.parent.mkdir(parents=True)
    credential.write_text('{"token": "the machine identity"}')
    other_store = owner / ".cheese/store" / str(uuid.uuid4())
    other_store.mkdir(parents=True)
    environment = {
        "revision": "sandboxed",
        "variables": {},
        "setup_script": "true",
        "startup_script": "",
    }
    payload = payload_for(
        project,
        resource,
        {
            "CHEESE_API": "http://127.0.0.1:1",
            "CHEESE_TOKEN": "test",
            "CHEESE_ENVIRONMENT": json.dumps(environment),
        },
        sandbox=True,
    )
    state = home / ".cheese/executor"
    store = owner / ".cheese/store" / str(project)

    def bash(command: str) -> dict:
        answer = runtime.request(
            state,
            "invoke",
            {"id": uuid.uuid4().hex, "tool": "Bash", "args": {"command": command}},
        )
        assert "error" not in answer, answer
        return answer["value"]

    _configure(payload, home)
    started = json.loads(capsys.readouterr().out)
    release = bootstrap.release_store(owner)
    try:
        assert started["workspace"] == str(home / "room")
        # Its own room and its project's store: written, and there outside.
        assert bash(f'touch "$HOME/mine" "{store}/cached" /tmp/scratch && echo ok')[
            "stdout"
        ].strip() == ("ok")
        assert (home / "mine").exists() and (store / "cached").exists()
        assert (home / ".cheese/tmp/scratch").exists()
        assert bash("printf %s $UV_LINK_MODE")["stdout"] == "copy"
        # Nothing of anyone else's, and none of the owner's.
        for hidden in (neighbour / "notes", credential, other_store):
            assert (
                bash(f'ls "{hidden}" || echo hidden')["stdout"]
                .strip()
                .endswith("hidden")
            ), hidden
        assert (
            bash(f'touch "{release}/planted" || echo refused')["stdout"]
            .strip()
            .endswith("refused")
        )
        assert not (release / "planted").exists()
        # The Docker daemon would be root on the machine to anyone it answers.
        assert bash("test -S /run/docker.sock && echo reachable || echo masked")[
            "stdout"
        ].strip() == ("masked")
        # No process outside its own: not this one, which started it.
        assert bash(f"test -e /proc/{os.getpid()} || echo unseen")[
            "stdout"
        ].strip() == ("unseen")
        # Read from outside, its environment is ready, though the pid the
        # runner recorded is one only the sandbox numbers that way.
        status = environment_runner.read_status(home / ".cheese-environment")
        assert status["state"] == "ready", status
    finally:
        stopped = _stop(state, Path(started["release"]) / "remote-execution/runtime.py")
    assert stopped.returncode == 0, stopped.stderr
    with pytest.raises(OSError):
        runtime.request(state, "ping")
    status = environment_runner.read_status(home / ".cheese-environment")
    assert status["state"] == "stopped", status


@sandboxed
def test_a_sandboxed_executor_survives_its_own_room_rewriting_its_programs(
    tmp_path, monkeypatch, capsys
):
    """The next install reaches a running executor from outside the sandbox
    with the programs it ships, never with the ones the room's config names."""
    owner = tmp_path / "owner"
    monkeypatch.setenv("HOME", str(owner))
    monkeypatch.setattr(bootstrap, "binary", lambda *_: sys.executable)
    project, resource = uuid.uuid4(), uuid.uuid4()
    payload = payload_for(
        project,
        resource,
        {"CHEESE_API": "http://127.0.0.1:1", "CHEESE_TOKEN": "test"},
        sandbox=True,
    )
    home = owner / ".cheese/home" / str(project) / str(resource)
    state = home / ".cheese/executor"
    _configure(payload, home)
    started = json.loads(capsys.readouterr().out)
    escaped = owner / "escaped"
    try:
        # What the room can write, as it would: a program of its own, and its
        # executor's config naming it as the release that is running.
        forged = home / "forged"
        (forged / "remote-execution").mkdir(parents=True)
        (forged / "remote-execution/runtime.py").write_text(
            f"open({str(escaped)!r}, 'w')\n"
        )
        config = json.loads((state / "config.json").read_text())
        (state / "config.json").write_text(
            json.dumps({**config, "release": str(forged)})
        )
        bootstrap.configure(payload)
        capsys.readouterr()
        assert not escaped.exists()
    finally:
        stopped = _stop(state, Path(started["release"]) / "remote-execution/runtime.py")
    assert stopped.returncode == 0, stopped.stderr


@pytest.mark.skipif(sys.platform != "linux", reason="pid namespaces are Linux's")
def test_a_status_read_from_another_pid_namespace_finds_no_process_by_its_number(
    tmp_path,
):
    """A pid the runner recorded in a namespace this reader is not in names a
    different process here; only the namespace's own process may answer for
    it, and none does."""
    directory = tmp_path / "environment"
    directory.mkdir()
    environment_runner.write_json(
        directory / "status.json",
        {
            "state": "ready",
            "pid": os.getpid(),
            "pid_namespace": "pid:[1]",
            "process_identity": environment_runner.process_identity(os.getpid()),
        },
    )
    assert environment_runner.read_status(directory)["state"] == "stopped"
