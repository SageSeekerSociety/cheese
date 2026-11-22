"""学情分析的全班一侧（§ learning analysis, step 3）：读的是第 2 步写下的行，写的是这一
步加的四张表（问题簇、误解、讲课建议、按周快照）与导出记录。

读法都是**按空间聚合**：老师只看全班整体，所以这里没有一个查询以「某个成员」为条件
—— 成员 id 只在把同一个人数成一个人时出现（``count(distinct user_id)``），以及代号
要用它算。每个读法一条语句，读整页要的几样东西各一次，不随成员数或知识点数涨。

对话的读法与 ``learning_insight_repositories`` 同一份口径（``member_words`` /
``readable_rooms``）：成员自己在本板题目项目里的发言，私聊与仅成员可见的频道不读。
不 import 别的领域的 repository（``tests/unit/test_domain_import_guard.py``），要的
行直接从各自的 model 读，和 ``learning_service`` 一样。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import Integer, cast, distinct, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import Block
from app.domain.conversation.services import room_column
from app.domain.project.models import Project
from app.domain.space.learning_service import member_words, readable_rooms
from app.domain.space.models import (
    SpaceKnowledgePoint,
    SpaceLearningAdvice,
    SpaceLearningCluster,
    SpaceLearningCursor,
    SpaceLearningExport,
    SpaceLearningJudgment,
    SpaceLearningMisconception,
    SpaceLearningQuestion,
    SpaceLearningWeek,
    SpaceMember,
)
from app.domain.task import judge_stats
from app.domain.task.models import (
    Task,
    TaskKnowledgePoint,
    TaskMembership,
    TaskSubmission,
)
from app.domain.task.submission_state import (
    COMPLETION_STATUS_PENDING_REVIEW,
    COMPLETION_STATUS_SUCCESS,
)
from app.domain.topic.models import Topic

#: 「卡住」的两档。
STUCK_LEVELS = ("shaky", "weak")


@dataclass(frozen=True)
class JudgmentRow:
    user_id: int
    point_id: int
    level: str
    misconception_id: int | None
    disputed: bool
    evidence: list[uuid.UUID]
    created_at: datetime


@dataclass(frozen=True)
class ClusterStat:
    cluster_id: int
    label: str
    point_id: int | None
    people: int
    asks: int
    people_this_week: int
    people_last_week: int
    first_asked: datetime


@dataclass(frozen=True)
class AskedBlock:
    """One filed question as an excerpt source: whose, which 题目, which message."""

    user_id: int
    task_id: int
    block_id: uuid.UUID
    asked_at: datetime
    group_id: int


@dataclass(frozen=True)
class Spoken:
    """One member message as the word counts read it."""

    author: str
    task_id: int
    content: str
    created_at: datetime


@dataclass(frozen=True)
class TaskClaims:
    claims: int
    in_progress: int
    submitted: int
    passed: int
    avg_hours_to_submit: float | None


@dataclass(frozen=True)
class ExcerptBlock:
    block_id: uuid.UUID
    conversation_id: uuid.UUID
    topic_id: uuid.UUID
    project_id: uuid.UUID
    task_id: int | None
    task_name: str
    author: str
    content: str
    created_at: datetime


class LearningClassReads:
    """全班一侧的读法。每个方法一条语句。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- 人 ---------------------------------------------------------------

    async def member_ids(self, space_id: int) -> set[int]:
        """这块板现在的成员（不含管理员）——「纳入分析的成员」就是他们。"""
        rows = await self._session.scalars(
            select(SpaceMember.user_id).where(
                SpaceMember.space_id == space_id, SpaceMember.deleted_at.is_(None)
            )
        )
        return {int(uid) for uid in rows.all()}

    # --- 判断与提问 -------------------------------------------------------

    async def judgments(self, space_id: int) -> list[JudgmentRow]:
        """这块板在 live 知识点上的全部当前判断。"""
        rows = await self._session.execute(
            select(
                SpaceLearningJudgment.user_id,
                SpaceLearningJudgment.knowledge_point_id,
                SpaceLearningJudgment.level,
                SpaceLearningJudgment.misconception_id,
                SpaceLearningJudgment.disputed_at,
                SpaceLearningJudgment.evidence_block_ids,
                SpaceLearningJudgment.created_at,
            )
            .join(
                SpaceKnowledgePoint,
                SpaceKnowledgePoint.id == SpaceLearningJudgment.knowledge_point_id,
            )
            .where(
                SpaceLearningJudgment.space_id == space_id,
                SpaceKnowledgePoint.deleted_at.is_(None),
            )
        )
        return [
            JudgmentRow(
                user_id=int(uid),
                point_id=int(pid),
                level=str(level),
                misconception_id=int(mid) if mid is not None else None,
                disputed=disputed is not None,
                evidence=list(evidence or []),
                created_at=created,
            )
            for uid, pid, level, mid, disputed, evidence, created in rows.all()
        ]

    async def askers_by_point(self, space_id: int) -> dict[int, set[int]]:
        """每个知识点下提过问的不同成员。"""
        rows = await self._session.execute(
            select(
                SpaceLearningQuestion.knowledge_point_id, SpaceLearningQuestion.user_id
            )
            .where(
                SpaceLearningQuestion.space_id == space_id,
                SpaceLearningQuestion.knowledge_point_id.is_not(None),
            )
            .distinct()
        )
        out: dict[int, set[int]] = {}
        for pid, uid in rows.all():
            out.setdefault(int(pid), set()).add(int(uid))
        return out

    async def question_totals(self, space_id: int) -> tuple[int, int]:
        """归过类的提问一共几条、出自几个人。"""
        row = (
            await self._session.execute(
                select(
                    func.count(SpaceLearningQuestion.id),
                    func.count(distinct(SpaceLearningQuestion.user_id)),
                ).where(SpaceLearningQuestion.space_id == space_id)
            )
        ).one()
        return int(row[0] or 0), int(row[1] or 0)

    async def cluster_stats(
        self, space_id: int, *, this_week: datetime, last_week: datetime
    ) -> list[ClusterStat]:
        """每一簇：问过的人数、次数，本周与上周各有几个人问过。"""
        q = SpaceLearningQuestion
        rows = await self._session.execute(
            select(
                SpaceLearningCluster.id,
                SpaceLearningCluster.label,
                SpaceLearningCluster.knowledge_point_id,
                func.count(distinct(q.user_id)),
                func.count(q.id),
                func.count(distinct(q.user_id)).filter(q.asked_at >= this_week),
                func.count(distinct(q.user_id)).filter(
                    q.asked_at >= last_week, q.asked_at < this_week
                ),
                func.min(q.asked_at),
            )
            .join(q, q.cluster_id == SpaceLearningCluster.id)
            .where(SpaceLearningCluster.space_id == space_id, q.space_id == space_id)
            .group_by(
                SpaceLearningCluster.id,
                SpaceLearningCluster.label,
                SpaceLearningCluster.knowledge_point_id,
            )
        )
        return [
            ClusterStat(
                cluster_id=int(cid),
                label=str(label),
                point_id=int(pid) if pid is not None else None,
                people=int(people),
                asks=int(asks),
                people_this_week=int(this or 0),
                people_last_week=int(last or 0),
                first_asked=first,
            )
            for cid, label, pid, people, asks, this, last, first in rows.all()
        ]

    async def cluster_count(self, space_id: int) -> int:
        return int(
            await self._session.scalar(
                select(func.count(SpaceLearningCluster.id)).where(
                    SpaceLearningCluster.space_id == space_id
                )
            )
            or 0
        )

    async def asked_in_clusters(
        self, space_id: int, cluster_ids: Sequence[int], *, limit: int
    ) -> list[AskedBlock]:
        """这几簇里最近的提问（新的在前），选例句用。"""
        if not cluster_ids:
            return []
        q = SpaceLearningQuestion
        rows = await self._session.execute(
            select(q.user_id, q.task_id, q.block_id, q.asked_at, q.cluster_id)
            .where(q.space_id == space_id, q.cluster_id.in_(list(cluster_ids)))
            .order_by(q.asked_at.desc())
            .limit(limit)
        )
        return [
            AskedBlock(
                user_id=int(uid),
                task_id=int(tid),
                block_id=bid,
                asked_at=at,
                group_id=int(cid),
            )
            for uid, tid, bid, at, cid in rows.all()
        ]

    async def kind_counts(self, space_id: int) -> dict[str, tuple[int, int]]:
        """每种提问类型：几条、几个人。"""
        q = SpaceLearningQuestion
        rows = await self._session.execute(
            select(q.kind, func.count(q.id), func.count(distinct(q.user_id)))
            .where(q.space_id == space_id)
            .group_by(q.kind)
        )
        return {str(k): (int(n), int(p)) for k, n, p in rows.all()}

    async def heat(
        self, space_id: int, zone: str
    ) -> list[tuple[int, int, int, list[int]]]:
        """(星期 1–7, 小时, 条数, 人数)，按 ``zone`` 的钟点。"""
        q = SpaceLearningQuestion
        local = func.timezone(zone, q.asked_at)
        dow = cast(func.extract("isodow", local), Integer)
        hour = cast(func.extract("hour", local), Integer)
        rows = await self._session.execute(
            select(dow, hour, func.count(q.id), func.array_agg(distinct(q.user_id)))
            .where(q.space_id == space_id)
            .group_by(dow, hour)
        )
        return [
            (int(d), int(h), int(n), [int(u) for u in uids or []])
            for d, h, n, uids in rows.all()
        ]

    async def answer_share_by_person(
        self, space_id: int
    ) -> list[tuple[int, int | None, int, int]]:
        """(成员, 题目, 提问条数, 其中「要答案」条数)；题目为 None 的那一组是全板合计。"""
        q = SpaceLearningQuestion
        answers = func.count(q.id).filter(q.kind == "answer")
        per_task = await self._session.execute(
            select(q.user_id, q.task_id, func.count(q.id), answers)
            .where(q.space_id == space_id)
            .group_by(q.user_id, q.task_id)
        )
        out: list[tuple[int, int | None, int, int]] = []
        totals: dict[int, list[int]] = {}
        for uid, tid, n, a in per_task.all():
            out.append((int(uid), int(tid), int(n), int(a or 0)))
            bucket = totals.setdefault(int(uid), [0, 0])
            bucket[0] += int(n)
            bucket[1] += int(a or 0)
        for uid, (n, a) in totals.items():
            out.append((uid, None, n, a))
        return out

    # --- 题目 -------------------------------------------------------------

    async def tasks(self, space_id: int) -> list[tuple[int, str]]:
        """这块板上还在的题，按发布先后。"""
        rows = await self._session.execute(
            select(Task.id, Task.name)
            .where(Task.space_id == space_id, Task.deleted_at.is_(None))
            .order_by(Task.created_at.asc(), Task.id.asc())
        )
        return [(int(tid), str(name)) for tid, name in rows.all()]

    async def task_descriptions(self, space_id: int) -> list[str]:
        rows = await self._session.scalars(
            select(Task.description).where(
                Task.space_id == space_id, Task.deleted_at.is_(None)
            )
        )
        return [str(d or "") for d in rows.all()]

    async def test_pass_rates(
        self, task_ids: Sequence[int]
    ) -> dict[int, tuple[float, int]]:
        """几道题各自的测试平均通过率和读得出分的份数（``judge_stats`` 那一份口径，
        只有全班的数）。"""
        return await judge_stats.pass_rates_of_tasks(self._session, task_ids)

    async def task_points(self, space_id: int) -> dict[int, set[int]]:
        """每道题挂的 live 知识点。"""
        rows = await self._session.execute(
            select(TaskKnowledgePoint.task_id, TaskKnowledgePoint.knowledge_point_id)
            .join(Task, Task.id == TaskKnowledgePoint.task_id)
            .join(
                SpaceKnowledgePoint,
                SpaceKnowledgePoint.id == TaskKnowledgePoint.knowledge_point_id,
            )
            .where(
                Task.space_id == space_id,
                Task.deleted_at.is_(None),
                SpaceKnowledgePoint.deleted_at.is_(None),
            )
        )
        out: dict[int, set[int]] = {}
        for tid, pid in rows.all():
            out.setdefault(int(tid), set()).add(int(pid))
        return out

    async def task_claims(self, space_id: int) -> dict[int, TaskClaims]:
        """每道题的领取：几条、各在哪一档，领题到第一次提交平均几小时。"""
        m = TaskMembership
        first_submit = (
            select(
                TaskSubmission.membership_id.label("mid"),
                func.min(TaskSubmission.created_at).label("first_at"),
            )
            .where(TaskSubmission.deleted_at.is_(None))
            .group_by(TaskSubmission.membership_id)
            .subquery()
        )
        hours = func.extract("epoch", first_submit.c.first_at - m.created_at) / 3600.0
        rows = await self._session.execute(
            select(
                m.task_id,
                func.count(m.id),
                func.count(m.id).filter(
                    m.completion_status == COMPLETION_STATUS_PENDING_REVIEW
                ),
                func.count(m.id).filter(
                    m.completion_status == COMPLETION_STATUS_SUCCESS
                ),
                func.avg(hours),
            )
            .join(Task, Task.id == m.task_id)
            .outerjoin(first_submit, first_submit.c.mid == m.id)
            .where(Task.space_id == space_id, Task.deleted_at.is_(None), m.live_claim)
            .group_by(m.task_id)
        )
        out: dict[int, TaskClaims] = {}
        for tid, claims, submitted, passed, avg in rows.all():
            claims, submitted, passed = int(claims), int(submitted), int(passed)
            out[int(tid)] = TaskClaims(
                claims=claims,
                in_progress=claims - submitted - passed,
                submitted=submitted,
                passed=passed,
                avg_hours_to_submit=float(avg) if avg is not None else None,
            )
        return out

    async def cursors_by_task(self, space_id: int) -> dict[int, list[tuple[int, int]]]:
        """每道题：(成员, 读过他几条发言)。"""
        rows = await self._session.execute(
            select(
                SpaceLearningCursor.task_id,
                SpaceLearningCursor.user_id,
                SpaceLearningCursor.messages_read,
            ).where(SpaceLearningCursor.space_id == space_id)
        )
        out: dict[int, list[tuple[int, int]]] = {}
        for tid, uid, n in rows.all():
            out.setdefault(int(tid), []).append((int(uid), int(n)))
        return out

    # --- 对话 -------------------------------------------------------------

    def _spoken_stmt(self, space_id: int):
        return (
            select(
                Block.author, Project.external_task_id, Block.content, Block.created_at
            )
            .select_from(Block)
            .join(Topic, Topic.id == room_column(Block.conversation_id))
            .join(Project, Project.id == Block.project_id)
            .join(Task, Task.id == Project.external_task_id)
            .where(
                Task.space_id == space_id,
                Task.deleted_at.is_(None),
                *member_words(),
                *readable_rooms(),
            )
        )

    async def spoken(
        self, space_id: int, *, since: datetime, limit: int
    ) -> list[Spoken]:
        """成员在本板题目里说过的话，``since`` 之后，新的在前，至多 ``limit`` 条。"""
        rows = await self._session.execute(
            self._spoken_stmt(space_id)
            .where(Block.created_at >= since)
            .order_by(Block.created_at.desc())
            .limit(limit)
        )
        return [
            Spoken(author=str(a), task_id=int(t), content=str(c or ""), created_at=at)
            for a, t, c, at in rows.all()
        ]

    async def spoken_count(self, space_id: int, *, since: datetime) -> int:
        stmt = self._spoken_stmt(space_id).where(Block.created_at >= since).subquery()
        return int(
            await self._session.scalar(select(func.count()).select_from(stmt)) or 0
        )

    async def excerpt_blocks(
        self, block_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, ExcerptBlock]:
        """这些消息本身，带上它的房间、项目和题目 —— 给摘录用，不论是谁说的。"""
        if not block_ids:
            return {}
        rows = await self._session.execute(
            select(Block, room_column(Block.conversation_id), Task.id, Task.name)
            .join(Project, Project.id == Block.project_id)
            .outerjoin(Task, Task.id == Project.external_task_id)
            .where(Block.id.in_(list(block_ids)), *member_words())
        )
        out: dict[uuid.UUID, ExcerptBlock] = {}
        for block, room, task_id, task_name in rows.all():
            out[block.id] = ExcerptBlock(
                block_id=block.id,
                conversation_id=block.conversation_id,
                topic_id=room,
                project_id=block.project_id,
                task_id=int(task_id) if task_id is not None else None,
                task_name=str(task_name or ""),
                author=block.author,
                content=block.content or "",
                created_at=block.created_at,
            )
        return out

    # --- 这一步自己的行 ---------------------------------------------------

    async def misconception_labels(self, space_id: int) -> dict[int, tuple[int, str]]:
        """误解 id → (知识点, 那句话)。"""
        rows = await self._session.execute(
            select(
                SpaceLearningMisconception.id,
                SpaceLearningMisconception.knowledge_point_id,
                SpaceLearningMisconception.label,
            ).where(SpaceLearningMisconception.space_id == space_id)
        )
        return {int(i): (int(p), str(label)) for i, p, label in rows.all()}

    async def advice(self, space_id: int) -> dict[int, SpaceLearningAdvice]:
        rows = await self._session.scalars(
            select(SpaceLearningAdvice)
            .where(SpaceLearningAdvice.space_id == space_id)
            .execution_options(populate_existing=True)
        )
        return {row.knowledge_point_id: row for row in rows.all()}

    async def weeks(self, space_id: int, *, since: date) -> list[SpaceLearningWeek]:
        rows = await self._session.scalars(
            select(SpaceLearningWeek)
            .where(
                SpaceLearningWeek.space_id == space_id,
                SpaceLearningWeek.week_start >= since,
            )
            .order_by(SpaceLearningWeek.week_start.asc())
        )
        return list(rows.all())


