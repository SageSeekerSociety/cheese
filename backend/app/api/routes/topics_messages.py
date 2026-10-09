"""Room messages, sister-thread notes, and the summons they cause.

Eleventh slice of `app/api/routes/topics.py` (arch review C-backend.md section
3.3), after `topics_attachments.py` (#2171), `topics_documents.py` +
`topics_preview.py` (#2175), `topics_side_routes.py` (#2190),
`topics_compute.py` (#2197), `topics_title.py` (#2201), `topics_shown.py`
(#2207), `topics_tasks.py` (#2215), `topics_transcript.py` (#2218) and
`topics_comments.py` (#2224). topics.py is 2,188 lines against a 1,500-line cap
that only ratchets down; this slice takes it to 1,939.

What moves, verbatim:

  POST /topics/{topic_id}/messages   (operation_id="chat-publish")
  POST /topics/{topic_id}/note

plus `ChatMessageIn` (the body schema of the first) and `_summon_the_named`
(the helper the first calls). They are one group because they are one
direction of the conversation -- what is said INTO the room: a message from a
person or an agent, the @-mentions an agent's message wakes through
`_summon_the_named`, and a note to a sister thread of the same handle. Each
writes a line and hands it to whoever is meant to read it; none of them reads
the room's history back.

An agent's question is posted by `POST /topics/{topic_id}/asks`
(`topics_asks.py`); its answer is an ordinary message through the route below.

What stays behind, and why. `POST /{topic_id}/summon` stays: it is the
general "wake an agent now" door that is not a message at all, and it reads
helpers that a dozen staying handlers share. (`POST /{topic_id}/deliveries`,
定时投递, has its own module, `topics_deliveries.py`.) `_actor_in_place`
(identity, then the room's roster), `DbSession`, `TopicService`, `TopicMemberService`,
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

This module does not read `AgentTurnRepository`.

The three function-level imports that travel with the code --
`app.domain.delivery.mention` and `app.domain.delivery.agent` inside
`_summon_the_named`, `app.domain.delivery.note` inside `leave_a_note` -- are
api -> domain, which the layer contract allows downward, and none of them
reaches a `.models` module, so C2 is untouched. Nothing here imports another
domain's models directly, and no repository PAIR is added.

Ordering. Discovery walks `app.api.routes` in filename order, so this module
sorts after `topics_compute.py` and `topics_documents.py` and before
`topics_preview.py` (`compute` < `documents` < `messages` < `preview`). Its
two routes therefore mount later in the route table than they did inside
topics.py. No route registered before them -- in topics.py or in the modules
mounted between -- has a parameter where `messages` or `note` sits, so
neither loses its first full match; resolving every path in the
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

from app.api.auth import ActorResolverDep
from app.api.deps import get_broker, get_chat_service, get_work_runner
from app.api.response import ok
from app.api.routes.topics import (
    BlockOut,
    BlockRepository,
    DbSession,
    _actor_in_place,
)
from app.api.write_access import CHEESE_ONLY_IN_ROOM
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    ValidationError,
)
from app.core.sentences import listing, say
from app.domain.agent.announce import announce
from app.domain.agent.chat import ChatService, project_refs_text
from app.domain.agent.platform_notices import (
    EVENT_MENTION_FUSED,
    SEVERITY_WARN,
    WHO_PLATFORM,
    notice,
)
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.block.message_input import ChatAttachmentIn, ChatMessageIn  # noqa: F401
from app.domain.delivery.agent import dispatch_pending
from app.domain.delivery.input_identity import InputReconciliationPending
from app.domain.delivery.note import send_note
from app.domain.room_task.services import TaskService
from app.domain.thread.services import answered_in
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topics"])


#: How many uploaded files one message carries. A larger selection keeps its
#: first nine, as the composer always has, rather than refusing the message.
ATTACHMENTS_PER_MESSAGE = 9


@router.post("/{topic_id}/messages", operation_id="chat-publish")
async def send_chat_message(
    topic_id: uuid.UUID,
    body: ChatMessageIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """The one door a message enters a room by, for people and agents alike.

    The seat decides what the message is. From one of the room's agents it is a
    publication: it lands in that agent's own turn and starts none of its own.
    From anyone else it is a person speaking: it is the input a turn answers,
    so whoever it addresses is woken or handed it mid-turn. Either way a
    teammate it @-mentions gets a turn, the same as when a person names it.

    `request_id` makes a retry safe: the same id returns the message already
    stored instead of posting it twice.

    In a task only the people working it speak — its owner and the
    collaborators the owner brought in — and its own session, publishing in
    its turn. What anyone else has to say about a task they say in the room.
    """
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    if not actor.authenticated:
        raise AuthenticationRequiredError("Sign in to send a message")
    task = place.task
    if task is not None:
        if actor.via == "cheese":
            if resolver.credential_conversation() != task.id:
                raise ForbiddenError(say("taskOwnerOnly"))
            return ok(await _publish_as_agent(chat, place, body, actor.handle, db))
        if not TaskService.takes_part(task, actor.handle):
            raise ForbiddenError(say("taskParticipantsOnly"))
        TaskService.require_open(task)
    elif await TopicMemberService(db).holds_an_agent_seat(place.room, actor.handle):
        return ok(await _publish_as_agent(chat, place, body, actor.handle, db))
    if task is None and actor.via == "cheese":
        # An agent credential whose seat in this room was revoked. The seat is
        # the grant, so it may not go on writing here under a person's rules.
        raise ForbiddenError("An agent must hold a seat in this room to write here")
    if place.room.status == "archived":
        # An archived channel is read, not spoken in: its main line, its 支线
        # and its tasks alike. Unarchiving it is how it is spoken in again.
        raise ForbiddenError(say("roomArchivedUnarchiveFirst"))
    if place.inner_id is None and not await TopicMemberService(db).may_speak(
        place.room, actor.handle
    ):
        # A channel's main line is its members speaking. Anyone in the project
        # reads it and answers in a 支线 under it; speaking in the main line
        # is joining first.
        raise ForbiddenError(say("channelJoinToPost"))
    content = body.content.strip()
    attachments = [
        {"path": a.path, "mime": a.mime}
        for a in body.attachments[:ATTACHMENTS_PER_MESSAGE]
    ]
    if not content and not attachments:
        raise ValidationError(say("messageOrAttachment"))
    # The turn a person's message starts is named after the block it anchors.
    anchor_id = await get_work_runner().receive_message(
        chat,
        place.conversation_id,
        author=actor.handle,
        content=content,
        reply_to=str(body.reply_to) if body.reply_to else None,
        attachments=attachments,
        quoted_context=body.quoted_context.model_dump(mode="json")
        if body.quoted_context
        else None,
        provision_actor=actor,
        client_id=str(body.request_id),
    )
    stored = await BlockRepository(db).get(anchor_id)
    return ok(BlockOut.model_validate(stored).model_dump(mode="json"))


async def _publish_as_agent(
    chat: ChatService, place, body: ChatMessageIn, author: str, db
) -> dict | None:
    """An agent's message: attributed to its live turn, never the start of one."""
    if body.attachments:
        raise ValidationError("An agent shares a file with `cheese show`, not here")
    content = body.content.strip()
    if not content:
        raise ValidationError("content must not be blank")
    if body.reply_to is not None:
        parent = await BlockRepository(db).get(body.reply_to)
        if parent is None or parent.conversation_id != place.conversation_id:
            raise ValidationError("reply_to must belong to this conversation")
    content = await project_refs_text(db, place.project_id, place.room_id, content)
    # The input request can finish while its terminal session is still working.
    runner = get_work_runner()
    work = runner.live_work_for_topic(place.conversation_id)
    turn_id = uuid.UUID(work["turn_id"]) if work is not None else None
    payload = await chat.messages.persist_assistant_message(
        project_id=place.project_id,
        topic_id=place.room_id,
        inner_id=place.inner_id,
        text=content,
        turn_id=turn_id,
        reply_to=body.reply_to,
        roster=None,
        topic_refs=[],
        publish=True,
        author=author,
        publication_id=str(body.request_id),
        extra_meta={"quoted_context": body.quoted_context.model_dump(mode="json")}
        if body.quoted_context
        else None,
    )
    await get_broker().publish(
        str(place.conversation_id), {"type": "assistant_block", "block": payload}
    )
    await chat.thread_replied(place.conversation_id)
    if turn_id is not None:
        runner.note_session_output(turn_id, tool=False)
    # A task's session names nobody into its task: only its owner speaks there.
    if payload is not None and place.task is None:
        await _summon_the_named(chat, runner, place, payload, author)
    return payload


