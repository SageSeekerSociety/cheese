"""Drive durable pi sessions and land their entries in the shared room log.

The four verbs map onto pi without translation, which is most of why this
harness is worth having: ``send`` is ``prompt``, a person interrupting with
words mid-turn is ``steer``, taking the work away is ``abort``, and reading from
a cursor is ``get_entries since=``. Nothing here has to reconstruct a boundary
the harness did not report.

What it does NOT do is hold the turn. The iterator ``run_turn`` hands back reads
a session that outlives it; a room's turn does not go through here at all. The
runner keeps working while this process is replaced, and ``recover`` finds it
again.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.driven.runtime import DrivenRuntime
from app.domain.agent.harness.pi.backlog import PiBacklog
from app.domain.agent.harness.pi.subscription import Subscription

logger = logging.getLogger(__name__)

PI = "pi"


@dataclass(frozen=True)
class Handle:
    session: SessionRef
    device_id: str
    state: str
    session_id: str
    agent_handle: str
    mirror: Path


class PiRuntime(DrivenRuntime[Handle]):
    harness = PI
    label = "pi"
    records = "pi entries"
    read_failure = "pi entry read failed"
    logger = logger
    # pi delivers it after the current tool calls finish and before the next
    # model call, which is the earliest point at which saying something can
    # still change what happens.
    steer = "steer"

    def conversation(self, handle: Handle) -> str:
        return handle.session_id

    def subscribe(
        self, handle: Handle, call: Callable[[str, dict], Awaitable[dict]]
    ) -> Subscription:
        return Subscription(
            handle.session,
            handle.mirror,
            call,
            self._consume,
            self._activity,
            handle.session_id,
            pulse=self.pulse,
        )

    def backlog(self, session: SessionRef) -> PiBacklog:
        handle = self.live.get(session.topic_id)
        return PiBacklog(
            handle.mirror if handle else None,
            handle.session_id if handle else None,
        )

    def working(self, status: dict) -> bool:
        return bool(status.get("working"))

    async def interrupt(self, session: SessionRef) -> bool:
        handle = self.live.get(session.topic_id)
        if handle is None:
            return False
        return bool((await self.channel.call(handle, "abort", {}))["aborted"])
