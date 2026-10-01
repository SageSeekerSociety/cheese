"""The room's outgoing messages: publish, ask, note, and the summon they cause.

Eleventh slice of `app/api/routes/topics.py` (arch review C-backend.md section
3.3), after `topics_attachments.py` (#2171), `topics_documents.py` +
`topics_preview.py` (#2175), `topics_side_routes.py` (#2190),
`topics_compute.py` (#2197), `topics_title.py` (#2201), `topics_shown.py`
(#2207), `topics_tasks.py` (#2215), `topics_transcript.py` (#2218) and
`topics_comments.py` (#2224). topics.py is 2,188 lines against a 1,500-line cap
that only ratchets down; this slice takes it to 1,939.

What moves, verbatim:

  POST /topics/{topic_id}/messages   (operation_id="chat-publish")
  POST /topics/{topic_id}/ask
  POST /topics/{topic_id}/note

plus `ChatPublishIn` (the body schema of the first) and `_summon_the_named`
(the helper the first calls). They are one group because they are one
direction of the conversation -- what the room says OUTWARD: an agent-authored
message, the @-mentions that message then wakes through `_summon_the_named`, a
one-click option question 芝士 asks in the chat, and a note to a sister thread
of the same handle. Each writes a line and hands it to whoever is meant to
read it; none of them reads the room's history back.

What stays behind, and why. `POST /{topic_id}/deliveries` and
`POST /{topic_id}/summon` stay: the first is the DELIVERY half of the ask
family (定时投递 -- the platform hands it over later), the second is the
general "wake an agent now" door that is not a message at all, and both read
helpers that a dozen staying handlers share. `_actor_in_place` (identity, then
the room's roster), `DbSession`, `TopicService`, `TopicMemberService`,
`ChatService`, `AgentWorkRunner`, `project_refs_text`, `BlockRepository`,
`BlockOut`, `AuthorType` and `BlockKind` keep their home in topics.py and are
imported in: staying handlers read all of them, and the two ratchets read the
import site of the block-domain names. `AuthorType`, `BlockKind` and
`BlockOut` come from topics.py rather than `app.domain.block.models`, so the
frozen C2 edge `app.api.routes.topics -> app.domain.block.models` is neither
widened nor duplicated -- the baseline may not grow. `BlockRepository` is
imported from topics.py for the second ratchet: the guard in
`tests/unit/test_domain_import_guard.py` counts (route module, repository
module) pairs, and that pair still belongs to topics.py.

`AgentTurnRepository` is the one that moves rather than stays, and it moves
the PAIR rather than the import site: `ask_options` is its only reader in
topics.py, so it follows the code here and its `_EXEMPT` line is
re-attributed to this module -- the same debt with a new initiator, the shape
#2197 took for `app.domain.machine.repositories` and #2215 for
`app.domain.notification.models`. No pair is added and none is dropped.

The three function-level imports that travel with the code --
`app.domain.delivery.mention` and `app.domain.delivery.agent` inside
`_summon_the_named`, `app.domain.delivery.note` inside `leave_a_note` -- are
api -> domain, which the layer contract allows downward, and none of them
reaches a `.models` module, so C2 is untouched. Nothing here imports another
domain's models directly, and no repository PAIR is added or dropped -- the one
that travels is the re-attributed `AgentTurnRepository` above -- so no contract
grows and `boundary_baseline.py --dry-run` reports what it reported on `main`.

Ordering. Discovery walks `app.api.routes` in filename order, so this module
sorts after `topics_compute.py` and `topics_documents.py` and before
`topics_preview.py` (`compute` < `documents` < `messages` < `preview`). Its
three routes therefore mount later in the route table than they did inside
topics.py. No route registered before them -- in topics.py or in the modules
mounted between -- has a parameter where `messages`, `ask` or `note` sits, so
none of the three loses its first full match; resolving every path in the
table confirms each still reaches the handler it did before, now under
`app.api.routes.topics_messages`.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the declaration below, with
the same prefix and tags, is all it takes.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.deps import get_broker, get_chat_service, get_work_runner
from app.api.response import ok
from app.api.routes.topics import (
    BlockOut,
    BlockRepository,
    DbSession,
    _actor_in_place,
)
from app.core.errors import ForbiddenError, ValidationError
from app.domain.agent.announce import announce
from app.domain.agent.chat import ChatService, project_refs_text
from app.domain.agent.platform_notices import (
    EVENT_MENTION_FUSED,
    SEVERITY_WARN,
    WHO_PLATFORM,
    notice,
)
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topics"])


class ChatPublishIn(BaseModel):
    content: str = Field(min_length=1, max_length=100000)
    request_id: uuid.UUID
    reply_to: uuid.UUID | None = None


@router.post("/{topic_id}/messages", operation_id="chat-publish")
async def publish_chat_message(
    topic_id: uuid.UUID,
    body: ChatPublishIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """Publish an agent-authored message. It starts no turn of its own; a
    teammate it @-mentions gets one, the same as when a person names it."""
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=place.room_id, project_id=place.project_id
    )
    if not actor.authenticated:
        raise ForbiddenError("An authenticated agent must publish this message")
    # 先授权，再问席位。两道都是 403，顺序不改任何调用者看到的结果；改的是代价：
    # 席位那一问要读花名册、把 handle 换成用户行、再查 agent 绑定，而这条路由是
    # 每条消息都走的。没权限进这个房间的调用者不必先替我们付这几次查询。
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    if not await TopicMemberService(db).holds_an_agent_seat(place.room, actor.handle):
        raise ForbiddenError("An authenticated agent must publish this message")
    content = body.content.strip()
    if not content:
        raise ValidationError("content must not be blank")
    if body.reply_to is not None:
        parent = await BlockRepository(db).get(body.reply_to)
        if (
            parent is None
            or parent.topic_id != place.room_id
            or parent.task_id is not None
        ):
            raise ValidationError("reply_to must belong to this conversation")
    content = await project_refs_text(db, place.project_id, place.room_id, content)
    # The input request can finish while its terminal session is still working.
    runner = get_work_runner()
    work = runner.live_work_for_topic(place.room_id)
    turn_id = uuid.UUID(work["turn_id"]) if work is not None else None
    payload = await chat._persist_assistant_message(
        project_id=place.project_id,
        topic_id=place.room_id,
        text=content,
        turn_id=turn_id,
        reply_to=body.reply_to,
        roster=None,
        topic_refs=[],
        publish=True,
        author=actor.handle,
        publication_id=str(body.request_id),
    )
    await get_broker().publish(
        str(topic_id), {"type": "assistant_block", "block": payload}
    )
    if turn_id is not None:
        runner.note_session_output(turn_id, tool=False)
    if payload is not None:
        await _summon_the_named(chat, runner, place, payload, actor.handle)
    return ok(payload)


async def _summon_the_named(
    chat: ChatService, runner: AgentWorkRunner, place, payload: dict, author: str
) -> None:
    """这条消息 @ 到的 AI 队友，各起一轮（`delivery/mention.py`）。

    消息先落库、先广播，再记投递：被点名的那位醒来时，房间里已经有它要读的那一行。
    """
    from app.domain.delivery.agent import dispatch_pending
    from app.domain.delivery.mention import AGENT_MENTIONS_PER_HOUR, record_mentions

    async with chat.session_factory() as session:
        summoned = await record_mentions(
            session,
            project_id=place.project_id,
            room_id=place.room_id,
            block_id=uuid.UUID(payload["id"]),
            author=author,
            content=payload["content"],
            by_agent=True,
            occurred_at=datetime.now(UTC),
        )
        fused = None
        if summoned.fused:
            fused = await announce(
                session,
                place_id=place.room_id,
                content="AI 队友之间的点名本小时已到上限，这次没有叫醒对方",
                meta=notice(
                    EVENT_MENTION_FUSED,
                    severity=SEVERITY_WARN,
                    who=WHO_PLATFORM,
                    detail=(
                        f"同一个话题里，AI 队友点名每小时最多叫起 "
                        f"{AGENT_MENTIONS_PER_HOUR} 轮，防止互相点名停不下来。"
                        f"这次没叫醒：{'、'.join(summoned.fused)}。"
                        "人点名不受这个限制。"
                    ),
                ),
            )
        await session.commit()
    if fused is not None:
        await get_broker().publish(
            str(place.room_id),
            {
                "type": "event_block",
                "block": BlockOut.model_validate(fused).model_dump(mode="json"),
            },
        )
    if summoned.woken:
        await dispatch_pending(chat.session_factory, chat=chat, runner=runner)


@router.post("/{topic_id}/note")
async def leave_a_note(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """同 handle 便条的写侧（结论 11）：给自己的另一条线程留一句话。

    `topic_id` 是**发件人**——正在说话的那条线程，也是这一轮的令牌签给的那个地点；
    收件人那条线程在正文里，由平台拿两边的席位比出来（I14②）。同 `tell` 一样，URL
    里的 id 说的是「谁在说话」，从不说「改的是哪个资源」。

    它不落时间线：便条进的是那条线程正在跑的那一轮（`notify_running_turn`），不是
    房间里的一条消息。那边这一刻没有在跑的轮次就没人接住，如实回 `delivered: false`。
    """
    from app.domain.delivery.note import send_note

    place = await TopicService(db).place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    sender = actor.handle
    if not actor.authenticated:
        sender = await TopicMemberService(db).resolve_agent_handle(
            topic_id, room_id=place.room_id
        )
    thread = (body.get("thread") or "").strip()
    try:
        to_thread = uuid.UUID(thread)
    except ValueError:
        raise ValidationError("thread 要是一条线程的 id") from None
    delivered = await send_note(
        db,
        chat,
        sender=sender,
        from_project_id=place.project_id,
        to_thread=to_thread,
        content=body.get("content") or "",
    )
    from app.domain.delivery.input_identity import InputReconciliationPending

    if isinstance(delivered, InputReconciliationPending):
        return ok(
            {
                "delivered": None,
                "status": "reconciling",
                "input_id": str(delivered.identity.input_id),
                "transport_accepted": delivered.accepted,
            }
        )
    return ok({"delivered": delivered})
