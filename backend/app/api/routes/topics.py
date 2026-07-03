"""Topic routes."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_chat_service, get_turn_runner
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import ValidationError
from app.domain.agent.chat import ChatService, conclusion_digest_prompt
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.topic.models import TopicStatus
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
    """Document-tree view of a topic: the living doc's structured node tree
    (B1, spec §5) in document order."""
    await TopicService(db).get_or_404(topic_id)
    nodes = await BlockRepository(db).list_doc_nodes(topic_id)
    items = [BlockOut.model_validate(b).model_dump(mode="json") for b in nodes]
    return ok(page(items, len(items)))


@router.get("/{topic_id}/comments")
async def list_comments(topic_id: uuid.UUID, db: DbSession) -> dict:
    """段落评论 (eval B4): inline comments, each anchored to a doc node via
    reply_to."""
    await TopicService(db).get_or_404(topic_id)
    comments = await BlockRepository(db).list_comments_for_topic(topic_id)
    items = [BlockOut.model_validate(c).model_dump(mode="json") for c in comments]
    return ok(page(items, len(items)))


@router.post("/{topic_id}/comments")
async def add_comment(topic_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """Add an inline comment anchored to a doc node (eval B4). Dual-use like the
    doc panel — a human selects text and comments; not cheese-gated."""
    topic = await TopicService(db).get_or_404(topic_id)
    anchor = (body.get("anchor") or "").strip()
    content = (body.get("content") or "").strip()
    if not content:
        raise ValidationError("评论内容不能为空")
    repo = BlockRepository(db)
    reply_to: uuid.UUID | None = None
    if anchor:
        node = await repo.get(uuid.UUID(anchor))
        if node is None or node.topic_id != topic_id:
            raise ValidationError("锚点不是本话题的文档块")
        reply_to = node.id
    # B4 Feishu-style: the exact selected span, kept for display next to the
    # comment. Bounded so a runaway selection can't bloat the row.
    quote = (body.get("quote") or "").strip() or None
    if quote and len(quote) > 500:
        quote = quote[:500]
    comment = await repo.add(
        project_id=topic.project_id,
        topic_id=topic_id,
        author=(body.get("author") or "anonymous"),
        author_type=AuthorType.human,
        content=content,
        kind=BlockKind.comment,
        reply_to=reply_to,
        anchor_quote=quote,
    )
    return ok(BlockOut.model_validate(comment).model_dump(mode="json"))


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
async def split_topic(
    topic_id: uuid.UUID,
    body: SplitIn,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """从上往下拆解：split a todo into a sub-topic (eval A2).

    The child is seeded with a task-brief living doc, then its 分身 is kicked
    off automatically (spec §8.4 分身异步工作): without this, a freshly split
    sub-topic just sits idle until a human wanders in and posts a message."""
    topic = await TopicService(db).split_to_subtopic(
        parent_topic_id=topic_id,
        title=body.title,
        created_by=body.created_by,
        brief=body.brief,
    )
    out = TopicOut.model_validate(topic).model_dump(mode="json")
    # Commit BEFORE kicking off: the 分身's first turn runs in the background
    # with its own session and must see the sub-topic + its brief doc.
    await db.commit()
    get_turn_runner().submit_kickoff(chat, topic.id)
    return ok(out)


@router.post("/{topic_id}/return-conclusion")
async def return_conclusion(
    topic_id: uuid.UUID,
    body: ConclusionIn,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """结论回流：write a sub-topic's conclusion back to its parent — then WAKE
    the parent to digest it (the return leg of the subagent loop: in Claude
    Code the parent resumes when the Task result arrives; here the parent 芝士
    runs a turn to weave the conclusion in and decide what's next)."""
    service = TopicService(db)
    block = await service.return_conclusion(
        subtopic_id=topic_id, conclusion=body.conclusion
    )
    parent = await service.get_or_404(block.topic_id)
    out = BlockOut.model_validate(block).model_dump(mode="json")
    wake = parent.status != TopicStatus.archived
    # Commit BEFORE waking: the parent's turn runs on its own session.
    await db.commit()
    if wake:
        get_turn_runner().submit_kickoff(
            chat, parent.id, prompt=conclusion_digest_prompt(block.content)
        )
    return ok(out)


# 芝士 → UI rendering (spec §9.1): an artifact is a file the AI explicitly points
# at + how to render it. The type comes from the tool call, never from parsing
# prose. MVP renders html/svg in the preview window; more types are additive.
_ARTIFACT_MIME = {
    "html": "text/html",
    "svg": "image/svg+xml",
}


def _clean_artifact_path(raw: str) -> str:
    """A workspace-relative pointer — reject absolute paths, traversal, and .git.
    The file itself is read later via the guarded workspace reader."""
    path = (raw or "").strip()
    if not path:
        raise ValidationError("path 不能为空")
    parts = path.split("/")
    if path.startswith("/") or ".." in parts or ".git" in parts:
        raise ValidationError("path 必须是工作区相对路径")
    return path


@router.post("/{topic_id}/artifact")
async def set_artifact(topic_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """芝士 marks a worktree file as a renderable artifact (spec §9.1) — used by
    `cheese artifact`. With no anchor it becomes the topic's current preview."""
    topic = await TopicService(db).get_or_404(topic_id)
    path = _clean_artifact_path(body.get("path") or "")
    as_ = (body.get("as") or "html").strip().lower()
    mime = _ARTIFACT_MIME.get(as_)
    if mime is None:
        allowed = "、".join(_ARTIFACT_MIME)
        raise ValidationError(f"暂不支持的类型 {as_!r}（可选：{allowed}）")
    block = await BlockRepository(db).add(
        project_id=topic.project_id,
        topic_id=topic_id,
        author="cheese",
        author_type=AuthorType.ai,
        content=path,
        kind=BlockKind.artifact,
        mime_type=mime,
        refs=[path],
    )
    return ok(BlockOut.model_validate(block).model_dump(mode="json"))


@router.get("/{topic_id}/preview")
async def get_preview(topic_id: uuid.UUID, db: DbSession) -> dict:
    """The topic's current preview (spec §7.1): the artifact 芝士 last pointed at,
    as {path, mime}. Null when none is set — the client may fall back to scanning
    the worktree. Content is fetched separately via the guarded file reader."""
    await TopicService(db).get_or_404(topic_id)
    art = await BlockRepository(db).latest_artifact(topic_id)
    if art is None:
        return ok(None)
    return ok({"path": art.content, "mime": art.mime_type})


# Block upgrade lives here (it produces a topic). Separate router prefix.
block_router = APIRouter(prefix="/api/blocks", tags=["topics"])


@block_router.post("/{block_id}/upgrade")
async def upgrade_block(
    block_id: uuid.UUID,
    body: UpgradeBlockIn,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """讨论升级：upgrade a block into its own topic (eval A1).

    Same mechanics as /split: the upgraded block is preset as the new topic's
    task-brief doc, and its 分身 kicks off automatically (it also names the
    topic on that first turn — upgraded topics start untitled)."""
    topic, created = await TopicService(db).upgrade_block_to_topic(
        block_id=block_id, created_by=body.created_by
    )
    out = TopicOut.model_validate(topic).model_dump(mode="json")
    # Commit BEFORE kicking off (the 分身's turn uses its own session); an
    # idempotent re-upgrade (created=False) must not kick the 分身 again.
    await db.commit()
    if created:
        get_turn_runner().submit_kickoff(chat, topic.id)
    return ok(out)
