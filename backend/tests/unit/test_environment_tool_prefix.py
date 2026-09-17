"""项目的工具装一次，房间各自的 HOME 不变。

The setup script's only addressable output is `$HOME`, and a room's `$HOME` has
to be its own — so until now every room of a project installed its own copy of
the same toolchain. These tests drive `environment_runner.run()` the way a
device does and assert where the bytes land.
"""

import json
import os
import subprocess
import sys

import pytest

from app.domain.agent import environment_runner


def _machine(tmp_path, *, store=True):
    """A machine with two rooms of one project, and the project's store."""
    machine = tmp_path / "machine"
    rooms = {}
    for name in ("a", "b"):
        home = machine / ".cheese/home/proj" / name
        work = machine / ".cheese/work/proj" / name
        home.mkdir(parents=True)
        work.mkdir(parents=True)
        rooms[name] = (home, work)
    prefix = machine / ".cheese/store/proj/env" if store else None
    return rooms, prefix


def _env(monkeypatch, home, work, prefix):
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CHEESE_WORK", str(work))
    if prefix is None:
        monkeypatch.delenv("CHEESE_STORE", raising=False)
    else:
        monkeypatch.setenv("CHEESE_STORE", str(prefix.parent))


def _config(script, revision="r1"):
    return {
        "revision": revision,
        "variables": {},
        "setup_script": script,
        "startup_script": "",
    }


def _run(home, config, seen=None):
    """One launch. `adopt` stands in for the agent so nothing is exec'd, and it
    is also how we read the environment the AGENT would have been given."""
    captured = {}

    def adopt(environment, _work):
        captured.update(environment)
        return os.getpid()

    code = environment_runner.run(config, home / ".cheese-environment", [], adopt=adopt)
    if seen is not None:
        seen.update(captured)
    return code, captured


def _log(home):
    directory = home / ".cheese-environment"
    return "\n".join(path.read_text() for path in sorted(directory.glob("*.log")))


def test_two_rooms_of_one_project_install_the_tools_once(tmp_path, monkeypatch):
    """The saving itself. Both rooms run the same setup script; it installs once.

    Measured on dev 2026-09-17, this is what it was costing: one Node 22 per
    room at ~254MB across 228 rooms, and five rooms fetching the same 54MB
    tarball inside three hours.
    """
    rooms, prefix = _machine(tmp_path)
    tally = tmp_path / "ran"
    script = f'printf x >> "{tally}"\nmkdir -p "$HOME/.local/bin"\n'
    script += 'printf tool > "$HOME/.local/bin/thing"\n'

    for name in ("a", "b"):
        home, work = rooms[name]
        _env(monkeypatch, home, work, prefix)
        code, _ = _run(home, _config(script))
        assert code == 0, _log(home)

    assert tally.read_text() == "x", "the script ran more than once"
    assert (prefix / ".local/bin/thing").read_text() == "tool"
    for home, _work in rooms.values():
        assert not (home / ".local/bin/thing").exists()
    assert "reused successful initialization" in _log(rooms["b"][0])


def test_the_installer_gets_the_prefix_and_the_agent_keeps_its_room(
    tmp_path, monkeypatch
):
    """Only the installer's HOME moves. The agent's is still the room's, because
    everything that makes a room a room lives under it — the hook spool, the
    drain credential, the transcript."""
    rooms, prefix = _machine(tmp_path)
    home, work = rooms["a"]
    _env(monkeypatch, home, work, prefix)
    written = tmp_path / "script-home"

    code, agent = _run(home, _config(f'printf "%s" "$HOME" > "{written}"\n'))

    assert code == 0, _log(home)
    assert written.read_text() == str(prefix)
    assert agent["HOME"] == str(home)
    # And the agent can still reach what the installer put down.
    assert str(prefix / ".local/bin") in agent["PATH"].split(os.pathsep)


def test_a_machine_with_no_store_installs_into_the_room_as_before(
    tmp_path, monkeypatch
):
    """No store is not an error — it is every tool back on its own default,
    which is where they were before any of this existed."""
    rooms, _ = _machine(tmp_path, store=False)
    home, work = rooms["a"]
    _env(monkeypatch, home, work, None)
    written = tmp_path / "script-home"

    code, agent = _run(home, _config(f'printf "%s" "$HOME" > "{written}"\n'))

    assert code == 0, _log(home)
    assert written.read_text() == str(home)
    assert agent["HOME"] == str(home)
    assert (home / ".cheese-environment/initialized.json").exists()


# One launch, in a process of its own — `run()` installs signal handlers, which
# only the main thread may do, and a device runs it as its own process anyway.
_LAUNCH = """
import importlib.util, json, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("er", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.exit(
    module.run(
        json.loads(sys.argv[2]), Path(sys.argv[3]), [],
        adopt=lambda environment, work: os.getpid(),
    )
)
"""


