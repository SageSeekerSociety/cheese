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
#: command's output — the path, printed last, would be cut off. The exit status
#: is the CLI's, for a failure's message to come back with it.
_CHECKOUT = (
    'out=$(mktemp); cheese worktree {topic} >"$out" 2>&1; '
    'code=$?; tail -n 1 "$out"; rm -f "$out"; exit $code'
)


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
        return last
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
