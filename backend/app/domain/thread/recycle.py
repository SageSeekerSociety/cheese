"""A 支线 nobody came back to: 收回「已回收」, and the road back.

一条支线里报错、而没人回头修，它就一直把整间频道点红（`block.waits` 只认恢复、
不认时间），也从概览的支线列表里一直挂着。满 `FAILURE_GIVES_UP_AFTER`（三天）之后，
`recycle_due` 把它收起来：记下 `recycled_at`，此后

- 它不再算进任何一行的红灯（`conversation/services._threads_in_rooms` 不再把它当成
  「还开着的支线」，`routes/topics._unresolved_failures` 也就看不到它）；
- 它从概览的支线列表里挪走，进「已回收」那一栏（`thread/reads.in_room` 分两栏）；
- 里面的一切照旧：块一条都不删，翻得到、搜得到、点得进去读。

收起来不等于封存：在里面说一句话，或者开这条支线的人、频道管理员点「恢复」，它当场
回到默认那一栏（`restore_thread`，接口 `POST /topics/{id}/thread/restore`）。放回来
的只有「在哪一栏、算不算数」这两件事：块一条没动，那次失败也已经过了时限，所以那间
房的灯**不会**跟着回来 —— 那是「找得回来」，不是「重新点灯」。

判据里的「没人碰过」是 `block.waits` 那一条：那一段对话里，出错的队友之后没再开口，
也没有任何一个会话读过输入。扫的时候另外要求此刻没有会话在跑 —— 正跑着的支线不是
死掉的支线，只是慢。
"""

import logging
import uuid
from collections.abc import Awaitable, Callable, Collection
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.domain.block.waits import FAILURE_GIVES_UP_AFTER, MemberWaits
from app.domain.conversation.models import Conversation, ConversationKind
from app.domain.thread.models import Thread

logger = logging.getLogger("cheesex.thread")

#: 这些对话里此刻有没有会话在跑 —— `{(对话): 有没有}` 那一份。会话那一域的事
#: （`agent.session_state.running_conversations`），由应用层递进来：`thread` 在
#: `block` 之上，读不了它下面那一层的表（`.importlinter` 的 C5）。
Running = Callable[[AsyncSession, Collection[uuid.UUID]], Awaitable[set[uuid.UUID]]]


async def due_threads(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    running: Running | None = None,
) -> list[Thread]:
    """还开着、报过错、拖过了时限、此刻也没有会话在跑的支线。

    一条支线算「还在当支线用」：原地转成任务的那条归它自己那件活（`threads` 行留
    着，但对话已经是任务的），频道详情那条路也是这么筛的
    （`conversation/services.threads_still_threads`）。

    这里问 `unresolved_failures(within=None)`：它答的是「按它自己的判据，这条失败还
    没被恢复」；时限的比对在这一层，因为同一个函数在页面那条路上带的是默认的时限
    （灯该灭了），而这里要的恰恰是它另一边 —— 哪些已经拖过去了。
    """
    at = now or datetime.now(UTC)
    still_threads = set(
        await session.scalars(
            select(Conversation.id).where(Conversation.kind == ConversationKind.thread)
        )
    )
    if not still_threads:
        return []
    open_threads = list(
        await session.scalars(
            select(Thread).where(
                Thread.recycled_at.is_(None),
                Thread.id.in_(still_threads),
            )
        )
    )
    if not open_threads:
        return []
    ids = [thread.id for thread in open_threads]
    unresolved = await MemberWaits(session).unresolved_failures(
        {thread_id: [thread_id] for thread_id in ids}, now=at, within=None
    )
    stale = [
        thread_id
        for thread_id, broke_at in unresolved.items()
        if broke_at < at - FAILURE_GIVES_UP_AFTER
    ]
    if not stale:
        return []
    # 正跑着的不是死掉的，只是慢：这一趟不收它。谁在跑是会话那一域的事实，由调用方
    # 递进来（`thread/periodic.py` 那一句），`thread` 不去读别人的表；没人给就是谁
    # 都不在跑。
    busy = await running(session, stale) if running is not None else set()
    due = [thread for thread in open_threads if thread.id in set(stale) - busy]
    logger.info(
        "thread_recycle_due stale=%d due=%d busy=%d", len(stale), len(due), len(busy)
    )
    return due


async def recycle_due(
    sessions: SessionFactory, *, running: Running, now: datetime | None = None
) -> int:
    """把到点的支线收起来，一个事务里做完；返回收了几条。

    收的是 `recycled_at` 这一个时刻。别人的这一趟同时也在收同一条时，后写的那一下
    只是把时刻推后一点点，没有别的后果（`WHERE recycled_at IS NULL` 让它不会覆盖）。

    ``running`` 是必给的：它答「这些对话里谁此刻在跑」，而没给就等于说谁都不在跑 ——
    那样一条正跑着的支线会被收掉。
    """
    at = now or datetime.now(UTC)
    async with sessions() as session:
        due = await due_threads(session, now=at, running=running)
        if not due:
            return 0
        await session.execute(
            update(Thread)
            .where(
                Thread.id.in_([thread.id for thread in due]),
                Thread.recycled_at.is_(None),
            )
            .values(recycled_at=at)
        )
        await session.commit()
    return len(due)


def recycled(thread: Thread) -> bool:
    """这条支线收起来了没有（模型的 `recycled` 属性，收在哪一处都读得出来）。"""
    return thread.recycled_at is not None


async def restore_thread(session: AsyncSession, thread: Thread) -> bool:
    """把一条收起来的支线放回来；已经是开着的就什么也不做，返回是不是这次放回来的。

    放回来的只有它「在不在默认列表里、算不算进频道那一行」这两件事。那条失败本身没
    被清掉（块一条没删），而它已经过了时限，所以频道那一行的红灯**不会**跟着回来 ——
    「恢复」是给要看内容、要接着用的人开的门，不是重新点灯。那间房要再红，得是之后
    又有一次没人恢复的失败，那是新的一个钟（`waits` 会往后看）。
    """
    if thread.recycled_at is None:
        return False
    thread.recycled_at = None
    await session.flush()
    return True
