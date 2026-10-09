"""Room visibility and the real roster used to prepare messages and turns."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.membership.roster import roster_rows
from app.domain.topic.models import Topic
from app.domain.user.services import timezones_by_handles


def _is_dm(topic: Topic) -> bool:
    """这间房是不是一间私聊。**这是 `is_private` 在这个文件里唯一的读点。**

    私聊是项目内名册两席的房间（结论 19），这一轮凡是「私聊要不一样」的地方，答
    案都从这里推出来，不再各自问一遍那个布尔：同一件事问 N 遍，N 遍的判据就会各
    自漂移，这次退役的正是漂开了的三十处。推出来的是两件事：

    - **这间房没有名册。**私聊不暴露成员列表，`@` 解析不到项目里的第三个人：解
      析表给 `[]`，`@某某` 原样留在正文里，显示成一条「项目中没有这个成员」
      。这一条管的是正文去了哪里，不只是渲染：名册还要往下走进
      `announce_mentions`，解析到的每个 handle 都会收到一条带正文前 200 字的强提醒。
    - **这一轮不租地点**（`needs_place`，结论 19、不变量 I2）：不碰仓库文件、不
      跑项目命令的一轮不去租手，所以它在所有执行机离线时也答得出来。它桌上只有
      对话、记忆和平台工具，加上会话自己那块 64 MiB 草稿区（不是一个地点，随会
      话生灭）。

    问的是这间房的性质，**不是名册上此刻坐了几个人**。「两席里的人是哪一位」由
    `_private_owner` 答，席位不齐时它答 None，而一间私聊的正文不会因为席位不齐
    就可以广播出去。两个问题分开问，是因为它们答错的后果不同：答不出「对面是
    谁」，退路是项目默认的芝士；答错「这间房有没有名册」，正文就出了房间。
    """
    return topic.is_private


async def room_roster(
    session: AsyncSession, project_id: uuid.UUID, topic: Topic | None
) -> list[dict]:
    """The names an agent's message is read against: the project roster, or
    none at all in a private room."""
    if topic is None or _is_dm(topic):
        return []
    rows = await roster_rows(session, project_id)
    # A person's zone goes with their row: the times an agent reads are UTC,
    # and what it writes for someone is read on that person's clock.
    zones = await timezones_by_handles(
        session, [row["handle"] for row in rows if not row["agent"]]
    )
    return [
        {**row, "timezone": zones[row["handle"]]} if row["handle"] in zones else row
        for row in rows
    ]
