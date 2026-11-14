"""`cheese_ask`: an agent asks, in the room, and ends its turn.

The question is written as ordinary messages with quick replies
(`block.questions`). Asking needs no live turn and holds none open, so it does
not matter which backend serves the call, or whether the turn that asked is
still running when the answer arrives: the answer is a person's message, and a
person's message addressed to an agent starts that agent's next turn like any
other (`announce.answer_questions` does the addressing).
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.errors import UnprocessableEntityError
from app.core.live_frames import show_once_committed
from app.core.sentences import say
from app.domain.agent.announce import (
    instance_of_seat,
    notices_now_answered,
    notify_question,
    who_started_the_open_turn,
)
from app.domain.agent.mentions import MENTION_ALL
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent_instance.adapters import named_teammate, project_team
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_instance.own import owned_by_seat, owner_of
from app.domain.block.asked import asked_of, settled, skipped_by_all
from app.domain.block.indexed_rows import QUESTION_ROWS
from app.domain.block.models import Block
from app.domain.block.questions import (
    expire_question,
    parse_questions,
    post_questions,
    skip_question,
    unskip_question,
    withdraw_question,
)
from app.domain.block.schemas import BlockOut
from app.domain.conversation.numbering import lock_conversations
from app.domain.delivery.agent import now, record_agent
from app.domain.delivery.ledger import settle, settle_for
from app.domain.delivery_events import DeliveryEvent
from app.domain.notification.models import NotificationType
from app.domain.room_task.place import Place
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

#: How long a question waits for an answer before the clock takes it back.
#: `None` (the "no deadline" a caller asks for) means it waits indefinitely.
#: A question nobody answers is not work anybody is doing — it is one line in a
#: list — so the default is short enough to clear a stale one within a person's
#: week and long enough to cover a weekend away (`block.questions.expire_question`).
DEFAULT_QUESTION_LIFETIME = timedelta(hours=72)

#: The longest lifetime a caller may ask for. A month is long past any window
#: where leaving a question open is better than taking it off the list; past
#: this it is a question nobody wants answered, and the screen is better off
#: without it.
MAX_QUESTION_LIFETIME_HOURS = 24 * 30


async def owner_chat(session, project_id, seat: str):
    """The private chat ``seat``'s owner holds with it, opened if it is not
    yet, and the owner's handle; None when the seat is nobody's own agent or
    its owner is gone. Opening one writes no line in it."""
    owned = await owned_by_seat(session, project_id, seat)
    owner = await owner_of(session, owned.instance_id) if owned else None
    if owned is None or owner is None:
        return None
    instance = await session.get(AgentInstance, owned.instance_id)
    assert instance is not None
    private = await TopicService(session).get_or_create_private(
        project_id=project_id,
        user_handle=owner[0],
        agent_handle=instance.handle,
        named_teammate=named_teammate,
    )
    return private, owner[0]


async def ask(session, *, place, seat, body) -> list:
    """Post ``body``'s questions under ``seat`` and notify whoever they ask.

    Whom a question asks is the agent's to choose, as a message's @-mentions
    are (``body["to"]``, `_addressees`). Left out, it asks whoever started the
    seat's open turn, read from the turn's row rather than from this process's
    memory; with no such turn, or one the platform started, it asks nobody in
    particular.

    Each question gets a deadline (`body["lifetime_hours"]`, or
    `DEFAULT_QUESTION_LIFETIME`): past it with nobody answering, the sweep
    (`expire_question`) stops it waiting. ``lifetime_hours: 0`` means no
    deadline — a question that must be answered whenever someone gets to it.
    """
    questions = parse_questions(body)
    if body.get("to_owner") is True:
        chat = await owner_chat(session, place.project_id, seat)
        if chat is None:
            raise UnprocessableEntityError(say("ownAgentHasNoOwner"))
        private, owner = chat
        place = Place(room=private)
        body = {**body, "to": [owner]}
    to = body.get("to")
    if to is None:
        starter = await who_started_the_open_turn(
            session, place.project_id, place.conversation_id, seat
        )
        asked, everyone = ((starter,) if starter else ()), False
    else:
        asked, everyone = await _addressees(session, place.room, to)
    request_id = body.get("request_id")
    rows = await post_questions(
        session,
        project_id=place.project_id,
        conversation_id=place.conversation_id,
        seat=seat,
        questions=questions,
        asked=asked,
        asked_all=everyone,
        request_id=str(request_id) if request_id else None,
        deadline=_deadline(body),
    )
    # One notice per call, keyed by its first question: a retry lands on it again.
    await notify_question(
        session,
        place=place,
        block=rows[0],
        question=rows[0].content,
        asker=seat,
        asked=asked_of(rows[0].meta),
    )
    for row in rows:
        show_once_committed(
            session,
            row.conversation_id,
            {
                "type": "assistant_block",
                "block": BlockOut.model_validate(row).model_dump(mode="json"),
            },
        )
    return rows


def _deadline(body) -> datetime | None:
    """When a call's questions stop waiting for an answer.

    ``lifetime_hours`` off the request: a positive number is that many hours
    from now, and ``0`` (or a negative) is "no deadline". Left out, the agent
    gets `DEFAULT_QUESTION_LIFETIME`. A caller may not ask for an unbounded
    number of hours — the field is a lifetime, not a wish."""
    hours = body.get("lifetime_hours")
    if hours is None:
        hours = DEFAULT_QUESTION_LIFETIME.total_seconds() / 3600
    if not isinstance(hours, (int, float)) or isinstance(hours, bool):
        raise UnprocessableEntityError(say("askLifetimeNumber"))
    if hours <= 0:
        return None
    if hours > MAX_QUESTION_LIFETIME_HOURS:
        raise UnprocessableEntityError(
            say("askLifetimeTooLong", limit=MAX_QUESTION_LIFETIME_HOURS)
        )
    return datetime.now(UTC) + timedelta(hours=hours)


async def _addressees(session, room, to) -> tuple[tuple[str, ...], bool]:
    """The people ``to`` names, and whether it named everyone.

    ``to`` is a list of handles, or holds the broadcast token ``all`` — the
    same literal as the composer's `<@all>` (`agent.mentions.MENTION_ALL`) and
    expanded the same way, to the people in the channel. A handle has to name a
    person who can see the channel: anyone in the project for an open channel,
    the people seated in a private one. Anything else is refused with the
    handles it could not place, so the agent can correct the call."""
    if not isinstance(to, list) or not to or not all(isinstance(h, str) for h in to):
        raise UnprocessableEntityError(say("askToList"))
    members = TopicMemberService(session)
    if MENTION_ALL in to:
        return tuple(await members.people_in(room, team=project_team)), True
    can_see = set(
        await members.people_in(room, team=project_team)
        if room.members_only
        else await members.project_people(room.project_id, team=project_team)
    )
    handles = tuple(dict.fromkeys(h.strip().removeprefix("@") for h in to))
    unknown = [h for h in handles if h not in can_see]
    if unknown:
        raise UnprocessableEntityError(
            say("askToNotInChannel", handles=", ".join(unknown))
        )
    return handles, False


#: 跳过一道题、撤销一次跳过叫醒提问那位时，投递 `payload.eventType` 上的名字。它不叫
#: `agent_mention`，所以不数进那条「两位队友互相 @」的熔断：这两下是人点的，和 agent
#: 之间互相叫醒不是一回事。
QUESTION_SKIPPED = "question_skipped"


def skip_prompt(*, handle, question, block_id) -> str:
    """有人跳过一道题之后，提问的那位这一轮读到的话。

    跳过不在房间里留任何一句话，提问的那位只能从这里知道：不告诉它，它就一直等一个
    不会来的答案。"""
    return (
        f"{handle} 跳过了这道题，没有回答（消息 id `{block_id}`）：\n\n"
        f"---\n{question}\n---\n\n"
        "别再等这道题的答案。按你最合理的判断定下来，并说明那是你的判断；"
        "实在定不了，就换个问法或者问别人。"
    )


def unskip_prompt(*, handle, question, block_id) -> str:
    """跳过又撤销之后，提问的那位这一轮读到的话。"""
    return (
        f"{handle} 撤销了对一道题的跳过（消息 id `{block_id}`）：\n\n"
        f"---\n{question}\n---\n\n"
        "这道题重新在等回答，选项已经重新出现。不用重问，等答复就好；"
        "如果你已经按自己的判断往下做了，回一句说明你定了什么。"
    )


async def withdraw_question_by_asker(session, *, block, room_id, handle) -> Block:
    """``handle``（提问的那位）撤回自己问出去的这道题。

    题上的记录由 `block.questions.withdraw_question` 改 —— 只有题块作者、且没人答过
    时撤得动。这里补上它做不到的两半：结掉那道题的通知，并把这次撤回推给房间（题卡
    要收起选项、写一行说明）。

    **不叫醒谁**：撤回是提问那位自己的动作，题已经不等人了，再叫它跑一轮只是白花钱。
    和跳过不同 —— 跳过是别人的动作，提问的那位必须知道，否则它一直等一个不会来的答
    案。

    不推房间：调用方（路由）在提交之后推，和跳过、回答同一条路。写调用方的 session，
    不提交。
    """
    question = await withdraw_question(session, question=block, by=handle)
    for notice in await notices_now_answered(session, [question]):
        await settle(session, notice, {"closed": "withdrawn"})
    return question


async def expire_due_questions(session, *, now: datetime | None = None) -> int:
    """到点还没人答的题：收掉，结掉通知，让房间看到它不再等人。

    每次扫描扫全部还没结算、还有 ``meta.deadline`` 的题；已经收过的（`settled`）
    不再进来。返回这一次收了几道。不叫醒谁 —— 到期这一下按约定不花模型调用。

    调用方自己 commit。
    """
    at = now or datetime.now(UTC)
    # 两次收窄，让这一趟只读那个小小的部分索引（`ix_blocks_questions` 的谓词就是
    # `QUESTION_ROWS`）：只要题，而且只要还可能没到期的。第二条是完备的——期限最长
    # `MAX_QUESTION_LIFETIME_HOURS`，所以更早问的题期限早到了（或者早就结算了），
    # 不会漏；不写它，扫描范围会随时间无界增长。
    rows = await session.scalars(
        select(Block).where(
            QUESTION_ROWS,
            Block.created_at >= at - timedelta(hours=MAX_QUESTION_LIFETIME_HOURS),
        )
    )
    due = [block for block in rows if _due(block.meta or {}, at)]
    if not due:
        return 0
    # Their conversations' numbering in one batch, then the questions read
    # again: an answer recorded after the scan took that numbering before it
    # wrote the question, so it is read here (the question no longer due)
    # rather than written over.
    await lock_conversations(session, {block.conversation_id for block in due})
    rows = await session.scalars(
        select(Block)
        .where(Block.id.in_([block.id for block in due]))
        .order_by(Block.created_at, Block.id)
        .execution_options(populate_existing=True)
    )
    taken = 0
    for block in rows:
        if not _due(block.meta or {}, at):
            continue
        question = await expire_question(session, question=block, at=at)
        for notice in await notices_now_answered(session, [question]):
            await settle(session, notice, {"closed": "expired"})
        await _publish_question(question)
        taken += 1
    return taken


def _due(meta: dict, at: datetime) -> bool:
    """Not settled, and its ``meta.deadline`` is at or before ``at``."""
    deadline = meta.get("deadline")
    if settled(meta) or deadline is None:
        return False
    try:
        due = datetime.fromisoformat(str(deadline))
    except ValueError:
        return False
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    return due <= at


async def _publish_question(question: Block) -> None:
    """把改完的这道题推给房间（`block_updated`，和跳过、回答同一条帧）。

    只有这个扫描用它 —— 它自己 commit，所以推的时候该看见的都已经落库了。撤回那条
    路由不推这里：它按 `_respond` 的形状在提交后推，好让两条路对外长得一样。
    """
    await get_broker().publish(
        str(question.conversation_id),
        {
            "type": "block_updated",
            "block": BlockOut.model_validate(question).model_dump(mode="json"),
        },
    )


async def unskip(session, *, block, project_id, room_id, handle) -> Block:
    """``handle`` 撤销自己在一道题上的跳过：题重新等他的回答。

    题上的记录由 `block.questions.unskip_question` 改 —— 只有跳过的人撤得动，答过的人撤
    不掉（答了就算数）。这里补的是它做不到的那半：叫醒提问的那位。只有那次跳过让题
    了结了（它点名的人都跳过了），提问的那位才被告知过，也才需要再告知一次：题又在
    等回答了。别的跳过它从来不知道，撤销了也不打扰它。
    """
    told_skipped = skipped_by_all(block.meta or {})
    question = await unskip_question(session, question=block, by=handle)
    if not told_skipped:
        return question
    await _wake_asker(
        session,
        question=question,
        project_id=project_id,
        room_id=room_id,
        handle=handle,
        event_type=QUESTION_SKIPPED,
        content=unskip_prompt(
            handle=handle, question=question.content, block_id=question.id
        ),
    )
    return question


async def skip(session, *, block, project_id, room_id, handle) -> Block:
    """``handle`` 跳过一道题：题上记下这次跳过，结掉他自己那条通知；它点名的人都
    跳过了，就结掉整道题的通知、告诉提问的那位。

    房间里不出现任何消息 —— 跳过是对这道题的回应，不是一句话。题问了几个人时，跳
    过只把题从他手上拿走，别人照旧等着回答，提问的那位也照旧等着；问所有人的题从不
    因为有人跳过而了结（`block.asked.skipped_by_all`）。提问的那位是从投递账本上那
    一条（`skip_prompt`）知道的，和撤回走同一条路，所以哪一种骨架都一样收到。写调
    用方的 session，不提交。
    """
    closed = skip_question(block, by=handle)
    await session.flush()
    meta = block.meta or {}
    for notice in await notices_now_answered(session, [block], of=handle):
        await settle_for(session, notice, handle, {"skipped": handle})
    if not closed:
        return block
    for notice in await notices_now_answered(session, [block]):
        await settle(session, notice, {"skipped": handle})
    await _wake_asker(
        session,
        question=block,
        project_id=project_id,
        room_id=room_id,
        handle=handle,
        event_type=QUESTION_SKIPPED,
        content=skip_prompt(
            handle="、".join(asked_of(meta)), question=block.content, block_id=block.id
        ),
    )
    return block


async def _wake_asker(
    session, *, question, project_id, room_id, handle, event_type, content
) -> None:
    """叫醒提问的那位，让它读 ``content`` 跑一轮。

    叫醒走的是投递账本（`delivery/mention.py` 的同一本），所以和人点名、定时任务叫
    醒是同一条路。提问的席位已经不在这个项目里，就只改记录，不记账。
    """
    instance = await instance_of_seat(session, project_id, question.author)
    if instance is None:
        return
    await record_agent(
        session,
        DeliveryEvent(
            # 每次各是一条事件：同一道题答了又撤、撤了再答，两次撤回是两次。点名
            # 那条路拿 block id 当事件 id，这里不能照抄 —— 账本按「事件 × 席位」去
            # 重，第二次会被当成重复，送不出去。
            id=uuid.uuid4(),
            type=NotificationType.MENTION,
            payload={
                "projectId": str(project_id),
                "topicId": str(room_id),
                "eventType": event_type,
                "author": handle,
                "blockId": str(question.id),
            },
            occurred_at=now(),
        ),
        conversation_id=question.conversation_id,
        instance_id=instance.id,
        content=content,
    )


async def publish_answered(conversation_id, questions) -> None:
    """Show the room what each question now records, once that is committed."""

    for question in questions:
        await get_broker().publish(
            str(conversation_id),
            {
                "type": "block_updated",
                "block": BlockOut.model_validate(question).model_dump(mode="json"),
            },
        )
