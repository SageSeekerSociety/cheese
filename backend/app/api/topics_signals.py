"""一页房间读出来的信号：谁在管它、它名下的活走到哪了、哪张卡停在检查上。

路由这一层从一批验收卡和 broker 里读出这几样，再交给 `topics._topic_out` 叠进响应。
它们不是路由——没有路径、没有请求体，只回答「这一页现在是什么样」——所以和
`topics.py` 分开放：那个文件已经顶在大小上限上，而这几件正是它身上可以整块搬走的。

摆在 `app/api/` 这一层、不摆在 `app/api/routes/` 里，是 C4 那条契约要求的：路由共用
的帮手住在 `app.api` 下的依赖模块里（`room_snapshot`、`task_origin` 都是这么放的），
一个路由模块不能是另一个路由模块的依赖 —— 哪怕这一份眼下只被 `topics.py` 用着，
它也不是路由（没有路径、没有请求体）。

判据都不在这里：哪张卡算「交付中」是 `presentation.card_column`，谁算「卡在检查上」
是 `presentation.agent_fix_kind`，哪种状态算「还没结算」是 `review.archive`。这一份
只负责把一页的行凑齐、按地方去重。
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.topics_rows import CardRow
from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.block.waits import REPLY_LOOKBACK, StuckCard
from app.domain.conversation.services import threads_of_rooms
from app.domain.review import archive
from app.domain.review.services import AcceptService
from app.domain.room_task import presentation


async def working_in_threads(
    db: AsyncSession, broker: InProcessBroker, room_ids: list[uuid.UUID]
) -> dict[uuid.UUID, set[str]]:
    """{房间: 正在它的某条支线里干活的队友} —— 一次查完一个项目。

    一条支线是这间房自己的：芝士在这条频道没有自己那条线，人 @ 它、它回话，都发生
    在那条消息的支线里（`_answering_in`）。所以「谁在管这间房」不只看主线，这份是
    主线之外那一半，用来把 `waits` 里已经在处理的成员去掉（`_topic_out`）。哪条支线
    属于哪个房间以数据库为准，不是 broker 记的那份：broker 只在一条支线里装过轮次时
    才认得它（`RoomActivity.note_thread`）。
    """
    if not room_ids:
        return {}
    threads = await threads_of_rooms(db, room_ids)
    return broker.activity.working_in(
        {str(thread_id): room_id for thread_id, room_id in threads.items()}
    )


async def live_cards(db: AsyncSession, room_ids: list[uuid.UUID]) -> Sequence[CardRow]:
    """这些房间（连同名下的活）上还没结算的验收卡，一次查完。

    房间自己那张（`own_cards`）和「卡停在检查红上」（`stuck_on_checks`）都从
    这一批里读，不各查一遍。只要没结算的：一张已经决议的卡对看板没有话说
    （`presentation` 读到它会让位给别的判据），所以拉全量只是白读。哪些状态算
    「还没结算」不在这里数——那张表是 `review/archive.py` 维护的，抄第二份就是
    让它们走散。

    """
    if not room_ids:
        return []
    # 结算过的也带上最近一周的：「退回了还没重递」要靠它看出来（`stuck_on_checks`）。
    return await AcceptService(db).list_recent_for_places(
        room_ids,
        open_statuses=archive.OPEN_CARD_STATUSES,
        settled_since=datetime.now(UTC) - REPLY_LOOKBACK,
    )


def own_cards(cards: Sequence[CardRow]) -> dict[uuid.UUID, CardRow]:
    """每个房间**自己**那张还没结算的验收卡。

    `task_id is None` 才是房间自己的卡：一条活递的卡把房间记在 `topic_id` 上，不
    过滤的话，一条活在等验收会让它上面那个房间也显示成等验收。
    """
    # 按 created_at 升序回来，所以同一个房间后写的覆盖先写的 = 留下最新那张。
    return {
        c.topic_id: c
        for c in cards
        if c.task_id is None and c.status in archive.OPEN_CARD_STATUSES
    }


def latest_per_place(cards: Sequence[CardRow]) -> dict[tuple, CardRow]:
    """同一个地方（房间自己，或某一条活）只留最新那张：旧卡被新卡顶掉后可能还没
    结算，它说的不代表现在。"""
    return {(c.topic_id, c.task_id): c for c in cards}


def delivering_rooms(cards: Sequence[CardRow]) -> set[uuid.UUID]:
    """这些房间里，哪几个**名下的活**已经递出去、走在交付链路上。

    房间自己那张卡不算：`room_presentation` 已经把它说进 `presentation` 那一列了
    （`own_cards`）。这一份补的是另一半 —— 一间房里一条活在等 CI / 换基 / 修检查时，
    这间房自己那条线多半是闲着的，而它名下的活正在往前走（侧栏那盏呼吸绿：「这个频道
    下有活正在往前走」）。缺了这一半，那种房间在侧栏上会一直不亮。

    只认「卡说得出交付中」：判据在 `presentation.card_column`（和看板同一处），
    这里不复述哪几种码算哪一列。
    """
    return {
        card.topic_id
        for card in latest_per_place(cards).values()
        if card.task_id is not None
        and presentation.card_column(card) is presentation.Column.delivering
    }


def stuck_on_checks(cards: Sequence[CardRow]) -> dict[uuid.UUID, StuckCard]:
    """房间（连同名下的活）里，最新那张卡停在「检查红 / 冲突，要 AI 修」上的。

    同一个地方（房间自己，或某一条活）只看最新那张：旧卡被新卡顶掉后可能还没结算，
    它的红不代表现在。判据和看板同一个（`presentation.card_needs_agent_fix`）。
    """
    stuck: dict[uuid.UUID, StuckCard] = {}
    for (room, _), card in latest_per_place(cards).items():
        kind = presentation.agent_fix_kind(card)
        if kind is not None and room not in stuck:
            stuck[room] = StuckCard(kind=kind, pr=card.pr_number)
    return stuck
