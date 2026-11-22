"""「数据 › 学情分析」：老师看全班整体（§ learning analysis, step 3）。

七个页签各一个方法 —— 总览、重难点、高频问题、高频词、题目难度、提问行为、趋势 ——
外加知识点详情、匿名对话摘录与导出报告要的那份整体数据。读的全是学情分析写下的行
（``learning_insight_analysis`` / ``learning_class_analysis``），这里不调模型。

三条规则在这一层成立，一条都不交给界面：

- **只有整体**：没有一个方法收「看谁」，返回值里没有成员 id、handle 或名字。要引用原
  话的地方（例句、摘录）只标本空间固定代号（``learning_privacy.member_code``），原话
  先遮掉姓名、学号、手机号、邮箱（``learning_privacy.Masker``）。
- **不足 3 人，不分析**：任何一组 —— 一个知识点、一簇问题、一种误解、一道题、一个
  词、一个时段 —— 不到 ``MIN_PEOPLE`` 个不同的人，就只报 ``analysed: false``，人数、
  内容一概不给。
- **只统计开了学情分析的板**：没开的板每个方法都答 ``enabled: false`` 与空内容。

「查看原文」的入口照旧只给读得了那个项目的人（``may_read_project``，导师）：摘录里的
``source`` 只在看的人能读那个项目时才有，否则为空 —— 权限不因这一步改变。
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.project_access import may_read_project
from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.space.learning_class_analysis import (
    MIN_PEOPLE,
    PointCounts,
    point_counts,
    week_start,
)
from app.domain.space.learning_class_repositories import (
    STUCK_LEVELS,
    ExcerptBlock,
    JudgmentRow,
    LearningClassReads,
    LearningClassWrites,
)
from app.domain.space.learning_insight_analysis import LOCAL_ZONE
from app.domain.space.learning_insight_repositories import LearningConversationReads
from app.domain.space.learning_practice_service import SpaceLearningPracticeService
from app.domain.space.learning_privacy import Masker, masker_for, member_code
from app.domain.space.learning_service import _excerpt
from app.domain.space.models import SpaceKnowledgePoint
from app.domain.space.repositories import SpaceKnowledgePointRepository

#: 「重难点」的两条分界的缺省值：问到的人 ≥ 成员数的 60% 算「重点」，涉及的人里卡住
#: ≥ 55% 算「难点」。页面上可调（查询参数），不存。人数门槛按比例而不是固定 25
#: 人：小班 20 人时 25 人的门槛永远够不着。
DEFAULT_ASKED_RATIO = 0.6
DEFAULT_STUCK_RATIO = 0.55
#: 「高频问题」最多列几类；「下节课值得讲的」几个；摘录最多几段；每个误解的例句几条。
CLUSTER_LIMIT = 30
FOCUS_LIMIT = 3
EXCERPT_LIMIT = 3
EXAMPLE_LIMIT = 2
#: 高频词读最近几周的话、至多几条；列几个词、几条报错。
WORD_WEEKS = 4
WORD_MESSAGE_LIMIT = 8000
WORD_LIMIT = 40
ERROR_LIMIT = 10
#: 「代写迹象」：一个人在一道题里至少问了几条、其中「要答案」占多少。
ANSWER_SEEKER_MIN_ASKS = 2
ANSWER_SEEKER_SHARE = 0.5
#: 依赖程度三档的分界（「要答案」占自己提问的比例）。
DEPENDENCY_LOW = 0.1
DEPENDENCY_HIGH = 0.3
#: 「什么时候问」的五个时段（本地钟点，左闭右开）。
HEAT_SLOTS = ((0, 8), (8, 12), (12, 18), (18, 22), (22, 24))
#: 趋势看最近几周。
TREND_WEEKS = 8


def _ms(moment: datetime | None) -> int | None:
    return int(moment.timestamp() * 1000) if moment is not None else None


def _enabled(space) -> bool:
    return bool(getattr(space, "learning_analysis_enabled", False))


@dataclass
class _Board:
    """一次请求里读一次就够的东西：清单、成员、判断、各知识点的人。"""

    points: list[SpaceKnowledgePoint]
    members: set[int]
    judgments: list[JudgmentRow]
    askers: dict[int, set[int]]
    counts: dict[int, PointCounts]

    @property
    def names(self) -> dict[int, str]:
        return {p.id: p.name for p in self.points}


class SpaceLearningClassService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._reads = LearningClassReads(session)

    @classmethod
    def of(cls, session: AsyncSession) -> SpaceLearningClassService:
        return cls(session)

    async def _board(self, space_id: int) -> _Board:
        points = list(
            await SpaceKnowledgePointRepository(self._session).list_for_space(space_id)
        )
        judgments = await self._reads.judgments(space_id)
        askers = await self._reads.askers_by_point(space_id)
        return _Board(
            points=points,
            members=await self._reads.member_ids(space_id),
            judgments=judgments,
            askers=askers,
            counts=point_counts(judgments, askers),
        )

    async def _masker(self, space_id: int) -> Masker:
        return await masker_for(self._session, space_id)

    # --- 重难点（总览也用） -------------------------------------------------

    @staticmethod
    def _point_rows(
        board: _Board, *, asked_ratio: float, stuck_ratio: float
    ) -> list[dict]:
        members = len(board.members)
        threshold = asked_ratio * members
        rows = []
        for point in board.points:
            counts = board.counts.get(point.id)
            involved = len(counts.involved) if counts else 0
            if involved < MIN_PEOPLE or counts is None:
                rows.append(
                    {
                        "id": point.id,
                        "name": point.name,
                        "analysed": False,
                        "asked": None,
                        "involved": None,
                        "stuck": None,
                        "stuckRate": None,
                        "quadrant": None,
                    }
                )
                continue
            asked = len(board.askers.get(point.id, set()))
            stuck = len(counts.stuck)
            rate = stuck / involved
            focus = members > 0 and asked >= threshold
            hard = rate >= stuck_ratio
            rows.append(
                {
                    "id": point.id,
                    "name": point.name,
                    "analysed": True,
                    "asked": asked,
                    "involved": involved,
                    "stuck": stuck,
                    "stuckRate": round(rate, 4),
                    "quadrant": (
                        "both"
                        if focus and hard
                        else "hard"
                        if hard
                        else "focus"
                        if focus
                        else "normal"
                    ),
                }
            )
        return rows

    async def points(
        self, space, *, asked_ratio: float | None, stuck_ratio: float | None
    ) -> dict:
        """重难点：每个知识点问到几人、涉及几人、卡住几人、卡住率、落在哪一格。"""
        asked_ratio = _ratio(asked_ratio, DEFAULT_ASKED_RATIO)
        stuck_ratio = _ratio(stuck_ratio, DEFAULT_STUCK_RATIO)
        base = {
            "enabled": _enabled(space),
            "minPeople": MIN_PEOPLE,
            "askedRatio": asked_ratio,
            "stuckRatio": stuck_ratio,
        }
        if not _enabled(space):
            return {**base, "members": 0, "askedThreshold": 0, "points": []}
        board = await self._board(space.id)
        return {
            **base,
            "members": len(board.members),
            "askedThreshold": _ceil(asked_ratio * len(board.members)),
            "points": self._point_rows(
                board, asked_ratio=asked_ratio, stuck_ratio=stuck_ratio
            ),
        }

    # --- 总览 ---------------------------------------------------------------

    async def overview(self, space) -> dict:
        """总览：完成率、纳入的成员、本周的对话、较弱的知识点；按题的完成分布；
        下节课值得讲的（卡住人数最多的三个知识点）。"""
        if not _enabled(space):
            return {
                "enabled": False,
                "minPeople": MIN_PEOPLE,
                "updatedAt": None,
                "members": 0,
                "completion": {"rate": None, "submitted": 0, "expected": 0},
                "messagesThisWeek": 0,
                "weakPoints": 0,
                "tasks": [],
                "focus": [],
            }
        board = await self._board(space.id)
        members = len(board.members)
        tasks = await self._reads.tasks(space.id)
        claims = await self._reads.task_claims(space.id)
        task_rows = []
        handed_in = 0
        for task_id, name in tasks:
            c = claims.get(task_id)
            done = min((c.submitted + c.passed) if c else 0, members)
            handed_in += done
            if c is None or c.claims < MIN_PEOPLE:
                task_rows.append(
                    {
                        "taskId": task_id,
                        "name": name,
                        "analysed": False,
                        "notStarted": None,
                        "inProgress": None,
                        "submitted": None,
                        "passed": None,
                    }
                )
                continue
            task_rows.append(
                {
                    "taskId": task_id,
                    "name": name,
                    "analysed": True,
                    "notStarted": max(members - c.claims, 0),
                    "inProgress": c.in_progress,
                    "submitted": c.submitted,
                    "passed": c.passed,
                }
            )
        expected = members * len(tasks)
        rows = self._point_rows(
            board, asked_ratio=DEFAULT_ASKED_RATIO, stuck_ratio=DEFAULT_STUCK_RATIO
        )
        focus = sorted(
            (r for r in rows if r["analysed"] and r["stuck"]),
            key=lambda r: (-r["stuck"], r["id"]),
        )[:FOCUS_LIMIT]
        now = datetime.now(UTC)
        updated = [row.created_at for row in board.judgments]
        return {
            "enabled": True,
            "minPeople": MIN_PEOPLE,
            "updatedAt": _ms(max(updated)) if updated else None,
            "members": members,
            "completion": {
                "rate": round(handed_in / expected, 4) if expected else None,
                "submitted": handed_in,
                "expected": expected,
            },
            "messagesThisWeek": await self._reads.spoken_count(
                space.id, since=_since_enabled(space, now - timedelta(days=7))
            ),
            "weakPoints": sum(1 for r in rows if r["quadrant"] == "both"),
            "tasks": task_rows,
            "focus": [
                {
                    "knowledgePointId": r["id"],
                    "name": r["name"],
                    "stuck": r["stuck"],
                    "involved": r["involved"],
                }
                for r in focus
            ],
        }

    # --- 知识点详情 ---------------------------------------------------------

    async def point_detail(self, space, point_id: int) -> dict:
        """一个知识点：卡住几人、常见误解（按人数）与代号例句、掌握分布、讲课建议。
        不列名单。"""
        point = await SpaceKnowledgePointRepository(self._session).get(
            space_id=space.id, point_id=point_id
        )
        if point is None:
            raise NotFoundError(
                say("learningPointNotFound"),
                data={"spaceId": space.id, "knowledgePointId": point_id},
            )
        base = {
            "enabled": _enabled(space),
            "minPeople": MIN_PEOPLE,
            "knowledgePoint": {"id": point.id, "name": point.name},
        }
        empty = {
            **base,
            "analysed": False,
            "members": 0,
            "involved": None,
            "stuck": None,
            "newThisWeek": None,
            "tasks": [],
            "mastery": None,
            "misconceptions": [],
            "hiddenMisconceptions": 0,
            "advice": None,
            "practice": None,
            "lastPush": None,
        }
        if not _enabled(space):
            return empty
        # 巩固题的整体数与最近一次推送（§ step 4）：不足 3 人时 practice 为空。
        empty.update(
            await SpaceLearningPracticeService(self._session).point_outcome(
                space.id, point.id
            )
        )
        board = await self._board(space.id)
        tasks = dict(await self._reads.tasks(space.id))
        task_points = await self._reads.task_points(space.id)
        linked = [
            tasks[tid]
            for tid, pids in task_points.items()
            if point.id in pids and tid in tasks
        ]
        counts = board.counts.get(point.id)
        empty["members"] = len(board.members)
        empty["tasks"] = linked
        if counts is None or len(counts.involved) < MIN_PEOPLE:
            return empty
        week_ago = datetime.now(UTC) - timedelta(days=7)
        on_point = [row for row in board.judgments if row.point_id == point.id]
        levels = Counter(row.level for row in on_point if not row.disputed)
        judged = {row.user_id for row in on_point}
        labels = await self._reads.misconception_labels(space.id)
        masker = await self._masker(space.id)
        shown = sorted(
            (
                (mid, people)
                for mid, people in counts.misconceptions.items()
                if len(people) >= MIN_PEOPLE and mid in labels
            ),
            key=lambda item: (-len(item[1]), item[0]),
        )
        hidden = sum(
            1 for people in counts.misconceptions.values() if len(people) < MIN_PEOPLE
        )
        evidence_of: dict[int, list[tuple[int, uuid.UUID]]] = {}
        for row in on_point:
            if row.misconception_id is None or row.disputed:
                continue
            if row.level not in STUCK_LEVELS or not row.evidence:
                continue
            evidence_of.setdefault(row.misconception_id, []).append(
                (row.user_id, row.evidence[0])
            )
        wanted = [
            bid
            for mid, _ in shown
            for _uid, bid in evidence_of.get(mid, [])[:EXAMPLE_LIMIT]
        ]
        blocks = await self._reads.excerpt_blocks(wanted)
        misconceptions = []
        for mid, people in shown:
            examples = []
            for uid, bid in evidence_of.get(mid, [])[:EXAMPLE_LIMIT]:
                block = blocks.get(bid)
                if block is None:
                    continue
                examples.append(
                    {
                        "code": member_code(space.id, uid),
                        "quote": _excerpt(masker.mask(block.content)),
                        "taskName": block.task_name,
                    }
                )
            misconceptions.append(
                {
                    "id": mid,
                    # 模型归纳的话，提示里不让写人名；照样遮一遍，宁可多遮。
                    "label": masker.mask(labels[mid][1]),
                    "people": len(people),
                    "examples": examples,
                }
            )
        advice = (await self._reads.advice(space.id)).get(point.id)
        return {
            **base,
            "analysed": True,
            "members": len(board.members),
            "involved": len(counts.involved),
            "stuck": len(counts.stuck),
            "newThisWeek": sum(
                1
                for row in on_point
                if row.level in STUCK_LEVELS
                and not row.disputed
                and row.created_at >= week_ago
            ),
            "tasks": linked,
            "mastery": {
                "mastered": levels.get("mastered", 0),
                "shaky": levels.get("shaky", 0),
                "weak": levels.get("weak", 0),
                "untouched": max(len(board.members - judged), 0),
            },
            "misconceptions": misconceptions,
            "hiddenMisconceptions": hidden,
            "advice": (
                {
                    "text": masker.mask(advice.advice),
                    "generatedAt": _ms(advice.generated_at),
                }
                if advice is not None
                else None
            ),
            "practice": empty["practice"],
            "lastPush": empty["lastPush"],
        }

    # --- 高频问题 -----------------------------------------------------------

    async def questions(self, space) -> dict:
        """高频问题：AI 归好的问题类，按问过的不同人数排；和上周比的变化；一句例句。"""
        empty = {
            "enabled": _enabled(space),
            "minPeople": MIN_PEOPLE,
            "totalQuestions": 0,
            "totalClusters": 0,
            "hiddenClusters": 0,
            "clusters": [],
        }
        if not _enabled(space):
            return empty
        now = datetime.now(UTC)
        this_week, last_week = now - timedelta(days=7), now - timedelta(days=14)
        stats = await self._reads.cluster_stats(
            space.id, this_week=this_week, last_week=last_week
        )
        total, _askers = await self._reads.question_totals(space.id)
        shown = sorted(
            (s for s in stats if s.people >= MIN_PEOPLE),
            key=lambda s: (-s.people, -s.asks, s.cluster_id),
        )[:CLUSTER_LIMIT]
        names = {
            p.id: p.name
            for p in await SpaceKnowledgePointRepository(self._session).list_for_space(
                space.id
            )
        }
        recent = await self._reads.asked_in_clusters(
            space.id, [s.cluster_id for s in shown], limit=CLUSTER_LIMIT * 20
        )
        first_of: dict[int, tuple[int, uuid.UUID]] = {}
        for asked in recent:
            first_of.setdefault(asked.group_id, (asked.user_id, asked.block_id))
        blocks = await self._reads.excerpt_blocks(
            [bid for _uid, bid in first_of.values()]
        )
        masker = await self._masker(space.id)
        clusters = []
        for s in shown:
            example = None
            if s.cluster_id in first_of:
                uid, bid = first_of[s.cluster_id]
                block = blocks.get(bid)
                if block is not None:
                    example = {
                        "code": member_code(space.id, uid),
                        "quote": _excerpt(masker.mask(block.content)),
                    }
            clusters.append(
                {
                    "id": s.cluster_id,
                    "label": masker.mask(s.label),
                    "knowledgePointId": s.point_id if s.point_id in names else None,
                    "knowledgePointName": names.get(s.point_id) if s.point_id else None,
                    "people": s.people,
                    "asks": s.asks,
                    "trend": _trend(s, this_week),
                    "example": example,
                }
            )
        return {
            **empty,
            "totalQuestions": total,
            "totalClusters": await self._reads.cluster_count(space.id),
            "hiddenClusters": sum(1 for s in stats if s.people < MIN_PEOPLE),
            "clusters": clusters,
        }

    # --- 匿名对话摘录 -------------------------------------------------------

    async def excerpts(
        self,
        space,
        *,
        viewer: str | None,
        cluster_id: int | None = None,
        misconception_id: int | None = None,
    ) -> dict:
        """一簇问题、或一种误解的匿名对话摘录：至多三段、出自三个不同的人，每段是
        成员那句话和芝士紧跟着的回复。只标代号，姓名学号遮掉。"""
        base = {"enabled": _enabled(space), "minPeople": MIN_PEOPLE}
        if not _enabled(space):
            return {**base, "analysed": False, "title": "", "excerpts": []}
        picked, title = await self._excerpt_sources(
            space.id, cluster_id=cluster_id, misconception_id=misconception_id
        )
        if picked is None:
            return {**base, "analysed": False, "title": title, "excerpts": []}
        masker = await self._masker(space.id)
        return {
            **base,
            "analysed": True,
            "title": masker.mask(title),
            "excerpts": await self._render_excerpts(
                space.id, picked, masker=masker, viewer=viewer
            ),
        }

    async def _excerpt_sources(
        self,
        space_id: int,
        *,
        cluster_id: int | None,
        misconception_id: int | None,
    ) -> tuple[list[tuple[int, uuid.UUID]] | None, str]:
        """(成员, 消息) 至多三对、三个不同的人；这一组不足 3 人时为 None。"""
        if cluster_id is not None:
            stats = await self._reads.cluster_stats(
                space_id,
                this_week=datetime.now(UTC),
                last_week=datetime.now(UTC),
            )
            found = next((s for s in stats if s.cluster_id == cluster_id), None)
            if found is None:
                raise NotFoundError(
                    say("learningGroupNotFound"),
                    data={"spaceId": space_id, "clusterId": cluster_id},
                )
            if found.people < MIN_PEOPLE:
                return None, found.label
            rows = await self._reads.asked_in_clusters(
                space_id, [cluster_id], limit=200
            )
            return _distinct_people((r.user_id, r.block_id) for r in rows), found.label
        labels = await self._reads.misconception_labels(space_id)
        if misconception_id is None or misconception_id not in labels:
            raise NotFoundError(
                say("learningGroupNotFound"),
                data={"spaceId": space_id, "misconceptionId": misconception_id},
            )
        rows = [
            row
            for row in await self._reads.judgments(space_id)
            if row.misconception_id == misconception_id
            and not row.disputed
            and row.level in STUCK_LEVELS
        ]
        label = labels[misconception_id][1]
        if len({row.user_id for row in rows}) < MIN_PEOPLE:
            return None, label
        rows.sort(key=lambda row: row.created_at, reverse=True)
        return (
            _distinct_people(
                (row.user_id, row.evidence[0]) for row in rows if row.evidence
            ),
            label,
        )

    async def _render_excerpts(
        self,
        space_id: int,
        picked: list[tuple[int, uuid.UUID]],
        *,
        masker: Masker,
        viewer: str | None,
        with_source: bool = True,
    ) -> list[dict]:
        blocks = await self._reads.excerpt_blocks([bid for _uid, bid in picked])
        conversation = LearningConversationReads(self._session)
        out = []
        for uid, bid in picked:
            block = blocks.get(bid)
            if block is None:
                continue
            code = member_code(space_id, uid)
            messages = [
                {
                    "role": "member",
                    "code": code,
                    "text": masker.mask(block.content),
                    "at": _ms(block.created_at),
                }
            ]
            reply = await _first_reply(conversation, block)
            if reply is not None:
                messages.append(
                    {
                        "role": "agent",
                        "code": None,
                        "text": masker.mask(reply.content),
                        "at": _ms(reply.created_at),
                    }
                )
            source = None
            if with_source and await may_read_project(
                self._session, project_id=block.project_id, handle=viewer
            ):
                source = {
                    "topicId": str(block.topic_id),
                    "projectId": str(block.project_id),
                    "blockId": str(block.block_id),
                }
            out.append(
                {
                    "code": code,
                    "taskName": block.task_name,
                    "at": _ms(block.created_at),
                    "messages": messages,
                    "source": source,
                }
            )
        return out

    # --- 高频词 -------------------------------------------------------------

    async def words(self, space) -> dict:
        """高频词与高频报错：从成员这周说的话里数，不调模型。去掉虚词和贴进来的题面；
        说过它的人不到 3 个的词不列（名字之类只有一个人会打的词也就进不来）。"""
        empty = {
            "enabled": _enabled(space),
            "minPeople": MIN_PEOPLE,
            "windowDays": 7,
            "words": [],
            "errors": [],
        }
        if not _enabled(space):
            return empty
        now = datetime.now(UTC)
        spoken = await self._reads.spoken(
            space.id,
            since=_since_enabled(space, now - timedelta(weeks=WORD_WEEKS)),
            limit=WORD_MESSAGE_LIMIT,
        )
        if not spoken:
            return empty
        masker = await self._masker(space.id)
        pasted = [_plain_text(d) for d in await self._reads.task_descriptions(space.id)]
        week_ago = now - timedelta(days=7)
        this_week: dict[str, Counter[str]] = {}
        people: dict[str, set[str]] = {}
        before: Counter[str] = Counter()
        shown_as: dict[str, Counter[str]] = {}
        lefts: dict[str, set[str]] = {}
        rights: dict[str, set[str]] = {}
        error_people: dict[str, set[str]] = {}
        error_count: Counter[str] = Counter()
        for said in spoken:
            text = _drop_pasted(masker.mask(said.content), pasted)
            fresh = said.created_at >= week_ago
            for key, surface, left, right in _tokens(text):
                lefts.setdefault(key, set()).add(left)
                rights.setdefault(key, set()).add(right)
                if fresh:
                    this_week.setdefault(key, Counter())[said.author] += 1
                    people.setdefault(key, set()).add(said.author)
                    shown_as.setdefault(key, Counter())[surface] += 1
                else:
                    before[key] += 1
            if fresh:
                for line in _error_lines(text):
                    error_people.setdefault(line, set()).add(said.author)
                    error_count[line] += 1
        counted = {
            key: sum(c.values())
            for key, c in this_week.items()
            if len(people[key]) >= MIN_PEOPLE and _is_word(lefts[key], rights[key])
        }
        ranked = sorted(counted, key=lambda k: (-len(people[k]), -counted[k], k))[
            :WORD_LIMIT
        ]
        words = [
            {
                "word": shown_as[key].most_common(1)[0][0],
                "count": counted[key],
                "people": len(people[key]),
                "isNew": before[key] == 0,
                "rising": before[key] > 0
                and counted[key] > 1.5 * before[key] / (WORD_WEEKS - 1),
            }
            for key in ranked
        ]
        errors = sorted(
            (line for line, who in error_people.items() if len(who) >= MIN_PEOPLE),
            key=lambda line: (-len(error_people[line]), -error_count[line], line),
        )[:ERROR_LIMIT]
        return {
            **empty,
            "words": words,
            "errors": [
                {
                    "line": line,
                    "count": error_count[line],
                    "people": len(error_people[line]),
                }
                for line in errors
            ],
        }

    # --- 题目难度 -----------------------------------------------------------

    async def tasks(self, space) -> dict:
        """每道题：完成率、平均对话轮数、平均用时、卡住率、代写迹象人数，挂着测试点的题
        再加测试平均通过率（``testPassRate``：每人每阶段最新那一版提交最近一次读得出分的
        评测，``passed/total`` 的全班平均；不足 ``MIN_PEOPLE`` 份时不给）。"""
        empty = {"enabled": _enabled(space), "minPeople": MIN_PEOPLE, "tasks": []}
        if not _enabled(space):
            return empty
        board = await self._board(space.id)
        tasks = await self._reads.tasks(space.id)
        claims = await self._reads.task_claims(space.id)
        cursors = await self._reads.cursors_by_task(space.id)
        task_points = await self._reads.task_points(space.id)
        shares = await self._reads.answer_share_by_person(space.id)
        test_rates = await self._reads.test_pass_rates([tid for tid, _ in tasks])
        stuck_on: dict[int, set[int]] = {}
        for row in board.judgments:
            if row.level in STUCK_LEVELS and not row.disputed:
                stuck_on.setdefault(row.point_id, set()).add(row.user_id)
        seekers: dict[int, int] = Counter()
        for _uid, tid, asks, answers in shares:
            if tid is None or asks < ANSWER_SEEKER_MIN_ASKS:
                continue
            if answers / asks >= ANSWER_SEEKER_SHARE:
                seekers[tid] += 1
        rows = []
        for task_id, name in tasks:
            c = claims.get(task_id)
            talked = cursors.get(task_id, [])
            people = {uid for uid, _n in talked}
            size = max(c.claims if c else 0, len(people))
            if size < MIN_PEOPLE:
                rows.append(
                    {
                        "taskId": task_id,
                        "name": name,
                        "analysed": False,
                        "claims": None,
                        "completionRate": None,
                        "avgTurns": None,
                        "avgHours": None,
                        "stuckRate": None,
                        "answerSeekers": None,
                        "testPassRate": None,
                        "testJudged": None,
                    }
                )
                continue
            test_rate, test_judged = test_rates.get(task_id, (None, 0))
            pids = task_points.get(task_id, set())
            stuck = {
                uid for pid in pids for uid in stuck_on.get(pid, set()) if uid in people
            }
            rows.append(
                {
                    "taskId": task_id,
                    "name": name,
                    "analysed": True,
                    "claims": c.claims if c else 0,
                    "completionRate": (
                        round((c.submitted + c.passed) / c.claims, 4)
                        if c and c.claims
                        else None
                    ),
                    "avgTurns": (
                        round(sum(n for _u, n in talked) / len(talked), 1)
                        if talked
                        else None
                    ),
                    "avgHours": (
                        round(c.avg_hours_to_submit, 1)
                        if c and c.avg_hours_to_submit is not None
                        else None
                    ),
                    "stuckRate": (
                        round(len(stuck) / len(people), 4)
                        if people and len(people) >= MIN_PEOPLE and pids
                        else None
                    ),
                    "answerSeekers": seekers.get(task_id, 0),
                    # 不足 3 份不给：和别的几列一样，一组人太少就不分析。
                    "testPassRate": (test_rate if test_judged >= MIN_PEOPLE else None),
                    "testJudged": test_judged or None,
                }
            )
        return {**empty, "tasks": rows}

    # --- 提问行为 -----------------------------------------------------------

    async def behavior(self, space) -> dict:
        """提问类型占比、星期 × 时段的提问热力、对 AI 的依赖程度（三档人数）。"""
        empty = {
            "enabled": _enabled(space),
            "minPeople": MIN_PEOPLE,
            "analysed": False,
            "totalQuestions": 0,
            "kinds": [],
            "slots": [f"{a}-{b}" for a, b in HEAT_SLOTS],
            "heat": [],
            "dependency": None,
        }
        if not _enabled(space):
            return empty
        kinds = await self._reads.kind_counts(space.id)
        total, askers = await self._reads.question_totals(space.id)
        if askers < MIN_PEOPLE:
            return {**empty, "totalQuestions": total}
        heat_rows = await self._reads.heat(space.id, str(LOCAL_ZONE))
        cells: dict[tuple[int, int], tuple[int, set[int]]] = {}
        for dow, hour, n, uids in heat_rows:
            slot = next(i for i, (a, b) in enumerate(HEAT_SLOTS) if a <= hour < b)
            count, who = cells.get((dow, slot), (0, set()))
            cells[(dow, slot)] = (count + n, who | set(uids or []))
        heat = [
            [
                (
                    cells[(dow, slot)][0]
                    if (dow, slot) in cells and len(cells[(dow, slot)][1]) >= MIN_PEOPLE
                    else None
                )
                for slot in range(len(HEAT_SLOTS))
            ]
            for dow in range(1, 8)
        ]
        tiers = {"low": 0, "mid": 0, "high": 0}
        for _uid, tid, asks, answers in await self._reads.answer_share_by_person(
            space.id
        ):
            if tid is not None or not asks:
                continue
            share = answers / asks
            tiers[
                "low"
                if share < DEPENDENCY_LOW
                else "high"
                if share >= DEPENDENCY_HIGH
                else "mid"
            ] += 1
        order = ("concept", "error", "answer", "verify", "other")
        return {
            **empty,
            "analysed": True,
            "totalQuestions": total,
            "kinds": [
                {
                    "kind": kind,
                    "count": kinds.get(kind, (0, 0))[0],
                    "share": round(kinds.get(kind, (0, 0))[0] / total, 4)
                    if total
                    else 0,
                }
                for kind in order
            ],
            "heat": heat,
            "dependency": tiers,
        }

    # --- 趋势 ---------------------------------------------------------------

    async def trend(self, space) -> dict:
        """各知识点每周卡住几人（学情分析写的按周快照），最近几周。"""
        empty = {
            "enabled": _enabled(space),
            "minPeople": MIN_PEOPLE,
            "weeks": [],
            "series": [],
        }
        if not _enabled(space):
            return empty
        today = datetime.now(UTC).astimezone(LOCAL_ZONE).date()
        first = week_start(today) - timedelta(weeks=TREND_WEEKS - 1)
        weeks = [first + timedelta(weeks=i) for i in range(TREND_WEEKS)]
        rows = await self._reads.weeks(space.id, since=first)
        by_point: dict[int, dict] = {}
        for row in rows:
            by_point.setdefault(row.knowledge_point_id, {})[row.week_start] = row
        points = await SpaceKnowledgePointRepository(self._session).list_for_space(
            space.id
        )
        series = []
        for point in points:
            got = by_point.get(point.id, {})
            values = [
                (
                    got[w].stuck_count
                    if w in got and got[w].involved_count >= MIN_PEOPLE
                    else None
                )
                for w in weeks
            ]
            series.append(
                {"knowledgePointId": point.id, "name": point.name, "values": values}
            )
        return {**empty, "weeks": [w.isoformat() for w in weeks], "series": series}

    # --- 导出报告 -----------------------------------------------------------

    async def report(
        self, space, *, sections: Sequence[str], point_id: int | None
    ) -> dict:
        """导出报告要的整体数据：按勾的几部分取，只有整体，摘录只标代号。

        ``point_id`` 给了就只出这一个知识点（「导出这个知识点」）。"""
        overview = await self.overview(space)
        points = await self.points(space, asked_ratio=None, stuck_ratio=None)
        wanted = [
            row for row in points["points"] if point_id is None or row["id"] == point_id
        ]
        out: dict = {
            "spaceName": getattr(space, "name", ""),
            "generatedAt": datetime.now(UTC),
            "members": overview["members"],
            "minPeople": MIN_PEOPLE,
            "tasks": overview["tasks"] if "tasks" in sections else None,
            "points": [],
        }
        masker = await self._masker(space.id) if "excerpts" in sections else None
        for row in wanted:
            item: dict = {
                "name": row["name"],
                "analysed": row["analysed"],
                "involved": row["involved"],
                "stuck": row["stuck"],
                "stuckRate": row["stuckRate"],
                "misconceptions": [],
                "advice": None,
            }
            if row["analysed"]:
                detail = await self.point_detail(space, row["id"])
                if "points" in sections:
                    item["misconceptions"] = [
                        {"label": m["label"], "people": m["people"], "excerpts": []}
                        for m in detail["misconceptions"]
                    ]
                if "advice" in sections and detail["advice"]:
                    item["advice"] = detail["advice"]["text"]
                if masker is not None:
                    for shown, m in zip(
                        item["misconceptions"], detail["misconceptions"], strict=False
                    ):
                        picked, _title = await self._excerpt_sources(
                            space.id, cluster_id=None, misconception_id=m["id"]
                        )
                        if picked:
                            shown["excerpts"] = await self._render_excerpts(
                                space.id,
                                picked,
                                masker=masker,
                                viewer=None,
                                with_source=False,
                            )
            out["points"].append(item)
        out["includePoints"] = "points" in sections
        out["includeAdvice"] = "advice" in sections
        out["includeExcerpts"] = "excerpts" in sections
        return out

    async def record_export(
        self,
        *,
        space_id: int,
        user_id: int,
        format: str,
        sections: list[str],
        point_id: int | None,
    ) -> None:
        """记下这次导出：谁、什么时候、什么格式、哪几部分（只追加）。"""
        await LearningClassWrites(self._session).record_export(
            space_id=space_id,
            user_id=user_id,
            format=format,
            sections=sections,
            point_id=point_id,
        )


# --- helpers ----------------------------------------------------------------


def _ratio(value: float | None, default: float) -> float:
    if value is None:
        return default
    return min(max(float(value), 0.0), 1.0)


def _ceil(value: float) -> int:
    whole = int(value)
    return whole if whole == value else whole + 1


def _since_enabled(space, moment: datetime) -> datetime:
    """``moment``, but never before the analysis was switched on: what was said
    before that is not read by anything of this feature."""
    enabled_at = getattr(space, "learning_analysis_enabled_at", None)
    return max(moment, enabled_at) if enabled_at is not None else moment


def _trend(stat, this_week: datetime) -> str | None:
    if stat.first_asked >= this_week:
        return "new"
    if stat.people_this_week > stat.people_last_week:
        return "up"
    if stat.people_this_week < stat.people_last_week:
        return "down"
    return None


def _distinct_people(
    pairs: Iterable[tuple[int, uuid.UUID]],
) -> list[tuple[int, uuid.UUID]]:
    seen: set[int] = set()
    out = []
    for uid, bid in pairs:
        if uid in seen:
            continue
        seen.add(uid)
        out.append((uid, bid))
        if len(out) >= EXCERPT_LIMIT:
            break
    return out


async def _first_reply(conversation: LearningConversationReads, block: ExcerptBlock):
    replies = await conversation.agent_replies(
        conversation_ids=[block.conversation_id], start=block.created_at, limit=2
    )
    return next((r for r in replies if r.created_at > block.created_at), None)


# --- words ------------------------------------------------------------------

#: 不成词的单字：在它们处断开中文串。
_BREAK_CHARS = set(
    "的了吗呢啊吧呀哦嗯是在我你他她它这那就也都和与或而及其之很太还又再把被给让"
    "对从向到于为着过么吗么请问一个些里上下中个能会要想说看做用写有没不得地"
)
#: 当成词也没有意义的那些（虚词、客套、对话里的常用词）。
_STOP_WORDS = {
    "怎么",
    "为什么",
    "什么",
    "如何",
    "怎样",
    "哪里",
    "哪个",
    "这个",
    "那个",
    "这样",
    "那样",
    "可以",
    "能够",
    "应该",
    "需要",
    "知道",
    "觉得",
    "感觉",
    "老师",
    "同学",
    "谢谢",
    "你好",
    "请问",
    "问题",
    "一下",
    "现在",
    "然后",
    "但是",
    "因为",
    "所以",
    "如果",
    "已经",
    "还是",
    "就是",
    "不是",
    "没有",
    "我们",
    "你们",
    "他们",
    "自己",
    "一直",
    "时候",
    "这里",
    "那里",
    "帮我",
    "帮忙",
    "告诉",
    "解释",
    "代码",
    "答案",
    "姓名",
    "学号",
    "手机号",
    "邮箱",
    "证件号",
}
_ASCII_STOP = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "to",
    "of",
    "and",
    "or",
    "in",
    "on",
    "it",
    "this",
    "that",
    "for",
    "with",
    "be",
    "as",
    "at",
    "by",
    "i",
    "you",
    "we",
    "my",
    "me",
    "do",
    "does",
    "not",
    "no",
    "yes",
    "ok",
    "can",
    "how",
    "what",
    "why",
}
_MARKS = re.compile(r"\[(?:姓名|学号|手机号|邮箱|证件号)\]")
_CJK_RUN = re.compile(r"[一-鿿]+")
_ASCII_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,30}")


#: 中文片段取几个字到几个字。
_GRAM_MIN, _GRAM_MAX = 2, 6
_EDGE = "|"


def _tokens(text: str) -> list[tuple[str, str, str, str]]:
    """(计数用的键, 显示用的写法, 左邻, 右邻)。

    英文按整词（左右邻记作边界）。中文串先在虚字处断开，再取 2–6 字的片段，记下每
    次出现时紧挨着它的左右两个字（在串的头尾就是边界）：一个片段只有左右都「换过
    邻居」或挨着边界，才是一个词（``_is_word``）—— 「子查」后面永远跟着「询」，就不
    是词；「相关子查询」前后什么都有，就是。这是不靠词典的分词里最常用的那条判据
    （左右邻接的多样性），够数一份高频词表用，不必为此加一个分词库。
    """
    text = _MARKS.sub(" ", text)
    out: list[tuple[str, str, str, str]] = []
    for word in _ASCII_WORD.findall(text):
        key = word.casefold()
        if key not in _ASCII_STOP and not key.isdigit():
            out.append((key, word, _EDGE, _EDGE))
    for run in _CJK_RUN.findall(text):
        segment = ""
        for ch in run + "的":
            if ch in _BREAK_CHARS:
                out.extend(_grams(segment))
                segment = ""
            else:
                segment += ch
    return out


def _grams(segment: str) -> list[tuple[str, str, str, str]]:
    if len(segment) < _GRAM_MIN:
        return []
    out = []
    for n in range(_GRAM_MIN, min(_GRAM_MAX, len(segment)) + 1):
        for i in range(len(segment) - n + 1):
            gram = segment[i : i + n]
            if gram in _STOP_WORDS:
                continue
            left = segment[i - 1] if i > 0 else _EDGE
            right = segment[i + n] if i + n < len(segment) else _EDGE
            out.append((gram, gram, left, right))
    return out


def _is_word(left: set[str], right: set[str]) -> bool:
    """左右两边各自要么只挨着边界，要么换过不止一个邻居。"""
    return (left == {_EDGE} or len(left) >= 2) and (right == {_EDGE} or len(right) >= 2)


def _plain_text(description: str) -> str:
    """题面的纯文本：富文本（ProseMirror JSON）取出每段 text，不是 JSON 就原样。"""
    try:
        doc = json.loads(description)
    except ValueError:
        return description
    parts: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("text"), str):
                parts.append(node["text"])
            for child in node.get("content") or []:
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(doc)
    return "\n".join(parts)


def _drop_pasted(text: str, pasted: Sequence[str]) -> str:
    """去掉成员原样贴进来的题面：一行（8 字以上）在某道题的题面里出现过就删。"""
    if not pasted:
        return text
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) >= 8 and any(stripped in body for body in pasted):
            continue
        kept.append(line)
    return "\n".join(kept)


_ERROR_PATTERNS = (
    re.compile(r"ERROR\s+\d{3,5}[^\n]{0,120}"),
    re.compile(r"ORA-\d{5}[^\n]{0,120}"),
    re.compile(r"SQLSTATE\[?[0-9A-Z]{5}\]?[^\n]{0,120}"),
    re.compile(r"\b[A-Z][A-Za-z]*(?:Error|Exception)\b[:：][^\n]{0,120}"),
    re.compile(r"(?i)\berror\s*[:：][^\n]{0,120}"),
)
_QUOTED = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"|`[^`\n]*`")


def _error_lines(text: str) -> list[str]:
    """一段话里像报错的那几行，归一化后（引号里的名字换成 ?、空白压成一个）。"""
    found: list[str] = []
    for pattern in _ERROR_PATTERNS:
        for match in pattern.findall(text):
            line = _QUOTED.sub("'?'", match)
            line = " ".join(line.split())[:120]
            if line and line not in found:
                found.append(line)
        if found:
            break
    return found
