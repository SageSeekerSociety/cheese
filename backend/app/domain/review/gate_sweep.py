"""闸门孤儿卡扫底 —— 把没人再会去结算的 `pending_gate` 卡判死 (2026-08-11).

## 为什么需要它

`review/gate.py` 的 `dispatch` 是纯内存的 `asyncio.create_task`，除了一个模块级
的引用表之外**没有任何持久化**。所以只要那个 task 不再运行，就再没有人会去调
`finish_gate`，卡永远停在 `pending_gate` 上。已经证实的三条路径：

1. **后端重部署 / 重启**（最常见）：task 随进程死。线上实测一张卡因此卡了 2 小时
   44 分，而同期健康的卡从建卡到落定全部在 17–23 秒。
2. **`_settle` 放弃**：卡的 INSERT 一直不可见时它重试约 10 秒后 `logger.error(
   "gate result dropped")` 就走了 —— 进程还活着，卡照样成孤儿。
3. **task 被取消 / `BaseException`**：退役前的 `gate._run` 只 `except Exception`。

而 `pending_gate` 这个状态**没有任何出口**：accept / reject / revoke / reassign
四条路由对它全是拒绝，`create_card` 又因为它拒绝再建新卡 —— 所以坏掉的不是一张
卡，是**整个话题**再也递不出验收卡。扫底就是那个出口的自动化那一半（另一半是
人工作废，见 `AcceptService.void`）。

## 「闸门没跑完」≠「检查未通过」

两者都落在 `gate_failed` 上（状态列区分不了），但对芝士意味着完全相反的下一步：
没跑完 → **原样重递**；没通过 → **去修代码**。所以判死时 note 和 gate_output 都
带上具名前缀 `services.GATE_ABANDONED_PREFIX`，给芝士的消息也明说"不是你的代码
有问题"。读卡的代码请认这个前缀，不要靠猜 `gate_output` 是不是空的。

## 为什么启动扫底之外还要周期兜底

启动扫底只盖得住上面的路径 1。路径 2 和 3 发生在**进程还活着**的时候：不重启就
永远不会被扫到，而后端连跑几周是常态。周期兜底同时也是唯一能在"卡住 → 被发现"
之间设上界的东西 —— 上限从"下次重部署"变成一个可配置的间隔。

误杀的防线有两条，都必须在：

* **计时留余量**：`GATE_TIMEOUT_S`（检查自己的硬上限）+ `GATE_STALE_GRACE_S`。
  工作区准备、排队都在这段余量里。
* **在跑的不碰**：`gate.in_flight_card_ids()` 精确回答"这张卡的闸门还在本进程里
  跑吗"。重启后这张表是空的，正好让启动扫底可以放心地把看到的全部判死。
"""

import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent.platform_notices import (
    EVENT_GATE_ABANDONED,
    SEVERITY_WARN,
    WHO_CHEESE,
    notice,
)
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.review import archive, notes
from app.domain.review.gate import GATE_TIMEOUT_S
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.repositories import AcceptCardRepository
from app.domain.review.services import GATE_ABANDONED_PREFIX
from app.domain.topic.repositories import TopicRepository

#: 检查自己的硬上限之外再给的余量：工作区准备（jj checkout 可能很慢）和排队都
#: 落在这里。10 分钟 —— 实测健康的闸门是 17–23 秒，所以这已经宽出两个数量级，
#: 而坏掉的卡从 2 小时 44 分（观测值）缩到 20 分钟被发现。
GATE_STALE_GRACE_S = 600

_ABANDONED_OUTPUT = (
    f"{GATE_ABANDONED_PREFIX}：平台没能拿到这次检查的结果（后端重启或闸门任务丢失），"
    "卡片被判死。这**不是**检查失败——检查根本没跑完，代码本身没有被判定有问题。"
)

_ABANDONED_NUDGE = (
    f"{GATE_ABANDONED_PREFIX}：你之前递的验收卡卡在平台质量检查上，"
    "平台没能拿到检查结果（后端重启或闸门任务丢失），已经把它判死，"
    "它不会送到验收人手上。\n\n"
    "**注意这不是「检查没通过」**——检查根本没跑完，没有任何证据说明你的代码有问题，"
    "所以不用去修什么。确认工作区还是你交付时的状态，然后**重新递一次验收卡**即可。"
)

#: 平台提示统一契约：房间里只留这一行，上面那段给芝士的说明收进 `meta.detail`。
#: 「判死」和「没通过」在这里也必须分得开 —— 这正是本模块 docstring 里那一节讲的
#: 事，只不过现在多了一个前端读得懂的码，不用再从正文里猜。
_ABANDONED_EVENT = "检查结果丢了，这张验收卡已判死"
_ABANDONED_DETAIL_LABEL = "怎么回事"


