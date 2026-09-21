"""平台在房间里说一句话 —— 落成系统事件，需要人动手时同时通知到人。

`platform_notices` 定的是**说什么**：一行 `content` 加一份结构化 `meta`。这里定
的是**怎么说出去**，两件事在一次调用里完成：

1. 这句话落进房间的时间线（`kind=event, author_type=system`）；
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

from app.domain.agent.platform_notices import (
    SEVERITY_INFO,
    WHO_CHEESE,
    WHO_HUMAN,
    WHO_PLATFORM,
)
from app.domain.block.about import EventAbout, landing
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.delivery.addressing import (
    NAMES_NOBODY,
    Event,
    Hand,
    address,
)
from app.domain.delivery.ledger import DeliveryEvent, deliver
from app.domain.notification.models import NotificationType
from app.domain.room_task.place import Place, PlaceResolver

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
) -> Block | None:
    """把 `content` 说进房间，并投给这条事件点到的那些人。

    `points_at` 只说这条事件点了谁的名（验收人 / 提需求的人 / 被问的那个人），默认
    谁也没点。要不要真的发出去由 `meta.who` 决定，不由调用点决定。

    写的是调用方的 session，不是自己开一个：卡、房间里那句话、通知三者一起提交，
    所以一次回滚不会留下「房间说递了卡，卡却不存在」。要跨事务活下来的调用点
    （合并结果已经在 GitHub 上发生了，房间必须知道）走
    `webhook.service.post_with_retries`，它每次重试开一个新 session 再调这里。

    返回落下的 block；房间已经不在了返回 None。
    """
    place = await PlaceResolver(session).resolve(place_id)
    if place is None:
        return None
    landed = landing(
        EventAbout.room,
        project_id=place.project_id,
        room_id=place.room_id,
    )
    block = await BlockRepository(session).add(
        project_id=landed.project_id,
        topic_id=landed.topic_id,
        task_id=landed.task_id,
        author=author,
        author_type=AuthorType.system,
        content=content,
        kind=BlockKind.event,
        turn_id=turn_id,
        meta=meta,
    )
    await _notify(
        session,
        place=place,
        block=block,
        content=content,
        meta=meta or {},
        points_at=points_at,
    )
    return block


async def notify_question(
    session: AsyncSession,
    *,
    place: Place,
    block: Block,
    question: str,
    asker: str,
    asked: str | None,
) -> None:
    """芝士提出待确认问题，本轮停止等待 —— 通知等这个回答的人。

    **不走 `announce`。** 提问本身就是时间线上那条消息（`kind=message`，作者是
    芝士），再 announce 一次等于同一件事在房间里说两遍。所以这里只做投递这一半。

    也不用 `ROOM_NOTICE`：那个码说的是「平台在房间里说的一行」，最长 40 字、措辞
    由平台决定。这一条是芝士自己的话，长度取决于它怎么问，两者不是一种东西 ——
    共用一个码，前端就无法区分该按哪一种渲染。

    这一处没有 `who` 码可读，下一步在谁手上是它自己的事实：本轮**停在这个问题上
    了**，在他回答之前没有任何一方能往下走。`asked` 是那个人，None 是「这个问题指
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
                # 提问固定在对话末尾（本轮停在它这里），所以进入房间即可看到 ——
                # 这个 id 留给「定位到该条消息」用，当前不依赖它也能找到。
                "blockId": str(block.id),
            },
            # 问出口的那一刻，不是走到这一行的那一刻 —— 收下整个 block 而不是它的
            # id，就是为了这个时刻拿得到。
            occurred_at=block.created_at,
        ),
        address(Event(asked=asked), Hand.participant),
    )


async def _notify(
    session: AsyncSession,
    *,
    place: Place,
    block: Block,
    content: str,
    meta: dict,
    points_at: Event,
) -> None:
    await deliver(
        session,
        DeliveryEvent(
            # 房间里刚落下的那一行就是这条事件 —— 投递账本按它去重、按它补发。
            id=block.id,
            # 一个类别码走完所有平台提示，具体是哪件事看 `eventType` —— 通知要显示
            # 的那句话是后端给的 `content`，不是前端按类别码拼出来的模板。
            type=NotificationType.ROOM_NOTICE,
            payload={
                "projectId": str(place.project_id),
                "topicId": str(place.room_id),
                "topicTitle": place.title,
                "content": content,
                "eventType": str(meta.get("event_type") or ""),
                "severity": str(meta.get("severity") or SEVERITY_INFO),
            },
            occurred_at=block.created_at,
        ),
        address(points_at, _HAND_OF_WHO[meta.get("who")]),
    )
