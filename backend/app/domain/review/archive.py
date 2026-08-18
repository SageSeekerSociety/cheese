"""话题归档时收敛验收卡（孤儿卡修复, 2026-08-10）.

在这之前 `TopicService._archive_one` 完全不碰卡：话题归档了，它那张还没决议的
验收卡就永远停在非终态上。对 `pr_open` 的卡来说这不是"停着"而是"还在动"——
`SchedulerService.poll_open_prs` 每 60 秒仍会拿着 GitHub 凭据去推进它（推分支、
查 CI、合并 PR、再归档一次已归档的话题）。用谁的凭据取决于 forge：接了平台
GitHub App 的项目用 App 自己的 write token，其余用 `decided_by` 本人连接的
token（`AcceptService._pr_poll_token`）。

修复分两层（双保险）：

1. **归档时收敛**（本模块）：话题一进 archived，它所有非终态的卡当场终结。
2. **轮询按话题状态过滤**（`AcceptCardRepository.list_pr_open_on_active_topics`）：
   即便某张卡漏网，轮询器也不会再碰归档话题上的卡。

## 归档时每种非终态卡的去向，以及为什么

- `pr_open` 且 `pr_merged_at` 非空（PR 已合并）：**收尾成 `accepted`**。人确实
  点过采纳、PR 确实进了 main，说它被撤销是假话。
  自 #206 起合并即终态，所以这一支不再是常态，而是两种情况的兜底：合并和下一次
  轮询之间那个窗口里被归档的卡，以及本次改动之前就停在"已合并等部署"的老卡
  （它们本身会在下一轮轮询里自愈——`_advance_pr_checks` 看到 PR 已合并就走
  `_settle_external_merge`，不需要数据迁移）。
- `pr_open` 且 `pr_merged_at` 为空（PR 还开着、没合并）：**`revoked`，并且
  平台不去动那个 PR**。这是本次唯一的产品判断，理由见下。
- `pending` / `pending_gate` / `conflict`：**`revoked`**——话题都归档了，
  这张请求自然作废。人要继续就重新递卡。
- `accepted` / `rejected` / `revoked` / `gate_failed` 已是终态，不碰。

## 为什么第一阶段的 PR 不自动关掉

归档一个话题的人，未必是当初授权开 PR 的那个人。用 A 的 token 去关掉 A 名下的
PR，只因为 B 归档了话题——这正是本次要修的"借来的钥匙"问题本身，再借一次去做
一个外部可见、别人未必想要的动作，方向是反的。PR 开着是惰性的（不耗 token、
不触发任何自动行为），关掉却可能丢掉一段人本来打算手动合并的工作。

所以选择是：**停止一切自动推进，但把 PR 原样留在 GitHub 上，并且必须留痕 +
通知到当初的授权人**——否则只是把"静默活动"换成了"静默遗留"。
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.alert.models import AlertKind, AlertLevel
from app.domain.alert.services import AlertService
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
    AcceptStatus.pr_open,
)

_NOTE_MAX = 2000
_ARCHIVE_NOTE_PREFIX = "📦"


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
    """终结 `topic_id` 上所有非终态的验收卡。返回被改动的卡。

    幂等：终态的卡不会被再动一次，所以重复归档（或先归档再取消归档再归档）
    不会重复写 note、重复发通知。调用方负责 flush/commit。
    """
    cards = await AcceptCardRepository(session).list_for_topic(topic_id)
    now = datetime.now(UTC)
    changed: list[AcceptCard] = []
    stranded: list[AcceptCard] = []

    for card in cards:
        if card.status not in OPEN_CARD_STATUSES:
            continue
        was = card.status
        if was == AcceptStatus.pr_open and card.pr_merged_at is not None:
            # PR 已经进 main 了，这是收尾，不是撤销。
            card.status = AcceptStatus.accepted
            wrapped = (
                f"{_ARCHIVE_NOTE_PREFIX} 话题归档收尾：PR #{card.pr_number} 已合并。"
            )
            notes.record(card, notes.NoteCode.archived, prefix_note(card.note, wrapped))
        elif was == AcceptStatus.pr_open:
            # 第一阶段：PR 还开着。停止推进，但不替任何人去关它。
            card.status = AcceptStatus.revoked
            notes.record(
                card,
                notes.NoteCode.archived,
                prefix_note(
                    card.note,
                    f"{_ARCHIVE_NOTE_PREFIX} 话题归档，平台已停止推进 PR "
                    f"#{card.pr_number}。PR 未合并、仍开在 GitHub 上，需要人工决定"
                    f"合并还是关闭：{card.pr_url or '(无链接)'}",
                ),
            )
            stranded.append(card)
        else:
            card.status = AcceptStatus.revoked
            closed = (
                f"{_ARCHIVE_NOTE_PREFIX} 话题归档，验收卡随之关闭（原状态：{was}）。"
            )
            notes.record(card, notes.NoteCode.archived, prefix_note(card.note, closed))
        # 只在空的时候补：`pr_open` 的卡上 decided_by/decided_at 记的是当初点
        # 采纳的人和时刻，覆盖掉就丢了授权来源。
        if card.decided_by is None:
            card.decided_by = by
        if card.decided_at is None:
            card.decided_at = now
        changed.append(card)

    if not changed:
        return []

    await session.flush()

    # 留痕 + 通知：一张开着的 PR 被平台放手了，这件事不能只躺在 note 里。
    for card in stranded:
        await BlockRepository(session).add(
            project_id=project_id,
            topic_id=topic_id,
            author="cheese",
            author_type=AuthorType.system,
            content=(
                f"⚠️ 话题归档，平台停止推进 PR #{card.pr_number}"
                f"（{card.pr_url or '无链接'}）。\n"
                f"这个 PR 还没合并，也**不会**被平台自动关闭——它是以 "
                f"<@{card.decided_by}> 的身份开的，关不关由人决定。"
            ),
            kind=BlockKind.event,
            meta={"platform": True},
        )
        if card.decided_by:
            await AlertService(session).create(
                project_id=project_id,
                level=AlertLevel.strong,
                kind=AlertKind.change_alert,
                title=f"话题「{topic_title}」归档了，你的 PR #{card.pr_number} 还开着",
                body=(
                    f"<@{by}> 归档了这个话题，平台已停止推进这张验收卡。"
                    f"PR #{card.pr_number} 是以你的身份开的，未合并，平台不会替你"
                    f"关掉：{card.pr_url or '(无链接)'}"
                ),
                target_handle=card.decided_by,
                topic_id=topic_id,
            )

    return changed
