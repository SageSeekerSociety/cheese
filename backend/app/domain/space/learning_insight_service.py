"""「我的学习洞察」：一个成员读自己在一块板上的学情分析结果，或标记一条判断不准。

只有本人：路由不收任何「看谁」的参数，读的永远是发请求的那个人自己的行。老师那一侧
（全班整体）是第 3 步的事，不在这里。

页面要的几样东西都从学情分析写下的行里读（``learning_insight_analysis``）：

- 全部知识点三档：这块板的清单（按清单顺序），每一条带上这个人的当前判断，没有判断的
  是「未涉及」；
- 依据摘录：判断里存的 block id，读回成**这个人自己**、还在的那几条消息；
- 最该补的：薄弱优先、其次不稳，被标「不准」的不参选（降权），同档取最近分析的那条；
- 我常问的：按「知识点 × 提问类型」数次数，最多几类；
- 读了哪些题：游标行（每道题读了几条发言），以及最近一次更新的时刻。
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.sentences import say
from app.domain.space import learning_live
from app.domain.space.learning_insight_repositories import (
    LearningConversationReads,
    SpaceLearningCursorRepository,
    SpaceLearningJudgmentRepository,
    SpaceLearningQuestionRepository,
)
from app.domain.space.learning_realtime_state import state as realtime_state
from app.domain.space.learning_service import _excerpt
from app.domain.space.models import SpaceLearningJudgment
from app.domain.space.repositories import SpaceKnowledgePointRepository
from app.domain.user.services import usernames_by_ids

#: 「我常问的」最多列几类。
FREQUENT_LIMIT = 5
#: 档位从弱到强：「最该补的」先挑排在前面的。
_NEED = {"weak": 0, "shaky": 1}


def _ms(moment: datetime | None) -> int | None:
    return int(moment.timestamp() * 1000) if moment is not None else None


class SpaceLearningInsightService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._judgments = SpaceLearningJudgmentRepository(session)

    @classmethod
    def of(cls, session: AsyncSession) -> SpaceLearningInsightService:
        return cls(session)

    async def mine(self, *, space, user_id: int) -> dict:
        """这个人在这块板的学习洞察。板没开学情分析时 ``enabled`` 为假、其余为空。"""
        if not bool(getattr(space, "learning_analysis_enabled", False)):
            return _empty(enabled=False)
        points = list(
            await SpaceKnowledgePointRepository(self._session).list_for_space(space.id)
        )
        judgments = await self._judgments.list_for_member(
            space_id=space.id, user_id=user_id
        )
        cursors = await SpaceLearningCursorRepository(self._session).list_for_member(
            space_id=space.id, user_id=user_id
        )
        questions = await SpaceLearningQuestionRepository(
            self._session
        ).list_for_member(space_id=space.id, user_id=user_id)

        reads = LearningConversationReads(self._session)
        handle = (await usernames_by_ids(self._session, [user_id])).get(user_id, "")
        excerpts = await reads.excerpts(
            handle=handle,
            block_ids=[bid for row in judgments for bid in row.evidence_block_ids],
        )
        task_names = await reads.task_names([c.task_id for c in cursors])

        by_point = {row.knowledge_point_id: row for row in judgments}
        point_names = {p.id: p.name for p in points}
        items = []
        for point in points:
            row = by_point.get(point.id)
            items.append(
                {
                    "id": point.id,
                    "name": point.name,
                    "judgment": _judgment_to_api(row, excerpts)
                    if row is not None
                    else None,
                }
            )

        moments = [row.analyzed_at for row in judgments] + [
            c.updated_at for c in cursors
        ]
        return {
            "enabled": True,
            "updatedAt": _ms(max(moments)) if moments else None,
            "knowledgePoints": items,
            "focusKnowledgePointId": _focus(judgments, point_names),
            "frequentQuestions": _frequent(questions, point_names),
            # 今天实时读取到哪了：还在读，还是到上限了、等明早（§ step 2 实时）。
            "realtime": await realtime_state(
                self._session, space_id=space.id, space=space
            ),
            "sources": {
                "messagesRead": sum(c.messages_read for c in cursors),
                "tasks": [
                    {
                        "taskId": c.task_id,
                        "name": task_names.get(c.task_id, ""),
                        "messagesRead": c.messages_read,
                    }
                    for c in cursors
                    if c.messages_read > 0
                ],
            },
        }

    async def dispute(self, *, space, user_id: int, judgment_id: int) -> dict:
        """「标记不准」：只能标自己的判断。标过的降权（不参选「最该补的」），下次
        分析这个知识点时复核；再标一次不改第一次的时间。"""
        if not bool(getattr(space, "learning_analysis_enabled", False)):
            raise ConflictError(say("learningAnalysisDisabled"))
        row = await self._judgments.get(
            space_id=space.id, user_id=user_id, judgment_id=judgment_id
        )
        if row is None:
            raise NotFoundError(
                say("learningJudgmentNotFound"),
                data={"spaceId": space.id, "judgmentId": judgment_id},
            )
        await self._judgments.dispute(row, now=datetime.now(UTC))
        # 这一条不参选「最该补的」了：这一页（和可能看着的同一个人）重读一次。
        await learning_live.publish(space.id)
        return {"judgmentId": row.id, "disputed": True}


def _empty(*, enabled: bool) -> dict:
    return {
        "enabled": enabled,
        "updatedAt": None,
        "knowledgePoints": [],
        "focusKnowledgePointId": None,
        "frequentQuestions": [],
        "realtime": None,
        "sources": {"messagesRead": 0, "tasks": []},
    }


def _judgment_to_api(row: SpaceLearningJudgment, excerpts: dict) -> dict:
    evidence = []
    for block_id in row.evidence_block_ids:
        found = excerpts.get(block_id)
        if found is None:
            continue
        evidence.append(
            {
                "blockId": str(found.block_id),
                "taskId": found.task_id,
                "taskName": found.task_name,
                "quote": _excerpt(found.content),
                "createdAt": _ms(found.created_at),
            }
        )
    return {
        "id": row.id,
        "level": row.level,
        "summary": row.summary,
        "disputed": row.disputed_at is not None,
        "analyzedAt": _ms(row.analyzed_at),
        "evidence": evidence,
        # 最近一组巩固题的作答（§ step 4）：做完一组改了档位时，它就是那次的依据。
        "practice": (
            {
                "correct": row.practice_correct or 0,
                "total": row.practice_total or 0,
                "at": _ms(row.practiced_at),
            }
            if row.practiced_at is not None
            else None
        ),
    }


def _focus(
    judgments: list[SpaceLearningJudgment], point_names: dict[int, str]
) -> int | None:
    """最该补的那个知识点：薄弱先于不稳，被标不准的不参选，同档取最近分析的。"""
    candidates = [
        row
        for row in judgments
        if row.level in _NEED
        and row.disputed_at is None
        and row.knowledge_point_id in point_names
    ]
    if not candidates:
        return None
    best = min(
        candidates,
        key=lambda row: (_NEED[row.level], -row.analyzed_at.timestamp()),
    )
    return best.knowledge_point_id


def _frequent(questions: list, point_names: dict[int, str]) -> list[dict]:
    """「我常问的」：按（知识点, 类型）数，次数多的在前；每类带最近一条的概括。"""
    counts: Counter[tuple[int | None, str]] = Counter()
    example: dict[tuple[int | None, str], str] = {}
    for q in questions:  # newest first, so the first gist seen is the latest
        point_id = q.knowledge_point_id if q.knowledge_point_id in point_names else None
        key = (point_id, q.kind)
        counts[key] += 1
        if key not in example and q.gist:
            example[key] = q.gist
    ranked = sorted(counts.items(), key=lambda item: -item[1])[:FREQUENT_LIMIT]
    return [
        {
            "knowledgePointId": point_id,
            "knowledgePointName": point_names.get(point_id) if point_id else None,
            "kind": kind,
            "count": count,
            "example": example.get((point_id, kind), ""),
        }
        for (point_id, kind), count in ranked
    ]
