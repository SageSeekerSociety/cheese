"""Topic routes."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import ValidationError
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.topic.schemas import (
    ConclusionIn,
    DocEditIn,
    SplitIn,
    TopicCreate,
    TopicOut,
    UpgradeBlockIn,
)
from app.domain.topic.services import TopicService
from app.domain.usage.repositories import UsageRepository

router = APIRouter(prefix="/api/topics", tags=["topics"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("")
async def create_topic(body: TopicCreate, db: DbSession) -> dict:
    topic = await TopicService(db).create(
        project_id=body.project_id,
        title=body.title,
        parent_id=body.parent_id,
        created_by=body.created_by,
    )
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


@router.get("")
async def list_topics(project_id: uuid.UUID, db: DbSession) -> dict:
    topics, total = await TopicService(db).list_for_project(project_id)
    items = [TopicOut.model_validate(t).model_dump(mode="json") for t in topics]
    return ok(page(items, total))


@router.get("/{topic_id}")
async def get_topic(topic_id: uuid.UUID, db: DbSession) -> dict:
    topic = await TopicService(db).get_or_404(topic_id)
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


@router.get("/{topic_id}/blocks")
async def list_topic_blocks(topic_id: uuid.UUID, db: DbSession) -> dict:
    await TopicService(db).get_or_404(topic_id)
    repo = BlockRepository(db)
    blocks = await repo.list_for_topic(topic_id)
    total = await repo.count_for_topic(topic_id)
    items = [BlockOut.model_validate(b).model_dump(mode="json") for b in blocks]
    return ok(page(items, total))


@router.get("/{topic_id}/transcript")
async def topic_transcript(topic_id: uuid.UUID, db: DbSession) -> dict:
    """施工现场 (spec §7.1): the topic's AI session record — 芝士's messages and
    tool/event actions, read-only."""
    await TopicService(db).get_or_404(topic_id)
    blocks = await BlockRepository(db).list_for_topic(topic_id)
    # 现场 = what 芝士 said (ai messages) + what it did (tool events). Exclude the
    # doc/decision artifacts (author_type is also ai) — those have their own views.
    site = [
        b
        for b in blocks
        if (b.author_type == AuthorType.ai and b.kind == BlockKind.message)
        or b.kind == BlockKind.event
    ]
    items = [BlockOut.model_validate(b).model_dump(mode="json") for b in site]
    return ok(page(items, len(items)))


@router.get("/{topic_id}/usage")
async def topic_usage(topic_id: uuid.UUID, db: DbSession) -> dict:
    """资源用量 (spec §9.1): token/cost for this topic."""
    await TopicService(db).get_or_404(topic_id)
    return ok(await UsageRepository(db).for_topic(topic_id))


@router.get("/{topic_id}/children")
async def list_topic_children(topic_id: uuid.UUID, db: DbSession) -> dict:
    children = await TopicService(db).list_children(topic_id)
    items = [TopicOut.model_validate(t).model_dump(mode="json") for t in children]
    return ok(page(items, len(items)))


@router.get("/{topic_id}/docs")
async def list_topic_docs(topic_id: uuid.UUID, db: DbSession) -> dict:
    """Document-tree view of a topic (doc blocks), spec §5."""
    await TopicService(db).get_or_404(topic_id)
    docs = await BlockRepository(db).list_docs_for_topic(topic_id)
    items = [BlockOut.model_validate(b).model_dump(mode="json") for b in docs]
    return ok(page(items, len(items)))


@router.get("/{topic_id}/doc")
async def get_topic_doc(topic_id: uuid.UUID, db: DbSession) -> dict:
    """The topic's single living doc (spec §2.2 docs-out)."""
    doc = await TopicService(db).get_doc(topic_id)
    if doc is None:
        return ok(None)
    return ok(BlockOut.model_validate(doc).model_dump(mode="json"))


@router.put("/{topic_id}/doc")
async def edit_topic_doc(topic_id: uuid.UUID, body: DocEditIn, db: DbSession) -> dict:
    """改文档即指令 (eval B2): edit the living doc; emits a conversation event."""
    doc = await TopicService(db).edit_doc(
        topic_id=topic_id, content=body.content, author=body.author
    )
    return ok(BlockOut.model_validate(doc).model_dump(mode="json"))


@router.post("/{topic_id}/decision")
async def record_decision(topic_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """记录关键决策到决策记录 (spec §7.1) — used by the `cheese decision` CLI."""
    topic = await TopicService(db).get_or_404(topic_id)
    decision = (body.get("decision") or "").strip()
    if not decision:
        raise ValidationError("decision 不能为空")
    block = await BlockRepository(db).add(
        project_id=topic.project_id,
        topic_id=topic_id,
        author="cheese",
        author_type=AuthorType.ai,
        content=decision,
        kind=BlockKind.decision,
        refs=[str(topic_id)],
    )
    return ok(BlockOut.model_validate(block).model_dump(mode="json"))


@router.post("/{topic_id}/title")
async def set_title(topic_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """给话题起/改标题 — used by `cheese title`. Titles are AI-generated (the agent
    names an untitled topic from the task), never deterministically derived."""
    topic = await TopicService(db).get_or_404(topic_id)
    title = (body.get("title") or "").strip()
    if not title:
        raise ValidationError("title 不能为空")
    topic.title = title[:80]
    await db.flush()
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


@router.post("/{topic_id}/split")
async def split_topic(topic_id: uuid.UUID, body: SplitIn, db: DbSession) -> dict:
    """从上往下拆解：split a todo into a sub-topic (eval A2)."""
    topic = await TopicService(db).split_to_subtopic(
        parent_topic_id=topic_id, title=body.title, created_by=body.created_by
    )
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


@router.post("/{topic_id}/return-conclusion")
async def return_conclusion(
    topic_id: uuid.UUID, body: ConclusionIn, db: DbSession
) -> dict:
    """结论回流：write a sub-topic's conclusion back to its parent."""
    block = await TopicService(db).return_conclusion(
        subtopic_id=topic_id, conclusion=body.conclusion
    )
    return ok(BlockOut.model_validate(block).model_dump(mode="json"))


# Block upgrade lives here (it produces a topic). Separate router prefix.
block_router = APIRouter(prefix="/api/blocks", tags=["topics"])


@block_router.post("/{block_id}/upgrade")
async def upgrade_block(
    block_id: uuid.UUID, body: UpgradeBlockIn, db: DbSession
) -> dict:
    """讨论升级：upgrade a block into its own topic (eval A1)."""
    topic = await TopicService(db).upgrade_block_to_topic(
        block_id=block_id, created_by=body.created_by
    )
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))
