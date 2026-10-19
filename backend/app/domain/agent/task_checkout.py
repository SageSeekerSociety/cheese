"""The checkout a task's session works in, opened on the machine that holds it.

A task's work is in a checkout of its own under the session's home on the
machine, and the project view its session would otherwise start in is not a
checkout at all: an agent there spends its first commands — and part of most
later ones — spelling that UUID out (`$T` is the short name for whichever
directory the session started in).

So the launch starts a task's session where its work is. `cheese worktree` on
the machine is the one place that decides where that checkout lives and which
branch it is on — a task that delivered a step lands on a new branch, and its
checkout follows — so it is asked rather than a path composed here.

It runs as the machine's own Bash (`RemoteClient.call` `invoke`), not on the
session host: the two are different machines when the room's work is on one
and the session process on another, and `Path.home()` is different on each.
"""

from __future__ import annotations

import ntpath
import posixpath
import shlex
import sys
import uuid

#: How long opening a checkout may take. A first open clones the project and
#: runs the environment's install; past this the launch starts where it always
#: did, and the command goes on as a background task for the next launch.
CHECKOUT_TIMEOUT_MS = 600_000

#: The command, with the topic substituted in. Its output goes to a file and
#: only the last line is answered: a first open prints the environment's own
#: preparation first, and the executor hands back only the front of a
#: command's output — the path, printed last, would be cut off. stderr goes to
#: its own file: the CLI's warnings are not the answer, and were the streams
#: merged, a warning written after the path would be answered as the checkout
#: (that is exactly what a set-aside snapshot says). The exit status is the
#: CLI's, and a failure's stderr goes back with it for its message.
_CHECKOUT = (
    "out=$(mktemp); err=$(mktemp); "
    'cheese worktree {topic} >"$out" 2>"$err"; code=$?; '
    'tail -n 1 "$out"; '
    'if [ "$code" -ne 0 ]; then cat "$err" >&2; fi; '
    'rm -f "$out" "$err"; exit "$code"'
)


def names_a_directory(value):
    """Whether `value` is a directory name rather than a notice.

    The machine answers where a task's checkout is on the last line of what it
    said; a warning is not a path. The machine and the session host need not
    run the same system, so either spelling is a path: a Windows executor names
    `C:\\...` for a Linux host, and a POSIX executor names `/...` for a Windows
    one. A target file written before this check can still hold a notice, and a
    launch must not start at it."""
    return isinstance(value, str) and (posixpath.isabs(value) or ntpath.isabs(value))


class TaskCheckoutFailed(RuntimeError):
    """The machine could not be asked, or answered something else than a path.

    Not a room: a room is answered ``workTaskNotInProject`` and is no failure
    at all. A caller that gets this one has not learned where the checkout is,
    and must not remember it as if it had."""


def task_checkout(client, topic, *, notice=True):
    """``topic``'s task checkout on the machine, opened if it is not there.

    None when this session works in a room, and the checkout's path otherwise.
    Raises ``TaskCheckoutFailed`` for anything else — the machine out of reach,
    a lease still preparing, a command that ended without saying where the
    checkout is. ``notice``: say that on stderr. A launch says it; a command
    being handed to the machine does not, since its stderr is the agent's."""
    request = {
        "tool": "Bash",
        "args": {
            "command": _CHECKOUT.format(topic=shlex.quote(str(topic))),
            "timeout": CHECKOUT_TIMEOUT_MS,
        },
        "platform": True,
    }
    answer = {}
    for attempt in range(2):
        try:
            request["id"] = "task-checkout-" + uuid.uuid4().hex
            answer = client.call("invoke", request)
            break
        except (RuntimeError, OSError, TimeoutError):
            # The first call that takes the machine's hands may be answered
            # "Work environment is ready..." instead of run; that answer asks
            # for the call again, once.
            if attempt:
                # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
                raise TaskCheckoutFailed("the machine did not answer") from None
    value = answer.get("value") or {}
    said = value.get("stdout") or answer.get("error") or ""
    lines = said.strip().splitlines()
    last = lines[-1].strip() if lines else ""
    failed = (
        answer.get("error")
        or value.get("backgroundTaskId")
        or said.startswith("Exit code")
    )
    if not failed and last:
        # The answer is the checkout's path and nothing else. Anything else
        # that reached stdout is not one, and must not become a launch's start
        # directory: take it as the failure it is.
        if names_a_directory(last):
            return last
        failed = True
    if "workTaskNotInProject" in said:  # a room is not a failure
        return None
    if notice:
        # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
        print(
            "[cheese] task checkout not opened: " + (last[-300:] or "no answer"),
            file=sys.stderr,
        )
    # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
    raise TaskCheckoutFailed(last or "the command said nothing")
