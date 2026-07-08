"""导出到文档 (export chat messages into a project document) — the 万物皆块 bridge
from chat blocks to document blocks, exposed under the ``/connector`` front door.

The actor is injected from the Bearer JWT (``get_current_user_id``, the trust-boundary
pattern the connector plane uses) — never a body field. Authorization is two-sided:
the actor must be a member of the source thread (checked here) AND a member of the
target project (enforced inside ``DocumentService``). Structure only — chat messages
are flattened to markdown sections, no NL/语义推断.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import get_current_user_id
from app.core.errors import ForbiddenError
from app.db.session import get_db
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.document.schemas import DocumentSummary
from app.domain.document.services import (
    DocumentService,
    ExportMessage,
)
from app.domain.thread.repositories import ThreadMembershipRepository


class ExportToDocumentBody(BaseModel):
    project_id: int
    block_ids: list[int]
    title: str | None = None
    document_id: int | None = None


def _author_name(author_id: int) -> str:
    # TODO: resolve real display names (build_member_dicts) — a simple id fallback keeps
    # this bridge free of the agent/hub dependency for now.
    return f"用户 {author_id}"


async def export_to_document(
    thread_id: int,
    body: ExportToDocumentBody,
    user_id: int,
    block_repo: BlockRepository,
    membership_repo: ThreadMembershipRepository,
    doc_service: DocumentService,
) -> dict[str, object]:
    """Load the given MESSAGE blocks of this thread (chronological, sorted by id
    ascending), render them, and create-or-append a document. Testable in isolation:
    all collaborators are passed in."""
    if not await membership_repo.is_member(thread_id, user_id):
        raise ForbiddenError("must be a member of the thread")

    messages: list[ExportMessage] = []
    for block_id in sorted(body.block_ids):
        block = await block_repo.get_message_in_thread(thread_id, block_id)
        if block is None or block.kind != BlockKind.MESSAGE:
            continue
        messages.append(
            ExportMessage(
                author_name=_author_name(block.author_id),
                text=block.content,
                timestamp=block.created_at,
            )
        )

    summary: DocumentSummary = await doc_service.export_messages(
        project_id=body.project_id,
        actor_id=user_id,
        messages=messages,
        title=body.title,
        document_id=body.document_id,
    )
    return {
        "code": 200,
        "message": "success",
        "data": {"document": summary.model_dump(by_alias=True)},
    }


def build_message_export_router() -> APIRouter:
    router = APIRouter(prefix="/connector", tags=["export"])

    @router.post("/threads/{threadId}/export-to-document")
    async def export_to_document_route(
        threadId: int,
        body: ExportToDocumentBody,
        user_id: int = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
    ) -> dict[str, object]:
        return await export_to_document(
            thread_id=threadId,
            body=body,
            user_id=user_id,
            block_repo=BlockRepository(db),
            membership_repo=ThreadMembershipRepository(db),
            doc_service=DocumentService(db),
        )

    return router
