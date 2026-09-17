"""`environment_status` must find the runner wherever a launcher put it.

Every copy of `cheese-environment.py` is byte-identical and every one of them
reads and writes the same `$HOME/.cheese-environment/status.json`, so whichever
one is on disk can answer for the place. What changes is where it was left: a
launcher decides that, and a place keeps whatever the launcher that prepared it
chose until something prepares it again. A root the platform has moved off is
therefore still live on every room that has not relaunched since.

The probe used to look under one directory only, so a place prepared by another
launcher read as `{"state": "pending"}` forever — the room waited out its whole
deadline with `"state": "ready"` sitting on disk one directory over. The last
test here is what keeps that from coming back: the write side may not move
without the read side.
"""

import importlib.util
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from app.domain.agent import environment_runner, machine_launcher, resource_cleanup
from app.domain.agent.device_provider import (
    ENVIRONMENT_RUNNER_PATHS,
    environment_status,
)
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.agent.harness.claude_code.remote_execution import bootstrap
from app.domain.agent.harness.claude_code.remote_execution.launch import payload_for


@dataclass
class Place:
    """A device with one place on it, and that place's isolated home."""

    device_home: Path  # the device's own $HOME, what the probe's shell expands
    home: Path  # $device_home/.cheese/home/<project>/<topic>
    project_id: uuid.UUID
    topic_id: uuid.UUID


class ShellHub:
    """Runs the probe's command for real, with this test's $HOME on the machine.

    The child gets a deliberately small environment — a real remote shell has
    its own, and inheriting this process's would let a variable some earlier
    test exported decide the answer here.
    """

    def __init__(self, device_home: Path) -> None:
        self.device_home = str(device_home)
        self.commands: list[list[str]] = []

    async def exec(
        self, device_id, argv, *, cwd=None, env=None, timeout=60, stdin=None
    ) -> dict:
        self.commands.append(argv)
        done = subprocess.run(
            argv,
            env={**os.environ, "HOME": self.device_home},
            capture_output=True,
            text=True,
        )
        return {
            "stdout": done.stdout,
            "stderr": done.stderr,
            "exit": done.returncode,
            "truncated": False,
        }


def _place(
    tmp_path, *, shipped_by=(), state='{"state": "ready"}', exit_code=0
) -> Place:
    """A device $HOME with one place in it, prepared by the named launchers.

    `shipped_by` names the directories a launcher has dropped the runner into:
    ".cheese" is where every launcher writes it today, ".claude" where a room
    prepared before the platform moved its own files still has it.
    """
    project_id, topic_id = uuid.uuid4(), uuid.uuid4()
    home = tmp_path / ".cheese" / "home" / str(project_id) / str(topic_id)
    home.mkdir(parents=True)
    for directory_name in shipped_by:
        directory = home / directory_name
        directory.mkdir()
        (directory / "cheese-environment.py").write_text(
            f"import sys\nprint({state!r})\nsys.exit({exit_code})\n"
        )
    return Place(tmp_path, home, project_id, topic_id)


async def _probe(place: Place, **kwargs):
    return await environment_status(
        ShellHub(place.device_home), "dev1", place.project_id, place.topic_id, **kwargs
    )


async def test_probe_finds_the_runner_a_previous_root_left(tmp_path):
    """The regression: a room prepared under the old root must still come up."""
    place = _place(tmp_path, shipped_by=(".claude",))

    assert await _probe(place) == {"state": "ready"}


async def test_probe_finds_the_runner_the_launchers_write_today(tmp_path):
    place = _place(tmp_path, shipped_by=(".cheese",))

    assert await _probe(place) == {"state": "ready"}


async def test_the_current_root_wins_when_both_are_on_disk(tmp_path):
    place = _place(tmp_path, shipped_by=(".cheese",))
    other = place.home / ".claude"
    other.mkdir()
    (other / "cheese-environment.py").write_text('print(\'{"state": "failed"}\')\n')

    assert await _probe(place) == {"state": "ready"}


async def test_probe_still_reports_pending_when_nothing_was_shipped(tmp_path):
    place = _place(tmp_path)

    assert await _probe(place) == {"state": "pending"}


