"""One root for everything the platform writes on a machine it borrows.

Two things have to hold for `cheese uninstall` to mean what it says. Everything
the platform puts on the machine has to be under one directory, and everything
that names that directory has to name the same one.

Neither is free here. Five programs run ON the borrowed machine with nothing of
ours importable — one arrives on stdin and has no `__file__`, one is exec'd out
of a string, one is written out beside the room's files and run as a script, one
is the CLI the agent runs inside the sandbox, one is Go — so they carry copies
of the name rather than asking for it. A copy that drifts does
not crash: the room prepares and the probe reads `pending` until the deadline
runs out, or the teardown decides a room with a live executor never had one and
deletes the home out from under the daemon. These tests keep the copies honest,
and keep the paths the backend hands a machine inside the one root the connector
knows how to remove.
"""

import re
import uuid
from pathlib import Path

from app.domain.agent import (
    device_provider,
    environment_runner,
    machine_launcher,
    resource_cleanup,
)
from app.domain.agent.harness.claude_code.remote_execution import bootstrap
from app.domain.agent.place import footprint_root, session_platform_dirs

REPOSITORY = Path(__file__).resolve().parents[3]
SANDBOX_CLI = REPOSITORY / "backend/sandbox/cheese"


def test_the_shipped_programs_carry_the_root_that_place_chose():
    """The copies that run on the machine, held to the one that chooses.

    Two names, because the machine has two layers and only one of them is the
    platform's to delete. Under the machine's own home there is the footprint
    root and nothing else; the second directory is the platform's older one
    INSIDE a session home, which is itself under the footprint root.
    """
    assert resource_cleanup.FOOTPRINT_ROOT == footprint_root()
    assert tuple(resource_cleanup.PLATFORM_DIRS) == session_platform_dirs()
    assert tuple(environment_runner.PLATFORM_DIRS) == session_platform_dirs()
    assert (bootstrap.PLATFORM_DIR, bootstrap.PREVIOUS_PLATFORM_DIR) == (
        session_platform_dirs()
    )


def test_the_connector_uninstalls_the_root_the_platform_writes():
    """The connector is Go, so its copy is the one nothing can type-check.

    It is also the copy that matters most: the connector's `uninstall` is the
    only thing on a borrowed machine that ever removes the footprint, and a name
    that has drifted removes nothing while reporting that it did. Only the name
    is checked here; that `uninstall` still reaches `removeFootprint` is checked
    in Go, where the identifier resolves — `cli/internal/daemoncmd/daemoncmd_test.go`.

    One declaration on that side too: `cli/internal/place` is read by the
    uninstall and by the writer that refuses a server-sent file aimed outside
    the root, and neither spells it itself.
    """
    source = (REPOSITORY / "cli/internal/place/place.go").read_text()
    declared = re.search(r'Root\s*=\s*"([^"]+)"', source)
    assert declared, "the connector stopped declaring a footprint root"
    assert declared.group(1) == footprint_root()


# What the sandbox CLI hangs off its own home: `Path.home() / "x"` and
# `os.path.join(home, "x")`.
SANDBOX_HOME_DIR = re.compile(r'(?:Path\.home\(\) /|os\.path\.join\(home,)\s*"([^"]+)"')

# The environment runner's own state directory, which the CLI reads for the
# room's configuration. It sits in the room's home rather than beside the root,
# and `place.py` does not choose its name, so this module does not hold it.
RUNNER_STATE_DIR = ".cheese-environment"


def test_the_sandbox_cli_carries_the_root_that_place_chose():
    """The copy inside the container, which is read as text and not imported.

    `backend/sandbox/cheese` is the CLI the agent calls from inside the sandbox.
    It is shipped into the container as a standalone stdlib-only script with
    nothing of ours importable beside it, so it spells the root the same way the
    other three do — and it is the copy whose drift is loudest: the runner it
    goes looking for is written under whichever root prepared the room, so a
    name that has moved means the agent cannot open a task at all.
    """
    source = SANDBOX_CLI.read_text()
    declared = re.search(r"ENVIRONMENT_RUNNER_PATHS = \(([^)]*)\)", source)
    assert declared, "the sandbox CLI stopped declaring where the runner may be"
    assert tuple(re.findall(r'"([^"]+)"', declared.group(1))) == (
        session_platform_dirs()
    )
    for spelled in sorted(
        {name.split("/")[0] for name in SANDBOX_HOME_DIR.findall(source)}
        - {RUNNER_STATE_DIR}
    ):
        assert spelled in session_platform_dirs(), (
            f"the sandbox CLI writes into ~/{spelled}, which is not a root "
            "`place.py` chose"
        )


