"""群聊 (threads) — human REST front door. Phase-A contract over the mock store.

An agent hits these very same paths via the `cheese` CLI with its session token;
the actor is injected here, never taken from the body.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db
from app.domain.workspace.actor import ok, resolve_actor
from app.domain.workspace.mock_store import STORE
from app.domain.workspace.schemas import (
    AddMemberRequest,
    CreateThreadRequest,
    PostMessageRequest,
)

router = APIRouter(tags=["Workspace · 群聊"])


@router.get("/projects/{projectId}/threads", summary="List threads")
async def list_threads(
    project_id: Annotated[int, Path(ge=1, alias="projectId")],
    _auth: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    threads = STORE.list_threads(project_id)
    return ok({"threads": [t.model_dump(by_alias=True) for t in threads]})


@router.post(
    "/projects/{projectId}/threads", summary="Create thread", status_code=status.HTTP_201_CREATED
)
async def create_thread(
    project_id: Annotated[int, Path(ge=1, alias="projectId")],
    payload: CreateThreadRequest,
    auth: AuthUserInfo = Depends(require_auth_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    actor = await resolve_actor(db, auth.user_id)
    thread = STORE.create_thread(project_id, actor, payload.title, payload.kind, payload.member_ids)
    return ok({"thread": thread.model_dump(by_alias=True)}, code=201, message="Created")


@router.get("/threads/{threadId}", summary="Get thread")
async def get_thread(
    thread_id: Annotated[int, Path(ge=1, alias="threadId")],
    _auth: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    return ok({"thread": STORE.get_thread(thread_id).model_dump(by_alias=True)})


@router.get("/threads/{threadId}/messages", summary="List messages")
async def list_messages(
    thread_id: Annotated[int, Path(ge=1, alias="threadId")],
    page_start: int | None = Query(default=None, alias="pageStart"),
    page_size: int = Query(default=30, alias="pageSize", ge=1, le=100),
    _auth: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    messages, page = STORE.list_messages(thread_id, page_start, page_size)
    return ok(
        {
            "messages": [m.model_dump(by_alias=True) for m in messages],
            "page": page.model_dump(by_alias=True),
        }
    )


@router.post(
    "/threads/{threadId}/messages", summary="Post message", status_code=status.HTTP_201_CREATED
)
async def post_message(
    thread_id: Annotated[int, Path(ge=1, alias="threadId")],
    payload: PostMessageRequest,
    auth: AuthUserInfo = Depends(require_auth_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    actor = await resolve_actor(db, auth.user_id)
    msg = STORE.post_message(thread_id, actor, payload.content, payload.reply_to_id, payload.refs)
    return ok({"message": msg.model_dump(by_alias=True)}, code=201, message="Created")


@router.get("/threads/{threadId}/members", summary="List thread members")
async def list_members(
    thread_id: Annotated[int, Path(ge=1, alias="threadId")],
    _auth: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    members = STORE.list_members(thread_id)
    return ok({"members": [m.model_dump(by_alias=True) for m in members]})


@router.post(
    "/threads/{threadId}/members", summary="Add thread member", status_code=status.HTTP_201_CREATED
)
async def add_member(
    thread_id: Annotated[int, Path(ge=1, alias="threadId")],
    payload: AddMemberRequest,
    auth: AuthUserInfo = Depends(require_auth_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ = await resolve_actor(db, auth.user_id)
    user = await resolve_actor(db, payload.user_id)
    member = STORE.add_member(thread_id, user, payload.role, payload.attention_policy_override)
    return ok({"member": member.model_dump(by_alias=True)}, code=201, message="Created")


@router.delete(
    "/threads/{threadId}/members/{userId}",
    summary="Remove thread member",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    thread_id: Annotated[int, Path(ge=1, alias="threadId")],
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    _auth: AuthUserInfo = Depends(require_auth_user),
) -> None:
    STORE.remove_member(thread_id, user_id)
