"""记忆整理 route — where a dreaming pass lands its proposal.

**The topic in the path is who is SPEAKING, not what is being organized.** A
per-turn sandbox token is scoped by topic id, so the URL has to name the turn
that is talking; which pools get reorganized is looked up from that topic on the
server (``dream_pools``). Putting a project or a pool in the URL would make the
scope something the caller asserts rather than something the platform derives.

This POST is a cheese-only write and therefore MUST also be listed in
``app.main._CHEESE_WRITE_PATHS``. A write path missing from that list does not
go through the per-turn token gate at all, and the symptom is silent
pass-through, not a 401.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.core.db import get_db
from app.domain.memory.dream import apply_dream
from app.domain.memory.schemas import DreamProposalIn

router = APIRouter(prefix="", tags=["memory"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("/topics/{topic_id}/memory/dream")
async def submit_dream(
    topic_id: uuid.UUID, body: DreamProposalIn, db: DbSession
) -> dict:
    """Apply one 记忆整理 proposal, in one transaction.

    The response reports ``skipped`` rather than failing: entries that moved
    after ``snapshot_at`` are left untouched by design, and 芝士 needs to know
    how much of its proposal that cost so it can say so in the room.
    """
    result = await apply_dream(
        db,
        topic_id=topic_id,
        snapshot_at=body.snapshot_at,
        merges=[m.model_dump() for m in body.merges],
        drops=body.drops,
        adds=body.adds,
        summary=body.summary,
    )
    out = result.as_dict()
    await db.commit()
    return ok(out)
