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

import asyncio
import base64
import binascii
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.auth import ActorResolverDep
from app.api.deps import get_broker, get_chat_service, get_work_runner
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.topics_deps import (
    BlockOut,
    BlockRepository,
    _actor_in_place,
    reply_preview,
    require_person_speaks,
)
from app.api.write_access import ROUTE_DECIDES, Can, InChannel
from app.auth.project_roles import may
from app.core import raster_images
from app.core.errors import (
    AuthenticationRequiredError,
    ConflictError,
    ForbiddenError,
    ValidationError,
)
from app.core.sentences import listing, say, size_text
from app.domain.agent.announce import announce
from app.domain.agent.chat import ChatService, project_refs_text
from app.domain.agent.mentions import mentioned_outside
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent.turn.intake.human import preview_addressing
from app.domain.agent.turn.store.human import AddressingChanged, HumanHandoff
from app.domain.agent_instance.adapters import project_team
from app.domain.block.message_input import (
    ChatAddressingIn,
    ChatAddressingOut,
    ChatAttachmentIn,
    ChatMessageIn,
)
from app.domain.delivery.agent import dispatch_pending
from app.domain.delivery.input_identity import InputReconciliationPending
from app.domain.delivery.note import send_note
from app.domain.library import service as library
from app.domain.notice_meta import (
    EVENT_MENTION_FUSED,
    SEVERITY_WARN,
    WHO_PLATFORM,
    notice,
)
from app.domain.project.reads import load_project
from app.domain.project_permissions import Permission
from app.domain.room_task import naming
from app.domain.room_task.place import place_or_404
from app.domain.thread.services import answered_in
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topics"])


#: How many uploaded files one message carries. A larger selection keeps its
#: first nine, as the composer always has, rather than refusing the message.
ATTACHMENTS_PER_MESSAGE = 9


