"""Accept-card / Review routes — the 验收 state machine (spec §4.4, §6.3)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.core.db import get_db
from app.domain.review.schemas import (
    AcceptCardCreate,
    AcceptCardOut,
    AcceptDecision,
    RejectDecision,
)
from app.domain.review.services import AcceptService

router = APIRouter(prefix="/api", tags=["accept"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _out(card) -> dict:
    return AcceptCardOut.model_validate(card).model_dump(mode="json")


@router.post("/topics/{topic_id}/accept-card")
async def create_accept_card(
    topic_id: uuid.UUID, body: AcceptCardCreate, db: DbSession
) -> dict:
    card = await AcceptService(db).create_card(
        topic_id=topic_id,
        reviewer_handle=body.reviewer_handle,
        routing_reason=body.routing_reason,
    )
    return ok(_out(card))


@router.get("/topics/{topic_id}/accept-card")
async def list_accept_cards(topic_id: uuid.UUID, db: DbSession) -> dict:
    cards, total = await AcceptService(db).list_for_topic(topic_id)
    return ok(page([_out(c) for c in cards], total))


@router.post("/accept-cards/{card_id}/accept")
async def accept_card(card_id: uuid.UUID, body: AcceptDecision, db: DbSession) -> dict:
    card = await AcceptService(db).accept(card_id=card_id, decided_by=body.decided_by)
    return ok(_out(card))


@router.post("/accept-cards/{card_id}/reassign")
async def reassign_card(
    card_id: uuid.UUID, body: AcceptCardCreate, db: DbSession
) -> dict:
    """改验收人 (spec §4.4)."""
    card = await AcceptService(db).reassign(
        card_id=card_id,
        reviewer_handle=body.reviewer_handle,
        reason=body.routing_reason,
    )
    return ok(_out(card))


@router.post("/accept-cards/{card_id}/reject")
async def reject_card(card_id: uuid.UUID, body: RejectDecision, db: DbSession) -> dict:
    card = await AcceptService(db).reject(
        card_id=card_id, decided_by=body.decided_by, note=body.note
    )
    return ok(_out(card))


@router.post("/accept-cards/{card_id}/revoke")
async def revoke_card(card_id: uuid.UUID, body: AcceptDecision, db: DbSession) -> dict:
    card = await AcceptService(db).revoke(card_id=card_id, decided_by=body.decided_by)
    return ok(_out(card))