async def _summon_the_named(
    chat: ChatService, runner: AgentWorkRunner, place, payload: dict, author: str
) -> None:
    """这条消息 @ 到的 AI 队友，各起一轮（`delivery/mention.py`）。

    消息先落库、先广播，再记投递：被点名的那位醒来时，房间里已经有它要读的那一行。
    """
    # deferred-import: tests patch this name on app.domain.delivery.mention
    from app.domain.delivery.mention import AGENT_MENTIONS_PER_HOUR, record_mentions

    async with chat.session_factory() as session:
        block = await BlockRepository(session).get(uuid.UUID(payload["id"]))
        summoned = await record_mentions(
            session,
            project_id=place.project_id,
            room_id=place.room_id,
            conversation_id=await answered_in(session, block)
            if block is not None
            else place.conversation_id,
            block_id=uuid.UUID(payload["id"]),
            author=author,
            content=payload["content"],
            quoted_context=(payload.get("meta") or {}).get("quoted_context"),
            by_agent=True,
            occurred_at=datetime.now(UTC),
        )
        fused = None
        if summoned.fused:
            fused = await announce(
                session,
                place_id=place.room_id,
                task_id=place.inner_id,
                content=say("mentionFused"),
                meta=notice(
                    EVENT_MENTION_FUSED,
                    severity=SEVERITY_WARN,
                    who=WHO_PLATFORM,
                    detail=say(
                        "mentionFusedDetail",
                        limit=AGENT_MENTIONS_PER_HOUR,
                        names=listing(summoned.fused),
                    ),
                ),
                published_by_caller=True,
            )
        await session.commit()
    if fused is not None:
        await get_broker().publish(
            str(place.conversation_id),
            {
                "type": "event_block",
                "block": BlockOut.model_validate(fused).model_dump(mode="json"),
            },
        )
    if summoned.woken:
        await dispatch_pending(chat.session_factory, chat=chat, runner=runner)


@router.post("/{topic_id}/note", dependencies=[CHEESE_ONLY_IN_ROOM])
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
        raise ValidationError(say("threadIdInvalid")) from None
    delivered = await send_note(
        db,
        chat,
        sender=sender,
        from_project_id=place.project_id,
        to_thread=to_thread,
        content=body.get("content") or "",
    )

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
