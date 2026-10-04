"""What a room is told about its sessions' sandboxes on cloud.

A room hears about the sandbox, never the host under it: which machine a
session landed on, and how that machine was opened, is the platform's own
scheduling. Each line is written in the caller's transaction and returned as
the payload to publish once that transaction has committed.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.block.schemas import BlockOut
from app.domain.machine.models import CloudHostHome


async def _line(
    session: AsyncSession, home: CloudHostHome, content: str, meta: dict
) -> dict | None:
    block = await announce(
        session,
        place_id=home.topic_id,
        content=content,
        meta={"who": "platform", "home": str(home.id), **meta},
    )
    if block is None:
        return None
    return BlockOut.model_validate(block).model_dump(mode="json")


async def tell_preparing(session: AsyncSession, home: CloudHostHome) -> dict | None:
    return await _line(
        session,
        home,
        say("sandboxPreparing"),
        {"event_type": "cloud_startup", "severity": "info"},
    )


async def tell_ready(session: AsyncSession, home: CloudHostHome) -> dict | None:
    return await _line(
        session,
        home,
        say("sandboxReady"),
        {"event_type": "cloud_provisioning", "state": "ready", "severity": "info"},
    )


async def tell_replaced(session: AsyncSession, home: CloudHostHome) -> dict | None:
    return await _line(
        session,
        home,
        say("sandboxReplaced"),
        {"event_type": "cloud_startup", "severity": "info"},
    )


async def publish_line(topic_id: uuid.UUID, payload: dict | None) -> None:
    from app.domain.agent.runtime import get_broker

    if payload is not None:
        await get_broker().publish(
            str(topic_id), {"type": "event_block", "block": payload}
        )
