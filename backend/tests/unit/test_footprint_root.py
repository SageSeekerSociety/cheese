"""One root for everything the platform writes on a machine it borrows.

Two things have to hold for `cheese uninstall` to mean what it says. Everything
the platform puts on the machine has to be under one directory, and everything
that names that directory has to name the same one.

Neither is free here. Several programs run ON the borrowed machine with nothing
of ours importable — one arrives on stdin and has no `__file__`, one is exec'd
out of a string, one is Go — so they carry copies of the name rather than asking
for it. A copy that drifts does not crash: the room prepares and the probe reads
`pending` until the deadline runs out, or the teardown decides a room with a live
executor never had one and deletes the home out from under the daemon. These
tests keep the copies honest, and keep the paths the backend hands a machine
inside the one root the connector knows how to remove.
"""

import re
import uuid
from pathlib import Path

from app.domain.agent import device_provider, environment_runner, machine_launcher
from app.domain.agent.harness.claude_code.remote_execution import bootstrap
from app.domain.agent.place import footprint_dirs, footprint_root
from app.domain.agent.resource_cleanup import PLATFORM_DIRS

REPOSITORY = Path(__file__).resolve().parents[3]


def test_the_shipped_programs_carry_the_root_that_place_chose():
    """The copies that run on the machine, held to the one that chooses."""
    assert tuple(PLATFORM_DIRS) == footprint_dirs()
    assert tuple(environment_runner.PLATFORM_DIRS) == footprint_dirs()
    assert (bootstrap.PLATFORM_DIR, bootstrap.PREVIOUS_PLATFORM_DIR) == footprint_dirs()


def test_the_connector_uninstalls_the_root_the_platform_writes():
    """The connector is Go, so its copy is the one nothing can type-check.

    It is also the copy that matters most: the connector's `uninstall` is the
    only thing on a borrowed machine that ever removes the footprint, and a name
    that has drifted removes nothing while reporting that it did. Only the name
    is checked here; that `uninstall` still reaches `removeFootprint` is checked
    in Go, where the identifier resolves — `cli/internal/daemoncmd/daemoncmd_test.go`.
    """
    source = (REPOSITORY / "cli/internal/daemoncmd/daemoncmd.go").read_text()
    declared = re.search(r'footprintRoot\s*=\s*"([^"]+)"', source)
    assert declared, "the connector stopped declaring a footprint root"
    assert declared.group(1) == footprint_root()


class RecordingHub:
    """A device that answers the probe and remembers what it was asked to run."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    async def exec(self, device_id, command, **_kwargs):
        self.commands.append(command)
        return {"exit": 0, "stdout": '{"state":"pending"}'}


def inside_the_footprint(text: str) -> None:
    """Every `$HOME`-relative path in `text` hangs off a footprint directory."""
    roots = tuple(f"$HOME/{root}" for root in footprint_dirs())
    for reference in re.findall(r"\$HOME/[A-Za-z0-9._/-]+", text):
        inside = reference in roots or reference.startswith(
            tuple(f"{root}/" for root in roots)
        )
        assert inside, (
            f"{reference} is outside the footprint root, so `cheese uninstall` "
            "walks past it"
        )


def test_the_directories_a_room_is_given_are_inside_the_footprint():
    """What an uninstall has to find, it has to be able to find in one place.

    The room's home, its work tree and the project's shared package store are
    the big ones — measured in hundreds of gigabytes on a machine that has run
    a project for a while. Each one that hangs off `$HOME` somewhere else is a
    directory `cheese uninstall` walks past on a machine whose owner has just
    been told the platform is gone.
    """
    project, room = uuid.uuid4(), uuid.uuid4()
    for path in (
        device_provider.device_home_dir(project, room),
        device_provider.device_work_dir(project, room),
        device_provider.device_store_dir(project),
        device_provider.launcher_path(room),
    ):
        inside_the_footprint(path)


# Where the launch script puts something on the machine: a redirection into
# `$HOME`/`$REAL_HOME`, or a directory made under one of them. Reads are not
# write points, and neither is the `rm -rf "$HOME/Library/Caches/uv"` a room
# does to its own caches on the way up — those are the machine owner's
# directories, cleared rather than installed into, and naming them here would
# demand the platform own them.
LAUNCHER_WRITE = re.compile(r'(?:>>?|mkdir -p) "\$(?:REAL_)?HOME/([^"/]+)')


def test_the_launcher_installs_only_inside_the_footprint():
    """The one place the root is spelled out literally, held to the chosen one.

    The launch script is a single shell string that names the root some sixty
    times, which is why it spells it rather than threading a name through. So
    nothing but this test stands between a move of the root and a launcher that
    goes on installing the room's own programs — the runner, the hook, the CLI,
    the drain, the tunnels — where `cheese uninstall` will not look. Nothing
    fails when that happens: the room launches, works, and leaves its files on
    a machine whose owner has been told the platform is gone.
    """
    script = machine_launcher.launch_script(command="$AGENT")
    written = LAUNCHER_WRITE.findall(script)
    assert written, "the launcher stopped writing anything under the home"
    for directory in written:
        assert directory == footprint_root(), (
            f"the launcher installs into $HOME/{directory}, which is outside "
            "the footprint root the connector removes"
        )


async def test_what_the_backend_asks_a_machine_to_run_stays_inside_the_footprint():
    """The same, for the commands rather than the paths.

    The environment probe both reads the machine and writes to it (the reset
    marker), and it is the path that has to keep working across a move of the
    root — so it is the one where a stray `$HOME/somewhere-else` would be least
    visible and most expensive.
    """
    hub = RecordingHub()
    await device_provider.environment_status(
        hub, "device", uuid.uuid4(), uuid.uuid4(), action="reset"
    )
    assert hub.commands, "the probe stopped asking the machine anything"
    for command in hub.commands:
        inside_the_footprint(" ".join(command))
