"""Drive durable Claude Code sessions and land their records in the shared room log.

The session lives in a runner on the session host (``runner.py``) that outlives
this process; ``DrivenRuntime`` reads its journal from a cursor, one poller per
room, and ``recover`` finds it again after a restart. What is Claude Code's here
is the protocol's vocabulary: a message said mid-turn is ``steer``, the receipt
is the echo rather than the write, and what the room asks of the session is a
control request written to its stdin.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.harness.claude_code.backlog import (
    ClaudeCodeBacklog,
    control_state,
)
from app.domain.agent.harness.claude_code.remote_execution.client import (
    REMOTE_CONTROLS,
)
from app.domain.agent.harness.claude_code.subscription import Subscription
from app.domain.agent.harness.driven.runtime import DrivenRuntime
from app.domain.delivery.input_identity import InputReceipt, WorkCompletion

logger = logging.getLogger(__name__)

#: What a room may ask a session. Each only reads: the room watches its session
#: and never steers it. Each is a ``control_request`` on the session's stdin
#: (`scripts/remote_execution/headless_contract.py` checks them against the
#: pinned build), except the file ones the room's executor answers
#: (``routes/agent_control.py``).
CONTROLS = (
    "initialize",
    "file_suggestions",
    "read_file",
    "get_workspace_diff",
    "get_context_usage",
    "get_usage",
    "mcp_status",
)


@dataclass(frozen=True)
class Handle:
    session: SessionRef
    device_id: str
    state: str
    session_id: str
    agent_handle: str
    mirror: Path


class ClaudeCodeRuntime(DrivenRuntime[Handle]):
    harness = CLAUDE_CODE
    label = "Claude Code"
    records = "Claude Code journal"
    read_failure = "Claude Code journal read failed"
    logger = logger
    # Written to stdin like any message; the build reads it at the next tool
    # boundary. `steer` rather than `send` only so a message the session reads
    # after its turn ended opens a turn of its own instead of the one it was
    # said to.
    steer = "steer"
    controls = CONTROLS
    # The files live on the executor, so it answers these.
    executor_controls = frozenset(REMOTE_CONTROLS)
    # 会话把记忆存成会话机上 `~/.cheese/memory/` 下的文件（文件工具在那里读写，
    # 见 `remote_execution/proxy.js` 的 `memoryPath`），runner 对得了账（下面那个
    # `memory()`），所以系统提示词里的「记忆」那一段对它说的是真话。
    keeps_memory = True

    def conversation(self, handle: Handle) -> str:
        return handle.session_id

    def subscribe(
        self, handle: Handle, call: Callable[[str, dict], Awaitable[dict]]
    ) -> Subscription:
        async def receipt(evidence: InputReceipt) -> None:
            if self.receipts is None:
                raise RuntimeError("Receipt consumer is not bound")
            await self.receipts(evidence)

        async def completion(evidence: WorkCompletion) -> None:
            if self.completions is None:
                raise RuntimeError("Completion consumer is not bound")
            await self.completions(evidence)

        async def announce() -> None:
            await self.announce(handle.session.topic_id)

        return Subscription(
            handle.session,
            handle.mirror,
            call,
            self._consume,
            self._activity,
            session_id=handle.session_id,
            recipient_handle=handle.agent_handle,
            announce=announce,
            receipts=receipt,
            completions=completion,
            pulse=self.pulse,
            memory=self._memory_hook(handle.session.topic_id),
        )

    async def memory(self, topic_id: uuid.UUID, request: dict) -> dict | None:
        """One memory reconciliation, over the runner that owns this session.

        A session that is not there (or a device that dropped) answers ``None``:
        the platform's copy is still the truth and nothing is lost — the agent's
        edits stay on that machine's disk and come back the next time it is
        reached, the same shape as every other call on this path.
        """
        seat = self._room_seat(topic_id)
        handle = self.live.get(seat) if seat is not None else None
        if handle is None:
            return None
        try:
            return await self.channel.call(handle, "memory", request)
        except (DeviceCallError, DeviceOffline):
            return None

    def backlog(self, session: SessionRef) -> ClaudeCodeBacklog:
        handle = self.live.get(self._seat_of(session))
        return ClaudeCodeBacklog(
            handle.mirror if handle else None,
            handle.session_id if handle else None,
        )

    def working(self, status: dict) -> bool:
        return bool(status.get("working"))

    async def interrupt(self, session: SessionRef) -> bool:
        handle = self.live.get(self._seat_of(session))
        if handle is None:
            return False
        return bool((await self.channel.call(handle, "interrupt", {}))["interrupted"])

    # --- what the room asks of its session -----------------------------------

    async def control_state(self, topic: uuid.UUID) -> dict:
        """What the room's controls show, from the mirror alone."""
        seat = self._room_seat(topic)
        handle = self.live.get(seat) if seat is not None else None
        subscription = self.subscriptions.get(seat) if seat is not None else None
        mirrored = (
            await subscription.on_disk(control_state, subscription.path)
            if subscription is not None
            else control_state(None)
        )
        return {
            "id": handle.session_id if handle else None,
            "connected": handle is not None,
            "controls": list(CONTROLS),
            **mirrored,
        }

    async def control(self, topic: uuid.UUID, request: dict) -> dict:
        """One control request on the session's stdin, to its response."""
        seat = self._room_seat(topic)
        handle = self.live.get(seat) if seat is not None else None
        if handle is None:
            raise LookupError("No session is running in this room")
        return await self.channel.call(handle, "control", {"request": request})

    async def announce(self, topic: uuid.UUID) -> None:
        """Tell the room its session's controls moved."""
        from app.domain.agent.runtime import get_broker

        await get_broker().publish(
            str(topic),
            {"type": "agent_control", "state": await self.control_state(topic)},
        )
