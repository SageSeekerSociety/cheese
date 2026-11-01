"""What a runner does when one of its stuck-work clocks fires.

A session a clock called stuck is asked to end the work itself: a build that
is merely slow answers the interrupt, and its own ``result`` follows. One that
stopped answering will not, and nothing else is left to report that the work
ended — until something does, the platform keeps counting it as running, and
so does everything that reads it (the room's 「正在工作」, the seat's state).

Separate from ``runner`` because that module is at its size cap: this is the
one decision a firing clock leads to, and it is the same for every clock.
"""

import asyncio
import contextlib
import sys

from app.domain.agent.harness.driven import lifecycle

#: How long a session a clock called stuck is given to end the work itself
#: after being interrupted. Past this the runner reports the ending for it.
STUCK_GRACE_S = 60.0


async def settle(runner, clock: str) -> None:
    """Ask the session to end this work; end it here if it will not.

    Stopping the process is part of the ending: a build that could not answer
    an interrupt is no use to the next work, and the next runner starts one.
    """
    runner.stuck = clock
    print(
        f"work {runner.work} stuck ({clock}); interrupting it",
        file=sys.stderr,
        flush=True,
    )
    with contextlib.suppress(Exception):
        await runner.control({"subtype": "interrupt"})
    await asyncio.sleep(STUCK_GRACE_S)
    if not runner.working or runner.stuck is None:
        return
    runner._drop_unread(clock)
    runner._finish(
        failure={"kind": lifecycle.STUCK, "stuck": clock}, text="", usage=None
    )
    # Nothing on the session moves any more to wake the platform's reader, so
    # the ending is announced here, as an exit is.
    runner.announce()
    if runner.process is not None and runner.process.returncode is None:
        runner.process.terminate()