class LearningClassWrites:
    """学情分析在这一步加的写，以及导出记录。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- 问题簇 -----------------------------------------------------------

    async def unclustered(
        self, space_id: int, *, limit: int
    ) -> list[tuple[int, str, int | None]]:
        """还没归进簇的提问（有概括的那些），旧的在前：(id, 概括, 知识点)。"""
        q = SpaceLearningQuestion
        rows = await self._session.execute(
            select(q.id, q.gist, q.knowledge_point_id)
            .where(q.space_id == space_id, q.cluster_id.is_(None), q.gist != "")
            .order_by(q.asked_at.asc(), q.id.asc())
            .limit(limit)
        )
        return [
            (int(i), str(g), int(p) if p is not None else None)
            for i, g, p in rows.all()
        ]

    async def askers_count(self, space_id: int) -> int:
        return int(
            await self._session.scalar(
                select(func.count(distinct(SpaceLearningQuestion.user_id))).where(
                    SpaceLearningQuestion.space_id == space_id
                )
            )
            or 0
        )

    async def clusters_for_prompt(
        self, space_id: int, *, limit: int
    ) -> list[tuple[int, str, int | None]]:
        """问的人最多的那些簇：(id, 代表性问题, 知识点)。"""
        q = SpaceLearningQuestion
        rows = await self._session.execute(
            select(
                SpaceLearningCluster.id,
                SpaceLearningCluster.label,
                SpaceLearningCluster.knowledge_point_id,
            )
            .outerjoin(q, q.cluster_id == SpaceLearningCluster.id)
            .where(SpaceLearningCluster.space_id == space_id)
            .group_by(SpaceLearningCluster.id)
            .order_by(func.count(distinct(q.user_id)).desc(), SpaceLearningCluster.id)
            .limit(limit)
        )
        return [
            (int(i), str(label), int(p) if p is not None else None)
            for i, label, p in rows.all()
        ]

    async def new_cluster(
        self, *, space_id: int, label: str, point_id: int | None, now: datetime
    ) -> int:
        row = SpaceLearningCluster(
            space_id=space_id, knowledge_point_id=point_id, label=label, created_at=now
        )
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def assign(
        self, *, space_id: int, cluster_id: int, ids: Sequence[int]
    ) -> None:
        if not ids:
            return
        await self._session.execute(
            update(SpaceLearningQuestion)
            .where(
                SpaceLearningQuestion.space_id == space_id,
                SpaceLearningQuestion.id.in_(list(ids)),
            )
            .values(cluster_id=cluster_id)
        )

    # --- 误解 -------------------------------------------------------------

    async def misconceptions_for(
        self, space_id: int, point_ids: Sequence[int], *, per_point: int
    ) -> dict[int, list[str]]:
        """这几个知识点已有的误解，按指向它的人数排，每个至多 ``per_point`` 条。"""
        if not point_ids:
            return {}
        j = SpaceLearningJudgment
        rows = await self._session.execute(
            select(
                SpaceLearningMisconception.knowledge_point_id,
                SpaceLearningMisconception.label,
                func.count(distinct(j.user_id)),
            )
            .outerjoin(j, j.misconception_id == SpaceLearningMisconception.id)
            .where(
                SpaceLearningMisconception.space_id == space_id,
                SpaceLearningMisconception.knowledge_point_id.in_(list(point_ids)),
            )
            .group_by(
                SpaceLearningMisconception.knowledge_point_id,
                SpaceLearningMisconception.label,
            )
            .order_by(func.count(distinct(j.user_id)).desc())
        )
        out: dict[int, list[str]] = {}
        for pid, label, _people in rows.all():
            bucket = out.setdefault(int(pid), [])
            if len(bucket) < per_point:
                bucket.append(str(label))
        return out

    async def misconception_id(
        self, *, space_id: int, point_id: int, label: str, now: datetime
    ) -> int:
        """这个知识点下这句误解的 id；还没有就建一行（同一句话只有一行）。"""
        stmt = pg_insert(SpaceLearningMisconception).values(
            space_id=space_id, knowledge_point_id=point_id, label=label, created_at=now
        )
        await self._session.execute(
            stmt.on_conflict_do_nothing(index_elements=["knowledge_point_id", "label"])
        )
        found = await self._session.scalar(
            select(SpaceLearningMisconception.id).where(
                SpaceLearningMisconception.knowledge_point_id == point_id,
                SpaceLearningMisconception.label == label,
            )
        )
        assert found is not None
        return int(found)

    # --- 讲课建议 ---------------------------------------------------------

    async def write_advice(
        self,
        *,
        space_id: int,
        point_id: int,
        advice: str,
        stuck_count: int,
        misconception_ids: list[int],
        now: datetime,
    ) -> None:
        stmt = pg_insert(SpaceLearningAdvice).values(
            space_id=space_id,
            knowledge_point_id=point_id,
            advice=advice,
            stuck_count=stuck_count,
            misconception_ids=misconception_ids,
            generated_at=now,
            created_at=now,
            updated_at=now,
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=["knowledge_point_id"],
                set_={
                    "advice": stmt.excluded.advice,
                    "stuck_count": stmt.excluded.stuck_count,
                    "misconception_ids": stmt.excluded.misconception_ids,
                    "generated_at": stmt.excluded.generated_at,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        )

    # --- 按周快照 ---------------------------------------------------------

    async def write_week(
        self,
        *,
        space_id: int,
        week_start: date,
        counts: dict[int, tuple[int, int]],
        now: datetime,
    ) -> None:
        """这一周每个知识点的（卡住人数, 涉及人数）；同一周再写一次覆盖。"""
        for point_id, (stuck, involved) in counts.items():
            stmt = pg_insert(SpaceLearningWeek).values(
                space_id=space_id,
                knowledge_point_id=point_id,
                week_start=week_start,
                stuck_count=stuck,
                involved_count=involved,
                created_at=now,
                updated_at=now,
            )
            await self._session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["space_id", "knowledge_point_id", "week_start"],
                    set_={
                        "stuck_count": stmt.excluded.stuck_count,
                        "involved_count": stmt.excluded.involved_count,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
            )

    # --- 导出记录 ---------------------------------------------------------

    async def record_export(
        self,
        *,
        space_id: int,
        user_id: int,
        format: str,
        sections: list[str],
        point_id: int | None,
    ) -> None:
        self._session.add(
            SpaceLearningExport(
                space_id=space_id,
                user_id=user_id,
                format=format,
                sections=sections,
                knowledge_point_id=point_id,
                created_at=datetime.now(UTC),
            )
        )
        await self._session.flush()
