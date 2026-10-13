"""A session's forwarded project view is let go once the session has ended.

The view is a FUSE mount served by a process of its own, started when a session
takes its seat. Nothing in the session's own lifetime ends that process, so
before this every seat that ever ran kept one, for as long as the host ran:
761 of them on dev's session host on 2026-10-13, ~20 GB resident, the oldest
16 days old, while 5 sessions were running.

The other half of the rule is the one that must never break: a session that is
still running keeps its view, whatever else is true.

These read the host's process table, so they need Linux's /proc. The mount
itself is the one thing stood in for: whether a path is mounted, and the
`fusermount` that takes it down.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code.remote_execution import (
    forwarded_fs,
    release,
)

pytestmark = pytest.mark.skipif(
    not Path("/proc/self/environ").exists(), reason="reads Linux's /proc"
)

SLEEP = "import time; time.sleep(60)"


@pytest.fixture
def mounts(monkeypatch):
    """Which views are mounted, and the unmounts asked for."""
    mounted = set()
    unmounted = []
    real_ismount = os.path.ismount

    def ismount(path):
        return Path(path) in mounted or real_ismount(path)

    def fusermount(argv, **_kwargs):
        unmounted.append(Path(argv[-1]))
        mounted.discard(Path(argv[-1]))

    monkeypatch.setattr(release.os.path, "ismount", ismount)
    monkeypatch.setattr(release.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(release.subprocess, "run", fusermount)
    return mounted, unmounted


@pytest.fixture
def processes():
    started = []

    def start(argv, directory):
        """A process of the session whose files are in ``directory``."""
        env = dict(
            os.environ, **{release.EXECUTION_CONFIG: str(directory / "execution.json")}
        )
        process = subprocess.Popen(argv, env=env)
        started.append(process)
        return process

    yield start
    for process in started:
        process.kill()
        process.wait()


def seat(root, name, mounts):
    """A seat's session directory whose view is mounted and served."""
    directory = root / name / "remote-session"
    view = directory / release.FORWARDED_VIEW
    view.mkdir(parents=True)
    mounts[0].add(view)
    return directory, view


def serve(directory, view, start, tmp_path):
    """The view's server: started by the session, so it carries the session's
    variable too, and it outlives the session."""
    program = tmp_path / release.VIEW_SERVER
    program.write_text(SLEEP)
    return start(
        [sys.executable, str(program), str(directory / "execution.json"), str(view)],
        directory,
    )


def stopped(process):
    process.terminate()
    process.wait()


def test_a_running_session_keeps_its_view_and_an_ended_one_lets_it_go(
    tmp_path, mounts, processes
):
    directory, view = seat(tmp_path, "seat", mounts)
    serve(directory, view, processes, tmp_path)
    build = processes([sys.executable, "-c", SLEEP], directory)

    assert release.release_abandoned(directory) is False
    assert mounts[1] == []

    stopped(build)

    assert release.release_abandoned(directory) is True
    assert mounts[1] == [view]


def test_a_view_just_taken_is_kept_until_its_session_can_start(
    tmp_path, mounts, processes
):
    """A caller may prepare in one process and start the build in another;
    between the two, nothing of the session is running yet."""
    directory, view = seat(tmp_path, "seat", mounts)
    with release.view_lock(directory, taking=True):
        pass

    assert release.release_abandoned(directory) is False

    taken = directory / f"{release.FORWARDED_VIEW}.taken"
    long_ago = time.time() - release.TAKEN_GRACE_S - 1
    os.utime(taken, (long_ago, long_ago))

    assert release.release_abandoned(directory) is True
    assert mounts[1] == [view]


def test_one_look_releases_every_abandoned_view_on_the_host(
    tmp_path, mounts, processes
):
    """The views served by a server from before the watch — which will never
    look for its session itself — go at the first look of any new one."""
    running, kept = seat(tmp_path, "running", mounts)
    serve(running, kept, processes, tmp_path)
    processes([sys.executable, "-c", SLEEP], running)
    ended = []
    for name in ("ended-1", "ended-2"):
        directory, view = seat(tmp_path, name, mounts)
        serve(directory, view, processes, tmp_path)
        stopped(processes([sys.executable, "-c", SLEEP], directory))
        ended.append(view)

    assert release.release_abandoned_views() == 2
    assert sorted(mounts[1]) == sorted(ended)
    assert kept in mounts[0]


def test_a_views_server_stops_watching_once_it_has_let_the_view_go(
    tmp_path, mounts, processes, monkeypatch
):
    # The server loads its siblings by bare name, beside it on a session host.
    monkeypatch.setitem(sys.modules, "release", release)
    directory, view = seat(tmp_path, "seat", mounts)
    serve(directory, view, processes, tmp_path)

    forwarded_fs.watch(directory, every=0)

    assert mounts[1] == [view]
