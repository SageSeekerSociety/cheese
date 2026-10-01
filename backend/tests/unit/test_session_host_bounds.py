"""What keeps one session from taking the session host down with it (#1544).

Every room's session shares the session host's kernel. These hold the three
bounds a session runs inside there: a memory cap of its own, temporary files
on disk that leave with the room, and no new session started on a host that
has no memory left for one.
"""

import os
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.domain.agent import admission, machine_launcher
from app.domain.agent import resource_cleanup as cleanup

HOST = "session-host"


def _room(tmp_path, *, cap: str | None = None, path: str | None = None):
    """A room on a machine whose own home is ``tmp_path/machine``, laid out the
    way the device channel lays it out, and the environment its screen gets."""
    project, resource = str(uuid.uuid4()), str(uuid.uuid4())
    machine = tmp_path / "machine"
    home, work = cleanup.resource_paths(machine, project, resource)
    (home / ".cheese").mkdir(parents=True)
    work.mkdir(parents=True)
    env = {
        **os.environ,
        "HOME": str(machine),
        "CHEESE_HOME": str(home),
        "CHEESE_WORK": str(work),
        "CHEESE_TOPIC": resource,
        "CHEESE_PROJECT": project,
        "CHEESE_TOKEN": "scoped-token",
        "CHEESE_TOKEN_EXPIRES": str(int(time.time()) + 3600),
    }
    if cap is not None:
        env["CHEESE_SESSION_MEMORY_MAX"] = cap
    if path is not None:
        env["PATH"] = f"{path}:{env['PATH']}"
    return machine, project, resource, env


def _launch(tmp_path, env, agent_script: str):
    agent = tmp_path / "agent.sh"
    agent.write_text("#!/bin/sh\n" + agent_script)
    agent.chmod(0o755)
    launcher = tmp_path / "launch.sh"
    launcher.write_text(
        machine_launcher.launch_script(prepare=f'AGENT="{agent}"\n', command="$AGENT")
    )
    return subprocess.run(
        ["sh", str(launcher)], env=env, capture_output=True, text=True, timeout=60
    )


