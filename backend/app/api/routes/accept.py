"""Accept-card / Review routes — the 验收 state machine (spec §4.4, §6.3)."""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_chat_service, get_turn_runner
from app.api.response import ok, page
from app.core.db import get_db
from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import TurnRunner
from app.domain.review.models import AcceptStatus
from app.domain.review.schemas import (
    AcceptCardCreate,
    AcceptCardOut,
    AcceptDecision,
    RejectDecision,
)
from app.domain.review.services import AcceptService
from app.domain.workspace import service as ws

logger = logging.getLogger("cheesex.accept")

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
async def accept_card(
    card_id: uuid.UUID,
    body: AcceptDecision,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[TurnRunner, Depends(get_turn_runner)],
) -> dict:
    card = await AcceptService(db).accept(card_id=card_id, decided_by=body.decided_by)
    if card.status == AcceptStatus.conflict:
        # 冲突不采纳 (spec §6.3): 芝士 first. Materialize the conflicted merge in
        # the topic's workspace, then dispatch a resolve turn. The reviewer
        # retries accept when 芝士 reports done.
        topic_id = card.topic_id
        try:
            files = ws.prepare_conflict_resolution(
                (await AcceptService(db)._topic_or_404(topic_id)).project_id,
                topic_id,
            )
        except Exception:  # noqa: BLE001 — dispatch anyway; the agent can dig
            logger.exception("prepare_conflict_resolution failed for %s", topic_id)
            files = []
        listing = "、".join(files[:15]) or "（见工作区冲突标记）"
        runner.submit(
            chat,
            topic_id,
            author="system",
            content=(
                "采纳这个话题时合并冲突了，暂时没归档。平台已把主分支合进你的工作区，"
                f"冲突标记就在这些文件里：{listing}。请打开这些文件解决所有 "
                "<<<<<<< 冲突标记（保留双方意图，语义化合并，不要机械二选一），"
                "跑相关测试确认没破坏，然后简短汇报解决思路——验收人会重新点采纳。"
            ),
            summon=True,
        )
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
