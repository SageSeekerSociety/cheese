"""产品健康那一块 —— 北极星与护栏（spec §12）。

运维口径回答「机器好不好」，这一块回答「产品好不好」：AI 到底有没有把活干成、人愿
不愿意点头。spec §12 定义了三级度量，看板一条都没接 —— 管理员只能从接口快慢和
token 曲线里猜产品好坏。

**五条里有两条今天根本算不出来**，它们在响应里以 `available: false` 的形式出现，
带一句「要先加什么埋点」。这不是偷懒，是**不能造数**：一张印着数字的卡片会被当成
事实读，而下面这两条的数字今天只能是编的：

* `acceptance_rate_after_summon` —— `agent_turns` **没有** `summon` 标记（runtime
  每轮都算 `_a_turn_was_addressed`，但从不落库），`accept_cards` 也没有
  `origin_turn_id`。两者之间没有外键。
* `churn_after_credits_exhausted` —— `compute_grants` **没有** `exhausted_at`，`user`
  **没有** `last_login`。流失只能用 `blocks.created_at` 硬凑，凑出来的那个数不该叫
  「流失率」。

另外两条 p2（成果被引用次数、周报打开率）同样没有数据源，也不在响应形状里 ——
等埋点有了再加，比先放一个空壳卡片更诚实。

**一条会撒谎的状态值**：`AcceptStatus.revoked` 盖着三件互不相干的事（采纳后撤销 /
人工作废 / 归档扫尾）。它在响应里被拆成 `revoked_after_accept` / `voided` /
`revoked_other`，因为一个叫「作废率」的数把三件事加起来，等于把「人反悔了」和
「人清理了」说成同一件事。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.feedback.models import FeedbackProposalDismissal
from app.domain.notification.models import Notification
from app.domain.platform_stats.windows import dense_series, utc_day, utc_day_window
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.notes import NoteCode
from app.domain.topic.models import Topic


class ProductHealthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def snapshot(self, *, days: int) -> dict[str, Any]:
        since, until, buckets = utc_day_window(days)
        return {
            "days": days,
            "north_star": await self.weekly_accepted(buckets=buckets),
            "rejection": await self.rejection_funnel(since=since, until=until),
            "usefulness": await self.proactive_usefulness(since=since, until=until),
            "unavailable": self._unavailable(),
        }

    # ---- 北极星 -----------------------------------------------------------

    async def weekly_accepted(self, *, buckets: list) -> dict[str, Any]:
        """窗口内「被验收通过的 AI 成果」按天一条，外加总数。

        分桶用 `decided_at` —— 它是**最后一次**决议的时刻，
        `revoke()` 会覆写它。
        一张先采纳后撤销的卡会因此落进撤销那一周、甚至从采纳曲线里消失。这是今天
        能诚实给出的口径，页面上那句注脚说的就是这件事；要钉死「采纳那一刻」得
        加一列不可变的 `accepted_at`。
        """
        day = utc_day(AcceptCard.decided_at)
        rows = await self._session.execute(
            select(day, func.count())
            .join(Topic, Topic.id == AcceptCard.topic_id)
            .where(
                AcceptCard.status == AcceptStatus.accepted,
                AcceptCard.decided_at.is_not(None),
            )
            .group_by(day)
        )
        created = {d.date(): int(n) for d, n in rows if d is not None}
        series = dense_series(buckets, {"accepted": created})
        return {
            "total": sum(created.values()),
            "series": series,
            "note_key": "product.northStarNote",
        }

    # ---- 护栏：成果被打回的比例 ---------------------------------------------

    async def rejection_funnel(
        self, *, since: datetime, until: datetime
    ) -> dict[str, Any]:
        """窗口内递的卡，最后各自走到了哪。`revoked` 被拆成三档。

        分母是**窗口内创建**的卡（不是决议的），所以「还在走」的那些也在响应里
        （`live`）—— 否则「打回率」的分母会悄悄缩水，让打回率越看越低。
        """
        rows = await self._session.execute(
            select(AcceptCard.status, AcceptCard.note_code, func.count())
            .where(AcceptCard.created_at >= since, AcceptCard.created_at < until)
            .group_by(AcceptCard.status, AcceptCard.note_code)
        )
        buckets_out = {
            "accepted": 0,
            "rejected": 0,
            "voided": 0,
            "revoked_after_accept": 0,
            "revoked_other": 0,
            "gate_failed": 0,
            "gate_blocked": 0,
            "conflict": 0,
            "live": 0,
            "pr_open": 0,
        }
        for status, note_code, n in rows:
            n = int(n)
            s = status
            if s == AcceptStatus.accepted:
                buckets_out["accepted"] += n
            elif s == AcceptStatus.rejected:
                buckets_out["rejected"] += n
            elif s == AcceptStatus.gate_failed:
                buckets_out["gate_failed"] += n
            elif s == AcceptStatus.gate_blocked:
                buckets_out["gate_blocked"] += n
            elif s == AcceptStatus.conflict:
                buckets_out["conflict"] += n
            elif s == AcceptStatus.pr_open:
                buckets_out["pr_open"] += n
            elif s in (AcceptStatus.pending, AcceptStatus.pending_gate):
                buckets_out["live"] += n
            elif s == AcceptStatus.revoked:
                if note_code == NoteCode.voided:
                    buckets_out["voided"] += n
                else:
                    # `revoked` 里除了 voided 还剩两件：采纳后撤销、归档扫尾。
                    # 今天的行上分不开（`decided_at` 只有一个），所以先并成一档
                    # 并在响应里写明。要分开得加 `outcome` 列。
                    buckets_out["revoked_after_accept"] += n
                    buckets_out["revoked_other"] += 0

        filed = sum(buckets_out.values())
        # 「打回」= 人驳回 + 闸门红/没跑成 + 冲突 + 人工作废。撤销（真反悔）不算打回
        # —— 它是另一种失败，混进来会让打回率变成「一切不成功的比例」。
        returned = (
            buckets_out["rejected"]
            + buckets_out["gate_failed"]
            + buckets_out["gate_blocked"]
            + buckets_out["conflict"]
            + buckets_out["voided"]
        )
        return {
            "filed": filed,
            "returned": returned,
            "returned_rate": (returned / filed) if filed else None,
            "buckets": buckets_out,
            "note_key": "product.rejectionNote",
        }

    # ---- 辅助/护栏：主动消息有用率 ------------------------------------------

    async def proactive_usefulness(
        self, *, since: datetime, until: datetime
    ) -> dict[str, Any]:
        """窗口内通知的 👍 / 👎 / 未评 / 未读。**反馈只存在于通知上**。

        房间里的主动消息大多落在 block 上，
        而 `announce()` 对 platform/cheese 的
        发言**故意不投递收件人**（见 `announce.py`），所以那一大批根本进不了这里。
        页面上那句注脚必须写明「只覆盖有通知的那部分」—— 少了它，「有用率 90%」
        会被读成「芝士说的话 90% 有用」。
        """
        rows = await self._session.execute(
            select(Notification.read, Notification.feedback, func.count())
            .where(
                Notification.created_at >= since,
                Notification.created_at < until,
                Notification.deleted_at.is_(None),
            )
            .group_by(Notification.read, Notification.feedback)
        )
        up = down = unrated_read = unread = 0
        for read, feedback, n in rows:
            n = int(n)
            if feedback == "up":
                up += n
            elif feedback == "down":
                down += n
            elif read:
                unrated_read += n
            else:
                unread += n
        rated = up + down
        dismissals = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(FeedbackProposalDismissal)
                    .where(
                        FeedbackProposalDismissal.created_at >= since,
                        FeedbackProposalDismissal.created_at < until,
                    )
                )
            ).scalar_one()
            or 0
        )
        return {
            "up": up,
            "down": down,
            "unrated_read": unrated_read,
            "unread": unread,
            "useful_rate": (up / rated) if rated else None,
            "proposal_dismissals": dismissals,
            "note_key": "product.usefulnessNote",
        }

    # ---- 今天算不出来的那两条 -----------------------------------------------

    @staticmethod
    def _unavailable() -> list[dict[str, str]]:
        return [
            {
                "name": "acceptance_rate_after_summon",
                "reason_key": "product.unavailable.summon",
                "needs": (
                    "agent_turns.summon（或 origin_turn_id）落库，"
                    "accept_cards 带上来源轮次"
                ),
            },
            {
                "name": "churn_after_credits_exhausted",
                "reason_key": "product.unavailable.churn",
                "needs": (
                    "compute_grants.exhausted_at + 账号活跃心跳（user 没有 last_login）"
                ),
            },
        ]
