"""巩固练习、复习提醒、讲解与推送（§ learning analysis, step 4）。

**成员那一侧只有本人**：每个方法收发请求的那个人的 ``user_id``，读写的永远是他自己的
行；别人的一组题答 404，与不存在同一个样子。

**一组题怎么并进判断**（``next_level``）：3 道全对升一档（薄弱→不稳→掌握），错两道及
以上降一档（掌握→不稳→薄弱），错一道保持。判断仍有依据：对话里的依据
（``evidence_block_ids``）原样留着，这一组的作答另记在判断上（``practice_correct`` /
``practice_total`` / ``practiced_at``），页面把它和档位一起说出来。做完一组也算对「标记
不准」的复核：标记清掉；升到掌握时背后的误解一并清掉（掌握那一档没有误解）。算的是
**第一次就选对**：一道题第一次选的那个选项定下来，之后不改。

**复习排期**（``next_review``）：固定间隔 1 / 3 / 7 / 14 天，不用 FSRS —— 一组只有 3
道题，拿不出 FSRS 要估的那几个参数，固定间隔说得清、学生也看得懂。全对连对次数加一，
错两道及以上归零，错一道不变；间隔按连对次数取，连对四组算巩固住了，不再提醒。到期
的在「我的学习洞察」的「复习提醒」里列出，点了再做一组（``source="review"``）。不发
通知：这一页就是提醒的地方。

**推送**（老师那一侧）：给当前卡在这个知识点上的成员（不稳 / 薄弱、没标不准、现在还
是这块板的成员）各排一组，已经有一组没做完的跳过。卡住的不足 3 人就不推（与「不足 3
人不分析」同一条界：人少时推给了谁几乎就等于说出了是谁）；推了也只报人数，不足 3 人
时连人数也不报。排上的组由定时任务（``run_queued``）出题，成员先打开时就当场出。
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.sentences import say
from app.domain.space import learning_practice_analysis as ai
from app.domain.space.knowledge_suggestions import Refused
from app.domain.space.learning_class_analysis import MIN_PEOPLE
from app.domain.space.learning_class_repositories import LearningClassReads
from app.domain.space.learning_insight_analysis import local_day
from app.domain.space.learning_insight_repositories import LearningConversationReads
from app.domain.space.learning_practice_repositories import LearningPracticeRepository
from app.domain.space.models import Space, SpaceLearningJudgment
from app.domain.space.practice_models import PRACTICE_SOURCES, SpaceLearningPractice
from app.domain.space.repositories import SpaceKnowledgePointRepository
from app.domain.user.services import usernames_by_ids

logger = logging.getLogger(__name__)

#: 复习间隔（天），按连对几组取；连对满这么多组就不再提醒。
REVIEW_INTERVALS = (1, 3, 7, 14)
#: 档位从弱到强。
_LADDER = ("weak", "shaky", "mastered")
#: 定时任务一次至多出几组老师推送的题。
QUEUE_BATCH = 30
#: 未作答的那一格。
_UNANSWERED = -1


def _ms(moment: datetime | None) -> int | None:
    return int(moment.timestamp() * 1000) if moment is not None else None


def _enabled(space) -> bool:
    return bool(getattr(space, "learning_analysis_enabled", False))


def next_level(level: str, *, correct: int, total: int) -> str:
    """全对升一档，错两道及以上降一档，错一道不变。"""
    rank = _LADDER.index(level)
    wrong = total - correct
    if wrong == 0:
        rank = min(rank + 1, len(_LADDER) - 1)
    elif wrong >= 2:
        rank = max(rank - 1, 0)
    return _LADDER[rank]


def next_review(
    streak: int, *, correct: int, total: int, today: date
) -> tuple[int, date | None]:
    """新的连对次数和下一次复习的日子；巩固住了为 ``None``。"""
    wrong = total - correct
    if wrong == 0:
        streak += 1
    elif wrong >= 2:
        streak = 0
    if streak >= len(REVIEW_INTERVALS):
        return streak, None
    return streak, today + timedelta(days=REVIEW_INTERVALS[streak])


def _improved(before: str | None, after: str | None) -> bool:
    if before not in _LADDER or after not in _LADDER:
        return False
    return _LADDER.index(after) > _LADDER.index(before)


def practice_to_api(row: SpaceLearningPractice, point_name: str) -> dict:
    """One set as its owner sees it: a question's answer and explanation only
    once it has been answered, so the page cannot show them early."""
    answers = list(row.answers or [])
    questions = []
    for index, item in enumerate(row.questions or []):
        chosen = answers[index] if index < len(answers) else _UNANSWERED
        answered = chosen != _UNANSWERED
        questions.append(
            {
                "stem": item.get("stem", ""),
                "options": list(item.get("options", [])),
                "chosen": chosen if answered else None,
                "correct": (chosen == item.get("answer")) if answered else None,
                "answer": item.get("answer") if answered else None,
                "explanation": item.get("explanation", "") if answered else None,
            }
        )
    return {
        "id": row.id,
        "knowledgePointId": row.knowledge_point_id,
        "knowledgePointName": point_name,
        "source": row.source,
        "ready": row.generated_at is not None,
        "done": row.completed_at is not None,
        "questions": questions,
        "correct": row.correct,
        "levelBefore": row.level_before,
        "levelAfter": row.level_after,
        "createdAt": _ms(row.created_at),
        "completedAt": _ms(row.completed_at),
    }


async def generate_into(
    session: AsyncSession,
    space: Space,
    row: SpaceLearningPractice,
    *,
    member_id: int | None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Refused | None:
    """Ask the model for this set's three questions and write them in.

    ``member_id`` is whose own daily cap the call counts against — ``None``
    for a set a teacher pushed."""
    repo = LearningPracticeRepository(session)
    judgment = await repo.judgment(
        space_id=space.id, user_id=row.user_id, point_id=row.knowledge_point_id
    )
    point = await SpaceKnowledgePointRepository(session).get(
        space_id=space.id, point_id=row.knowledge_point_id
    )
    if judgment is None or point is None:
        return Refused(409, say("learningPracticeNoJudgment"))
    prompt = await _material(session, space.id, judgment, point.name)
    content = await ai.ask(
        session,
        space=space,
        system=ai.PRACTICE_PROMPT,
        prompt=prompt,
        member_id=member_id,
        transport=transport,
    )
    if isinstance(content, Refused):
        return content
    questions = ai.parse_questions(content)
    if questions is None:
        logger.warning("learning practice: unreadable questions space=%s", space.id)
        return Refused(503, say("learningPracticeUnreadable"), 30)
    row.questions = questions
    row.answers = [_UNANSWERED] * len(questions)
    row.generated_at = datetime.now(UTC)
    session.add(row)
    await session.commit()
    return None


async def _material(
    session: AsyncSession, space_id: int, judgment: SpaceLearningJudgment, name: str
) -> str:
    repo = LearningPracticeRepository(session)
    handle = (await usernames_by_ids(session, [judgment.user_id])).get(
        judgment.user_id, ""
    )
    found = await LearningConversationReads(session).excerpts(
        handle=handle, block_ids=list(judgment.evidence_block_ids)
    )
    quotes = [found[bid] for bid in judgment.evidence_block_ids if bid in found]
    return ai.material(
        point_name=name,
        level=judgment.level,
        summary=judgment.summary,
        misconception=await repo.misconception_label(judgment.misconception_id),
        quotes=quotes,
        earlier_stems=await repo.recent_stems(
            space_id=space_id,
            user_id=judgment.user_id,
            point_id=judgment.knowledge_point_id,
        ),
    )


class SpaceLearningPracticeService:
    def __init__(
        self, session: AsyncSession, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._session = session
        self._repo = LearningPracticeRepository(session)
        self._transport = transport

    @classmethod
    def of(cls, session: AsyncSession) -> SpaceLearningPracticeService:
        return cls(session)

    async def _names(self, space_id: int) -> dict[int, str]:
        points = await SpaceKnowledgePointRepository(self._session).list_for_space(
            space_id
        )
        return {p.id: p.name for p in points}

    def _require_enabled(self, space) -> None:
        if not _enabled(space):
            raise ConflictError(say("learningAnalysisDisabled"))

    async def _own(
        self, space, user_id: int, practice_id: int
    ) -> SpaceLearningPractice:
        row = await self._repo.get(
            space_id=space.id, user_id=user_id, practice_id=practice_id
        )
        if row is None:
            raise NotFoundError(
                say("learningPracticeNotFound"),
                data={"spaceId": space.id, "practiceId": practice_id},
            )
        return row

    # --- 成员：本人那一份 -------------------------------------------------

    async def overview(self, space, *, user_id: int) -> dict:
        """这个人没做完的几组、复习提醒、今天还能要几次。"""
        empty = {
            "enabled": _enabled(space),
            "dailyLimit": ai.DAILY_CALLS_PER_MEMBER,
            "usedToday": 0,
            "open": [],
            "due": [],
            "upcoming": None,
        }
        if not _enabled(space):
            return empty
        names = await self._names(space.id)
        today, _ = local_day(datetime.now(UTC))
        sets = await self._repo.open_sets(space_id=space.id, user_id=user_id)
        reviews = [
            r
            for r in await self._repo.reviews(space_id=space.id, user_id=user_id)
            if r.knowledge_point_id in names
        ]

        def review_to_api(review) -> dict:
            return {
                "knowledgePointId": review.knowledge_point_id,
                "knowledgePointName": names[review.knowledge_point_id],
                "dueOn": review.due_on.isoformat() if review.due_on else None,
                "streak": review.streak,
            }

        # ``reviews`` only lists rows still scheduled, so every ``due_on`` is set.
        due = [review_to_api(r) for r in reviews if (r.due_on or today) <= today]
        later = [r for r in reviews if (r.due_on or today) > today]
        return {
            **empty,
            "usedToday": await self._repo.calls_today(
                space_id=space.id, user_id=user_id, day=today
            ),
            "open": [
                {
                    "id": row.id,
                    "knowledgePointId": row.knowledge_point_id,
                    "knowledgePointName": names.get(row.knowledge_point_id, ""),
                    "source": row.source,
                    "ready": row.generated_at is not None,
                    "answered": sum(
                        1 for chosen in (row.answers or []) if chosen != _UNANSWERED
                    ),
                    "total": len(row.questions or []) or ai.QUESTIONS_PER_SET,
                    "createdAt": _ms(row.created_at),
                }
                for row in sets
            ],
            "due": due,
            "upcoming": review_to_api(later[0]) if later else None,
        }

    async def start(
        self, space, *, user_id: int, point_id: int, source: str, fresh: bool
    ) -> dict | Refused:
        """「生成 3 道巩固题」/「换一组」/ 复习时「再做一组」。

        这个知识点上已经有一组出好了没做完的，原样交回（``fresh`` 时换掉它）；
        老师推送、还排着队的那一组就地出题，不另开一组，也不算这个人自己的次数。
        """
        self._require_enabled(space)
        if source not in PRACTICE_SOURCES or source == "pushed":
            raise BadRequestError(say("learningPracticeBadAnswer"))
        judgment = await self._repo.judgment(
            space_id=space.id, user_id=user_id, point_id=point_id
        )
        if judgment is None:
            raise NotFoundError(
                say("learningPracticeNoJudgment"),
                data={"spaceId": space.id, "knowledgePointId": point_id},
            )
        names = await self._names(space.id)
        row = await self._repo.open_set(
            space_id=space.id, user_id=user_id, point_id=point_id
        )
        if row is not None and row.generated_at is not None:
            if not fresh:
                return practice_to_api(row, names.get(point_id, ""))
            await self._repo.drop(row)
            row = None
        created = row is None
        if row is None:
            row = await self._repo.start(
                space_id=space.id,
                user_id=user_id,
                point_id=point_id,
                source=source,
                now=datetime.now(UTC),
            )
        await self._session.commit()
        refused = await generate_into(
            self._session,
            space,
            row,
            member_id=user_id if created else None,
            transport=self._transport,
        )
        if refused is not None:
            if created:
                # 没出成的那一组不留着：它会占住「一个知识点一组」的位置。
                await self._repo.drop(row)
                await self._session.commit()
            return refused
        return practice_to_api(row, names.get(point_id, ""))

    async def read(self, space, *, user_id: int, practice_id: int) -> dict:
        row = await self._own(space, user_id, practice_id)
        names = await self._names(space.id)
        return practice_to_api(row, names.get(row.knowledge_point_id, ""))

    async def answer(
        self, space, *, user_id: int, practice_id: int, question: int, choice: int
    ) -> dict:
        """答一道：第一次选的定下来，当场交回对错、正确答案和讲解。"""
        row = await self._own(space, user_id, practice_id)
        if row.generated_at is None:
            raise ConflictError(say("learningPracticeNotReady"))
        questions = list(row.questions or [])
        if not 0 <= question < len(questions):
            raise BadRequestError(say("learningPracticeBadAnswer"))
        item = questions[question]
        if not 0 <= choice < len(item.get("options", [])):
            raise BadRequestError(say("learningPracticeBadAnswer"))
        answers = list(row.answers or [_UNANSWERED] * len(questions))
        if answers[question] == _UNANSWERED and row.completed_at is None:
            answers[question] = choice
            row.answers = answers
            self._session.add(row)
            await self._session.flush()
        chosen = answers[question]
        return {
            "question": question,
            "chosen": chosen,
            "correct": chosen == item.get("answer"),
            "answer": item.get("answer"),
            "explanation": item.get("explanation", ""),
        }

    async def complete(self, space, *, user_id: int, practice_id: int) -> dict:
        """「做完了」：把这一组并进这个知识点的判断，排下一次复习。再点一次不重算。"""
        row = await self._own(space, user_id, practice_id)
        if row.completed_at is None:
            await self._settle(space, row)
        review = await self._repo.review(
            space_id=space.id, user_id=user_id, point_id=row.knowledge_point_id
        )
        return {
            "practiceId": row.id,
            "correct": row.correct or 0,
            "total": len(row.questions or []),
            "levelBefore": row.level_before,
            "levelAfter": row.level_after,
            "nextReviewOn": review.due_on.isoformat()
            if review is not None and review.due_on is not None
            else None,
        }

    async def _settle(self, space, row: SpaceLearningPractice) -> None:
        self._require_enabled(space)
        if row.generated_at is None:
            raise ConflictError(say("learningPracticeNotReady"))
        questions = list(row.questions or [])
        answers = list(row.answers or [])
        if len(answers) < len(questions) or _UNANSWERED in answers:
            raise ConflictError(say("learningPracticeUnanswered"))
        total = len(questions)
        correct = sum(
            1
            for item, chosen in zip(questions, answers[: len(questions)], strict=True)
            if item.get("answer") == chosen
        )
        now = datetime.now(UTC)
        judgment = await self._repo.judgment(
            space_id=space.id, user_id=row.user_id, point_id=row.knowledge_point_id
        )
        if judgment is not None:
            row.level_before = judgment.level
            level = next_level(judgment.level, correct=correct, total=total)
            row.level_after = level
            judgment.level = level
            judgment.practice_correct = correct
            judgment.practice_total = total
            judgment.practiced_at = now
            judgment.disputed_at = None
            if level == "mastered":
                judgment.misconception_id = None
            judgment.updated_at = now
            self._session.add(judgment)
        row.correct = correct
        row.completed_at = now
        self._session.add(row)
        today, _ = local_day(now)
        review = await self._repo.review(
            space_id=space.id, user_id=row.user_id, point_id=row.knowledge_point_id
        )
        streak, due_on = next_review(
            review.streak if review is not None else 0,
            correct=correct,
            total=total,
            today=today,
        )
        await self._repo.schedule(
            space_id=space.id,
            user_id=row.user_id,
            point_id=row.knowledge_point_id,
            streak=streak,
            due_on=due_on,
            now=now,
        )
        await self._session.flush()

    async def explain(self, space, *, user_id: int, point_id: int) -> dict | Refused:
        """「先看一段讲解」：判断没变就读缓存，变了（或没有）才问一次模型。"""
        self._require_enabled(space)
        judgment = await self._repo.judgment(
            space_id=space.id, user_id=user_id, point_id=point_id
        )
        point = await SpaceKnowledgePointRepository(self._session).get(
            space_id=space.id, point_id=point_id
        )
        if judgment is None or point is None:
            raise NotFoundError(
                say("learningPracticeNoJudgment"),
                data={"spaceId": space.id, "knowledgePointId": point_id},
            )
        cached = await self._repo.explanation(
            space_id=space.id, user_id=user_id, point_id=point_id
        )
        if cached is not None and cached.basis_at == judgment.updated_at:
            return _lecture_to_api(point_id, cached.body, cached.generated_at)
        basis = judgment.updated_at
        content = await ai.ask(
            self._session,
            space=space,
            system=ai.LECTURE_PROMPT,
            prompt=await _material(self._session, space.id, judgment, point.name),
            member_id=user_id,
            transport=self._transport,
        )
        if isinstance(content, Refused):
            return content
        body = ai.parse_lecture(content)
        if body is None:
            return Refused(503, say("learningPracticeUnreadable"), 30)
        now = datetime.now(UTC)
        await self._repo.write_explanation(
            space_id=space.id,
            user_id=user_id,
            point_id=point_id,
            body=body,
            basis_at=basis,
            now=now,
        )
        await self._session.commit()
        return _lecture_to_api(point_id, body, now)

    # --- 老师：推送与整体数 -----------------------------------------------

    async def push(self, space, *, point_id: int, actor_id: int) -> dict:
        """给卡在这个知识点上的成员各排一组；只报人数，不足 3 人不推。"""
        self._require_enabled(space)
        point = await SpaceKnowledgePointRepository(self._session).get(
            space_id=space.id, point_id=point_id
        )
        if point is None:
            raise NotFoundError(
                say("learningPointNotFound"),
                data={"spaceId": space.id, "knowledgePointId": point_id},
            )
        members = await LearningClassReads(self._session).member_ids(space.id)
        stuck = [
            uid
            for uid in await self._repo.stuck_members(
                space_id=space.id, point_id=point_id
            )
            if uid in members
        ]
        if len(stuck) < MIN_PEOPLE:
            return {"pushed": False, "recipients": None, "minPeople": MIN_PEOPLE}
        record = await self._repo.record_push(
            space_id=space.id,
            point_id=point_id,
            pushed_by=actor_id,
            now=datetime.now(UTC),
        )
        queued = await self._repo.queue_for(
            space_id=space.id, point_id=point_id, user_ids=stuck, push_id=record.id
        )
        await self._repo.set_recipients(record, queued)
        await self._session.flush()
        return {
            "pushed": True,
            "recipients": queued if queued >= MIN_PEOPLE else None,
            "minPeople": MIN_PEOPLE,
        }

    async def point_outcome(self, space_id: int, point_id: int) -> dict:
        """知识点详情里那两格：做过巩固题的人里几人升档（不足 3 人不报），最近
        一次推送。"""
        firsts: dict[int, str | None] = {}
        lasts: dict[int, str | None] = {}
        for outcome in await self._repo.outcomes(space_id, point_id):
            firsts.setdefault(outcome.user_id, outcome.level_before)
            lasts[outcome.user_id] = outcome.level_after
        practiced = len(firsts)
        push = await self._repo.last_push(space_id, point_id)
        return {
            "practice": (
                {
                    "practiced": practiced,
                    "improved": sum(
                        1 for uid in firsts if _improved(firsts[uid], lasts.get(uid))
                    ),
                }
                if practiced >= MIN_PEOPLE
                else None
            ),
            "lastPush": (
                {
                    "at": _ms(push.created_at),
                    "recipients": push.recipients
                    if push.recipients >= MIN_PEOPLE
                    else None,
                }
                if push is not None
                else None
            ),
        }


def _lecture_to_api(point_id: int, body: str, at: datetime) -> dict:
    return {"knowledgePointId": point_id, "text": body, "generatedAt": _ms(at)}


async def run_queued(
    sessions: SessionFactory,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, int]:
    """Write the questions of the sets teachers pushed, oldest first.

    A board that refuses (its daily cap, its payer out of credit) is left for
    the next tick; a set whose board has switched the analysis off waits too —
    its member can still open it once the board is back on."""
    counts = {"generated": 0, "refused": 0}
    async with sessions() as session:
        pending = [
            (row.id, row.space_id)
            for row in await LearningPracticeRepository(session).queued(
                limit=QUEUE_BATCH
            )
        ]
    refused_spaces: set[int] = set()
    for practice_id, space_id in pending:
        if space_id in refused_spaces:
            continue
        async with sessions() as session:
            space = await session.get(Space, space_id)
            row = await session.get(SpaceLearningPractice, practice_id)
            if (
                space is None
                or not _enabled(space)
                or row is None
                or row.generated_at is not None
                or row.completed_at is not None
            ):
                continue
            try:
                refused = await generate_into(
                    session, space, row, member_id=None, transport=transport
                )
            except Exception:  # one set must not stop the others
                await session.rollback()
                logger.exception("learning practice queue failed id=%s", practice_id)
                continue
            if refused is not None:
                counts["refused"] += 1
                refused_spaces.add(space_id)
            else:
                counts["generated"] += 1
    return counts