@router.post(
    "/{topic_id}/messages", operation_id="chat-publish", dependencies=[ROUTE_DECIDES]
)
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

    Anyone in the project speaks in a task, as in a 支线 — it is often one
    that became the task, with the same people in it. What its AI teammate
    acts on is its owner's and collaborators' word (`agent.turn.intake.human`).
    A closed task is still talked in: its teammate answers when named and
    changes nothing (`Place.keeps_work`). Of the agents, only the task's own
    session publishes there, in its turn.
    """
    place = await place_or_404(db, topic_id)
    if place.inner_id is None:
        await TopicMemberService(db)._repo.list_for_topic(place.room_id)
    actor = await _actor_in_place(resolver, place)
    if not actor.authenticated:
        raise AuthenticationRequiredError("Sign in to send a message")
    task = place.task
    if task is not None:
        if actor.via == "cheese":
            if resolver.credential_conversation() != task.id:
                raise ForbiddenError(say("taskOwnSessionSpeaks"))
            return ok(await _publish_as_agent(chat, place, body, actor.handle, db))
    elif await TopicMemberService(db).holds_an_agent_seat(place.room, actor.handle):
        return ok(await _publish_as_agent(chat, place, body, actor.handle, db))
    if task is None and actor.via == "cheese":
        # An agent credential whose seat in this room was revoked. The seat is
        # the grant, so it may not go on writing here under a person's rules.
        raise ForbiddenError("An agent must hold a seat in this room to write here")
    members = await require_person_speaks(db, actor, place)
    content = body.content.strip()
    attachments = [
        {"path": a.path, "mime": a.mime}
        for a in body.attachments[:ATTACHMENTS_PER_MESSAGE]
    ]
    if not content and not attachments:
        raise ValidationError(say("messageOrAttachment"))
    may_summon = await may(db, place.project_id, actor.handle, Permission.ask_agent)
    # Compute the room's people before `receive_message` commits its writer
    # session and invalidates `request_facts`, reusing the roster and agent
    # binding facts already loaded above.
    people = (
        [m.handle for m in members if not m.agent]
        if members is not None
        else await TopicMemberService(db).people_in(place.room, team=project_team)
    )

    async def addressed_now() -> AddressingChanged:
        """Refuse a send shown as one for an AI teammate that is for none now,
        saying whom it is for now."""
        if place.task is not None:
            # This request read the task before the writer refused the send;
            # whom it is for now turns on the task as it is now.
            await db.refresh(place.task)
        now = await preview_addressing(
            db,
            place,
            content,
            actor.handle,
            reply_to=body.reply_to,
            with_text=bool(content),
            may_summon=may_summon,
        )
        return AddressingChanged(now.as_json())

    if body.expects_agent and not may_summon:
        raise await addressed_now()
    landed: list[dict] = []
    try:
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
            client_id=str(body.request_id),
            may_summon=may_summon,
            # The writer opens its own session; what this request already read of
            # the project goes with the message instead of being read there again.
            handoff=HumanHandoff(
                project=await load_project(db, place.project_id),
                roster=members,
                place=place,
            ),
            by_assistant=actor.by_assistant,
            expects_agent=body.expects_agent,
            landed=landed,
        )
    except AddressingChanged:
        raise await addressed_now() from None
    # The anchor as the room was told it, preview of the parent included: what
    # reading it back produced, without the reads.
    told = next((p for p in landed if p.get("id") == str(anchor_id)), None)
    if told is not None:
        sent = dict(told)
    else:
        stored = await BlockRepository(db).get(anchor_id)
        sent = BlockOut.model_validate(stored).model_dump(mode="json")
        # The sender's own copy may be the first one the client sees. It
        # carries the same one-line preview as the broadcast, or the cue would
        # blink out when the parent is on a page this client never loaded.
        if stored is not None and stored.reply_to is not None:
            parent = await BlockRepository(db).get(stored.reply_to)
            if parent is not None and parent.conversation_id == stored.conversation_id:
                sent["reply_preview"] = reply_preview(parent)
    # Told to the sender alone, never stored: nobody else is in this channel and
    # the message asked no AI teammate, so nobody is going to answer it. Said
    # once, under their message, so the silence that follows is not a mystery.
    recipient = (sent.get("meta") or {}).get("agent_recipient") or {}
    if not (may_summon and recipient.get("mentioned")):
        if set(people) == {actor.handle}:
            sent["alone"] = True
    # Told to the sender alone, in this answer, and never stored: whom the
    # message names that the channel does not seat (Slack's "X isn't in this
    # channel"). The sender decides whether to add them.
    outside = await mentioned_outside(db, place.room, sent["content"], actor.handle)
    if outside is not None:
        sent["mentioned_outside"] = outside.as_json()
    return ok(sent)


@router.post(
    "/{topic_id}/messages/addressing",
    operation_id="chat-addressing",
    **typed_response(ChatAddressingOut),
)
async def preview_message_addressing(
    body: ChatAddressingIn,
    db: DbSession,
    at: Annotated[InChannel, Can(Permission.speak, at="topic_id")],
) -> dict:
    """Who a person's message would be for if sent now: the AI teammate it
    would start a turn for and the rule that calls it, or none; and the people
    it @-s. The composer shows it before the message is sent, so it asks what
    sending asks: someone who may speak in this channel.

    It reads the draft the way sending reads a message
    (`agent.turn.intake.human.preview_addressing`) and writes nothing: no
    block, no seat, no turn."""
    addressing = await preview_addressing(
        db,
        at.place,
        body.content.strip(),
        at.actor.handle,
        reply_to=body.reply_to,
        with_text=body.with_text,
        may_summon=await may(
            db, at.place.project_id, at.actor.handle, Permission.ask_agent
        ),
    )
    return ok(addressing.as_json())


def _read_agent_attachments(items: list[ChatAttachmentIn]) -> list[dict]:
    """Decode and check every picture before anything is written.

    An agent's file lives on the executor, so it arrives as bytes, not as a
    path. The bytes decide what it is (`raster_images.sniff`), and only the
    four pictures a browser and a model both take inline get through. Reading
    all of them first keeps a bad second file from leaving the first one's
    bytes behind with no message to own them.
    """
    prepared: list[dict] = []
    for item in items[:ATTACHMENTS_PER_MESSAGE]:
        if not item.content_b64:
            # i18n-exempt: the agent reads this tool refusal, not a person's screen
            raise ValidationError("An agent sends a file by its bytes, not by a path")
        try:
            raw = base64.b64decode(item.content_b64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValidationError(say("contentB64Invalid")) from exc
        if not raw:
            raise ValidationError(say("emptyFile"))
        if len(raw) > library.MAX_FILE_BYTES:
            raise ValidationError(
                say("fileTooLarge", size=size_text(library.MAX_FILE_BYTES))
            )
        mime = raster_images.sniff(raw[:12])
        if mime is None:
            # i18n-exempt: the agent reads this tool refusal, not a person's screen
            raise ValidationError(
                "Only pictures (PNG, JPEG, GIF, WebP) go into the conversation; "
                "other files go out with `cheese show`"
            )
        suffix = raster_images.MEDIA_TYPE_SUFFIX[mime]
        name = item.name or item.path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        stem = name.rsplit(".", 1)[0] if "." in name else name
        prepared.append(
            {
                "name": library.clean_upload_name(stem) + suffix,
                "mime": mime,
                "digest": hashlib.sha256(raw).hexdigest(),
                "raw": raw,
            }
        )
    return prepared


async def _write_agent_attachments(place, prepared: list[dict]) -> list[dict]:
    """Put the checked pictures under this room's `uploads/<random>/<name>`.

    The address is the one a paste already gets, so the conversation draws the
    picture in the bubble without touching the preview panel.
    """
    written: list[dict] = []
    for item in prepared:
        path = f"{library.MESSAGE_ATTACHMENT_PREFIX}/{uuid.uuid4().hex}/{item['name']}"
        await asyncio.to_thread(
            library.write_room_file,
            place.project_id,
            place.room_id,
            path,
            item["raw"],
        )
        written.append({"path": path, "mime": item["mime"], "digest": item["digest"]})
    return written


def _publication_input(text: str, reply_to, prepared: list[dict]) -> dict:
    """What `request_id` stands for: the text, the reply, and the pictures.

    The same id with a different picture is a different publication; without the
    digests the idempotency rule would accept it and silently drop the new
    image.
    """
    return {
        "text": text,
        "reply_to": str(reply_to) if reply_to else None,
        **({"attachments": [item["digest"] for item in prepared]} if prepared else {}),
    }


async def _publish_as_agent(
    chat: ChatService, place, body: ChatMessageIn, author: str, db
) -> dict | None:
    """An agent's message: attributed to its live turn, never the start of one."""
    content = body.content.strip()
    reply_parent = None
    if body.reply_to is not None:
        parent = await BlockRepository(db).get(body.reply_to)
        if parent is None or parent.conversation_id != place.conversation_id:
            raise ValidationError("reply_to must belong to this conversation")
        reply_parent = parent
    content = await project_refs_text(db, place.project_id, place.room_id, content)
    prepared = (
        await asyncio.to_thread(_read_agent_attachments, body.attachments)
        if body.attachments
        else []
    )
    if not content and not prepared:
        raise ValidationError("content must not be blank")
    # The input request can finish while its terminal session is still working.
    runner = get_work_runner()
    work = runner.live_work_for_topic(place.conversation_id)
    turn_id = uuid.UUID(work["turn_id"]) if work is not None else None
    publication_id = str(body.request_id)
    # A retry whose response was lost carries the same request_id. The room
    # already has that message, so read its answer instead of writing a second
    # copy of the bytes that nothing will ever point at.
    previous = await chat.messages.stored_publication(
        topic_id=place.room_id, author=author, publication_id=publication_id
    )
    if previous is not None:
        if previous.get("input") != _publication_input(
            content, body.reply_to, prepared
        ):
            raise ConflictError(say("chatPublicationIdReused"))
        block = previous.get("block")
        # Older stored publications predate the preview; a retry still hands
        # the caller the same one-line quote the live path carries.
        if (
            block is not None
            and reply_parent is not None
            and block.get("reply_to") is not None
            and "reply_preview" not in block
        ):
            block["reply_preview"] = reply_preview(reply_parent)
        # The same frame a retry got before this check existed: the caller's
        # socket still sees the stored message come back, and the room's
        # timeline de-duplicates it by id.
        if block is not None:
            await get_broker().publish(
                str(place.conversation_id),
                {"type": "assistant_block", "block": block},
            )
        if turn_id is not None:
            runner.note_session_output(turn_id, tool=False)
        # A retry may follow a process that committed the block and died before
        # recording its mention deliveries. The ledger is idempotent by
        # event × seat (`delivery/mention.py`), so running it again here is the
        # same at-least-once recovery the retry got before this early return.
        if block is not None:
            if place.task is not None:
                naming.nudge(place.conversation_id, "message")
            await _summon_the_named(chat, runner, place, block, author)
        return block
    attachments = await _write_agent_attachments(place, prepared)
    (
        payload,
        attachment_payloads,
    ) = await chat.messages.persist_assistant_message_with_attachments(
        attachments=attachments,
        project_id=place.project_id,
        topic_id=place.room_id,
        inner_id=place.inner_id,
        text=content,
        turn_id=turn_id,
        reply_to=body.reply_to,
        roster=None,
        topic_refs=[],
        author=author,
        publication_id=publication_id,
        extra_meta={"quoted_context": body.quoted_context.model_dump(mode="json")}
        if body.quoted_context
        else None,
    )
    await get_broker().publish(
        str(place.conversation_id), {"type": "assistant_block", "block": payload}
    )
    # The pictures follow the words, in the order they were sent.
    for block in attachment_payloads:
        await get_broker().publish(
            str(place.conversation_id), {"type": "assistant_block", "block": block}
        )
    if turn_id is not None:
        runner.note_session_output(turn_id, tool=False)
    if payload is not None:
        if place.task is not None:
            naming.nudge(place.conversation_id, "message")
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


@router.post("/{topic_id}/note")
async def leave_a_note(
    at: Annotated[InChannel, Can(Permission.note_own_threads, at="topic_id")],
    body: dict,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """同 handle 便条的写侧（结论 11）：给自己的另一条线程留一句话。

    `topic_id` 是**发件人**——正在说话的那条线程，也是这一轮的令牌签给的那个地点；
    收件人那条线程在正文里，由平台拿两边的席位比出来（I14②）。同 `tell` 一样，URL
    里的 id 说的是「谁在说话」，从不说「改的是哪个资源」。

    它不落时间线：便条进的是那条线程里正在干活的会话（`notify_running_session`），不是
    房间里的一条消息。那边这一刻没有在干活的会话就没人接住，如实回 `delivered: false`。
    """

    place, actor = at.place, at.actor
    sender = actor.handle
    if not actor.authenticated:
        sender = await TopicMemberService(db).resolve_agent_handle(
            place.conversation_id, room_id=place.room_id
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
