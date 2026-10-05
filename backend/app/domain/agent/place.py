"""A place: what of ours is on a machine we borrow, and what it can do.

Where the platform's own files sit
----------------------------------

A room's machine belongs to someone else. Everything the platform installs on
it — the executor and its helpers, the CLI, the environment runner, the launch
scripts, the session homes, the shared package store — goes under one root, so
that removing the platform from a machine is deleting one directory rather than
recalling every path some launcher ever wrote. That root is `footprint_root()`,
directly under the machine owner's own `$HOME`, and it is the whole of what
`cheese uninstall` removes.

Inside a session home there is a second name to read. `.claude` is the root
earlier launchers installed into, and a room does not move: it keeps its
executor, its markers and its helpers where the launcher that prepared it put
them until something prepares it again. Reading only the current root answers
"this room never had an executor" for a room that has one running. So the
current root is what we write, and `session_platform_dirs()` is what we read —
in this order, most recent first.

That pair exists INSIDE A SESSION HOME and nowhere else. On the machine's own
`$HOME` the platform has only ever written `footprint_root()`; the `~/.claude`
beside it is the machine owner's — their Claude Code credentials, their
settings, every transcript they have — and nothing here writes it, moves it or
removes it. An uninstall walks the footprint root and stops.

Six programs cannot ask this module and carry the names themselves: the
environment runner, the cleanup script, the executor bootstrap, the session
transfer and the sandbox CLI (`backend/sandbox/cheese`) all run where nothing of
ours is importable — two are piped in on stdin and have no `__file__` to look
at, one is written out beside the room's own files and run as a script, one is
exec'd out of a string, one is shipped into the agent's container as a
standalone program — and the connector is Go. Their copies are checked against
this module by `backend/tests/unit/test_footprint_root.py`. The values are
chosen here and nowhere else.

What it can do
--------------

The same module answers the other question about that borrowed machine: what
this place gives out. The capability a place can be asked for is named here, so
that the one physical fact behind it — are the hands the very machine the
session process runs on — is what upstream reads, and nothing has to ask a
channel which class it is (结论 24).
"""

import hashlib
import uuid

_ROOT = ".cheese"

# The directory the platform installed into inside a session home before the
# root moved. Session homes only: see the module docstring.
_PREVIOUS_SESSION_DIR = ".claude"


def footprint_root() -> str:
    """The one directory the platform writes under a machine's own `$HOME`."""
    return _ROOT


def session_platform_dirs() -> tuple[str, ...]:
    """The platform's directories inside a SESSION home, current one first."""
    return (_ROOT, _PREVIOUS_SESSION_DIR)


# What a room's HOME is SHARED out of, and what a seat keeps for itself.
#
# A room's teammates share the machine, the checkout and the project store, and
# everything that says where any of those are. They share none of what a TURN
# writes: the forwarded-fs token, the execution target, the harness's system
# prompt, and every file the execution client derives from the config beside
# it (its `tmp/`, `plugin/`, `forwarded-project`, `mcp.json`, the shell prefix).
# Those belong to one seat's session, and two seats writing one path is how a
# room's second teammate starting a turn used to kill the first one's: the
# token was replaced under a turn that was mid-flight (its command died with
# exit 137) and the session came back pointed at the other seat's target.
#
# So the files that are per-seat live under this directory, one child per seat,
# inside the room's home. The room keeps shared transcripts in
# `$HOME/.claude/projects`, plus `$HOME/.cheese/executor`,
# `.cheese-environment/status.json` and the `room/` checkout. Programs that
# resolve room paths still find those (`release.stage`'s busy scan,
# `session_transfer`, `resource_cleanup`). Claude settings, skills and execution
# helpers belong to a seat: a new launch must not replace a busy peer's files.
SEATS_DIR = "seats"


def seat_key(agent_handle: str, task_id: object = None) -> str:
    """Which seat a session takes: the agent's in its room, or that agent's
    in one task of the room.

    A task's session is a conversation of its own beside the room's, for the
    same agent on the same machine, and it writes everything a seat writes —
    its launcher, its state, its credential, its prompt. Keyed by the handle
    alone, a task starting wrote the room session's credential with its own,
    and the room's agent was refused everywhere as working another
    conversation, while the task's launcher came up on the room's state and
    never answered.
    """
    return agent_handle if task_id is None else f"{agent_handle}@{task_id}"


def seat_name(seat: str) -> str:
    """One seat's name under `SEATS_DIR`, derived from its key (`seat_key`).

    The same sha256 the launcher file and the runner's state directory are
    named by (`launcher_path`,
    `machine_launcher.state_dir`): one key, one name, everywhere. An empty
    key is the room's own seat — a screen from before seats, a probe, a
    fixture — and hashes to a name of its own rather than to any teammate's.
    """
    return hashlib.sha256(seat.encode()).hexdigest()[:12]


def launcher_path(topic_id: uuid.UUID, seat: str = "") -> str:
    """The launcher file a screen runs, where `_ship_launcher` writes it.

    One per SEAT (`seat_key`), not one per room. The file says where that
    seat's runner keeps its state (``CLAUDE_STATE``); a screen reads it exactly
    once, at birth, and another seat's turn writing it in between leaves the
    first one coming up on the wrong state — its own socket, the one every
    later call dials, never bound. ``seat`` empty is the room's own file: a
    caller that has no seat (recovery of a screen from before seats) asks for
    the room's.
    """
    return f"$HOME/{_ROOT}/launch/{topic_id}-{seat_name(seat)}.sh"


def seat_dir(home: str, seat: str = "") -> str:
    """Where one seat's own files go inside the room's home.

    ``home`` is the room's home as the backend names it — a path whose literal
    ``$HOME`` only the machine can resolve (`device_provider.device_home_dir`)
    — so the answer keeps that placeholder and stays a path the device's own
    shell is the one to expand.
    """
    return f"{home}/{_ROOT}/{SEATS_DIR}/{seat_name(seat)}"


# The directory inside a session's home that files fetched for the agent go
# into. The checkout the agent works in is that home's `room/`
# (`device_provider._work_dir`), so a name chosen here is a name chosen NOT to
# be in it.
#
# The sandbox CLI carries a copy (`backend/sandbox/cheese`): `cheese library
# get` writes its default here rather than into the work tree, and the two
# copies are held together by `tests/unit/test_footprint_root.py`.
STAGED_DIR = "attachments"

#: The checkout, relative to a session's home — **the name is chosen here**, and
#: the code that creates the directory reads it from here. That is the whole
#: point: a rule that names the directory it excludes is only checkable while
#: the name it excludes and the name the checkout actually gets are the same
#: string. Declared beside the rule and read by nobody who makes the directory,
#: it would go on rejecting `room/` after a rename while waving the real
#: checkout through.
#:
#: `device_provider._work_dir` builds the path the backend hands a machine.
#: Three programs run ON the machine with nothing of ours importable — the
#: executor bootstrap, the cleanup script and the session transfer — so they
#: carry copies, held to this one by `tests/unit/test_footprint_root.py`, the
#: same way they already carry the footprint root.
#:
CHECKOUT_DIR = "room"

#: Under the footprint root, beside the rooms and never inside one: which rooms
#: run in a sandbox, a file per room naming the release it was started from
#: (`remote_execution/bootstrap.record_sandbox`). A sandboxed room can write all
#: of its home, so what runs outside its sandbox for it — the teardown, the
#: environment reset — reads this to take its programs from that release, which
#: no room can write, rather than from the room. Copied by the bootstrap and the
#: cleanup script, held to this one by `tests/unit/test_footprint_root.py`.
SANDBOXES_DIR = "sandboxes"