def stale_before(now: datetime | None = None) -> datetime:
    """判死线：早于这个时刻还在 `pending_gate` 的卡，认定没人会再结算它。"""
    return (now or datetime.now(UTC)) - timedelta(
        seconds=GATE_TIMEOUT_S + GATE_STALE_GRACE_S
    )


async def find_abandoned(
    session: AsyncSession,
    *,
    skip_card_ids: Iterable[uuid.UUID] = (),
    now: datetime | None = None,
) -> list[AcceptCard]:
    """还在 `pending_gate`、早过判死线、且闸门不在本进程里跑的卡。"""
    skip = frozenset(skip_card_ids)
    cards = await AcceptCardRepository(session).list_stale_pending_gate(
        stale_before(now)
    )
    return [c for c in cards if c.id not in skip]


async def condemn(session: AsyncSession, card: AcceptCard) -> None:
    """把一张孤儿卡判死并留痕。调用方负责 flush/commit。

    幂等：判死后卡是 `gate_failed`，下一轮扫底的查询就不再选中它。
    """
    card.status = AcceptStatus.gate_failed
    card.gate_output = archive.prefix_note(card.gate_output, _ABANDONED_OUTPUT)
    notes.record(
        card,
        notes.NoteCode.gate_abandoned,
        archive.prefix_note(
            card.note,
            f"{GATE_ABANDONED_PREFIX}：检查没跑完就失去结果，卡片判死，话题可以重新递卡。",
        ),
    )
    # decided_by/decided_at 留空是刻意的：没有人做过这个决定，写上谁都是假的。
    await session.flush()

    topic = await TopicRepository(session).get(card.topic_id)
    if topic is None:  # pragma: no cover — FK cascade makes this unreachable
        return
    await BlockRepository(session).add(
        project_id=topic.project_id,
        topic_id=card.topic_id,
        author="cheese",
        author_type=AuthorType.system,
        content="检查没跑完，这张验收卡已判死",
        kind=BlockKind.event,
        meta={
            "platform": True,
            **notice(
                EVENT_GATE_ABANDONED,
                severity=SEVERITY_WARN,
                who=WHO_CHEESE,
                detail=(
                    "平台没能拿到这次检查的结果，多半是后端重启时闸门任务"
                    "随进程丢了。\n"
                    "这不是检查没通过——检查根本没跑完，没有任何证据说明代码"
                    "有问题。重新递一次卡即可。"
                ),
                detail_label="为什么判死",
            ),
        },
    )


async def sweep(
    session_factory: async_sessionmaker,
    *,
    nudge: Callable[[uuid.UUID, str, str, dict], None] | None = None,
    skip_card_ids: Iterable[uuid.UUID] | None = None,
    now: datetime | None = None,
) -> dict:
    """扫一轮。返回 `{"condemned": [card_id...], "errors": [...]}`。

    ``skip_card_ids`` 默认取 `gate.in_flight_card_ids()`；测试可以显式传 `()`。
    ``nudge(topic_id, content, event, meta)`` 用来叫醒芝士去重递；不传就只判死不
    叫人（启动早期 runner 还没准备好时用得上）。`content` 是给芝士的完整说明，
    `event` + `meta` 是房间里那一行（平台提示统一契约）。

    一张卡一个事务，跟 `SchedulerService.poll_open_prs` 同样的理由：一张卡出错
    不能把另一张卡已经判死的结果回滚掉。
    """
    from app.domain.review import gate

    if skip_card_ids is None:
        skip_card_ids = gate.in_flight_card_ids()

    async with session_factory() as session:
        stale = await find_abandoned(session, skip_card_ids=skip_card_ids, now=now)
        targets = [(c.id, c.topic_id) for c in stale]

    condemned: list[uuid.UUID] = []
    errors: list[str] = []
    for card_id, topic_id in targets:
        async with session_factory() as session:
            try:
                card = await AcceptCardRepository(session).get(card_id)
                # 重查一次状态：从上面那次查询到现在，闸门可能刚好结算完了。
                if card is None or card.status != AcceptStatus.pending_gate:
                    continue
                await condemn(session, card)
                await session.commit()
            except Exception as exc:  # noqa: BLE001 — 一张卡失败不能停下整轮
                await session.rollback()
                errors.append(f"{card_id}: {exc}")
                continue
        condemned.append(card_id)
        if nudge is not None:
            nudge(
                topic_id,
                _ABANDONED_NUDGE,
                _ABANDONED_EVENT,
                notice(
                    EVENT_GATE_ABANDONED,
                    severity=SEVERITY_WARN,
                    who=WHO_CHEESE,
                    detail=_ABANDONED_NUDGE,
                    detail_label=_ABANDONED_DETAIL_LABEL,
                ),
            )
    return {"condemned": condemned, "errors": errors}
