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
defined; the five names topics.py read only for these routes -- `Addressee`,
`UpgradeBlockIn`, `EVENT_BLOCK_UPGRADED`, `SEVERITY_INFO` and `WHO_HUMAN` --
leave its imports with them. topics.py imports nothing from this module, so
there is no cycle.

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
from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.agent.chat import ChatService
from app.domain.agent.platform_notices import (
    EVENT_BLOCK_UPGRADED,
    SEVERITY_INFO,
    WHO_HUMAN,
    notice,
)
from app.domain.delivery.addressing import Event as Addressee
from app.domain.room_task.schemas import TaskOut
from app.domain.topic.schemas import TopicOut, UpgradeBlockIn
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
    """话题级未读数 (Feishu-style badges): {topic_id: unread_count} for the
    calling user, one query. Topics with zero unread are omitted.

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
    return ok({str(topic_id): count for topic_id, count in counts.items()})


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


# Block upgrade lives here (it produces a topic). Separate router prefix.
block_router = APIRouter(prefix="/blocks", tags=["topics"])


@block_router.post("/{block_id}/upgrade")
async def upgrade_block(
    block_id: uuid.UUID,
    body: UpgradeBlockIn,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    resolver: ActorResolverDep,
) -> dict:
    """讨论升级：upgrade a block into a place of its own (eval A1).

    Same mechanics as /split: the upgraded block is preset as the new place's
    task-brief doc, and it starts untitled.

    A message in a room becomes a CARD of work in that room; a message in a
    private chat becomes a room, because private chats are not in the topic tree
    and a card there would be one nobody else could open. The response says
    which by carrying either a task or a topic.

    **升级留下的是一条事件，不是一轮被平台点起来的对话**（结论 13）。以前这里 kickoff
    一轮：作者 `system`、提示词是平台写的一段开工说明，房间被平台叫醒去给这条活起名
    字、起分身。按结论 31，开一条活剩下的只有分支、卡和负责人，谁来做是负责人的事 ——
    所以平台在这里只做投递：房间时间线上落一条事件，收件人恰好是这条活的负责人。

    这是一条**在房间里造东西**的写：升级的 block 住在哪个房间，就要在那个房间站得
    住。以前两样都没有 —— block id 就是全部的门票，一个匿名调用者能往别人的房间里
    落一张卡，`created_by` 填谁它就是谁的。凭据由 resolve/authorize_topic 认
    （`app.api.auth`：会话说 token、agent 的 scoped token、或沙箱 token），房间由
    block 自己带 —— block 的 `topic_id` 就是那个房间，不是它自己去请求体里说。

    升级的人也一样由凭据说：请求体里没有 `created_by` 这一栏。登录的成员升级出来
    的卡（或房间）就是他自己的；只凭沙箱 token 进来的调用没有人可认，这里传
    None，归属走 `_resolve_owner` 那条梯子（房间主人 → 项目主人 → 团队主人）。
    """
    block = await BlockRepository(db).get(block_id)
    if block is None:
        raise NotFoundError("Block not found")
    parent = await TopicService(db).get_or_404(block.topic_id)
    actor = await resolver.resolve(topic_id=parent.id, project_id=parent.project_id)
    await resolver.authorize_topic(
        actor, project_id=parent.project_id, topic_id=parent.id
    )
    created_by = actor.handle if actor.authenticated else None
    room, thread, created = await TopicService(db).upgrade_block_to_place(
        block_id=block_id,
        created_by=created_by,
        reviewer_handle=body.reviewer_handle,
    )
    out = (
        TaskOut.model_validate(thread).model_dump(mode="json")
        if thread is not None
        else TopicOut.model_validate(room).model_dump(mode="json")
    )
    if created:
        # 负责人：卡是递给验收人的，没写验收人就是升级的那个人自己。事件和投递写在
        # 同一个事务里，和这次升级一起提交 —— 回滚了就不会留下一条指向不存在的活的
        # 通知。**幂等**：重复升级（created=False）不再落第二条事件。
        owner = (body.reviewer_handle or created_by or "").strip()
        await announce(
            db,
            place_id=room.id,
            content=(
                say("blockUpgradedToTask")
                if thread is not None
                else say("blockUpgradedToRoom")
            ),
            meta=notice(
                EVENT_BLOCK_UPGRADED,
                severity=SEVERITY_INFO,
                who=WHO_HUMAN,
                detail=(
                    say("blockUpgradedTaskId", id=thread.id)
                    if thread is not None
                    else say("blockUpgradedRoomId", id=room.id)
                ),
                detail_label=say("labelUpgradedTo"),
            ),
            points_at=Addressee(reviewers=(owner,) if owner else ()),
        )
    await db.commit()
    return ok(out)
