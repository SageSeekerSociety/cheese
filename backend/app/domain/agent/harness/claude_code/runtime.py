"""Drive durable Claude Code sessions and land their records in the shared room log.

The session lives in a runner on the session host (``runner.py``) that outlives
this process; ``DrivenRuntime`` reads its journal from a cursor, one poller per
room, and ``recover`` finds it again after a restart. What is Claude Code's here
is the protocol's vocabulary: a message said mid-turn is ``steer``, the receipt
is the echo rather than the write, and the room's controls are control requests
written to the session's stdin.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

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

logger = logging.getLogger(__name__)

#: The controls a room may send a session. Each is a ``control_request`` on the
#: session's stdin (`scripts/remote_execution/headless_contract.py` checks them
#: against the pinned build), except the file ones the room's executor answers
#: (``routes/agent_control.py``). ``interrupt`` also ends the room's open turn
#: whether or not the session answers it (``DrivenRuntime.stop``).
CONTROLS = (
    "initialize",
    "interrupt",
    "background_tasks",
    "stop_task",
    "set_model",
    "set_permission_mode",
    "set_max_thinking_tokens",
    "apply_flag_settings",
    "rename_session",
    "file_suggestions",
    "read_file",
    "get_workspace_diff",
    "get_context_usage",
    "get_usage",
    "mcp_status",
    "mcp_authenticate",
    "mcp_oauth_callback_url",
    "mcp_reconnect",
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
    # Written is not read: the echo of the input is (`Subscription.receipt`).
    receipt_on_accept = False
    controls = CONTROLS
    # The files live on the executor, so it answers these.
    executor_controls = frozenset(REMOTE_CONTROLS)

    def conversation(self, handle: Handle) -> str:
        return handle.session_id

    def subscribe(
        self, handle: Handle, call: Callable[[str, dict], Awaitable[dict]]
    ) -> Subscription:
        async def receipt(topic: uuid.UUID, text: str) -> None:
            if self.receipts is not None:
                await self.receipts(topic, text)

        async def announce() -> None:
            await self.announce(handle.session.topic_id)

        return Subscription(
            handle.session,
            handle.mirror,
            call,
            self._consume,
            self._activity,
            session_id=handle.session_id,
            announce=announce,
            receipts=receipt,
            pulse=self.pulse,
        )

    def backlog(self, session: SessionRef) -> ClaudeCodeBacklog:
        handle = self.live.get(session.topic_id)
        return ClaudeCodeBacklog(
            handle.mirror if handle else None,
            handle.session_id if handle else None,
        )

    def working(self, status: dict) -> bool:
        return bool(status.get("working"))

    async def interrupt(self, session: SessionRef) -> bool:
        handle = self.live.get(session.topic_id)
        if handle is None:
            return False
        return bool((await self.channel.call(handle, "interrupt", {}))["interrupted"])

    # --- the room's controls -------------------------------------------------

    async def control_state(self, topic: uuid.UUID) -> dict:
        """What the room's controls show, from the mirror alone."""
        handle = self.live.get(topic)
        subscription = self.subscriptions.get(topic)
        mirrored = (
            await subscription.on_disk(control_state, subscription.path)
            if subscription is not None
            else control_state(None)
        )
        return {
            "id": handle.session_id if handle else None,
            "agent_handle": handle.agent_handle if handle else None,
            "connected": handle is not None,
            "controls": list(CONTROLS),
            **mirrored,
        }

    async def control(self, topic: uuid.UUID, request: dict) -> dict:
        """One control request on the session's stdin, to its response."""
        handle = self.live.get(topic)
        if handle is None:
            raise LookupError("No session is running in this room")
        if request.get("subtype") == "interrupt":
            # The room's stop. Not relayed as a bare control: the session may
            # be the thing not answering, and a turn it does end on a bare
            # control ends as a failure whose messages are sent again.
            work = await self.stop(topic)
            return {
                "subtype": "success",
                "response": {"stopped": work is not None},
                **({"work_id": str(work)} if work is not None else {}),
            }
        return await self.channel.call(handle, "control", {"request": request})

    async def announce(self, topic: uuid.UUID) -> None:
        """Tell the room its session's controls moved."""
        from app.domain.agent.runtime import get_broker

        await get_broker().publish(
            str(topic),
            {"type": "agent_control", "state": await self.control_state(topic)},
        )
