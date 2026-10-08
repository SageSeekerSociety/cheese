"""The two routes of topics.py that a topic path cannot carry.

Fourth slice of `app/api/routes/topics.py` (arch review C-backend.md section 3.3),
after `topics_attachments.py` (#2171) and `topics_documents.py` +
`topics_preview.py` (#2175). topics.py is 3,480 lines against a 1,500-line cap
that only ratchets down, and what moves here is its tail: the two routers that
were declared in it but never under `/topics`, because neither path can be
written that way. The per-project unread maps
(`GET /projects/{project_id}/topic-unread`, `.../private-unread`) are addressed
by project -- a `/unread` path under `/topics` would be shadowed by the
`/{topic_id}` route -- and the upgrade (`POST /blocks/{block_id}/upgrade`) is
addressed by the block it turns into a place. Each keeps its own router and
prefix (`project_router`, `block_router`) and both keep `tags=["topics"]`,
because that is where these endpoints are published today and a move changes
where a handler lives and nothing else.

What stays behind, and why. `BlockRepository` is the one name read here that
topics.py merely imports, and it is imported from topics.py rather than from
`app.domain.block.repositories` on purpose -- the shape `topics_preview.py` uses,
for the same reason: the guard in `tests/unit/test_domain_import_guard.py`
ratchets (route module, repository module) pairs, and a direct import would add a
line to that ratchet. topics.py still reads `BlockRepository` in a dozen
handlers, so its own line stays matched and this move adds no exemption to any
boundary. Everything else here keeps its home and is imported from where it is
defined. topics.py imports nothing from this module, so there is no cycle.

Ordering. This module sorts after `topics.py` and after the other three
`topics_*` modules (`_` > `.`, and `side` > `preview`), so its three paths mount
after every route those files register. Nothing registered earlier can shadow
them: no route anywhere has a parameter where `topic-unread`, `private-unread` or
`upgrade` sits, so none of the three has a parameterized route to lose to.
Resolving every path in the route table confirms each still reaches the handler
it did before.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the two declarations below,
with the same prefixes and tags, are all it takes.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.auth import ActorResolverDep
from app.api.deps import get_chat_service
from app.api.response import ok
from app.api.routes.topics import BlockRepository, DbSession
from app.api.task_instructions import dispatch, source_text, tell_task
from app.api.task_origin import discussion, materials, materials_text
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.agent.chat import ChatService
from app.domain.agent.harness.prompt import task_opening_prompt
from app.domain.agent.runtime import announce_stale
from app.domain.conversation.services import room_of
from app.domain.room_task.schemas import TaskOut
from app.domain.topic.services import TopicService

# Per-project unread map lives under /api/projects (a "/unread" path under
# /api/topics would be shadowed by the /{topic_id} route). Separate router.
project_router = APIRouter(prefix="/projects", tags=["topics"])


@project_router.get("/{project_id}/topic-unread")
async def project_topic_unread(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    handle: str | None = None,
) -> dict:
    """What each channel and task has waiting for the calling user:
    {conversation_id: {"count", "new", "messages"}} — the number on it,
    whether its name is bold, and how many messages came since the last read
    (`TopicRepository.unread_counts`). Ones with nothing are omitted.

    Read-state is per-person, so the recipient comes from the verified
    credential (``handle`` is only checked against it) — a caller without one
    used to read anybody's badge map by naming them here.

    The project door comes first, the way it does on every other route that
    takes a ``project_id``: without it this map is a room directory. A
    non-member has no read cursor, so every count equals that topic's message
    total — the shape of the answer is "which rooms exist, and how busy each
    one is", even though the topics themselves answer 403 to the same caller."""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    recipient = await resolver.resolve_recipient(
        requested=handle, project_id=project_id, allow_anonymous=False
    )
    counts = await TopicService(db).unread_counts(project_id, recipient)
    return ok(
        {
            str(topic_id): {
                "count": unread.count,
                "new": unread.new,
                "messages": unread.messages,
            }
            for topic_id, unread in counts.items()
        }
    )