def _launch(home, config, prefix, work):
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _LAUNCH,
            environment_runner.__file__,
            json.dumps(config),
            str(home / ".cheese-environment"),
        ],
        env={
            **os.environ,
            "HOME": str(home),
            "CHEESE_WORK": str(work),
            "CHEESE_STORE": str(prefix.parent),
        },
    )


def _hold(lock):
    """Hold `lock` the way another room's installer holds it."""
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl,sys,time\n"
            "f=open(sys.argv[1],'a')\n"
            "fcntl.flock(f, fcntl.LOCK_EX)\n"
            "sys.stdout.write('held\\n'); sys.stdout.flush()\n"
            "time.sleep(60)\n",
            str(lock),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "held"
    return holder


def test_a_room_waits_for_another_rooms_install_instead_of_failing(
    tmp_path, monkeypatch
):
    """Two rooms starting at once must not both install into one directory, and
    the one that loses must WAIT — its user is owed the tools, not an error.

    Its own `status.json` says preparing/setup throughout, so the room reads as
    「正在安装工具」 the whole time. That is true; it is just not this room doing
    the installing.
    """
    rooms, prefix = _machine(tmp_path)
    state = prefix / ".cheese-environment"
    state.mkdir(parents=True)
    holder = _hold(state / "lock")
    home, work = rooms["a"]
    waiter = None
    try:
        waiter = _launch(home, _config("true\n"), prefix, work)
        # Still waiting, not failed. 75 is what a second attempt in the SAME
        # room gets; another room's install is a different thing entirely.
        with pytest.raises(subprocess.TimeoutExpired):
            waiter.wait(timeout=3)
        assert "another room of this project is installing" in _log(home)

        holder.terminate()
        assert waiter.wait(timeout=60) == 0, _log(home)
    finally:
        holder.terminate()
        holder.wait(timeout=10)
        if waiter is not None and waiter.poll() is None:
            waiter.kill()


def test_a_room_that_waited_does_not_reinstall_what_it_waited_for(
    tmp_path, monkeypatch
):
    """The point of waiting. Whoever held the lock was installing the very thing
    this room wants, so the receipt has to be read AGAIN after the wait — the
    read before the wait happened when it was still stale. Without the second
    read both rooms install, one after the other, and the lock bought nothing.
    """
    rooms, prefix = _machine(tmp_path)
    home_a, work_a = rooms["a"]
    tally = tmp_path / "ran"
    script = f'printf x >> "{tally}"\n'

    _env(monkeypatch, home_a, work_a, prefix)
    assert _run(home_a, _config(script))[0] == 0
    assert tally.read_text() == "x"

    # Put room B where a room that is about to wait actually stands: the receipt
    # it can see is NOT the revision it wants, so it will go for the lock.
    receipt = prefix / ".cheese-environment/initialized.json"
    wanted = json.loads(receipt.read_text())
    environment_runner.write_json(receipt, "a-revision-nobody-installed")

    holder = _hold(prefix / ".cheese-environment/lock")
    home_b, work_b = rooms["b"]
    waiter = None
    try:
        waiter = _launch(home_b, _config(script), prefix, work_b)
        with pytest.raises(subprocess.TimeoutExpired):
            waiter.wait(timeout=3)
        assert "another room of this project is installing" in _log(home_b)

        # The room holding the lock finishes, leaving behind exactly what B
        # wants — which B cannot know until it reads the receipt again.
        environment_runner.write_json(receipt, wanted)
        holder.terminate()

        assert waiter.wait(timeout=60) == 0, _log(home_b)
    finally:
        holder.terminate()
        holder.wait(timeout=10)
        if waiter is not None and waiter.poll() is None:
            waiter.kill()

    assert "installed by another room" in _log(home_b)
    assert tally.read_text() == "x", "room B reinstalled what it had waited for"


@pytest.mark.parametrize("stage", ["startup"])
def test_a_tasks_startup_keeps_the_rooms_home(tmp_path, monkeypatch, stage):
    """`startup` prepares one task's checkout and writes into that checkout. It
    keeps the room's HOME: a shared one would let two tasks of one project race
    in a directory neither of them locks, and it buys nothing, because what a
    startup script installs lands in the checkout anyway."""
    rooms, prefix = _machine(tmp_path)
    home, work = rooms["a"]
    _env(monkeypatch, home, work, prefix)
    written = tmp_path / "startup-home"
    config = {
        "revision": "r1",
        "variables": {},
        "setup_script": "",
        "startup_script": f'printf "%s" "$HOME" > "{written}"\n',
    }
    task = work / "checkout"
    task.mkdir()

    code = environment_runner.run(
        config, home / ".cheese-environment/tasks/t", [], task_work=task
    )

    assert code == 0
    assert written.read_text() == str(home)
