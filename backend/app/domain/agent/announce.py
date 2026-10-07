"""平台在房间里说一句话 —— 落成系统事件，需要人动手时同时通知到人。

`platform_notices` 定的是**说什么**：一行 `content` 加一份结构化 `meta`。这里定
的是**怎么说出去**，两件事在一次调用里完成：

1. 这句话落进房间的时间线（`kind=event, author_type=platform`）；
2. 这条事件点到的那些人各收到同一句话 —— 前提是下一步确实在参与者手上。

芝士自己的提问走 `notify_question`：那条消息已经在时间线上，只缺投递这一半。

通知里的文字就是房间里那一行，没有第二套措辞：人在通知里读到的和回房间看到的是
同一句，同一件事不会有两种说法。再加一个渠道（浏览器推送之类）也只改这一个函数。

**调用点说的是这条事件点了谁的名，不是收件人名单，也不是要不要发（I11）。**「下一
步在谁手上」由这一层从 `meta.who` 读出来 —— 那个码本来就是这句话：`platform`（平
台自己在重试/自愈）、`cheese`（芝士接着处理）、`human`（等人）。三个码翻成
`delivery/addressing.py` 的两档 `Hand`，`address()` 再答谁会收到、凭什么收到。

所以一条平台正在处理的提示**发不出收件人**。它**写得出来**：`points_at` 和 `meta` 是
两个独立参数，两个都填上不报错：`who` 说了下一步不在参与者手上，点的那些名就只是
被忽略。这不是吞掉一个错误：一条事件点了谁的名和下一步在谁手上本来就是两个事实，平
台自己在重试的时候那张卡照样有它的验收人。以前这一条是 `meta["who"] != WHO_HUMAN`
就抛 `ValueError`，一道当场炸的闸门。拦得住，但拦的方式是要求每个调用点记住别这么
写；现在两个参数各说各的一件事：`who` 决定发不发，`points_at` 只决定发给谁。

那道旧闸门还兼着第二件事 —— 「agent 不能当收件人」。这一半不在这里：谁该收到对人和
agent 是同一句话，分岔只在**怎么送到**，那是 `identity/arrival.py`（人有浏览器走站
内信，agent 有一条会话，在自己房间的时间线上读到上面刚落下的那一行）。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import notice_message
from app.domain.agent.mentions import _MENTION_RE
from app.domain.agent.platform_notices import (
    SEVERITY_INFO,
    WHO_CHEESE,
    WHO_HUMAN,
    WHO_PLATFORM,
)
from app.domain.block.about import EventAbout, landing
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.conversation.services import room_of
from app.domain.delivery.addressing import (
    NAMES_NOBODY,
    Event,
    Hand,
    address,
)
from app.domain.delivery.ledger import DeliveryEvent, deliver, settle
from app.domain.identity.handles import agent_instance_handle
from app.domain.notification.models import NotificationType
from app.domain.room_task.place import Place, PlaceResolver
from app.domain.topic_membership.services import TopicMemberService

#: `who` 码 → 下一步在谁手上。`who` 回答的是「谁在管这件事」，那正是投递要问的那一
#: 句，只是用的是通知契约的词，所以这里不做第二次判断，只把同一个答案翻成投递这一
#: 侧的词 —— 和 `addressing.hand_of(column)` 对看板那一列做的是同一件事。
#:
#: `None` 是「这条提示没说谁在管」（`meta` 不是 `notice()` 拼的）：没人声明下一步到
#: 了参与者手上，就谁都不通知 —— 和看板上一格没挪是同一个意思。
#:
#: **封闭表**：`platform_notices` 多一个码而这里没跟上，查表当场抛 `KeyError`，而不
#: 是让新的码悄悄落进某一档。
_HAND_OF_WHO: dict[str | None, Hand] = {
    WHO_PLATFORM: Hand.platform,
    WHO_CHEESE: Hand.platform,
    WHO_HUMAN: Hand.participant,
    None: Hand.platform,
}


async def announce(
    session: AsyncSession,
    *,
    place_id: uuid.UUID,
    content: str,
    meta: dict | None = None,
    author: str = "system",
    turn_id: uuid.UUID | None = None,
    points_at: Event = NAMES_NOBODY,
    event_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> Block | None:
    """把 `content` 说进房间，并投给这条事件点到的那些人。

    `points_at` 只说这条事件点了谁的名（验收人 / 提需求的人 / 被问的那个人），默认
    谁也没点。要不要真的发出去由 `meta.who` 决定，不由调用点决定。

    写的是调用方的 session，不是自己开一个：卡、房间里那句话、通知三者一起提交，
    所以一次回滚不会留下「房间说递了卡，卡却不存在」。要跨事务活下来的调用点
    （合并结果已经在 GitHub 上发生了，房间必须知道）走
    `webhook.service.post_with_retries`，它每次重试开一个新 session 再调这里。

    `event_id` 是这条事件的身份，投递账本按它去重（结论 58「去重键跟事件」）。默认
    就是房间里刚落下的那一行 —— 一次性的提示说完即止，它的身份和它那一行同生。给
    得出一个更长命的身份的调用点才填它：同一件事被问第二遍仍然是同一条事件，那个
    id 不能每次新建（`domain/policy/proposals.py`）。

    返回落下的 block；房间已经不在了返回 None。
    """
    place = await PlaceResolver(session).resolve(place_id)
    if place is None:
        return None
    if task_id is not None:
        # A task's conversation, or a 支线's: either way one inside this room.
        from app.core.errors import ValidationError

        inner = await PlaceResolver(session).conversation(task_id)
        if inner is None or inner.inner_id is None or inner.room_id != place.room_id:
            raise ValidationError("Event task does not belong to this room")
    landed = landing(
        EventAbout.task if task_id is not None else EventAbout.room,
        project_id=place.project_id,
        room_id=place.room_id,
        task_id=task_id,
    )
    block = await BlockRepository(session).add(
        project_id=landed.project_id,
        conversation_id=landed.conversation_id,
        author=author,
        author_type=AuthorType.platform,
        content=content,
        kind=BlockKind.event,
        turn_id=turn_id,
        meta=meta,
    )
    _show_once_committed(session, block)
    await _notify(
        session,
        place=place,
        block=block,
        content=content,
        meta=meta or {},
        points_at=points_at,
        event_id=event_id,
    )
    return block


#: `session.info` 里的一格：这个会话落下、提交之后要当场推给页面的那几行，各带
#: 它所在那段对话的频道。推送本身在 broker 那一侧（`runtime`），这里只记下来。
SHOW_ONCE_COMMITTED = "notices_shown_once_committed"


def _show_once_committed(session: AsyncSession, block: Block) -> None:
    """让开着这段对话的页面当场看见这一行，而不是等下一次刷新。

    发在这一行所在的那段对话上 —— 一个任务的提示落在任务自己的对话里，任务页的
    socket 听的也正是那一段；房间页听房间。提交之后才发：一次回滚不能留下一行页面上
    看得见、库里却没有的字。载荷在这里就定下来，提交之后这一行的属性已经过期，
    读不回来。
    """
    frame = {
        "type": "event_block",
        "block": BlockOut.model_validate(block).model_dump(mode="json"),
    }
    session.info.setdefault(SHOW_ONCE_COMMITTED, []).append(
        (str(block.conversation_id), frame)
    )


async def notify_question(
    session: AsyncSession,
    *,
    place: Place,
    block: Block,
    question: str,
    asker: str,
    asked: str | None,
) -> None:
    """芝士问了一个问题、这一轮就此结束 —— 通知等这个回答的人。

    **不走 `announce`。** 提问本身就是时间线上那条消息（`kind=message`，作者是
    芝士），再 announce 一次等于同一件事在房间里说两遍。所以这里只做投递这一半。

    也不用 `ROOM_NOTICE`：那个码说的是「平台在房间里说的一行」，最长 40 字、措辞
    由平台决定。这一条是芝士自己的话，长度取决于它怎么问，两者不是一种东西 ——
    共用一个码，前端就无法区分该按哪一种渲染。

    这一处没有 `who` 码可读，下一步在谁手上是它自己的事实：这件事**停在这个问题
    上了**，在他回答之前没有任何一方能往下走。`asked` 是那个人，None 是「这个问题指
    不到具体的人」（平台发起的轮次），那就谁也不通知。
    """
    await deliver(
        session,
        DeliveryEvent(
            # 这条事件的身份就是那条提问消息 —— 去重键跟着它走，所以同一个问题被
            # 算第二遍也只打扰他一次。
            id=block.id,
            type=NotificationType.CHEESE_QUESTION,
            payload={
                "projectId": str(place.project_id),
                "topicId": str(place.room_id),
                "topicTitle": place.title,
                "question": question,
                "asker": asker,
                # 那条提问消息：通知据它定位到房间里的那一行。
                "blockId": str(block.id),
            },
            # 问出口的那一刻，不是走到这一行的那一刻 —— 收下整个 block 而不是它的
            # id，就是为了这个时刻拿得到。
            occurred_at=block.created_at,
        ),
        address(Event(asked=asked), Hand.participant),
    )


#: 通知里引一句回答，最多这么长 —— 那是一行说明，不是聊天记录。
ANSWER_EXCERPT_CHARS = 40


async def answer_questions(
    session: AsyncSession, reply: Block, recipient: dict
) -> list[Block]:
    """``reply`` 答掉了哪几道题：记在题上、结掉题的通知，并把它送到提问的那位手上。

    点选项就是回一句话（选项文字，回复那道题），打字也是回一句话，两条走的是同一扇
    门；哪几道题算被这句话答了由 `BlockRepository.questions_a_reply_answers` 定，和
    看板读的是同一条判据。每一句回答都追加进题上的 `answer_log`，几个人都答了就记
    几条。

    这句话没点任何 agent 的名、却答了题，它就是说给提问的那位听的：``recipient``
    （这条消息的 `agent_recipient`，原地改）指到那个席位并记为点了名，于是它开的
    就是那位的下一轮 —— 和人 @ 它一模一样。提问的那一轮早就结束了也没关系，这里
    不找任何一轮。提问的那位已经不在房间里，答案照样记下，只是没人可送。
    """
    named = set(_MENTION_RE.findall(reply.content or ""))
    questions = await BlockRepository(session).questions_a_reply_answers(
        reply, first_word_only=not named
    )
    if reply.reply_to is None:
        questions = _typed_answers(reply, questions)
    if not questions:
        return []
    said = " ".join(_MENTION_RE.sub("", reply.content or "").split())
    for question in questions:
        meta = dict(question.meta or {})
        picked = _offered(meta).get(said)
        meta["answer_log"] = [
            *(meta.get("answer_log") or []),
            {
                # One of its options (what a click sends), or words of his own.
                "kind": "option" if picked else "note",
                "option": picked,
                "note": None if picked else said,
                "by": reply.author,
                "at": reply.created_at.isoformat(),
                "reply_id": str(reply.id),
            },
        ]
        question.meta = meta
    excerpt = said[:ANSWER_EXCERPT_CHARS] + (
        "…" if len(said) > ANSWER_EXCERPT_CHARS else ""
    )
    for notice in await _notices_now_answered(session, questions):
        await settle(session, notice, {"answered": excerpt})
    seat = questions[-1].author
    if not recipient.get("mentioned") and seat in await TopicMemberService(
        session
    ).agent_handles(await room_of(session, reply.conversation_id)):
        instance = await instance_of_seat(session, reply.project_id, seat)
        if instance is not None:
            recipient["instance_id"] = str(instance.id)
            recipient["handle"] = instance.handle
        # A seat still under the room-derived handle names no instance, and that
        # seat IS the agent the room points at: the recipient already names it.
        recipient["mentioned"] = True
        reply.meta = {**(reply.meta or {}), "agent_recipient": dict(recipient)}
    return questions


def _typed_answers(reply: Block, questions: list[Block]) -> list[Block]:
    """Which open questions a message that replies to nothing answers.

    It is said to one asker: the one it @-mentions, or else whoever asked most
    recently. A question that waits on nobody in particular (asked in a turn the
    platform started) is answered only by a message that names its asker:
    people talking to each other in the room is not an answer to it.
    """
    named = set(_MENTION_RE.findall(reply.content or ""))
    if named:
        # Addressed to the one who asked, it answers whatever is open of his.
        return [q for q in questions if named == {q.author}]
    questions = [q for q in questions if (q.meta or {}).get("asked") == reply.author]
    if not questions:
        return []
    seat = questions[-1].author
    return [q for q in questions if q.author == seat]


def _offered(meta: dict) -> dict[str, str]:
    """A question's options by what a click sends (the text without the model's
    " (Recommended)" mark) and by their own text. A question a person asked
    before options carried explanations stores them as plain strings."""
    offered: dict[str, str] = {}
    for option in meta.get("options") or []:
        text = option.get("text") if isinstance(option, dict) else option
        if isinstance(text, str):
            offered[text] = text
            offered.setdefault(text.removesuffix(" (Recommended)"), text)
    return offered


async def _notices_now_answered(session: AsyncSession, questions: list[Block]):
    """The question notices every question of which now has an answer.

    One `cheese_ask` call sends one notice for all its questions (keyed by the
    first, `meta.notice_id`); it is settled only once none of them is open.
    """
    from sqlalchemy import select

    done = []
    for notice in {(q.meta or {}).get("notice_id") or str(q.id) for q in questions}:
        siblings = list(
            await session.scalars(
                select(Block).where(
                    Block.conversation_id == questions[0].conversation_id,
                    Block.meta["notice_id"].as_string() == notice,
                )
            )
        )
        pending = [
            q
            for q in siblings
            if q not in questions and not (q.meta or {}).get("answer_log")
        ]
        if not pending:
            done.append(uuid.UUID(notice))
    return done


async def instance_of_seat(session: AsyncSession, project_id, seat: str):
    """The agent instance behind ``seat`` in this project, or None."""
    from app.domain.agent_instance.services import AgentInstanceService

    for instance in await AgentInstanceService(session).list_for_project(project_id):
        if agent_instance_handle(instance.id) == seat:
            return instance
    return None


async def _notify(
    session: AsyncSession,
    *,
    place: Place,
    block: Block,
    content: str,
    meta: dict,
    points_at: Event,
    event_id: uuid.UUID | None = None,
) -> None:
    await deliver(
        session,
        DeliveryEvent(
            # 房间里刚落下的那一行就是这条事件 —— 投递账本按它去重、按它补发。
            # 调用点给了身份就用它：那件事比它这一行长命。
            id=event_id or block.id,
            # 一个类别码走完所有平台提示，具体是哪件事看 `eventType`。通知显示的
            # 就是房间里那一行：`message` 是它的键和参数，读者的屏幕按读者的语言
            # 渲染；`content` 是那句中文，给没有键的旧行和读不了目录的渠道（推送）。
            type=NotificationType.ROOM_NOTICE,
            payload={
                "projectId": str(place.project_id),
                "topicId": str(place.room_id),
                "topicTitle": place.title,
                "content": content,
                **notice_message(block.meta),
                "eventType": str(meta.get("event_type") or ""),
                "severity": str(meta.get("severity") or SEVERITY_INFO),
            },
            occurred_at=block.created_at,
        ),
        address(points_at, _HAND_OF_WHO[meta.get("who")]),
    )
