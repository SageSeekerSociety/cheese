"""结论卡 routes — 默认采信 (accept-by-default) 的三个出口.

**Mounted under the PARENT topic on purpose.** The per-turn token a sandbox
carries is scoped by the topic id in the URL; the parent settles a card during
its OWN turn, so a path keyed by the child (whose card it is) would 401 every
time. `{topic_id}` here is always the receiver — the service refuses a card
addressed to anyone else.

These three POSTs are cheese-only writes and therefore MUST also be listed in
``app.main._CHEESE_WRITE_PATHS``: a write path missing from that list does not
go through the per-turn token gate at all, and the symptom is silent pass-through,
not a 401. (The GET below is a read and deliberately stays off that list, as do
all authorization-granting routes.)
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_chat_service, get_work_runner
from app.api.response import ok, page
from app.core.db import get_db
from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.conclusion.repositories import ConclusionCardRepository
from app.domain.conclusion.schemas import (
    ConclusionCardOut,
    ConclusionSettleIn,
    EscalateIn,
    NeedEvidenceIn,
)
from app.domain.conclusion.services import (
    SYSTEM_ACTOR,
    ConclusionCardService,
    need_evidence_prompt,
)

logger = logging.getLogger("cheesex.conclusion")

router = APIRouter(prefix="", tags=["conclusion"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _out(card) -> dict:
    return ConclusionCardOut.model_validate(card).model_dump(mode="json")


@router.get("/topics/{topic_id}/conclusion-cards")
async def list_conclusion_cards(topic_id: uuid.UUID, db: DbSession) -> dict:
    """Cards this place PRODUCED (its own 回流 history), newest first."""
    cards = await ConclusionCardRepository(db).list_for_place(topic_id)
    return ok(page([_out(c) for c in cards], len(cards)))


@router.post("/topics/{topic_id}/conclusion-cards/{card_id}/accept")
async def accept_conclusion_card(
    topic_id: uuid.UUID,
    card_id: uuid.UUID,
    body: ConclusionSettleIn,
    db: DbSession,
) -> dict:
    """采信 —— explicit form of what happens anyway when this turn ends."""
    svc = ConclusionCardService(db)
    card = await svc.get_for_receiver(receiver_topic_id=topic_id, card_id=card_id)
    await svc.accept(card, by=body.decided_by or SYSTEM_ACTOR)
    out = _out(card)
    await db.commit()
    return ok(out)


@router.post("/topics/{topic_id}/conclusion-cards/{card_id}/need-evidence")
async def need_evidence(
    topic_id: uuid.UUID,
    card_id: uuid.UUID,
    body: NeedEvidenceIn,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """补证据 —— costs a whole turn (the sub-topic is woken), which is precisely
    the asymmetry that makes 采信 the path of least resistance."""
    svc = ConclusionCardService(db)
    card = await svc.get_for_receiver(receiver_topic_id=topic_id, card_id=card_id)
    await svc.need_evidence(
        card,
        by=body.decided_by or SYSTEM_ACTOR,
        reason=body.reason,
        blocking_ref=body.blocking_ref,
    )
    out = _out(card)
    prompt = need_evidence_prompt(card)
    # The THREAD that produced the conclusion, not the room it hangs in. Waking
    # the room would put "go get more evidence" in front of everyone except the
    # 分身 the instruction is for.
    sub_id = card.task_id or card.topic_id
    # Commit BEFORE waking: the thread's turn runs on its own session and
    # must see the card already in `returned`.
    await db.commit()
    runner.submit_kickoff(chat, sub_id, prompt=prompt)
    return ok(out)


@router.post("/topics/{topic_id}/conclusion-cards/{card_id}/escalate")
async def escalate_conclusion_card(
    topic_id: uuid.UUID,
    card_id: uuid.UUID,
    body: EscalateIn,
    db: DbSession,
) -> dict:
    """升级 —— the conclusion needs a person's authority, which no conclusion
    card can grant. The sub-topic stays active so the follow-up has a home."""
    svc = ConclusionCardService(db)
    card = await svc.get_for_receiver(receiver_topic_id=topic_id, card_id=card_id)
    await svc.escalate(card, by=body.decided_by or SYSTEM_ACTOR, reason=body.reason)
    out = _out(card)
    await db.commit()
    return ok(out)
