"""A session's isolated environment on a Mac a person enrolled (#2320 step 2):
`sandbox-exec`. Collected only on macOS (`tests/unit/conftest.py`).
"""

import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from app.domain.agent import resource_cleanup as cleanup
from app.domain.agent.harness.claude_code.remote_execution import bootstrap, runtime

# The same fixtures as the Linux tests, by name: their parameters below shadow
# these imports, which is how pytest finds them.
from tests.unit.test_device_sandbox import owner, rooms  # noqa: F401

pytestmark = pytest.mark.skipif(
    not os.access(bootstrap.SANDBOX_EXEC, os.X_OK), reason="needs sandbox-exec"
)


def _within(path: str, directory: Path) -> bool:
    return Path(os.path.realpath(path)).is_relative_to(os.path.realpath(directory))


def test_a_session_on_a_mac_sees_its_own_home_and_none_of_the_persons(owner, rooms):  # noqa: F811
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


def test_a_session_on_a_mac_signals_and_dials_nothing_of_the_persons(rooms):  # noqa: F811
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


def test_the_teardown_stops_a_sandboxed_rooms_executor_on_a_mac(rooms):  # noqa: F811
    """Stopping a room ends its executor: afterwards nothing answers on it."""
    room = rooms()
    assert room.bash("echo up") == "up"

    room.stop()

    with pytest.raises(OSError):
        runtime.request(room.state, "ping")


def _detach(room, marker: str) -> None:
    """Have the room set `marker` loose: a new session, adopted by launchd."""
    detach = (
        "import os\n"
        "if os.fork() == 0:\n"
        "    os.setsid()\n"
        "    if os.fork() == 0:\n"
        "        devnull = os.open(os.devnull, os.O_RDWR)\n"
        "        for fd in (0, 1, 2):\n"
        "            os.dup2(devnull, fd)\n"
        f"        os.execvp('sleep', {marker.split()!r})\n"
        "    os._exit(0)\n"
    )
    room.bash(f"python3 -c {shlex.quote(detach)}")
    _wait_until(lambda: _running(marker), "the detached process never started")


def _running(marker: str) -> bool:
    listing = subprocess.run(
        ["ps", "-A", "-o", "args="], capture_output=True, text=True
    )
    return any(line.strip() == marker for line in listing.stdout.splitlines())


def _wait_until(condition, what: str) -> None:
    deadline = time.monotonic() + 10
    while not condition():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def test_the_teardown_ends_what_a_room_detached_on_a_mac(rooms):  # noqa: F811
    """A process the room set loose (a new session, adopted by launchd, out of
    every tree of the room's) still ends with the room."""
    room = rooms()
    marker = f"sleep {32000 + os.getpid() % 1000}"
    _detach(room, marker)

    room.stop()

    _wait_until(lambda: not _running(marker), "the detached process outlived its room")


def test_a_rooms_teardown_on_a_mac_ends_nothing_of_another_room_or_the_person(
    rooms,  # noqa: F811
):
    """What the teardown ends is told by the room's own sandbox: another room's
    process, sandboxed too, and the person's own, which is not, run on."""
    room, neighbour = rooms(), rooms()
    base = 33000 + os.getpid() % 1000
    mine, theirs, persons = f"sleep {base}", f"sleep {base + 1}", f"sleep {base + 2}"
    _detach(room, mine)
    _detach(neighbour, theirs)
    person = subprocess.Popen(persons.split())
    try:
        room.stop()

        _wait_until(lambda: not _running(mine), "the room's process outlived it")
        assert _running(theirs)
        assert person.poll() is None
    finally:
        person.kill()
        neighbour.stop()


def test_teardown_git_on_a_mac_runs_inside_the_rooms_directories(owner, rooms):  # noqa: F811
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
