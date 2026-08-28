"""房间要知道：这一轮的改动没能变成提交。

平台每轮结束替话题的工作区做一次快照，那一步是**文件**和**提交**之间唯一的桥。
桥断了，磁盘上的东西一个字节都没少，但后面每一个读的人读的都是提交：房间里的
改动摘要是数新提交算出来的，验收卡的 diff 比的是分支，PR 推的是分支，采纳合的
是分支——于是这一轮在所有这些地方一起消失，而且长得和「这一轮本来就没干活」
一模一样。

所以这条提示存在的理由不是「记一笔错误」，是**把一个看不出来的失败变成看得出来
的失败**。它比替人重做便宜两个数量级：真正丢过活的那次，代价不是没能恢复，是
两个小时里没有一个人起疑。

形状是固定的：一行人话进房间，原话进展开区——一个没人会主动去查、
又必须说出口的后台事实，就该这么说。
"""

import logging
import uuid

from app.core.db import async_session_factory
from app.domain.agent.platform_notices import (
    EVENT_SNAPSHOT_FAILED,
    SEVERITY_ERROR,
    WHO_HUMAN,
    notice,
)
from app.domain.agent.runtime import get_broker
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.topic.repositories import TopicRepository

logger = logging.getLogger(__name__)

#: 房间里那一行。≤40 字，说的是「发生了什么」，不是「谁的错」。
SNAPSHOT_FAILED_LINE = "这一轮的改动没能提交"

_CONSEQUENCE = (
    "文件都还在工作区里，一个字节都没丢；但这一轮没有变成提交，"
    "所以话题分支、改动摘要、验收卡的 diff 和 PR 里都看不到它。"
    "下一轮结束时平台会再试一次；连着几轮都这样，就要人去看这个话题的工作区。"
)


def snapshot_failed_notice(detail: str) -> tuple[str, dict]:
    """房间那一行 + 展开区。

    展开区里放的是**工具自己的原话**，不是平台的转述：快照失败最常见的原因是
    工作区过期，而那句报错自带恢复命令。改写它只会把唯一能照着做的一行弄丢。
    """
    body = " ".join(detail.split())[:2000]
    return (
        SNAPSHOT_FAILED_LINE,
        notice(
            EVENT_SNAPSHOT_FAILED,
            severity=SEVERITY_ERROR,
            who=WHO_HUMAN,
            detail=f"{_CONSEQUENCE}\n\n{body}" if body else _CONSEQUENCE,
            detail_label="快照报错",
        ),
    )


async def warn_snapshot_failed(topic_id: uuid.UUID, detail: str) -> None:
    """把这条提示落进话题时间线并广播出去。

    Never raises: this is the thing that says a turn went wrong, and it must not
    be able to take a turn down on its way to saying so.
    """
    content, meta = snapshot_failed_notice(detail)
    try:
        async with async_session_factory() as session:
            topic = await TopicRepository(session).get(topic_id)
            if topic is None:
                return
            block = await BlockRepository(session).add(
                project_id=topic.project_id,
                topic_id=topic.id,
                author="system",
                author_type=AuthorType.system,
                content=content,
                kind=BlockKind.event,
                meta=meta,
            )
            payload = BlockOut.model_validate(block).model_dump(mode="json")
            await session.commit()
        await get_broker().publish(
            str(topic.id), {"type": "event_block", "block": payload}
        )
    except Exception:  # noqa: BLE001 — a notice must never fail the turn
        logger.warning(
            "could not tell topic %s that its snapshot failed", topic_id, exc_info=True
        )
