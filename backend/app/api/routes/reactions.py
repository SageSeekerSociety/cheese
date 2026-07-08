"""消息表情回应 (message reactions) — the human/agent REST front door under ``/connector``.

Mirrors the thread routes: the actor is injected from the Bearer JWT
(``get_current_user_id``), never a body field; all authorization lives in
``ReactionService``. Emoji travel in the request body (not the URL path) to avoid
emoji-in-URL encoding issues.
"""

from fastapi import APIRouter, Depends

from app.common.auth import get_current_user_id
from app.db.session import AsyncSessionLocal
from app.domain.reaction.schemas import ReactionBody, ReactionsQueryBody
from app.domain.reaction.services import ReactionService


def build_reactions_router() -> APIRouter:
    router = APIRouter(prefix="/connector", tags=["reactions"])
    service = ReactionService(AsyncSessionLocal)

    @router.put("/threads/{tid}/messages/{block_id}/reactions")
    async def react(
        tid: int,
        block_id: int,
        body: ReactionBody,
        user_id: int = Depends(get_current_user_id),
    ) -> dict[str, object]:
        return await service.react(user_id, tid, block_id, body.emoji)

    @router.delete("/threads/{tid}/messages/{block_id}/reactions")
    async def unreact(
        tid: int,
        block_id: int,
        body: ReactionBody,
        user_id: int = Depends(get_current_user_id),
    ) -> dict[str, object]:
        return await service.unreact(user_id, tid, block_id, body.emoji)

    @router.post("/threads/{tid}/reactions:query")
    async def query_reactions(
        tid: int,
        body: ReactionsQueryBody,
        user_id: int = Depends(get_current_user_id),
    ) -> dict[str, object]:
        return await service.reactions_for_thread(user_id, tid, body.block_ids)

    return router
