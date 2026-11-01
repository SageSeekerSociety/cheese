"""话题归档时收敛验收卡（孤儿卡修复, 2026-08-10）.

在这之前 `TopicService._archive_one` 完全不碰卡：话题归档了，它那张还没决议的
验收卡就永远停在非终态上——而合并态轮询器每 60 秒仍会拿着 GitHub 凭据去跟进它。

修复分两层（双保险）：

1. **归档时收敛**（本模块）：话题一进 archived，它所有非终态的卡当场终结。
2. **轮询按话题状态过滤**
   （`AcceptCardRepository.list_awaiting_merge_on_active_topics`）：
   即便某张卡漏网，轮询器也不会再碰归档话题上的卡。

## 归档时每种非终态卡的去向，以及为什么

- `pending` / `pending_gate` / `conflict`：**`revoked`**——话题都归档了，
  这张请求自然作废。人要继续就重新递卡。
- 骑着未合并 PR 的卡额外多一句：**平台不去动那个 PR**（理由见下），note 和
  房间事件都要说清 PR 还开在 GitHub 上。PR 被关掉的卡轮不到这里：轮询器一看到
  PR 关闭且没有合并，就已经把卡作废了。
- `accepted` / `rejected` / `revoked` / `gate_failed` 已是终态，不碰。

## 为什么开着的 PR 不自动关掉

归档话题的人未必是这个 PR 名义上的作者（PR 以递卡人的身份开出）。替别人关掉
一个外部可见的 PR，方向是反的：PR 开着是惰性的（不耗 token、不触发任何自动
行为），关掉却可能丢掉一段人本来打算手动合并的工作。

所以选择是：**停止一切自动跟进，但把 PR 原样留在 GitHub 上，并且留痕**——
否则只是把"静默活动"换成了"静默遗留"。
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.delivery_events import SettleCard
from app.domain.event_about import EventAbout, landing
from app.domain.event_author import AuthorType
from app.domain.notice_meta import (
    EVENT_ACCEPT_STOPPED,
    SEVERITY_WARN,
    WHO_HUMAN,
    notice,
)
from app.domain.review import notes, room_notices
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.repositories import AcceptCardRepository

# 非终态：话题归档时必须收敛掉的卡。`gate_failed` 不在其中——闸门红了卡本来
# 就作废了，它已经是终态。
OPEN_CARD_STATUSES: tuple[AcceptStatus, ...] = (
    AcceptStatus.pending,
    AcceptStatus.pending_gate,
    AcceptStatus.conflict,
)

_NOTE_MAX = 2000


def pr_left_open(card: AcceptCard) -> bool:
    """这张未决卡骑着的 PR 还开在 GitHub 上、没合并——平台放手时要人去决定它的去留。

    未决卡的 PR 只会是开着的：轮询器看到 PR 关闭且没有合并时，卡当场作废。
    """
    return card.pr_number is not None and card.pr_merged_at is None


def prefix_note(note: str, added: str) -> str:
    """把收尾说明放在最前面（它是这张卡最后、也最该被读到的一句），旧 note 保留在后。"""
    old = (note or "").strip()
    return (f"{added}\n{old}" if old else added)[:_NOTE_MAX]


async def _converge_card(
    session: AsyncSession,
    card: AcceptCard,
    *,
    outcome: str,
    note_code: notes.NoteCode,
    note: str,
    by: str | None,
    settle_card: SettleCard | None,
    now: datetime,
) -> None:
    """把一张非终态的卡收掉，并留下为什么。

    归档和关任务是两种收尾，处理同一件事：卡跟着它所在的那一块一起结束。所以
    状态、收件箱、留痕、决议时间都在这一处写，两边不各写一份会走散的规矩。
    """
    card.status = AcceptStatus.revoked
    notes.record(card, note_code, prefix_note(card.note, note))
    # 只在空的时候补：已有的决议痕迹不覆盖。
    if card.decided_by is None and by is not None:
        card.decided_by = by
        card.decided_by_assistant = False
    if card.decided_at is None:
        card.decided_at = now
    # 卡跟着终结：等验收人动手的那几句一起收掉，别留在他的收件箱里说着「等你」。
    await room_notices._settle_the_card(session, card, outcome, settle_card=settle_card)


async def close_cards_for_archived_topic(
    session: AsyncSession,
    *,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
    topic_title: str,
    overview_room_id: uuid.UUID,
    by: str,
    settle_card: SettleCard | None,
) -> list[AcceptCard]:
    """终结这个房间里所有非终态的验收卡（房间自己的，和它的卡上挂着的）。返回被
    改动的卡。

    幂等：终态的卡不会被再动一次，所以重复归档（或先归档再取消归档再归档）
    不会重复写 note、重复发通知。调用方负责 flush/commit。

    ``settle_card`` 是 `topic.archive` 一路递下来的那一个
    （`app.domain.review.ports.SettleCard`）；``None`` 是门漏了一处，由
    `room_notices._settle_the_card` 当场说清，不在这里静默跳过。
    """
    del topic_title  # 通知落在事件里，标题由房间自己带
    cards = await AcceptCardRepository(session).list_everywhere_in_room(topic_id)
    now = datetime.now(UTC)
    changed: list[AcceptCard] = []
    stranded: list[AcceptCard] = []

    for card in cards:
        if card.status not in OPEN_CARD_STATUSES:
            continue
        was = card.status
        if pr_left_open(card):
            # 骑着未合并的 PR：停止跟进，但不替任何人去关它。
            note = (
                f"频道已归档，平台已停止跟进 PR #{card.pr_number}。PR 未合并，"
                f"仍在 GitHub 上打开，需要人决定合并还是关闭："
                f"{card.pr_url or '（无链接）'}"
            )
            stranded.append(card)
        else:
            note = f"频道已归档，审阅随之关闭，关闭时状态为「{was}」。"
        await _converge_card(
            session,
            card,
            outcome="archived",
            note_code=notes.NoteCode.archived,
            note=note,
            by=by,
            settle_card=settle_card,
            now=now,
        )
        changed.append(card)

    if not changed:
        return []

    await session.flush()

    # 留痕：一张开着的 PR 被平台放手了，这件事不能只躺在 note 里。
    # 落点按结论 14：卡的事落那张卡。为房间本身递的卡（`task_id` 空）没有卡可落，
    # 而它的房间此刻正在关掉——和归档记录同一个理由（`TopicService._archive_one`）：
    # 关掉的房间没人再打开它的时间线，而这条 notice 的 `who` 是人，它要的就是有人
    # 去处理那个未合并的 PR。所以它落项目总览，不落刚关掉的那个房间。
    for card in stranded:
        if card.task_id is not None:
            landed = landing(
                EventAbout.task,
                project_id=project_id,
                room_id=topic_id,
                task_id=card.task_id,
            )
        else:
            landed = landing(
                EventAbout.project, project_id=project_id, room_id=overview_room_id
            )
        await BlockRepository(session).add(
            project_id=landed.project_id,
            conversation_id=landed.conversation_id,
            author="cheese",
            author_type=AuthorType.platform,
            content=say("archiveStoppedFollowing", pr=card.pr_number),
            kind=BlockKind.event,
            meta={
                "platform": True,
                **notice(
                    EVENT_ACCEPT_STOPPED,
                    severity=SEVERITY_WARN,
                    who=WHO_HUMAN,
                    detail=say(
                        "archiveStoppedFollowingDetail",
                        url=card.pr_url or say("prNoLink"),
                    ),
                    detail_label=say("labelNextStep"),
                ),
            },
        )

    return changed


def _note_for_closed_task(card: AcceptCard, was: AcceptStatus) -> str:
    """一条活关掉之后，卡面上留的那一句。

    和归档那句只差一个原因：归档是房间没了，关任务是活没了。骑着的 PR 不在这里
    处理 —— 一条活关掉时它自己的 PR 怎么处置是 `review.task_pr` 的事，这条 note
    只说平台不再跟进了。
    """
    if pr_left_open(card):
        return (
            f"任务已关闭，平台已停止跟进 PR #{card.pr_number}。"
            f"PR 未合并：{card.pr_url or '（无链接）'}"
        )
    return f"任务已关闭，审阅随之关闭，关闭时状态为「{was}」。"


async def close_cards_for_closed_task(
    session: AsyncSession,
    *,
    task_id: uuid.UUID,
    by: str | None,
    settle_card: SettleCard | None,
) -> list[AcceptCard]:
    """一条活被关掉，它还没结算的卡跟着作废。返回被改动的卡。

    这是归档那一套在关任务上的对应物：卡不能比它挂着的活活得久。漏掉这一步，
    卡就永远停在非终态上，而轮询和侧栏都把它当成还有东西在走 —— 一件 10-12
    就关掉的活，到了 10-17 还在让房间亮着绿灯。

    幂等：终态的卡不动，所以重复调用（关任务一次，轮询器补一次）不会重复写
    note。``by`` 是关任务的人；轮询器补的那一次没有人在场，留空，和闸门孤儿卡
    同一条理由（`gate_sweep`：没有人做过这个决定，写上谁都是假的）。调用方负责
    flush/commit。
    """
    cards = await AcceptCardRepository(session).list_for_task(task_id)
    return await _close_the_open_cards(session, cards, by=by, settle_card=settle_card)


async def close_cards_for_closed_tasks(
    session: AsyncSession,
    *,
    settle_card: SettleCard | None,
    project_id: uuid.UUID | None = None,
) -> list[AcceptCard]:
    """第二道锁：轮询器每一轮把已关的活上漏下的非终态卡收掉。

    关任务的那条路（`routes/topics_tasks`）当场收；这里兜住其余路径和这次修复
    之前就停在 pending 上的行 —— 和归档那条路同一个双保险（`pr_poll` 不再跟进，
    这里把行也收敛掉）。``project_id`` 有值就只看这个项目。
    """
    cards = await AcceptCardRepository(session).list_open_on_closed_tasks(
        OPEN_CARD_STATUSES, project_id=project_id
    )
    return await _close_the_open_cards(session, cards, by=None, settle_card=settle_card)


async def _close_the_open_cards(
    session: AsyncSession,
    cards: list[AcceptCard],
    *,
    by: str | None,
    settle_card: SettleCard | None,
) -> list[AcceptCard]:
    now = datetime.now(UTC)
    changed: list[AcceptCard] = []
    for card in cards:
        if card.status not in OPEN_CARD_STATUSES:
            continue
        was = card.status
        await _converge_card(
            session,
            card,
            outcome="closed",
            note_code=notes.NoteCode.task_closed,
            note=_note_for_closed_task(card, was),
            by=by,
            settle_card=settle_card,
            now=now,
        )
        changed.append(card)
    if changed:
        await session.flush()
    return changed
