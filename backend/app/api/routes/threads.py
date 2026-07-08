"""群聊 (threads) — the human/agent REST front door under ``/connector``.

Every path authorizes the actor injected from the Bearer JWT (``get_current_user_id``,
the same trust-boundary pattern the connector plane uses) — never a body field. All
business logic & authorization live in ``ThreadService``; these handlers only parse
parameters, call the service, and return its JSON.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.common.auth import get_current_user_id
from app.domain.thread.services import ThreadService


class CreateThreadBody(BaseModel):
    title: str
    member_ids: list[int] | None = None


class RenameThreadBody(BaseModel):
    title: str


class PostMessageBody(BaseModel):
    text: str
    mention_user_ids: list[int] | None = None
    reply_to_id: int | None = None


class MarkReadBody(BaseModel):
    last_read_id: int


class ForwardMessageBody(BaseModel):
    targetThreadId: int  # wire contract is camelCase


class AddMemberBody(BaseModel):
    user_id: int
    role: int | None = None


class ChangeRoleBody(BaseModel):
    role: int


class SetAttentionBody(BaseModel):
    mode: str
    interval_minutes: int | None = None


def build_thread_router(thread_service: ThreadService) -> APIRouter:
    router = APIRouter(prefix="/connector", tags=["connector"])

    # -- threads -----------------------------------------------------------

    @router.get("/threads")
    async def list_threads(user_id: int = Depends(get_current_user_id)) -> dict[str, object]:
        return await thread_service.list_threads(user_id)

    @router.post("/threads")
    async def create_thread(
        body: CreateThreadBody, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.create_thread(user_id, body.title, body.member_ids)

    @router.get("/threads/{tid}")
    async def get_thread(
        tid: int, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.get_thread(user_id, tid)

    @router.patch("/threads/{tid}")
    async def rename_thread(
        tid: int, body: RenameThreadBody, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.rename(user_id, tid, body.title)

    # -- messages ----------------------------------------------------------

    @router.get("/threads/{tid}/messages")
    async def list_messages(
        tid: int,
        after: int = Query(default=0),
        user_id: int = Depends(get_current_user_id),
    ) -> dict[str, object]:
        return await thread_service.list_messages(user_id, tid, after)

    @router.post("/threads/{tid}/messages")
    async def post_message(
        tid: int, body: PostMessageBody, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.post_message(
            user_id, tid, body.text, body.mention_user_ids, body.reply_to_id
        )

    @router.post("/threads/{tid}/read")
    async def mark_read(
        tid: int, body: MarkReadBody, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.mark_read(user_id, tid, body.last_read_id)

    # -- message actions (删除 / 置顶 / 转发) ------------------------------

    @router.delete("/threads/{tid}/messages/{block_id}")
    async def delete_message(
        tid: int, block_id: int, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.delete_message(user_id, tid, block_id)

    @router.post("/threads/{tid}/messages/{block_id}/pin")
    async def pin_message(
        tid: int, block_id: int, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.pin_message(user_id, tid, block_id)

    @router.delete("/threads/{tid}/messages/{block_id}/pin")
    async def unpin_message(
        tid: int, block_id: int, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.unpin_message(user_id, tid, block_id)

    @router.get("/threads/{tid}/pins")
    async def list_pins(
        tid: int, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.list_pins(user_id, tid)

    @router.post("/threads/{tid}/messages/{block_id}/forward")
    async def forward_message(
        tid: int,
        block_id: int,
        body: ForwardMessageBody,
        user_id: int = Depends(get_current_user_id),
    ) -> dict[str, object]:
        return await thread_service.forward_message(
            user_id, tid, block_id, body.targetThreadId
        )

    # -- members -----------------------------------------------------------

    @router.get("/threads/{tid}/members")
    async def list_members(
        tid: int, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.members(user_id, tid)

    @router.post("/threads/{tid}/members")
    async def add_member(
        tid: int, body: AddMemberBody, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.add_member(
            user_id, tid, body.user_id, body.role if body.role is not None else 0
        )

    @router.delete("/threads/{tid}/members/{member_user_id}")
    async def remove_member(
        tid: int, member_user_id: int, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.remove_member(user_id, tid, member_user_id)

    @router.post("/threads/{tid}/members/{member_user_id}/role")
    async def change_role(
        tid: int,
        member_user_id: int,
        body: ChangeRoleBody,
        user_id: int = Depends(get_current_user_id),
    ) -> dict[str, object]:
        return await thread_service.change_role(user_id, tid, member_user_id, body.role)

    @router.delete("/threads/{tid}")
    async def dissolve_thread(
        tid: int, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.dissolve(user_id, tid)

    @router.get("/threads/{tid}/candidates")
    async def candidates(
        tid: int,
        q: str = Query(default=""),
        user_id: int = Depends(get_current_user_id),
    ) -> dict[str, object]:
        return await thread_service.candidates(user_id, tid, q)

    # -- attention policy --------------------------------------------------

    @router.get("/threads/{tid}/attention")
    async def get_attention(
        tid: int, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.get_attention(user_id, tid)

    @router.post("/threads/{tid}/attention/{agent_user_id}")
    async def set_attention(
        tid: int,
        agent_user_id: int,
        body: SetAttentionBody,
        user_id: int = Depends(get_current_user_id),
    ) -> dict[str, object]:
        return await thread_service.set_attention(
            user_id, tid, agent_user_id, body.mode, body.interval_minutes
        )

    # -- applications ------------------------------------------------------

    @router.get("/threads/{tid}/applications")
    async def list_thread_applications(
        tid: int, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.list_thread_applications(user_id, tid)

    @router.delete("/threads/{tid}/applications/{app_id}")
    async def cancel_thread_application(
        tid: int, app_id: int, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.cancel_application(user_id, tid, app_id)

    @router.get("/thread-applications")
    async def list_applications(
        user_id: int = Depends(get_current_user_id),
    ) -> dict[str, object]:
        return await thread_service.list_applications(user_id)

    @router.post("/thread-applications/{app_id}/approve")
    async def approve_application(
        app_id: int, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.approve_application(user_id, app_id)

    @router.post("/thread-applications/{app_id}/reject")
    async def reject_application(
        app_id: int, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        return await thread_service.reject_application(user_id, app_id)

    return router
