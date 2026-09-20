"""Where the platform's own files sit on a machine it borrows.

A room's machine belongs to someone else. Everything the platform installs on
it — the executor and its helpers, the CLI, the environment runner, the launch
scripts, the session homes, the shared package store — goes under one root, so
that removing the platform from a machine is deleting one directory rather than
recalling every path some launcher ever wrote.

`.claude` is here because it is the root earlier launchers installed into, and a
room does not move: it keeps its executor, its markers and its helpers where the
launcher that prepared it put them until something prepares it again. Reading
only the current root answers "this room never had an executor" for a room that
has one running. So the current root is what we write, and both are what we
read — in this order, most recent first.

Three programs cannot ask this module and carry the name themselves: the
environment runner, the cleanup script and the executor bootstrap all run ON the
borrowed machine, where nothing of ours is importable — one is piped in on
stdin and has no `__file__` to look at, one is exec'd out of a string. Their
copies are checked against this module by
`backend/tests/unit/test_footprint_root.py`, and so is the connector's, which is
Go. The value is chosen here and nowhere else.
"""

_FOOTPRINT_DIRS = (".cheese", ".claude")


def footprint_root() -> str:
    """The directory name the platform installs into today."""
    return _FOOTPRINT_DIRS[0]


def footprint_dirs() -> tuple[str, ...]:
    """Every directory name the platform has installed into, current one first."""
    return _FOOTPRINT_DIRS
