"""Human-facing routes for 2.0 threads (群聊) and their messages.

The human half of the human<->agent conversation: create a thread, list a
project's threads, post a message, and read a thread's blocks. Agents write to
the same block/thread substrate via the tool path, so both sides share one
conversation.

Access is gated on project membership (member or leader) — a defensible
baseline. Finer per-thread membership authz (via ThreadMembership) is a TODO.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes._shared import require_project_access
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db
from app.domain.block.models import AuthorKind, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.services import BlockService
from app.domain.thread.models import Thread, ThreadKind
from app.domain.thread.repositories import ThreadRepository
from app.domain.thread.services import ThreadService

router = APIRouter(prefix="/threads", tags=["Threads"])


def _thread_service(db: AsyncSession) -> ThreadService:
    return ThreadService(ThreadRepository(db))


def _thread_to_api(t: Thread) -> dict:
    return {
        "id": t.id,
        "projectId": t.project_id,
        "parentThreadId": t.parent_thread_id,
        "kind": t.kind,
        "title": t.title,
        "createdById": t.created_by_id,
        "createdAt": int(t.created_at.timestamp() * 1000) if t.created_at else 0,
    }


def _block_to_api(b: Block) -> dict:
    return {
        "id": b.id,
        "threadId": b.thread_id,
        "projectId": b.project_id,
        "kind": b.kind,
        "content": b.content,
        "authorId": b.author_id,
        "authorKind": b.author_kind,
        "createdAt": int(b.created_at.timestamp() * 1000) if b.created_at else 0,
    }


class CreateThreadRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_id: int = Field(..., alias="projectId", gt=0)
    title: str = ""
    parent_thread_id: int | None = Field(default=None, alias="parentThreadId")


class PostMessageRequest(BaseModel):
    content: str = Field(..., min_length=1)


@router.post("", summary="Create Thread", status_code=status.HTTP_201_CREATED)
async def create_thread(
    payload: CreateThreadRequest,
    db: AsyncSession = Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    await require_project_access(db, auth_user.user_id, payload.project_id)
    thread = await _thread_service(db).create_thread(
        project_id=payload.project_id,
        created_by_id=auth_user.user_id,
        title=payload.title,
        kind=ThreadKind.GENERAL,
        parent_thread_id=payload.parent_thread_id,
    )
    return {"code": 201, "message": "Created", "data": {"thread": _thread_to_api(thread)}}


@router.get("", summary="List Project Threads")
async def list_threads(
    project_id: int = Query(..., alias="projectId", gt=0),
    db: AsyncSession = Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    await require_project_access(db, auth_user.user_id, project_id)
    threads = await _thread_service(db).list_by_project(project_id)
    return {
        "code": 200,
        "message": "success",
        "data": {"threads": [_thread_to_api(t) for t in threads]},
    }


@router.post("/{threadId}/messages", summary="Post Message", status_code=status.HTTP_201_CREATED)
async def post_message(
    payload: PostMessageRequest,
    thread_id: Annotated[int, Path(alias="threadId", ge=1)],
    db: AsyncSession = Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    thread = await _thread_service(db).get_thread(thread_id)
    await require_project_access(db, auth_user.user_id, thread.project_id)
    block = await BlockService(BlockRepository(db)).create_block(
        project_id=thread.project_id,
        content=payload.content,
        author_id=auth_user.user_id,
        author_kind=AuthorKind.USER,
        kind=BlockKind.MESSAGE,
        thread_id=thread_id,
    )
    return {"code": 201, "message": "Created", "data": {"block": _block_to_api(block)}}


@router.get("/{threadId}/messages", summary="List Thread Messages")
async def list_messages(
    thread_id: Annotated[int, Path(alias="threadId", ge=1)],
    page_start: str | None = Query(default=None, alias="pageStart"),
    page_size: int = Query(default=50, alias="pageSize", ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    thread = await _thread_service(db).get_thread(thread_id)
    await require_project_access(db, auth_user.user_id, thread.project_id)
    after_id = int(page_start) if page_start and page_start.isdigit() else 0
    blocks = await BlockService(BlockRepository(db)).list_thread(
        thread_id, after_id=after_id, limit=page_size
    )
    next_start = str(blocks[-1].id) if len(blocks) == page_size else None
    return {
        "code": 200,
        "message": "success",
        "data": {
            "blocks": [_block_to_api(b) for b in blocks],
            "page": {
                "pageStart": page_start or "0",
                "pageSize": len(blocks),
                "hasMore": next_start is not None,
                "nextStart": next_start,
            },
        },
    }
