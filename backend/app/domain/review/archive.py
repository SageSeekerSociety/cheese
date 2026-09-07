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
  房间事件都要说清 PR 还开在 GitHub 上。
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

from app.domain.agent.platform_notices import (
    EVENT_ACCEPT_STOPPED,
    SEVERITY_WARN,
    WHO_HUMAN,
    notice,
)
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.review import notes
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


def prefix_note(note: str, added: str) -> str:
    """把归档说明放在最前面（它是这张卡最后、也最该被读到的一句），旧 note 保留在后。"""
    old = (note or "").strip()
    return (f"{added}\n{old}" if old else added)[:_NOTE_MAX]


async def close_cards_for_archived_topic(
    session: AsyncSession,
    *,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
    topic_title: str,
    by: str,
) -> list[AcceptCard]:
    """终结这个房间里所有非终态的验收卡（房间自己的，和它的卡上挂着的）。返回被
    改动的卡。

    幂等：终态的卡不会被再动一次，所以重复归档（或先归档再取消归档再归档）
    不会重复写 note、重复发通知。调用方负责 flush/commit。
    """
    del topic_title  # 通知落在房间事件里，标题由房间自己带
    cards = await AcceptCardRepository(session).list_everywhere_in_room(topic_id)
    now = datetime.now(UTC)
    changed: list[AcceptCard] = []
    stranded: list[AcceptCard] = []

    for card in cards:
        if card.status not in OPEN_CARD_STATUSES:
            continue
        was = card.status
        card.status = AcceptStatus.revoked
        if card.pr_number is not None and card.pr_merged_at is None:
            # 骑着未合并的 PR：停止跟进，但不替任何人去关它。
            notes.record(
                card,
                notes.NoteCode.archived,
                prefix_note(
                    card.note,
                    f"话题归档，平台已停止跟进 PR #{card.pr_number}。PR 未合并、"
                    f"仍开在 GitHub 上，合并还是关闭由人决定："
                    f"{card.pr_url or '(无链接)'}",
                ),
            )
            stranded.append(card)
        else:
            closed = f"话题归档，验收卡随之关闭于状态「{was}」。"
            notes.record(card, notes.NoteCode.archived, prefix_note(card.note, closed))
        # 只在空的时候补：已有的决议痕迹不覆盖。
        if card.decided_by is None:
            card.decided_by = by
        if card.decided_at is None:
            card.decided_at = now
        changed.append(card)

    if not changed:
        return []

    await session.flush()

    # 留痕：一张开着的 PR 被平台放手了，这件事不能只躺在 note 里。
    for card in stranded:
        await BlockRepository(session).add(
            project_id=project_id,
            topic_id=topic_id,
            author="cheese",
            author_type=AuthorType.system,
            content=f"话题归档，平台停止跟进 PR #{card.pr_number}",
            kind=BlockKind.event,
            meta={
                "platform": True,
                **notice(
                    EVENT_ACCEPT_STOPPED,
                    severity=SEVERITY_WARN,
                    who=WHO_HUMAN,
                    detail=(
                        f"{card.pr_url or '无链接'}\n"
                        "这个 PR 还没合并，平台也不会自动关掉它——关不关由人决定。"
                    ),
                    detail_label="这个 PR 怎么办",
                ),
            },
        )

    return changed
