"""review 领域的**窄读**出口：一条活此刻骑的那张卡，折成路由画得出来的几个值。

侧栏每画一条活，都要知道它骑的那张验收卡停在哪 —— 而这只需要卡上很少的几位：
状态、它骑的 PR、卡停什么上的码、合并态镜像里的 state/who，以及是谁采纳过或布防
过自动合。其余十几列（变更说明、门禁输出、回流账本……）侧栏一个字都不读。

以前路由直接把这个形状的答案从 `AcceptCardRepository.latest_by_task` 拿回来 ——
拿到的是**整行 ORM**，于是每个调用方都揣着一整张卡、外加一个能顺势多查一行的
session。这里交出去的 `RailCard` 是一份冻结的值：没有 session、没有懒加载、没有
「顺手再读一列」。`presentation.facts_for_card` 对 `AcceptCard` 和 `RailCard`
产出同一份 `CardFacts`，所以窄读换汤不换药 —— 用户看到的话一模一样，测试钉住了
这件事（`tests/unit/test_presentation.py`）。
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.review.models import AcceptCard
from app.domain.review.notes import NoteCode
from app.domain.review.repositories import AcceptCardRepository


@dataclass(frozen=True, slots=True)
class RailCard:
    """侧栏一行读得着的全部卡事实 —— 一份纯值，不是数据库那一行。

    字段就是**消费者真正读的那几个**（`room_task/presentation.py` 算那一格，
    `routes/projects.py` 拼那一行的 `card`）。`merge_state` 原样留着那份镜像
    （state/who/reasons/head_sha……）但收成 `dict | None`：镜像在库里是一整包，
    `facts_for_card` 却只从里面读 state 和 who，所以这里不替它挑字段 —— 挑错了
    就是又一处「谁记得改」；只把「不是 dict 就不当镜像读」这条钉在类型上。
    `decided_by` 和 `auto_merge_armed_by` 分开留着 —— `decided` 是两者之一，但
    「谁采纳的」和「谁布的防」不是同一个事实，合并成一个布尔就再也分不出来。
    """

    id: uuid.UUID
    status: str
    pr_number: int | None
    pr_url: str | None
    note_code: NoteCode | None
    merge_state: dict | None
    decided_by: str | None
    auto_merge_armed_by: str | None


def _rail_card(card: AcceptCard) -> RailCard:
    return RailCard(
        id=card.id,
        # `status` 在线上是一个字符串（`AcceptStatus` 是 StrEnum，`str()` 就是它的
        # 值），而这一层交出去的值要和 ORM 那边读出来的同一份，所以在这里就折成
        # 字符串，别让 `str()` 散落在每个调用点上。
        status=str(card.status),
        pr_number=card.pr_number,
        pr_url=card.pr_url,
        note_code=card.note_code,
        merge_state=card.merge_state if isinstance(card.merge_state, dict) else None,
        decided_by=card.decided_by,
        auto_merge_armed_by=card.auto_merge_armed_by,
    )


async def latest_cards_by_task(
    db: AsyncSession, task_ids: list[uuid.UUID]
) -> dict[uuid.UUID, RailCard]:
    """每条活此刻骑的那张卡，一次查完 —— 见 `RailCard`，交出去的绝不是 ORM 行。

    一批而不是逐条：侧栏为项目里每一件活都要它，一条一查就是一次请求乘上这个项目
    有史以来派出去的每一件活。最新的赢，因为一条活被退回后可以再递一张，而此刻
    算数的是那最新的一张。
    """
    cards = await AcceptCardRepository(db).latest_by_task(task_ids)
    return {task_id: _rail_card(card) for task_id, card in cards.items()}
