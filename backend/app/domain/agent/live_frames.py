"""What an agent is in the middle of writing, told to its room as it is written.

A runner shows the block its agent is generating — text, or a tool call with its
arguments as raw JSON so far (``harness/driven/runner.py``) — and the poller
hands each change here. It goes out as a ``live`` frame on the room's channel
and nowhere else: it is not a block, nothing stores it, and a client that missed
one has lost nothing, since the next frame, or the record that lands when the
block is finished, says all of it. Empty ``blocks`` means nothing is being
written any more.
"""

import uuid

from app.domain.agent.realtime.broker import get_broker


async def publish_live(
    topic_id: uuid.UUID,
    turn_id: uuid.UUID | None,
    agent_handle: str,
    blocks: list[dict],
) -> None:

    await get_broker().publish(
        str(topic_id),
        {
            "type": "live",
            "turn_id": str(turn_id) if turn_id else None,
            "agent": agent_handle,
            "blocks": blocks,
        },
    )
