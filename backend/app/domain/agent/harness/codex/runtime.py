"""Drive durable Codex sessions and land their events in the shared room log."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.codex.backlog import CodexBacklog
from app.domain.agent.harness.codex.subscription import Subscription
from app.domain.agent.harness.driven.runtime import DrivenRuntime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Handle:
    session: SessionRef
    device_id: str
    state: str
    thread_id: str
    agent_handle: str
    mirror: Path
    #: What its runner said it can do when it was greeted (``driven.runner``).
    capabilities: frozenset[str] = frozenset()
    #: What of its launch the host holds a running runner to (``launch_identity``),
    #: as this process last ensured the runner with it: a send whose launch is
    #: the same reuses this handle without asking the host. Not part of which
    #: session this is.
    launch: str = field(default="", compare=False)


class CodexRuntime(DrivenRuntime[Handle]):
    harness = "codex"
    label = "Codex"
    records = "Codex journal"
    read_failure = "Codex journal read failed"
    logger = logger
    # The runner's ``send`` becomes ``turn/steer`` while a turn is open.
    steer = "send"

    def conversation(self, handle: Handle) -> str:
        return handle.thread_id

    def subscribe(
        self, handle: Handle, call: Callable[[str, dict], Awaitable[dict]]
    ) -> Subscription:
        return Subscription(
            handle.session,
            handle.mirror,
            call,
            self._consume,
            self._activity,
            pulse=self.pulse,
            memory=self._memory_hook(handle.session.topic_id),
        )

    def backlog(self, session: SessionRef) -> CodexBacklog:
        handle = self.live.get(self._seat_of(session))
        return CodexBacklog(handle.mirror if handle else None)

    def working(self, status: dict) -> bool:
        return bool(status.get("turn_id"))

    async def interrupt(self, session: SessionRef) -> bool:
        handle = self.live.get(self._seat_of(session))
        if handle is None:
            return False
        return bool((await self.channel.call(handle, "interrupt", {}))["interrupted"])
