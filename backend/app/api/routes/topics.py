"""Topic routes."""

import shutil
import uuid
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.deps import (
    get_broker,
    get_chat_service,
    get_turn_runner,
    project_device_online,
)
from app.api.response import ok, page
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import NotFoundError, ValidationError
from app.domain.agent.chat import ChatService, conclusion_digest_prompt
from app.domain.agent.market import (
    compute_default_name,
    compute_listings,
    compute_selectable,
)
from app.domain.agent.runtime import TurnRunner
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.mentions import canonicalize_refs
from app.domain.project.repositories import ProjectRepository
from app.domain.review.models import AcceptCard
from app.domain.review.repositories import AcceptCardRepository
from app.domain.team.repositories import TeamRepository
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
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.repositories import ComputeGrantRepository, UsageRepository
from app.domain.webhook import service as webhook_service
from app.domain.workspace import service as ws

router = APIRouter(prefix="/api/topics", tags=["topics"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("")
async def create_topic(
    body: TopicCreate, db: DbSession, resolver: ActorResolverDep
) -> dict:
    # The creator becomes the topic's roster owner (fusion-design §3). Resolve
    # them at the trust boundary (P1): the token's actor wins over any body
    # value, so the roster owner is who's really logged in — and body.created_by
    # stays a Phase-0 fallback for token-less callers.
    actor = await resolver.resolve(
        fallback_handle=body.created_by, project_id=body.project_id
    )
    topic = await TopicService(db).create(
        project_id=body.project_id,
        title=body.title,
        parent_id=body.parent_id,
        created_by=actor.handle if actor.handle != "anonymous" else body.created_by,
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
    # Emoji reactions ride the same payload — ONE batch query, no per-block N+1.
    reactions = await repo.reactions_for_blocks([b.id for b in blocks])
    items = []
    for b in blocks:
        item = BlockOut.model_validate(b).model_dump(mode="json")
        if b.id in reactions:
            item["reactions"] = reactions[b.id]
        items.append(item)
    return ok(page(items, total))


@router.get("/{topic_id}/transcript")
async def topic_transcript(topic_id: uuid.UUID, db: DbSession) -> dict:
    """施工现场 (spec §7.1): the topic's AI session record — 芝士's messages and
    tool/event actions, read-only."""
    await TopicService(db).get_or_404(topic_id)
    blocks = await BlockRepository(db).list_for_topic(topic_id)
    # 现场 = what 芝士 DID (tool/system events), full stop. Its messages belong
    # to the conversation pane — mirroring them here just duplicates the chat.
    site = [b for b in blocks if b.kind == BlockKind.event]
    items = [BlockOut.model_validate(b).model_dump(mode="json") for b in site]
    return ok(page(items, len(items)))


@router.get("/{topic_id}/usage")
async def topic_usage(topic_id: uuid.UUID, db: DbSession) -> dict:
    """资源用量 (spec §9.1): token/cost for this topic."""
    await TopicService(db).get_or_404(topic_id)
    return ok(await UsageRepository(db).for_topic(topic_id))


# The agent-facing gate-output slice: enough to read the failure, small enough
# for a prompt. The full output is already capped at persist time (GATE_TAIL).
_GATE_OUTPUT_TAIL = 2000


def _card_snapshot(card: AcceptCard) -> dict:
    return {
        "id": str(card.id),
        "status": str(card.status),
        "reviewer": card.reviewer_handle,
        "decided_by": card.decided_by,
        "decided_at": card.decided_at.isoformat() if card.decided_at else None,
        "note": card.note,
        "gate_passed_at": (
            card.gate_passed_at.isoformat() if card.gate_passed_at else None
        ),
        "gate_output_tail": card.gate_output[-_GATE_OUTPUT_TAIL:],
        "created_at": card.created_at.isoformat(),
    }


def _disk_snapshot(root: str) -> dict | None:
    try:
        du = shutil.disk_usage(root)
    except OSError:
        return None
    used_pct = round((du.total - du.free) * 100 / du.total) if du.total else None
    return {
        "free_gb": round(du.free / 2**30, 1),
        "total_gb": round(du.total / 2**30, 1),
        "used_pct": used_pct,
    }


@router.get("/{topic_id}/status")
async def topic_status(
    topic_id: uuid.UUID,
    db: DbSession,
    runner: Annotated[TurnRunner, Depends(get_turn_runner)],
) -> dict:
    """盲飞防护: one snapshot of "what is going on" for this topic — accept
    cards with their gate output, the current/last turn's time budget, and the
    platform waterlines (disk/queue/credits) — so an agent (via `cheese
    status`) or a debugging human doesn't have to poll several endpoints and
    guess. Read path, open like the rest of the MVP read surface."""
    topic = await TopicService(db).get_or_404(topic_id)
    cards = await AcceptCardRepository(db).list_for_topic(topic_id)
    credits = await ComputeGrantRepository(db).summary(topic.project_id)
    return ok(
        {
            "topic": {
                "id": str(topic.id),
                "title": topic.title,
                "status": str(topic.status),
                "branch": topic.branch_name,
            },
            "turn": runner.topic_turn(topic_id),
            "cards": [_card_snapshot(c) for c in cards],
            "platform": {
                "active_turns": runner.active_turns(),
                "queued_turns": runner.project_queue_depth(topic.project_id),
                "disk": _disk_snapshot(settings.workspace_root),
                "credits": {
                    "unlimited": credits["unlimited"],
                    "remaining": credits["credits_remaining"],
                },
            },
        }
    )


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
async def add_comment(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[TurnRunner, Depends(get_turn_runner)],
) -> dict:
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
    actor = await resolver.resolve(
        fallback_handle=body.get("author"),
        topic_id=topic_id,
        project_id=topic.project_id,
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    author = actor.handle
    comment = await repo.add(
        project_id=topic.project_id,
        topic_id=topic_id,
        author=author,
        author_type=AuthorType.human,
        content=content,
        kind=BlockKind.comment,
        reply_to=reply_to,
        anchor_quote=quote,
    )
    payload = BlockOut.model_validate(comment).model_dump(mode="json")
    await db.commit()  # the comment must be visible before the turn reads it
    # 评论即反馈：文档是芝士维护的界面，人评论了就叫它来处理（回应/改文档）。

    where = f"「{quote[:80]}」" if quote else "整篇"
    runner.submit(
        chat,
        topic_id,
        author="system",
        content=(
            f"{author} 在活文档 {where} 处评论：{content}\n"
            "请处理这条评论：需要改文档就直接改；有分歧就在对话里简短回应。"
        ),
        summon=True,
        nudge_event=f"💬 {author} 在文档上留了评论，芝士来处理",
    )
    return ok(payload)


@router.get("/{topic_id}/doc")
async def get_topic_doc(topic_id: uuid.UUID, db: DbSession) -> dict:
    """The topic's single living doc (spec §2.2 docs-out)."""
    doc = await TopicService(db).get_doc(topic_id)
    if doc is None:
        return ok(None)
    return ok(BlockOut.model_validate(doc).model_dump(mode="json"))


@router.put("/{topic_id}/doc")
async def edit_topic_doc(
    topic_id: uuid.UUID,
    body: DocEditIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """改文档即指令 (eval B2): edit the living doc; emits a conversation event."""
    topic = await TopicService(db).get_or_404(topic_id)
    # actor 在信任边界注入: prefer the verified token, fall back to body.author.
    actor = await resolver.resolve(
        fallback_handle=body.author,
        topic_id=topic_id,
        project_id=topic.project_id,
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    # Same backstop as chat replies: friendly "@名字 / @话题名" → structured
    # token, so refs in the doc render as clickable chips (docs used to skip
    # this and stayed plain text).
    content = await canonicalize_refs(
        db, topic.project_id, body.content, exclude_topic_id=topic_id
    )
    doc = await TopicService(db).edit_doc(
        topic_id=topic_id, content=content, author=actor.handle
    )
    return ok(BlockOut.model_validate(doc).model_dump(mode="json"))


@router.get("/{topic_id}/compute-profile")
async def get_topic_compute_profile(topic_id: uuid.UUID, db: DbSession) -> dict:
    """The compute this topic runs on (execution-architecture v4 会话级选择).

    `current` is the effective pool
    (topic choice → project sticky → team default → platform default).
    `locked` is true once the topic has run (session_id set) — the picker freezes
    then, matching the device-affinity boundary. `sticky` is the effective starting
    choice for a new topic (project memory, then team default); `profiles` include
    unavailable targets so a locked offline device still has a readable label."""
    topic = await TopicService(db).get_or_404(topic_id)
    project = await ProjectRepository(db).get(topic.project_id)
    sticky = (project.settings or {}).get("compute_profile") if project else None
    team_default = None
    if project is not None and project.team_id is not None:
        team = await TeamRepository(db).get_by_id(project.team_id)
        team_default = team.compute_profile if team is not None else None
    device_online = await project_device_online(db, topic.project_id)
    return ok(
        {
            "current": (
                topic.compute_profile
                or sticky
                or team_default
                or compute_default_name()
            ),
            "locked": topic.session_id is not None,
            "inherited": topic.compute_profile is None,
            "sticky": sticky or team_default or compute_default_name(),
            "profiles": [
                asdict(v)
                for v in compute_listings(settings, device_online=device_online)
            ],
        }
    )


@router.put("/{topic_id}/compute-profile")
async def set_topic_compute_profile(
    topic_id: uuid.UUID, body: dict, db: DbSession
) -> dict:
    """Pick the topic's compute pool. Allowed only before the first turn
    (session_id NULL); once the topic has run the pin is frozen so its work tree /
    session never move. The choice also updates the project's sticky default, so
    the next new topic inherits it (spec v4: 选了之后持久化，除非新 session 又改)."""
    topic = await TopicService(db).get_or_404(topic_id)
    if topic.session_id is not None:
        raise ValidationError("话题已开始，算力已锁定；新建话题可另选算力")
    name = (body.get("profile") or "").strip() or compute_default_name()
    device_online = await project_device_online(db, topic.project_id)
    allowed = {v.id for v in compute_selectable(settings, device_online=device_online)}
    if name not in allowed:
        raise ValidationError(f"算力池 {name!r} 尚未接入，暂不可选")
    topic.compute_profile = name
    project = await ProjectRepository(db).get(topic.project_id)
    if project is not None:
        project.settings = {**(project.settings or {}), "compute_profile": name}
    await db.flush()
    return ok({"current": name, "locked": False, "inherited": False})


@router.post("/{topic_id}/ask")
async def ask_options(topic_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """芝士 asks an option question IN the chat (cheese ask): a message block
    whose meta.options renders as one-click buttons. Structured interaction —
    the answer comes back as data, never parsed from prose (spec §14.5)."""
    topic = await TopicService(db).get_or_404(topic_id)
    question = (body.get("question") or "").strip()
    options = [str(o).strip() for o in (body.get("options") or []) if str(o).strip()]
    if not question:
        raise ValidationError("question is required")
    if not 2 <= len(options) <= 4:
        raise ValidationError("需要 2-4 个选项")
    blk = await BlockRepository(db).add(
        project_id=topic.project_id,
        topic_id=topic_id,
        author=await TopicMemberService(db).resolve_agent_handle(topic_id),
        author_type=AuthorType.ai,
        content=question,
        kind=BlockKind.message,
        meta={"options": options},
    )
    await db.commit()
    payload = BlockOut.model_validate(blk).model_dump(mode="json")
    await get_broker().publish(
        str(topic_id), {"type": "assistant_block", "block": payload}
    )
    return ok(payload)


@router.post("/blocks/{block_id}/answer")
async def answer_options(
    block_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[TurnRunner, Depends(get_turn_runner)],
) -> dict:
    """One-click answer to an option question: validates the choice against the
    ask block's own options, records it on the block (meta.answered), and posts
    the choice as the answerer's message with summon — 芝士 continues."""
    option = (body.get("option") or "").strip()
    repo = BlockRepository(db)
    blk = await repo.get(block_id)
    if blk is None:
        raise NotFoundError("问题不存在")
    actor = await resolver.resolve(
        fallback_handle=body.get("author"),
        topic_id=blk.topic_id,
        project_id=blk.project_id,
    )
    await resolver.authorize_topic(
        actor, project_id=blk.project_id, topic_id=blk.topic_id
    )
    author = actor.handle
    if author == "anonymous" or not option:
        raise ValidationError("author 和 option 都要有")
    meta = dict(blk.meta or {})
    options = meta.get("options") or []
    if option not in options:
        raise ValidationError("不在选项里")
    if meta.get("answered"):
        raise ValidationError(
            f"已由 {meta.get('answered_by')} 选过：{meta.get('answered')}"
        )
    meta["answered"] = option
    meta["answered_by"] = author
    blk.meta = meta
    await db.flush()
    updated = BlockOut.model_validate(blk).model_dump(mode="json")
    await db.commit()
    await get_broker().publish(
        str(blk.topic_id), {"type": "block_updated", "block": updated}
    )
    # The choice lands as the answerer's own message + summons 芝士 to continue.
    runner.submit(
        chat,
        blk.topic_id,
        author=author,
        content=option,
        summon=True,
    )
    return ok(updated)


@router.post("/{topic_id}/webhook-token")
async def mint_webhook_token(topic_id: uuid.UUID, db: DbSession) -> dict:
    """Mint (or rotate) this topic's webhook credential — used by the `cheese`
    CLI to hand a caller a token for POST /webhooks/{topic_id}. Rotating
    invalidates every previously-minted token for this topic; the raw value is
    returned once and never recoverable afterwards."""
    topic = await TopicService(db).get_or_404(topic_id)
    token = await webhook_service.mint(
        db, topic_id=topic_id, project_id=topic.project_id
    )
    await db.commit()
    return ok({"token": token})


@router.post("/{topic_id}/decision")
async def record_decision(topic_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """记录关键决策到决策记录 (spec §7.1) — used by the `cheese decision` CLI."""
    topic = await TopicService(db).get_or_404(topic_id)
    decision = (body.get("decision") or "").strip()
    if not decision:
        raise ValidationError("decision 不能为空")
    decision = await canonicalize_refs(
        db, topic.project_id, decision, exclude_topic_id=topic_id
    )
    block = await BlockRepository(db).add(
        project_id=topic.project_id,
        topic_id=topic_id,
        author=await TopicMemberService(db).resolve_agent_handle(topic_id),
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


@router.post("/{topic_id}/read")
async def mark_topic_read(topic_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """话题级已读位: bump the user's read cursor (opening a topic clears its
    unread badge, Feishu-style)."""
    handle = (body.get("handle") or "").strip()
    if not handle:
        raise ValidationError("handle 不能为空")
    await TopicService(db).mark_read(topic_id, handle)
    return ok({"topic_id": str(topic_id), "handle": handle})


@router.post("/{topic_id}/archive")
async def archive_topic(topic_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """手动归档 (归档去向): explicit archive, independent of 采纳."""
    by = (body.get("by") or "anonymous").strip() or "anonymous"
    topic = await TopicService(db).archive(topic_id, by=by)
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


@router.post("/{topic_id}/unarchive")
async def unarchive_topic(topic_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """取消归档: bring an archived topic back to active."""
    by = (body.get("by") or "anonymous").strip() or "anonymous"
    topic = await TopicService(db).unarchive(topic_id, by=by)
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


@router.post("/{topic_id}/split")
async def split_topic(
    topic_id: uuid.UUID,
    body: SplitIn,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    resolver: ActorResolverDep,
) -> dict:
    """从上往下拆解：split a todo into a sub-topic (eval A2).

    The child is seeded with a task-brief living doc, then its 分身 is kicked
    off automatically (spec §8.4 分身异步工作): without this, a freshly split
    sub-topic just sits idle until a human wanders in and posts a message."""
    service = TopicService(db)
    parent = await service.get_or_404(topic_id)
    # actor 在信任边界注入 (同 edit_topic_doc): prefer the verified token, fall
    # back to body.created_by, and require the caller actually have access to
    # the PARENT topic — a body-trusted `created_by` let anyone split anyone
    # else's topic and mint an arbitrary roster owner.
    actor = await resolver.resolve(
        fallback_handle=body.created_by,
        topic_id=topic_id,
        project_id=parent.project_id,
    )
    await resolver.authorize_topic(
        actor, project_id=parent.project_id, topic_id=topic_id
    )
    topic = await service.split_to_subtopic(
        parent_topic_id=topic_id,
        title=body.title,
        created_by=actor.handle if actor.handle != "anonymous" else body.created_by,
        brief=body.brief,
    )
    out = TopicOut.model_validate(topic).model_dump(mode="json")
    # Commit BEFORE kicking off: the 分身's first turn runs in the background
    # with its own session and must see the sub-topic + its brief doc.
    await db.commit()
    get_turn_runner().submit_kickoff(chat, topic.id)
    return ok(out)


@router.post("/{topic_id}/clone-from")
async def clone_topic_from(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Clone (transcript-fork) another topic's Claude conversation onto THIS
    topic (fusion-design §6 clone — 「复制自」/并行探索, not 分身).

    Auth: the actor must have access to BOTH the target (where the copy lands)
    and the SOURCE (whose conversation is being read out). A token alone is
    necessary-not-sufficient — access is checked per actor on each topic (§4).
    Only meaningful on backends with real session files (tmux/device); the sdk
    backend returns a clear 422 (degrade to a fresh 子话题)."""
    source_raw = (body.get("source_topic_id") or "").strip()
    if not source_raw:
        raise ValidationError("source_topic_id 必填")
    try:
        source_id = uuid.UUID(source_raw)
    except ValueError as exc:
        raise ValidationError("source_topic_id 不是合法的话题 id") from exc
    service = TopicService(db)
    target = await service.get_or_404(topic_id)
    source = await service.get_or_404(source_id)
    actor = await resolver.resolve(
        fallback_handle=body.get("by"),
        topic_id=topic_id,
        project_id=target.project_id,
    )
    # Must be allowed on BOTH ends: reading the source's session is as sensitive
    # as writing into the target.
    await resolver.authorize_topic(
        actor, project_id=target.project_id, topic_id=topic_id
    )
    await resolver.authorize_topic(
        actor, project_id=source.project_id, topic_id=source_id
    )
    topic = await service.clone_from(
        target_topic_id=topic_id, source_topic_id=source_id
    )
    await db.commit()
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


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
    topic = await service.get_or_404(topic_id)
    # Friendly "@名字/@话题名" in the conclusion → structured tokens BEFORE it
    # lands in the parent (chips render + notifications fire there).
    conclusion = await canonicalize_refs(
        db, topic.project_id, body.conclusion, exclude_topic_id=topic_id
    )
    block = await service.return_conclusion(subtopic_id=topic_id, conclusion=conclusion)
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
    # 运行环境预览: the artifact is a RUNNING app inside the topic's container,
    # listening on the conventional $CHEESE_APP_PORT. HOW to run it is the AI's
    # judgment (per-project); the platform only proxies the published port.
    "app": "application/x-cheesex-app",
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
    as_ = (body.get("as") or "html").strip().lower()
    # An app artifact points at the running server, not a file — the stored
    # content is a human note ("Vue dev server"), not a path.
    if as_ == "app":
        path = (body.get("path") or "app").strip()[:120]
    else:
        path = _clean_artifact_path(body.get("path") or "")
    mime = _ARTIFACT_MIME.get(as_)
    if mime is None:
        allowed = "、".join(_ARTIFACT_MIME)
        raise ValidationError(f"暂不支持的类型 {as_!r}（可选：{allowed}）")
    block = await BlockRepository(db).add(
        project_id=topic.project_id,
        topic_id=topic_id,
        author=await TopicMemberService(db).resolve_agent_handle(topic_id),
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
    if art.mime_type == _ARTIFACT_MIME["app"]:
        # Resolve the container's published port LIVE — the mapping only exists
        # while the topic's container is up.
        url = ws.app_preview_url(topic_id)
        return ok(
            {
                "kind": "app",
                "path": art.content,
                "mime": art.mime_type,
                "url": url,
            }
        )
    return ok({"kind": "file", "path": art.content, "mime": art.mime_type})


@router.get("/{topic_id}/preview/raw")
async def get_preview_raw(topic_id: uuid.UUID, db: DbSession) -> Response:
    """The current file artifact served as a real page — 在新窗口打开 (Claude
    Artifacts style). CSP `sandbox allow-scripts` keeps it an opaque origin so
    artifact JS can't call our API as the user."""
    topic = await TopicService(db).get_or_404(topic_id)
    art = await BlockRepository(db).latest_artifact(topic_id)
    if art is None or art.mime_type == _ARTIFACT_MIME["app"]:
        raise NotFoundError("没有可打开的文件 artifact")
    data = ws.read_file_bytes(topic.project_id, art.content, topic_id=topic_id)
    return Response(
        content=data,
        media_type=art.mime_type or "text/html",
        headers={"Content-Security-Policy": "sandbox allow-scripts"},
    )


# ---- 聊天图片附件 (图片输入) -------------------------------------------------
# An attachment is a REAL file in the topic's worktree (所有产出都是 git): the
# upload writes bytes under uploads/, the message references it as an
# attachment block, and 芝士 sees it by Read-ing the file in its sandbox.

# Images only for now; the mime comes from the upload's content-type and the
# raw reader re-derives it from the extension (never from file sniffing).
_IMAGE_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
_EXT_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10MB per image


@router.post("/{topic_id}/attachments")
async def upload_attachment(
    topic_id: uuid.UUID, file: UploadFile, db: DbSession
) -> dict:
    """Upload a chat image into the topic's worktree (uploads/…). Returns the
    {path, mime} the client then references when sending the message."""
    topic = await TopicService(db).get_or_404(topic_id)
    mime = (file.content_type or "").split(";")[0].strip().lower()
    ext = _IMAGE_MIME_EXT.get(mime)
    if ext is None:
        allowed = "、".join(sorted(_IMAGE_MIME_EXT))
        raise ValidationError(f"只支持图片（{allowed}）")
    data = await file.read(MAX_ATTACHMENT_BYTES + 1)
    if not data:
        raise ValidationError("空文件")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ValidationError("图片太大（上限 10MB）")
    # Structural name only (uuid + extension) — nothing derived from content.
    path = f"uploads/img-{uuid.uuid4().hex[:12]}{ext}"
    ws.write_file_bytes(topic.project_id, path, data, topic_id=topic_id)
    return ok({"path": path, "mime": mime, "bytes": len(data)})


@router.get("/{topic_id}/attachments/raw")
async def attachment_raw(topic_id: uuid.UUID, path: str, db: DbSession) -> Response:
    """Raw bytes of an image attachment, for <img src=…>. Extension-whitelisted
    to images so this can never serve executable HTML from the worktree."""
    topic = await TopicService(db).get_or_404(topic_id)
    clean = _clean_artifact_path(path)
    suffix = "." + clean.rsplit(".", 1)[-1].lower() if "." in clean else ""
    mime = _EXT_IMAGE_MIME.get(suffix)
    if mime is None:
        raise ValidationError("只能读取图片附件")
    data = ws.read_file_bytes(topic.project_id, clean, topic_id=topic_id)
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "private, max-age=3600",
        },
    )


# Per-project unread map lives under /api/projects (a "/unread" path under
# /api/topics would be shadowed by the /{topic_id} route). Separate router.
project_router = APIRouter(prefix="/api/projects", tags=["topics"])


@project_router.get("/{project_id}/topic-unread")
async def project_topic_unread(
    project_id: uuid.UUID, handle: str, db: DbSession
) -> dict:
    """话题级未读数 (Feishu-style badges): {topic_id: unread_count} for one
    user, one query. Topics with zero unread are omitted."""
    counts = await TopicService(db).unread_counts(project_id, handle)
    return ok({str(topic_id): count for topic_id, count in counts.items()})


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