def _systemd_run(tmp_path, *, makes_scopes: bool):
    """A ``systemd-run`` that records how it was asked to run something, and
    runs it the way ``--scope`` does: the command itself, in the foreground."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    asked = tmp_path / "systemd-run.args"
    fake = bin_dir / "systemd-run"
    fake.write_text(
        "#!/bin/sh\n"
        + ("" if makes_scopes else "exit 1\n")
        + f'echo "$@" >> "{asked}"\n'
        + 'while [ "$1" != "--" ] && [ "$1" != "true" ]; do shift; done\n'
        + '[ "$1" = "true" ] && exit 0\n'
        + 'shift\nexec "$@"\n'
    )
    fake.chmod(0o755)
    return str(bin_dir), asked


def test_a_capped_session_runs_under_its_cap(tmp_path):
    path, asked = _systemd_run(tmp_path, makes_scopes=True)
    _m, _p, _r, env = _room(tmp_path, cap="3072M", path=path)
    ran = tmp_path / "agent.ran"

    result = _launch(tmp_path, env, f'cat /proc/self/oom_score_adj > "{ran}"\n')

    assert result.returncode == 0, result.stderr
    scopes = [line for line in asked.read_text().splitlines() if "--" in line.split()]
    assert len(scopes) == 1
    assert "MemoryMax=3072M" in scopes[0].split()
    # And the kernel may kill it there, rather than stall at the cap: no
    # session inherits an exemption from whatever started it.
    assert int(ran.read_text()) > 0


def test_a_machine_without_user_scopes_still_runs_the_session(tmp_path):
    path, _asked = _systemd_run(tmp_path, makes_scopes=False)
    _m, _p, _r, env = _room(tmp_path, cap="3072M", path=path)
    ran = tmp_path / "agent.ran"

    result = _launch(tmp_path, env, f'touch "{ran}"\n')

    assert result.returncode == 0, result.stderr
    assert ran.exists()


def test_an_uncapped_session_is_not_put_in_a_scope(tmp_path):
    path, asked = _systemd_run(tmp_path, makes_scopes=True)
    _m, _p, _r, env = _room(tmp_path, path=path)

    result = _launch(tmp_path, env, "exit 0\n")

    assert result.returncode == 0, result.stderr
    assert not asked.exists()


def test_a_sessions_temporary_files_are_on_the_machines_disk_and_leave_with_the_room(
    tmp_path,
):
    machine, project, resource, env = _room(tmp_path)
    seen = tmp_path / "tmpdir.seen"

    result = _launch(
        tmp_path, env, f'echo "$TMPDIR" > "{seen}"\necho left > "$TMPDIR/left"\n'
    )
    assert result.returncode == 0, result.stderr
    tmpdir = seen.read_text().strip()
    assert tmpdir.startswith(str(machine / ".cheese"))
    assert os.path.exists(f"{tmpdir}/left")

    removed = subprocess.run(
        [sys.executable, cleanup.__file__, "remove", project, resource]
        + [str(uuid.uuid4()), "-"],
        env={**os.environ, "HOME": str(machine)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert removed.returncode == 0, removed.stderr
    assert not os.path.exists(tmpdir)


class _Hub:
    def __init__(self, meminfo: str | None, screens=()):
        self.meminfo = meminfo
        self.screens = list(screens)
        self.reads = 0

    def screens_for_topic(self, topic_id):
        return [s for s in self.screens if s.topic_id == topic_id]

    async def exec(self, device_id, argv, **_):
        self.reads += 1
        if self.meminfo is None:
            raise TimeoutError
        return {"exit": 0, "stdout": self.meminfo}


def _meminfo(available_mb: int) -> str:
    return (
        "MemTotal:       65000000 kB\n"
        "MemFree:          100000 kB\n"
        f"MemAvailable:   {available_mb * 1024} kB\n"
    )


@pytest.fixture
def capped_host(monkeypatch):
    monkeypatch.setattr(settings, "agent_session_device_id", HOST)
    monkeypatch.setattr(settings, "agent_session_memory_max_mb", 3072)


@pytest.mark.anyio
async def test_a_host_without_memory_for_one_more_session_holds_a_new_one(
    capped_host,
):
    room = uuid.uuid4()
    assert not await admission.HostMemory(_Hub(_meminfo(2000))).has_room(room)
    assert await admission.HostMemory(_Hub(_meminfo(8000))).has_room(room)


@pytest.mark.anyio
async def test_a_room_whose_session_is_running_is_never_held(capped_host):
    room = uuid.uuid4()
    running = SimpleNamespace(topic_id=room, device_id=HOST)
    assert await admission.HostMemory(_Hub(_meminfo(10), [running])).has_room(room)


@pytest.mark.anyio
async def test_a_host_that_cannot_be_read_holds_nobody(capped_host):
    assert await admission.HostMemory(_Hub(None)).has_room(uuid.uuid4())


async def _room_on(db_factory, harness: str) -> uuid.UUID:
    from sqlalchemy import update

    from app.domain.project.models import Project
    from app.domain.topic.repositories import TopicRepository
    from tests.turn_log import a_topic

    topic_id = await a_topic(db_factory)
    async with db_factory() as session:
        topic = await TopicRepository(session).get(topic_id)
        await session.execute(
            update(Project)
            .where(Project.id == topic.project_id)
            .values(settings={"harness": harness})
        )
        await session.commit()
    return topic_id


@pytest.mark.anyio
async def test_only_a_turn_whose_session_starts_on_the_session_host_is_gated_there(
    db_factory, monkeypatch
):
    from app.core.config import settings
    from app.domain.agent.compute import build_compute_pool
    from app.domain.agent.harness import CLAUDE_CODE, PI
    from app.domain.agent.work_policy import work_policy

    monkeypatch.setattr(settings, "agent_harnesses", [CLAUDE_CODE, PI])
    pool = build_compute_pool()
    beside = await work_policy(db_factory, pool, await _room_on(db_factory, PI))
    central = await work_policy(
        db_factory, pool, await _room_on(db_factory, CLAUDE_CODE)
    )
    assert beside is not None and beside["on_session_host"] is False
    assert central is not None and central["on_session_host"] is True
