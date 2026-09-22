"""交付管线那一块 —— 「AI 干活、人验收」这条主链现在堵在哪。

看板其余四块都是运维口径（反馈队列、用量、账号、接口耗时），产品自己的核心承诺
一个数字都不在墙上。这一块补的就是它：验收卡积压、各段停留时长、待人动手的清单、
轮次失败。

**口径上三条硬事实，写在最前面**，因为它们每一条都能让一个看起来对的数变成谎言：

1. **机器闸门已退役**（#296）。`create_card` 不再铸 `pending_gate`，`gate_failed` /
   `gate_blocked` / `pr_open` 都是死写入 —— 库里只剩历史行。所以「闸门」那一段只能
   历史注释，不能画成活的漏斗一级。
2. **`void` 不是一个状态**。人工作废走 `AcceptService.void()`，落的是
   `status=revoked` + `note_code=NoteCode.voided`。而 `revoked` 一共盖着三件互不相
   干的事（采纳后撤销 / 人工作废 / 归档扫尾），**不能直接画成一档**。
3. **`decided_at` 是最后一次决议的时刻**，`revoke()` 会把它覆写掉 —— 一张先采纳后
   撤销的卡会丢掉采纳那一刻。按周数「采纳了多少」时要么接受这个限制，要么退回
   `created_at` 分桶，并在页面上写明。

另外：一张非终态的卡会**堵死整间房的重新递卡**（`_CARD_BLOCKS_NEW_CARD`），所以
积压不只是「排队慢」，它可能是「这个话题再也交不出东西了」。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.dispatch_log import DispatchRow
from app.domain.agent.models import AgentTurn
from app.domain.device.models import DeviceHealthRow
from app.domain.review.archive import OPEN_CARD_STATUSES
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.notes import _STUCK as STUCK_NOTE_CODES  # noqa: PLC2701
from app.domain.review.notes import NoteCode
from app.domain.room_task.models import Task, TaskStatus
from app.domain.topic.models import Topic, TopicStatus

#: 漏斗里「还在走」的那几档。**不是 `AcceptStatus` 的全部**，也不是 `archive` 那份
#: `OPEN_CARD_STATUSES` 的同义词：那份管的是「归档时要不要收敛」，这里是「现在还
#: 挡着重新递卡吗」。两份故意不同（`gate_failed` 已是终态、却仍然不是成功），任何
#: 按状态计数的实现都必须点名自己数的是哪一份语义。
LIVE_STATUSES: tuple[AcceptStatus, ...] = OPEN_CARD_STATUSES

#: 已经走完的那几档里，`revoked` 单列 —— 理由见模块 docstring 第 2 条。
SETTLED_STATUSES: tuple[AcceptStatus, ...] = (
    AcceptStatus.accepted,
    AcceptStatus.rejected,
    AcceptStatus.revoked,
    AcceptStatus.gate_failed,
    AcceptStatus.gate_blocked,
)

#: 人工作废的判据：**`note_code`，不是 `status`**（见 docstring 第 2 条）。
VOIDED = NoteCode.voided

#: 停住了的 note 码 —— 从 `notes._STUCK` 读，**不在这里再抄一份名单**。抄两份的症状
#: 是新加一个码时只改了一边，它在看板上永远安静（`notes.py` 的 docstring 记着这个
#: 坑：`🌿 分支分叉` 和 `🚪 PR 被关` 都这么红不起来过）。名单本身见文件头 import。

#: 轮次失败的结构化来源是 `blocks.meta`（`event_type=platform_error` / `turn_failed`
#: 上的 `code`），不是 `agent_turns` —— 那张表**没有**失败列。见模块 docstring。
#: 这里只点名我们承诺在看板上分开报的那几个码；别的码进 `other`。
TURN_FAILURE_CODES: tuple[str, ...] = (
    "turn_timeout",
    "prompt_undelivered",
    "host_unreachable",
    "storage_exhausted",
    "runtime_image_missing",
    "subscription_credential_expired",
    "workspace_vcs_perms",
)


class PipelineRepository:
    """交付管线的读。全部是**存量 + 窗口**两种口径，方法名里写清楚是哪一种。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---- 存量：此刻的积压 -------------------------------------------------

    async def backlog(self) -> dict[str, Any]:
        """验收卡按状态的存量，外加「卡住」和「堵死重新递卡」两个数。

        `by_status` 把**每一个** `AcceptStatus` 都给出来（包括死写入的那几个），
        值为 0 的档位也要在 —— 缺档和 0 在页面上必须长得不一样（见 `AdminKpiCard`）。
        """
        rows = await self._session.execute(
            select(AcceptCard.status, func.count()).group_by(AcceptCard.status)
        )
        counts = {status.value: 0 for status in AcceptStatus}
        for status, n in rows:
            counts[str(status)] = int(n)

        stuck = await self._count(
            AcceptCard, AcceptCard.note_code.in_(tuple(STUCK_NOTE_CODES))
        )
        blocking_refile = await self._count(
            AcceptCard, AcceptCard.status.in_(tuple(LIVE_STATUSES))
        )
        voided = await self._count(AcceptCard, AcceptCard.note_code == VOIDED)
        return {
            "by_status": counts,
            "stuck": stuck,
            "blocking_refile": blocking_refile,
            "voided_stock": voided,
            "live_total": sum(counts[s.value] for s in LIVE_STATUSES),
            "settled_total": sum(counts[s.value] for s in SETTLED_STATUSES),
        }

    async def stuck_cards(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """停住了的卡，最老的在前。判据是 `note_code` 属于 `_STUCK`，不是 note 文案。"""
        stmt = (
            select(
                AcceptCard.id,
                AcceptCard.topic_id,
                AcceptCard.task_id,
                AcceptCard.reviewer_handle,
                AcceptCard.status,
                AcceptCard.note_code,
                AcceptCard.note,
                AcceptCard.created_at,
                AcceptCard.change_subject,
                Topic.title.label("topic_title"),
                Topic.project_id,
            )
            .join(Topic, Topic.id == AcceptCard.topic_id)
            .where(
                AcceptCard.note_code.in_(tuple(STUCK_NOTE_CODES)),
                Topic.status != TopicStatus.archived,
            )
            .order_by(AcceptCard.created_at)
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        now = datetime.now(UTC)
        return [
            {
                "card_id": str(r.id),
                "topic_id": str(r.topic_id),
                "topic_title": r.topic_title,
                "project_id": str(r.project_id),
                "task_id": str(r.task_id) if r.task_id else None,
                "reviewer_handle": r.reviewer_handle,
                "status": str(r.status),
                "note_code": str(r.note_code) if r.note_code else None,
                "note": r.note,
                "change_subject": r.change_subject,
                "age_seconds": max(0, int((now - r.created_at).total_seconds())),
            }
            for r in rows
        ]

    async def dwell(self, *, since: datetime, until: datetime) -> dict[str, Any]:
        """递卡 → 决议的停留时长。**只统计已决议的卡**；在途的卡单独给年龄。

        分母收窄的理由：未决议的卡是右删失样本，把它们算成 0 或算进平均数都在说谎。
        页面上那句口径说的就是这件事。
        """
        decided = await self._percentile_dwell(
            AcceptCard.decided_at,
            AcceptCard.created_at,
            since=since,
            until=until,
            extra=(AcceptCard.decided_at.is_not(None),),
        )
        open_age = await self._open_age(
            AcceptCard.created_at, status_in=tuple(LIVE_STATUSES)
        )
        merged = await self._percentile_dwell(
            AcceptCard.pr_merged_at,
            AcceptCard.created_at,
            since=since,
            until=until,
            extra=(AcceptCard.pr_merged_at.is_not(None),),
        )
        accepted_not_archived = await self._accepted_not_archived()
        return {
            "filed_to_decision": decided,
            "filed_to_merge": merged,
            "open_card_age": open_age,
            "accepted_not_archived": accepted_not_archived,
        }

    async def needs_you(self, *, limit: int = 20) -> dict[str, Any]:
        """平台版「待你处理」：跨项目、还没人动手的那些事，按原因分堆。

        判据是 `delivery/addressing.py` 的三个码（`reviewer` / `reporter` /
        `asked`），**不发明第四个** —— 那张表同时是通知收件人的判据，看板自己造一个
        新理由的后果是「看板说要人动手、通知没叫人」。
        """
        # 这里给的是**按原因分堆的存量**，不是某一个人的清单（那份在 /awaiting-me）。
        # reviewer / reporter 两栏从卡和任务上直接数；asked 那一栏要读未答问题块，
        # 由 `awaiting_questions()` 单独给 —— 合成一条会把两个不同来源的数加在一起。
        reviewer = await self._count(
            AcceptCard,
            AcceptCard.status == AcceptStatus.pending,
        )
        open_tasks = await self._count(Task, Task.status == TaskStatus.open)
        questions = await self.awaiting_questions(limit=limit)
        return {
            "reviewer_pending": reviewer,
            "open_tasks": open_tasks,
            "awaiting_answer": questions["count"],
            "reasons": {
                "reviewer": reviewer,
                "reporter": open_tasks,
                "asked": questions["count"],
            },
            "items": questions["items"],
        }

    async def awaiting_questions(self, *, limit: int = 20) -> dict[str, Any]:
        """还堵在「芝士问了、没人答」上的事。来源是块的内容状态，不是状态列。"""
        from app.domain.block.repositories import BlockRepository

        # `_awaiting_an_answer` 的签名是「这些 id 里哪几个」，不是「全平台哪几个」——
        # 先取出候选（未关的活、未归档的房），再让块仓储挑出停在未答提问上的那些。
        open_task_rows = await self._session.execute(
            select(Task.id, Task.title, Task.project_id, Task.room_id).where(
                Task.status == TaskStatus.open
            )
        )
        open_tasks = list(open_task_rows)
        open_room_rows = await self._session.execute(
            select(Topic.id, Topic.title, Topic.project_id, Topic.updated_at).where(
                Topic.status != TopicStatus.archived
            )
        )
        open_rooms = list(open_room_rows)

        repo = BlockRepository(self._session)
        task_ids = await repo.tasks_awaiting_an_answer([r.id for r in open_tasks])
        room_ids = await repo.rooms_awaiting_an_answer([r.id for r in open_rooms])

        items: list[dict[str, Any]] = []
        for r in open_tasks:
            if r.id in task_ids:
                items.append(
                    {
                        "kind": "task",
                        "id": str(r.id),
                        "title": r.title,
                        "project_id": str(r.project_id),
                        "topic_id": str(r.room_id),
                        "at": None,
                    }
                )
        for r in open_rooms:
            if r.id in room_ids:
                items.append(
                    {
                        "kind": "room",
                        "id": str(r.id),
                        "title": r.title,
                        "project_id": str(r.project_id),
                        "topic_id": str(r.id),
                        "at": r.updated_at.isoformat(),
                    }
                )
        return {"count": len(task_ids) + len(room_ids), "items": items[:limit]}

    async def turn_failures(
        self, *, since: datetime, until: datetime
    ) -> dict[str, Any]:
        """轮次失败按码分开。**来源是 `blocks.meta`，不是 `agent_turns`**。

        `agent_turns` 没有失败列（见模型 docstring），失败身份只活在房间事件块的
        `meta.code` 上。按 meta 过滤是**全表过滤扫**（只有
        `event_type='cloud_provisioning'` 有偏索引），当前规模可以接受，但读的人该
        知道这一点 —— 所以它和「有索引的列」不是一类查询。
        """
        from app.domain.block.models import Block

        code_col = func.coalesce(Block.meta.op("->>")("code"), "unclassified")
        rows = await self._session.execute(
            select(code_col, func.count())
            .where(
                Block.created_at >= since,
                Block.created_at < until,
                Block.meta.op("->>")("event_type").in_(
                    ("platform_error", "turn_failed")
                ),
            )
            .group_by(code_col)
        )
        by_code = {code: 0 for code in TURN_FAILURE_CODES}
        other = 0
        for code, n in rows:
            key = str(code)
            if key in by_code:
                by_code[key] = int(n)
            else:
                other += int(n)

        # `credits_refused_at` 是**唯一**可靠的额度拒答来源：那条 notice 故意不带
        # 自己的 code（和通用 turn_failed 同路），按文案匹配是在拿句子当结构。
        credits_refused = await self._count(
            AgentTurn,
            AgentTurn.credits_refused_at.is_not(None)
            & (AgentTurn.started_at >= since)
            & (AgentTurn.started_at < until),
        )
        undelivered = await self._count(
            AgentTurn,
            AgentTurn.delivered_at.is_(None)
            & AgentTurn.stopped_at.is_not(None)
            & (AgentTurn.started_at >= since)
            & (AgentTurn.started_at < until),
        )
        return {
            "by_code": by_code,
            "other": other,
            "credits_refused": credits_refused,
            "prompt_undelivered": undelivered,
        }

    async def host_health(self) -> dict[str, Any]:
        """机器的当前连败与隔离。**只保留当前 streak** —— 成功一次就删行。

        所以它答不了「上周隔离过几台」。要历史得另开埋点，别在这张表上假装有。
        """
        rows = (
            (
                await self._session.execute(
                    select(DeviceHealthRow).order_by(
                        DeviceHealthRow.consecutive_failures.desc()
                    )
                )
            )
            .scalars()
            .all()
        )
        now = datetime.now(UTC)
        quarantined = [
            r for r in rows if r.quarantined_until and r.quarantined_until > now
        ]
        return {
            "tracked": len(rows),
            "quarantined": len(quarantined),
            "rows": [
                {
                    "device_id": r.device_id,
                    "consecutive_failures": r.consecutive_failures,
                    "last_failure_code": r.last_failure_code,
                    "last_failure_at": (
                        r.last_failure_at.isoformat() if r.last_failure_at else None
                    ),
                    "quarantined_until": (
                        r.quarantined_until.isoformat() if r.quarantined_until else None
                    ),
                }
                for r in rows[:20]
            ],
        }

    async def unsettled_dispatches(self) -> int:
        """工具调用发出去了、结果永远不会回来的条数。`outcome IS NULL` 即是。

        这不是「慢」，按设计就是「不会再有人写回来」（`dispatch_log` 的 docstring）。
        """
        return await self._count(DispatchRow, DispatchRow.outcome.is_(None))

    # ---- 内部 -------------------------------------------------------------

    async def _count(self, model: Any, *where: Any) -> int:
        stmt = select(func.count()).select_from(model)
        if where:
            stmt = stmt.where(*where)
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def _percentile_dwell(
        self,
        end_col: Any,
        start_col: Any,
        *,
        since: datetime,
        until: datetime,
        extra: tuple[Any, ...] = (),
    ) -> dict[str, Any]:
        age = func.extract("epoch", end_col - start_col)
        stmt = select(
            func.count(),
            func.percentile_cont(0.5).within_group(age),
            func.percentile_cont(0.9).within_group(age),
            func.max(age),
        ).where(
            start_col >= since,
            start_col < until,
            *extra,
        )
        n, p50, p90, mx = (await self._session.execute(stmt)).one()
        return {
            "count": int(n or 0),
            "p50_seconds": None if p50 is None else float(p50),
            "p90_seconds": None if p90 is None else float(p90),
            "max_seconds": None if mx is None else float(mx),
        }

    async def _open_age(
        self, start_col: Any, *, status_in: tuple[AcceptStatus, ...]
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        age = func.extract("epoch", func.now() - start_col)
        stmt = select(
            func.count(),
            func.percentile_cont(0.5).within_group(age),
            func.max(age),
        ).where(AcceptCard.status.in_(status_in))
        n, p50, mx = (await self._session.execute(stmt)).one()
        return {
            "count": int(n or 0),
            "p50_seconds": None if p50 is None else float(p50),
            "max_seconds": None if mx is None else float(mx),
            "measured_at": now.isoformat(),
        }

    async def _accepted_not_archived(self) -> dict[str, Any]:
        """采纳了、还没归档的房间有多少，最老的等了多久。

        `accepted_at` 与 `archived_at` **互不牵连**（#442）：
        一个房间可以采纳很多次，
        归档是另一件人工的事。所以这两段时间不能拼成一条「自动流水线」。
        """
        stmt = select(
            func.count(),
            func.max(func.extract("epoch", func.now() - Topic.accepted_at)),
        ).where(
            Topic.accepted_at.is_not(None),
            Topic.archived_at.is_(None),
            Topic.status != TopicStatus.archived,
        )
        n, mx = (await self._session.execute(stmt)).one()
        return {"count": int(n or 0), "max_seconds": None if mx is None else float(mx)}
