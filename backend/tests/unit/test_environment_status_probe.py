"""`environment_status` must find the runner wherever the launcher put it.

Two launchers ship the same `cheese-environment.py` to two directories: the
machine launcher writes `$HOME/.cheese/cheese-environment.py`, the claude-code
remote-execution payload writes `$HOME/.claude/cheese-environment.py`. Both
copies are byte-identical and both read and write the same
`$HOME/.cheese-environment/status.json`, so whichever one is on disk can answer.

The probe used to look only under `.cheese/`, so a place prepared by the other
launcher read as `{"state": "pending"}` forever — the room waited out its whole
deadline with `"state": "ready"` sitting on disk one directory over.
"""

import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.domain.agent.device_provider import environment_status
from app.domain.agent.harness.channel import ScreenSetupError


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

    `shipped_by` names the directories a launcher has dropped the runner into —
    ".cheese" for the machine launcher, ".claude" for remote execution.
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


async def test_probe_finds_the_runner_remote_execution_ships(tmp_path):
    """The regression: cc prepares `.claude/`, and the room must still come up."""
    place = _place(tmp_path, shipped_by=(".claude",))

    assert await _probe(place) == {"state": "ready"}


async def test_probe_still_finds_the_runner_the_machine_launcher_writes(tmp_path):
    place = _place(tmp_path, shipped_by=(".cheese",))

    assert await _probe(place) == {"state": "ready"}


async def test_machine_launcher_copy_wins_when_both_are_on_disk(tmp_path):
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