class RecordingHub:
    """A device that answers the probe and remembers what it was asked to run."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    async def exec(self, device_id, command, **_kwargs):
        self.commands.append(command)
        return {"exit": 0, "stdout": '{"state":"pending"}'}


def inside_the_footprint(text: str) -> None:
    """Every `$HOME`-relative path in `text` hangs off the footprint root.

    The root, not the roots: what `cheese uninstall` removes is one directory,
    so the earlier root is somewhere a path may still be *found* and never
    somewhere a new one may be *written*.
    """
    root = f"$HOME/{footprint_root()}"
    for reference in re.findall(r"\$HOME/[A-Za-z0-9._/-]+", text):
        assert reference == root or reference.startswith(f"{root}/"), (
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


def test_the_teardown_looks_where_the_launcher_built():
    """The other side of the same paths, which no other test here reaches.

    `device_provider` builds a room's home and work tree under the footprint
    root; `resource_cleanup` is what deletes them, and it runs on the machine
    with nothing of ours importable, so it carries its own copy of the root.
    The two copies agreeing is not enough on its own — the teardown could carry
    the right root and still spell a path with a literal — so what is checked
    here is the path it actually builds, against the root `place.py` chose.

    A teardown pointed one directory over does not fail. Every check it runs
    passes vacuously on paths that do not exist, the room is marked reclaimed,
    and the home and worktree it was supposed to remove — hundreds of gigabytes
    on a machine that has run a project for a while — stay where they are with
    nothing left that knows to look for them.
    """
    machine_home = Path("/machine-home")
    for path in resource_cleanup.resource_paths(
        machine_home, str(uuid.uuid4()), str(uuid.uuid4())
    ):
        inside_the_footprint("$HOME" + str(path).removeprefix(str(machine_home)))


# Where the launch script puts something on the machine. Two shapes, because a
# shell write verb takes its target at either end: right after the verb for a
# redirection or a `mkdir -p`/`touch`/`tee`, and at the far end of the line for
# `mv`/`cp`. The space after the verb is optional because the launcher leaves it
# out where the target is a log (`>"$HOME/..."`), and a guard that reads only
# the spaced form is a guard with two of the script's own write points already
# outside it.
#
# Reads are not write points, and neither is the `rm -rf "$HOME/Library/Caches/uv"`
# a room does to its own caches on the way up — those are the machine owner's
# directories, cleared rather than installed into, and naming them here would
# demand the platform own them.
LAUNCHER_REDIRECT = re.compile(
    r'(?:>>?|mkdir -p|touch|tee)\s*"\$(?:REAL_)?HOME/([^"/]+)'
)
LAUNCHER_MOVE = re.compile(r"^[^#\n]*?\b(?:mv|cp)\b(?P<arguments>[^\n]*)$", re.M)
HOME_ARGUMENT = re.compile(r'"\$(?:REAL_)?HOME/([^"/]+)')


def launcher_write_points(script: str) -> list[str]:
    """Every directory under the machine's home the launch script writes into."""
    written = LAUNCHER_REDIRECT.findall(script)
    for line in LAUNCHER_MOVE.finditer(script):
        # `mv`/`cp` read their first arguments and write the last one.
        written.extend(HOME_ARGUMENT.findall(line.group("arguments"))[-1:])
    return written


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
    written = launcher_write_points(script)
    assert written, "the launcher stopped writing anything under the home"
    for directory in written:
        assert directory == footprint_root(), (
            f"the launcher installs into $HOME/{directory}, which is outside "
            "the footprint root the connector removes"
        )


EXPORTED_HOME = re.compile(r'export HOME="([^"]+)"')


def as_the_machine_reads_it(command: str) -> str:
    """The command with the `$HOME` its own shell will see already filled in.

    A command that opens with `export HOME="<the room's home>"` spells two
    different directories `$HOME`: the machine owner's, once, inside that
    assignment, and the room's home in everything after it. Reading them as one
    is how `$HOME/.claude/cheese-environment.py` looks like a path outside the
    root — it is a file in the room's home, which is itself inside the root, and
    resolving the assignment is what says so.
    """
    exported = EXPORTED_HOME.search(command)
    if not exported:
        return command
    head, tail = command[: exported.end()], command[exported.end() :]
    return head + tail.replace("$HOME", exported.group(1))


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
        inside_the_footprint(as_the_machine_reads_it(" ".join(command)))
