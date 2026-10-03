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

plus `ChatMessageIn` (the body schema of the first) and `_summon_the_named`
(the helper the first calls). They are one group because they are one
direction of the conversation -- what is said INTO the room: a message from a
person or an agent, the @-mentions an agent's message wakes through
`_summon_the_named`, a one-click option question a member asks in the chat,
and a note to a sister thread of the same handle. Each writes a line and hands
it to whoever is meant to read it; none of them reads the room's history back.

`POST /topics/blocks/{block_id}/answer` followed later: the answer to an option
question is the other half of the ask, and who it goes back to is decided
beside the rule that decides who asked. No route of this prefix mounted before
this module has three literal-or-parameter segments ending in `answer`, so it
still reaches the same handler.

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

from app.api.auth import ActorResolverDep
from app.api.deps import get_broker, get_chat_service, get_work_runner
from app.api.response import ok
from app.api.routes.topics import (
    AuthorType,
    BlockKind,
    BlockOut,
    BlockRepository,
    DbSession,
    _actor_in_place,
)
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.domain.agent.announce import announce, notify_question
from app.domain.agent.chat import ChatService, project_refs_text
from app.domain.agent.platform_notices import (
    EVENT_MENTION_FUSED,
    SEVERITY_WARN,
    WHO_PLATFORM,
    notice,
)
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.block.message_input import ChatAttachmentIn, ChatMessageIn  # noqa: F401
from app.domain.block.notice_text import listing, say
from app.domain.block.schemas import OptionAnswerIn
from app.domain.delivery.ledger import settle
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
    """
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    if not actor.authenticated:
        raise AuthenticationRequiredError("Sign in to send a message")
    members = TopicMemberService(db)
    if await members.holds_an_agent_seat(place.room, actor.handle):
        return ok(await _publish_as_agent(chat, place, body, actor.handle, db))
    if actor.via == "cheese":
        # An agent credential whose seat in this room was revoked. The seat is
        # the grant, so it may not go on writing here under a person's rules.
        raise ForbiddenError("An agent must hold a seat in this room to write here")
    content = body.content.strip()
    attachments = [
        {"path": a.path, "mime": a.mime}
        for a in body.attachments[:ATTACHMENTS_PER_MESSAGE]
    ]
    if not content and not attachments:
        raise ValidationError(say("messageOrAttachment"))
    # The turn a person's message starts is named after the block it anchors.
    anchor_id = await get_broker().receive_message(
        chat,
        place.room_id,
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
        author=author,
        publication_id=str(body.request_id),
        extra_meta={"quoted_context": body.quoted_context.model_dump(mode="json")}
        if body.quoted_context
        else None,
    )
    await get_broker().publish(
        str(place.room_id), {"type": "assistant_block", "block": payload}
    )
    if turn_id is not None:
        runner.note_session_output(turn_id, tool=False)
    if payload is not None:
        await _summon_the_named(chat, runner, place, payload, author)
    return payload


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
            quoted_context=(payload.get("meta") or {}).get("quoted_context"),
            by_agent=True,
            occurred_at=datetime.now(UTC),
        )
        fused = None
        if summoned.fused:
            fused = await announce(
                session,
                place_id=place.room_id,
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


@router.post("/{topic_id}/ask")
async def ask_options(
    topic_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """An option question IN the chat: a message block whose meta.options
    renders as one-click buttons. Structured interaction — the answer comes
    back as data, never parsed from prose (spec §14.5).

    Any member of the room may ask one: 芝士 through `cheese_ask`, a person
    from the composer. Who may is the room's roster (`_actor_in_place`); what
    differs is only what the question does to a turn, and that is a fact about
    the asker's seat.

    芝士问的那种，本轮停在这里等回答，所以它同时通知发起这一轮的人（#1084）：其余
    每一种「下一步在人手上」都是一轮结束之后的状态，唯独这一种**中断**运行，而房间
    安静下来这件事本身没有人会注意到。人问的那种不停任何一轮，问的是整个房间，所以
    不指名、不另发通知：它就是时间线上的一条消息。
    """
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    question = (body.get("question") or "").strip()
    options = [str(o).strip() for o in (body.get("options") or []) if str(o).strip()]
    if not question:
        raise ValidationError("question is required")
    if not 2 <= len(options) <= 4:
        raise ValidationError(say("optionsTwoToFour"))
    if len(set(options)) != len(options):
        # An answer is matched to its option by text, so two equal options are
        # one button that cannot be told apart from the other.
        raise ValidationError(say("optionsDistinct"))
    # 署名是 agent 席位的那一支，这道题是芝士自己问出口的：它在等**人**按下那个按钮，
    # 不是在等自己把它读一遍。轮次号在这条路上填不出——`cheese_ask` 只在 CHEESE_TURN
    # 非空时才带 X-Cheese-Turn，而没有一处产品代码写那个环境变量，于是 `add` 的兜底
    # 拿到的永远是 None，「署名是 agent 且落在某一轮里」在这里答不出来。所以由写入端
    # 直接说明（`own_output`）：不说明的话这道题会盖上待读标记，「忘了 @」的补救按钮
    # 不再答「没有待读的东西」，白开一轮，而那一轮的 prompt 里躺着芝士刚问出口的这道
    # 题，它对着自己的问题再答一遍。人在房间里问出的那种照旧是一条待读输入。
    if actor.authenticated:
        author = actor.handle
        asked_by_agent = await TopicMemberService(db).holds_an_agent_seat(
            place.room, author
        )
    else:
        author = await TopicMemberService(db).resolve_agent_handle(
            topic_id, room_id=place.room_id
        )
        asked_by_agent = True
    asked: str | None = None
    if asked_by_agent:
        # 发起这一轮的人 —— 芝士是代他执行这件事的，这个问题也只有他能回答。平台
        # 发起的轮次（resume、各类提醒）作者是 system，那种提问指不到具体的人。
        #
        # 记在这道题自己身上（`meta.asked`），不留到以后再去问轮次：`cheese_ask`
        # 不等回答，芝士问完就收尾，这一轮随即关闭——过一会儿再问「开着的那一轮是
        # 谁的」，答案已经是「没有」，而题还摆在那儿等人。
        waiting_for = await AgentTurnRepository(db).open_turn_author_for_topic(
            place.room_id
        )
        asked = None if waiting_for == "system" else waiting_for
        if waiting_for is None:
            # 没有开着的轮次区间可问（有的执行路径不记它）：芝士此刻在回应的，就
            # 是最近点它名的那个人。不兜底的话 `asked` 为空，谁那里都不亮黄灯。
            asked = await BlockRepository(db).last_summoner(place.room_id)
    blk = await BlockRepository(db).add(
        project_id=place.project_id,
        # The place id: `add` splits it, so a thread's question is asked in the
        # thread rather than shouted into the room around it.
        topic_id=topic_id,
        author=author,
        author_type=AuthorType.participant,
        content=question,
        kind=BlockKind.message,
        # A person's question is answered back to them; the seat decided that
        # here, so the answer route reads it rather than guessing from a handle.
        meta={"options": options, "asked": asked}
        | ({} if asked_by_agent else {"answer_to": author}),
        own_output=asked_by_agent,
    )
    if asked_by_agent:
        await notify_question(
            db,
            place=place,
            block=blk,
            question=question,
            asker=blk.author,
            asked=asked,
        )
    await db.commit()
    payload = BlockOut.model_validate(blk).model_dump(mode="json")
    # A person's question arrives the way a person's message does, so the room
    # does not read it as the end of an agent's reply.
    frame = "assistant_block" if asked_by_agent else "user_block"
    await get_broker().publish(str(topic_id), {"type": frame, "block": payload})
    return ok(payload)


@router.post("/blocks/{block_id}/answer")
async def answer_options(
    block_id: uuid.UUID,
    body: OptionAnswerIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """One-click answer to an option question: validates the choice against the
    ask block's own options, records it on the block (meta.answered), and posts
    the choice as the answerer's message, addressed to whoever asked — the
    teammate whose turn waits on it, or the person who put the question to the
    room. The asker does not answer their own question.

    The answer is filed under the caller the credential names and nobody else:
    the body carries no author. So the global sandbox token alone (the trusted
    dev credential, naming nobody) gets a 401, since there is no one to answer
    as."""
    option = body.option.strip()
    repo = BlockRepository(db)
    blk = await repo.get(block_id)
    if blk is None:
        raise NotFoundError(say("optionQuestionNotFound"))
    actor = await resolver.resolve(
        topic_id=blk.topic_id,
        project_id=blk.project_id,
    )
    await resolver.authorize_topic(
        actor, project_id=blk.project_id, topic_id=blk.topic_id
    )
    if not actor.authenticated:
        raise AuthenticationRequiredError("Sign in to answer a question")
    author = actor.handle
    if not option:
        raise ValidationError(say("optionEmpty"))
    if author == blk.author:
        raise ForbiddenError(say("optionOwnQuestion"))
    meta = dict(blk.meta or {})
    options = meta.get("options") or []
    if option not in options:
        raise ValidationError(say("optionNotOffered"))
    if meta.get("answered"):
        raise ValidationError(
            say("optionTaken", by=meta.get("answered_by"), option=meta.get("answered"))
        )
    meta["answered"] = option
    meta["answered_by"] = author
    blk.meta = meta
    # 芝士问出口时通知过等这个回答的人（`notify_question`，事件身份就是这道题）。
    # 题答完了，那条通知跟着结掉，不再说「待你回答」。
    await settle(db, blk.id, {"answered": option})
    await db.flush()
    updated = BlockOut.model_validate(blk).model_dump(mode="json")
    await db.commit()
    await get_broker().publish(
        str(blk.topic_id), {"type": "block_updated", "block": updated}
    )
    # 选项是回答一个待确认问题，收件人就是问问题的那个席位。**@ 写进正文**，不在
    # 帧上另置一位：时间线上那条消息得自己说明它叫了谁，否则读的人看到的是一条谁
    # 也没叫的消息却起了一轮（这也是浏览器发消息时遵守的同一条规矩）。
    #
    # 只认名册上真有的席位（`addressable_agent_handle`）：正文里的 @ 是由名册解析
    # 回来的，塞一个不在名册上的 handle 进去，落在时间线上就是一个谁也对不上的
    # chip，而这一下点选项什么也不会发生。名册上没有 agent 时就谁也不点，选择照
    # 样记在卡上。
    #
    # 「问问题的那个席位」就是这张卡的署名：一个房间可以坐好几位 AI 队友，点房间
    # 的默认席位的话，别的队友问出的题一点选项就换成默认芝士来接，而它手上没有那
    # 道题的来龙去脉。署名者已不在名册上（被请出房间）才退回默认席位。
    #
    # 人问的题，答案回到问的那个人手上（问的时候按席位记下的 `answer_to`）：@ 他，
    # 不叫醒任何一位 AI 队友 —— 他问的是房间里的人，一位队友被这一下叫起来白跑一轮，
    # 读到的是一个不是问它的答案。
    members = TopicMemberService(db)
    if blk.author in await members.agent_handles(blk.topic_id):
        seat: str | None = blk.author
    elif meta.get("answer_to"):
        seat = meta["answer_to"]
    else:
        seat = await members.addressable_agent_handle(blk.topic_id)
    await get_broker().receive_message(
        chat,
        blk.topic_id,
        author=author,
        content=f"<@{seat}> {option}" if seat else option,
        provision_actor=actor,
    )
    return ok(updated)


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
        raise ValidationError(say("threadIdInvalid")) from None
    delivered = await send_note(
        db,
        chat,
        sender=sender,
        from_project_id=place.project_id,
        to_thread=to_thread,
        content=body.get("content") or "",
    )
    return ok({"delivered": delivered})
