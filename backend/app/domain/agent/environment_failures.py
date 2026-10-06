"""A channel's work environment failed to prepare: who is told, and where.

The teammate finds out first: its tool call answers that the environment is
not ready (`machine/session_work.py`). The people waiting on it are told in
the same conversation, the 支线 or the task the call was made in, once per
failed attempt: what failed, the end of the log, and the way to the project's
environment settings. That line is also the record the settings read the
failure back from (``open_failures``), and the list of the conversations to go
back to once someone retries (``waiting_on``).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say, with_keys
from app.domain.agent.announce import announce
from app.domain.agent.platform_notices import (
    EVENT_ENVIRONMENT_FAILED,
    SEVERITY_WARN,
    WHO_HUMAN,
    notice,
)
from app.domain.block.models import Block, BlockKind
from app.domain.block.schemas import BlockOut
from app.domain.conversation.services import of_room, of_rooms

#: How much of the end of the log the line keeps: what failed is at the end.
LOG_TAIL = 8000


def _failures():
    return select(Block).where(
        Block.kind == BlockKind.event,
        Block.meta["event_type"].as_string() == EVENT_ENVIRONMENT_FAILED,
    )


async def tell_failed(
    session: AsyncSession,
    *,
    room_id: uuid.UUID,
    conversation_id: uuid.UUID,
    status: dict,
) -> dict | None:
    """Say in ``conversation_id`` that the channel's environment failed, unless
    this attempt was already said there. Returns the line to publish once the
    caller commits."""
    attempt = str(status.get("attempt") or "")
    said = await session.scalar(
        _failures()
        .where(
            Block.conversation_id == conversation_id,
            Block.meta["attempt"].as_string() == attempt,
        )
        .limit(1)
    )
    if said is not None:
        return None
    stage = str(status.get("stage") or "setup")
    exit_code = status.get("exit_code")
    line = say(
        "environmentFailedStartup" if stage == "startup" else "environmentFailedSetup"
    )
    log = str(status.get("log") or "")[-LOG_TAIL:]
    block = await announce(
        session,
        place_id=room_id,
        task_id=conversation_id if conversation_id != room_id else None,
        content=line,
        meta=with_keys(
            {
                **notice(
                    EVENT_ENVIRONMENT_FAILED,
                    severity=SEVERITY_WARN,
                    who=WHO_HUMAN,
                    detail=log,
                    detail_label=say("labelEnvironmentLog"),
                ),
                "room_id": str(room_id),
                "attempt": attempt,
                "stage": stage,
                "exit_code": exit_code,
            },
            content=line,
        ),
    )
    if block is None:
        return None
    return BlockOut.model_validate(block).model_dump(mode="json")


async def open_failures(
    session: AsyncSession, room_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict]:
    """Each of these channels' latest failure that nobody has retried yet:
    when, what failed and the end of its log, and how many conversations
    wait on it. Keyed by channel."""
    if not room_ids:
        return {}
    rows = await session.scalars(
        _failures()
        .where(
            # The channels' own conversations first: a failure is said in one
            # of them, and the blocks table is every conversation's.
            of_rooms(Block.conversation_id, room_ids),
            Block.meta["room_id"].as_string().in_([str(r) for r in room_ids]),
            Block.meta["retried_at"].as_string().is_(None),
        )
        .order_by(Block.created_at.desc())
    )
    out: dict[uuid.UUID, dict] = {}
    waiting: dict[tuple[uuid.UUID, str], set[uuid.UUID]] = {}
    for block in rows:
        meta = block.meta or {}
        room = uuid.UUID(meta["room_id"])
        attempt = meta.get("attempt") or ""
        waiting.setdefault((room, attempt), set()).add(block.conversation_id)
        if room in out:
            continue
        out[room] = {
            "attempt": attempt,
            "at": block.created_at.isoformat(),
            "stage": meta.get("stage"),
            "exit_code": meta.get("exit_code"),
            "log": meta.get("detail") or "",
        }
    for room, failure in out.items():
        failure["waiting"] = len(waiting.get((room, failure["attempt"]), ()))
    return out


async def waiting_on(
    session: AsyncSession, room_id: uuid.UUID, attempt: str
) -> list[uuid.UUID]:
    """The conversations told about this failed attempt, oldest first, each
    marked as retried: someone retried, and they are gone back to."""
    rows = list(
        await session.scalars(
            _failures()
            .where(
                of_room(Block.conversation_id, room_id),
                Block.meta["room_id"].as_string() == str(room_id),
                Block.meta["attempt"].as_string() == attempt,
                Block.meta["retried_at"].as_string().is_(None),
            )
            .order_by(Block.created_at)
        )
    )
    stamp = datetime.now(UTC).isoformat()
    conversations: list[uuid.UUID] = []
    for block in rows:
        block.meta = {**(block.meta or {}), "retried_at": stamp}
        if block.conversation_id not in conversations:
            conversations.append(block.conversation_id)
    return conversations
