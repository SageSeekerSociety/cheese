"""Where the platform's own files sit on a machine it borrows.

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

`write()` is the other half of the same rule, and the reason it lives here:
what the platform writes on a borrowed machine goes under this root, and the
hosted checkout is not under it. One function means one assertion rather than a
path-prefix argument spread over every caller (结论 49，不变量 I21b).

Five programs cannot ask this module and carry the name themselves: the
environment runner, the cleanup script, the executor bootstrap and the sandbox
CLI (`backend/sandbox/cheese`) all run where nothing of ours is importable — one
is written out beside the room's own files and run as a script, one is piped in
on stdin and has no `__file__` to look at, one is exec'd out of a string, one is
shipped into the agent's container as a standalone program — and the connector
is Go. Their copies are checked against this module by
`backend/tests/unit/test_footprint_root.py`. The value is chosen here and
nowhere else.
"""

import base64

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


# What a session's own home is called from the machine's side, and the one
# directory inside it the platform stages files into. The checkout the agent
# works in is that home's `room/` (`device_provider._work_dir`), so a name
# chosen here is a name chosen NOT to be in it.
STAGED_DIR = "attachments"

#: The checkout, relative to a session's home. Named here because the whole
#: point of `write` is to stay out of it, and a rule that names the directory it
#: excludes is a rule a reader can check.
CHECKOUT_DIR = "room"


class OutsideFootprint(RuntimeError):
    """A platform write aimed somewhere the platform does not own.

    Raised rather than logged: a write that lands outside the footprint is
    either a file `cheese uninstall` will walk past, or — the case this exists
    for — a file in the hosted checkout, which the platform does not write into
    at all (结论 49，不变量 I21b).
    """


def staged_path(home: str, name: str) -> tuple[str, str]:
    """Where one staged file goes, as the machine and the session each spell it.

    Two spellings of one destination because the two transports that reach a
    machine do not share a `$HOME`. The connector runs as the machine's owner,
    so it is handed the `$HOME`-anchored path and expands it against that home.
    The executor was launched with `HOME` set to the session's own home, so it
    is handed the path relative to that and resolves it against `Path.home()`.
    Handing either one the other's spelling writes a real file in a real
    directory that nothing will ever look in.
    """
    relative = f"{STAGED_DIR}/{name}"
    return f"{home}/{relative}", relative


async def write(
    data: bytes,
    *,
    home: str,
    name: str,
    hub,
    device_id: str,
    screen: str,
    execution_target: dict | None = None,
    timeout: float = 30,
) -> str:
    """Put one file the platform owns on a machine it borrows, and say where.

    **The only call in `app/` that writes a file onto a remote machine.** That
    is not tidiness: 结论 49 says the platform's own things — its configuration,
    hooks, skills, prompts, progress, memories, drafts, backups — never enter
    the hosted checkout, in the tree or as an untracked file beside it, and a
    rule about where writes land can only be checked where the writes are. One
    entry point makes it one assertion (below) and one AST count
    (`tests/unit/test_platform_writes_nothing_into_checkout.py`) rather than an
    argument about every path some caller builds at runtime.

    `home` is the session's home on that machine, `$HOME`-anchored. The staged
    file goes beside the checkout, never inside it: the checkout is that home's
    `room/`, and an uploaded image dropped in there is an untracked file in
    somebody's repository that they never put there and we never take away.

    **One write, to the filesystem the agent actually reads.** A screen with an
    executor is reached through it, because that is where the agent runs — a
    private chat's executor is a container, and a file written on the host it
    sits on is a file the agent cannot open. Only a screen with no executor is
    written to over the connector. Doing both, which is what this replaces, put
    the same bytes at the same path twice for every room executor, and hid the
    fact that the two transports resolve a path against different homes.

    Returns the absolute path **as the machine reports it** — the backend cannot
    expand that machine's `$HOME`, and the agent is handed this path verbatim.
    """
    destination, relative = staged_path(home, name)
    root = f"$HOME/{footprint_root()}/"
    if not destination.startswith(root):
        raise OutsideFootprint(
            f"{destination} is outside $HOME/{footprint_root()}, which is the "
            "whole of what the platform writes on a machine"
        )
    if f"/{CHECKOUT_DIR}/" in destination.removeprefix(root):
        raise OutsideFootprint(
            f"{destination} is inside the hosted checkout, which the platform "
            "does not write into (结论 49)"
        )
    if execution_target:
        from app.domain.agent import private_chat

        answer = await private_chat.control(
            execution_target,
            {
                "subtype": "stage_file",
                "path": relative,
                "data": base64.b64encode(data).decode(),
            },
            hub=hub,
        )
    else:
        answer = await hub.put_file(
            device_id, screen, destination, data, timeout=timeout
        )
    return str(answer["path"])