async def test_a_runner_that_fails_still_raises(tmp_path):
    place = _place(
        tmp_path, shipped_by=(".claude",), state='{"state": "pending"}', exit_code=3
    )

    with pytest.raises(ScreenSetupError):
        await _probe(place)


async def test_reset_still_leaves_the_restart_marker(tmp_path):
    """The marker is touched *after* the runner runs — don't return early."""
    place = _place(tmp_path, shipped_by=(".claude",))

    await _probe(place, action="reset")

    assert (place.home / ".cheese" / "environment-restart").exists()


async def test_probe_asks_the_runner_for_the_action_it_was_given(tmp_path):
    place = _place(tmp_path, shipped_by=(".claude",))
    hub = ShellHub(place.device_home)

    await environment_status(
        hub, "dev1", place.project_id, place.topic_id, action="prepare", wait_ready=True
    )

    assert "prepare" in hub.commands[0][-1]
    assert "CHEESE_STATUS_WAIT=1" in hub.commands[0][-1]


def test_the_cli_looks_for_the_runner_where_the_probe_does():
    """The CLI runs the runner too, from inside the place, and cannot import
    this module — it ships into the sandbox as a stdlib-only script. So its
    candidates are a second copy, and a copy that drifts is exactly how a room
    on a machine prepared by the other launcher stopped being able to open a
    task at all: the write side moved and the read side did not."""
    loader = SourceFileLoader(
        "cheese_cli", str(Path(__file__).resolve().parents[2] / "sandbox" / "cheese")
    )
    cli = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(loader.name, loader)
    )
    loader.exec_module(cli)

    assert [
        f"$HOME/{directory}/cheese-environment.py"
        for directory in cli.ENVIRONMENT_RUNNER_PATHS
    ] == list(ENVIRONMENT_RUNNER_PATHS)


def test_every_launcher_writes_the_runner_where_the_probe_looks():
    """The two sides of one fact, pinned to each other.

    A launcher that installs the runner somewhere this list does not name is
    invisible rather than broken: the room really does prepare, the status
    really is written, and the probe answers `pending` until the deadline runs
    out. Nothing fails, so nothing says so. Read out of what each launcher
    actually ships, not restated here, because a second copy of the answer is
    the thing that drifts.
    """
    written = set(
        re.findall(
            r'cat > "\$HOME/([^/"]+)/cheese-environment\.py"',
            machine_launcher.launch_script(command="$AGENT"),
        )
    )
    assert written, "the machine launcher stopped writing the runner"
    # The executor's payload names the file; its bootstrap picks the directory.
    payload = payload_for(uuid.uuid4(), uuid.uuid4(), {})
    assert "cheese-environment.py" in payload["file_names"]
    written.add(bootstrap.PLATFORM_DIR)

    assert {f"$HOME/{directory}/cheese-environment.py" for directory in written} <= set(
        ENVIRONMENT_RUNNER_PATHS
    )


def test_every_reader_of_the_platform_root_accepts_every_root_written():
    """One fact, spelled in four places that cannot import each other.

    Three of these ship to a machine as standalone programs — the environment
    runner, the cleanup script, the executor bootstrap — so the roots are copied
    rather than shared, and a copy that drifts gives a silent wrong answer
    rather than crashing. The probe drifting stalls a room at `pending`. The
    cleanup script drifting is worse: a room installed under a root it does not
    read is torn down as though it never had an executor, so the detached daemon
    is left running under a home that is then deleted, the private seat it holds
    is never released, and publication is checked on the wrong branch.
    """
    written = {bootstrap.PLATFORM_DIR, bootstrap.PREVIOUS_PLATFORM_DIR}
    assert machine_launcher.PLATFORM_DIR in written

    readers = {
        "environment_status probe": [
            path.split("/")[1] for path in ENVIRONMENT_RUNNER_PATHS
        ],
        "environment runner reset": list(environment_runner.PLATFORM_DIRS),
        "resource cleanup": list(resource_cleanup.PLATFORM_DIRS),
    }
    for name, accepted in readers.items():
        assert written <= set(accepted), name
        # In precedence order: a room migrated mid-life has leftovers under
        # both, and must be read as the root it is actually installed in.
        assert accepted[0] == machine_launcher.PLATFORM_DIR, name