@project_router.get("/{project_id}/topic-notify-levels")
async def project_topic_notify_levels(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """我在这个项目里不在默认档位的频道：{topic_id: {"level", "muted_until"}}，
    过了期的静音算回默认、不列。同 ``topic-unread`` 的项目门和本人规则。"""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    recipient = await resolver.resolve_recipient(
        requested=None, project_id=project_id, allow_anonymous=False
    )
    levels = await TopicService(db).notify_levels(project_id, recipient)
    return ok(
        {
            str(topic_id): {
                "level": level,
                "muted_until": until.isoformat() if until else None,
            }
            for topic_id, (level, until) in levels.items()
        }
    )


@project_router.post("/{project_id}/read-all")
async def project_mark_all_read(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """全部标为已读：把我在这个项目里每一间有未读的房间的已读位推到现在。动的就是
    ``topic-unread`` 列出来的那些房间，看不见的房间一间都不碰。"""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    recipient = await resolver.resolve_recipient(
        requested=None, project_id=project_id, allow_anonymous=False
    )
    marked = await TopicService(db).mark_all_read(project_id, recipient)
    return ok({"topic_ids": [str(topic_id) for topic_id in marked]})


@project_router.get("/{project_id}/private-unread")
async def project_private_unread(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    handle: str | None = None,
) -> dict:
    """私聊未读数: {peer: unread_count} for the calling user, one query.

    Keyed by the other party rather than by topic id — private chats are not in
    the topic tree, so the roster page renders their rows from the member list
    and has no topic id to look one up with. A person is their handle; an AI
    teammate is `agent:<handle>`, which is also the word the DM's URL uses.
    Peers with zero unread are omitted.

    Same rule as ``topic-unread``: the recipient comes from the verified
    credential, never from the query string. It matters more here — this map
    names who a person is talking to privately, so honouring a caller-supplied
    handle would leak the shape of everyone's DMs.

    Same project door as ``topic-unread`` too, and for the same reason: the
    recipient question ("whose inbox") is not the resource question ("may you
    see this project"), and this route only used to ask the first."""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    recipient = await resolver.resolve_recipient(
        requested=handle, project_id=project_id, allow_anonymous=False
    )
    counts = await TopicService(db).private_unread_counts(project_id, recipient)
    return ok(counts)


# 转为任务 lives here (it starts from a block). Separate router prefix.
block_router = APIRouter(prefix="/blocks", tags=["topics"])


@block_router.post("/{block_id}/upgrade")
async def upgrade_block(
    block_id: uuid.UUID,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    resolver: ActorResolverDep,
) -> dict:
    """转为任务：a message in a channel, or a reply in its 支线, becomes a task
    owned by whoever turned it, and its agent drafts the task's document from
    the message and what was said around it. One message can become several
    tasks. A private chat's messages do not leave it.

    这是一条**在频道里造东西**的写：凭据由 resolve/authorize_topic 认，频道由 block
    自己带 —— block 的对话所在的频道就是那个频道，不是它自己去请求体里说。转的人也
    由凭据说，而且只能是一个人：AI 队友用自己的工具建任务（`cheese_task`）。
    """
    block = await BlockRepository(db).get(block_id)
    if block is None:
        raise NotFoundError("Block not found")
    parent = await TopicService(db).get_or_404(await room_of(db, block.conversation_id))
    actor = await resolver.resolve(topic_id=parent.id, project_id=parent.project_id)
    await resolver.authorize_topic(
        actor, project_id=parent.project_id, topic_id=parent.id
    )
    if not actor.authenticated or actor.via == "cheese":
        raise ForbiddenError(say("taskCreatedByPerson"))
    room, task = await TopicService(db).upgrade_block_to_place(
        block_id=block_id,
        created_by=actor.handle,
    )
    out = TaskOut.model_validate(task).model_dump(mode="json")
    # The task's agent drafts its document from the message and what was said
    # around it. Recorded in this transaction, so a rolled-back upgrade leaves
    # no instruction for a task that does not exist.
    await tell_task(
        db,
        task,
        task_opening_prompt(
            title=task.title,
            owner=task.owner_handle,
            source=await source_text(db, block),
            materials=materials_text(materials((await discussion(db, task))[1])),
        ),
        opening=True,
    )
    await db.commit()
    await announce_stale(room.id, "topics", id=room.id)
    await dispatch(chat)
    return ok(out)
