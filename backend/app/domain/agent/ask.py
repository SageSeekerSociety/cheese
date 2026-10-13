"""`cheese_ask`: an agent asks, in the room, and ends its turn.

The question is written as ordinary messages with quick replies
(`block.questions`). Asking needs no live turn and holds none open, so it does
not matter which backend serves the call, or whether the turn that asked is
still running when the answer arrives: the answer is a person's message, and a
person's message addressed to an agent starts that agent's next turn like any
other (`announce.answer_questions` does the addressing).
"""

import uuid

from app.domain.agent.announce import (
    instance_of_seat,
    notices_now_answered,
    notify_question,
)
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.block.models import Block
from app.domain.block.questions import (
    answered_by,
    parse_questions,
    post_questions,
    skip_question,
    skipped,
    withdraw_answer,
)
from app.domain.block.schemas import BlockOut
from app.domain.delivery.agent import now, record_agent
from app.domain.delivery.ledger import DeliveryEvent, settle
from app.domain.identity.handles import names_a_person
from app.domain.notification.models import NotificationType


async def ask(session, *, place, seat, body) -> list:
    """Post ``body``'s questions under ``seat`` and notify whoever they wait on.

    The person a question waits on is whoever started the seat's open turn, read
    from the turn's row rather than from this process's memory. With no such
    turn, or one the platform started, it waits on nobody in particular.
    """
    questions = parse_questions(body)
    asked = await _who_started_the_open_turn(session, place, seat)
    request_id = body.get("request_id")
    rows = await post_questions(
        session,
        project_id=place.project_id,
        conversation_id=place.conversation_id,
        seat=seat,
        questions=questions,
        asked=asked,
        request_id=str(request_id) if request_id else None,
    )
    # One notice per call, keyed by its first question: a retry lands on it again.
    await notify_question(
        session,
        place=place,
        block=rows[0],
        question=rows[0].content,
        asker=seat,
        asked=(rows[0].meta or {}).get("asked"),
    )
    return rows


async def _who_started_the_open_turn(session, place, seat) -> str | None:
    """The person whose message opened ``seat``'s running turn, if a person did.

    A turn row names the agent by its instance's handle (the conversation it
    runs in), a seat by the instance's id; either may be on the row.
    """
    instance = await instance_of_seat(session, place.project_id, seat)
    turns = AgentTurnRepository(session)
    for handle in (seat, *((instance.handle,) if instance is not None else ())):
        author = await turns.open_turn_author_for_topic(
            place.conversation_id, agent_handle=handle
        )
        if author is not None:
            return author if names_a_person(author) else None
    return None


#: 撤回一次答复叫醒提问那位时，投递 `payload.eventType` 上的名字。它不叫
#: `agent_mention`，所以不数进那条「两位队友互相 @」的熔断：撤回是人点的，和 agent
#: 之间互相叫醒不是一回事。
ANSWER_WITHDRAWN = "answer_withdrawn"
#: 跳过一道题叫醒提问那位时的名字，同上理由不叫 `agent_mention`。
QUESTION_SKIPPED = "question_skipped"


def withdrawal_prompt(*, handle, question, block_id) -> str:
    """撤回之后，提问的那位这一轮读到的话。"""
    return (
        f"{handle} 撤回了对一道题的答复（消息 id `{block_id}`）：\n\n"
        f"---\n{question}\n---\n\n"
        "选项已经重新出现在他那边，他可以从头再选一次。这道题不用你重问一遍，等他"
        "的新答复就好；有别的要说就回一句。"
    )


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
        "这道题重新等他回答，选项已经重新出现在他那边。不用重问，等他的答复就好；"
        "如果你已经按自己的判断往下做了，回一句说明你定了什么。"
    )


async def withdraw(session, *, block, project_id, room_id, handle) -> Block:
    """``handle`` 撤回自己在一道题上的答复或跳过，并让提问的那位重跑一轮。

    题上的记录由 `block.questions.withdraw_answer` 改 —— 只有答过的人撤得动。这里
    补的是它做不到的那半：叫醒提问的那位。他原来那一轮是看着一个已经不算数的答案往
    下走的，不告诉他，他就带着旧答案继续做。撤销的是一次跳过时，他读到的是题又在等
    回答了。
    """
    was_skip = answered_by(block, handle) and all(
        skipped(entry)
        for entry in ((block.meta or {}).get("answer_log") or [])
        if (entry or {}).get("by") == handle
    )
    question = await withdraw_answer(session, question=block, by=handle)
    prompt = unskip_prompt if was_skip else withdrawal_prompt
    await _wake_asker(
        session,
        question=question,
        project_id=project_id,
        room_id=room_id,
        handle=handle,
        event_type=ANSWER_WITHDRAWN,
        content=prompt(handle=handle, question=question.content, block_id=question.id),
    )
    return question


async def skip(session, *, block, project_id, room_id, handle) -> Block:
    """``handle`` 跳过一道题：题上记下这次跳过，结掉题的通知，告诉提问的那位。

    房间里不出现任何消息 —— 跳过是对这道题的回应，不是一句话。提问的那位是从投递
    账本上那一条（`skip_prompt`）知道的，和撤回走同一条路，所以哪一种骨架都一样收
    到。写调用方的 session，不提交。
    """
    question = skip_question(block, by=handle)
    await session.flush()
    for notice in await notices_now_answered(session, [question]):
        await settle(session, notice, {"skipped": handle})
    await _wake_asker(
        session,
        question=question,
        project_id=project_id,
        room_id=room_id,
        handle=handle,
        event_type=QUESTION_SKIPPED,
        content=skip_prompt(
            handle=handle, question=question.content, block_id=question.id
        ),
    )
    return question


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
