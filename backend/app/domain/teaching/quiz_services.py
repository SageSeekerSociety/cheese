"""小测的规则：谁看得见、什么时候判、谁判。

判「谁能改」不在这里（那是路由先过 ``is_space_admin`` 的门）。这里管的是内容与
时序：题型对不对、答案键的形状对不对（**错形状的答案键会让整题永远判错，而且看
不出来**，所以宁可 400）、截止过没过、主观题判完之后一次作答才算判完。
"""

from datetime import UTC, datetime
from typing import Any

from app.core.errors import BadRequestError, NotFoundError
from app.domain.teaching.models import TeachingUnit
from app.domain.teaching.quiz_models import (
    FILL_BLANK,
    MULTIPLE_CHOICE,
    QUESTION_KINDS,
    SINGLE_CHOICE,
    TRUE_FALSE,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizQuestion,
    is_objective,
)
from app.domain.teaching.quiz_repositories import (
    QuizAnswerRepository,
    QuizAttemptRepository,
    QuizQuestionRepository,
    QuizRepository,
)
from app.domain.teaching.repositories import TeachingUnitRepository


class QuizService:
    def __init__(
        self,
        *,
        quizzes: QuizRepository,
        questions: QuizQuestionRepository,
        attempts: QuizAttemptRepository,
        answers: QuizAnswerRepository,
        units: TeachingUnitRepository,
    ) -> None:
        self._quizzes = quizzes
        self._questions = questions
        self._attempts = attempts
        self._answers = answers
        self._units = units

    # ------------------------------------------------------------------
    # 管理员：出题
    # ------------------------------------------------------------------

    async def create_quiz(
        self,
        *,
        space_id: int,
        unit_id: int,
        actor_id: int,
        title: str,
        due_at: datetime | None = None,
    ) -> Quiz:
        unit = await self._get_unit(space_id=space_id, unit_id=unit_id)
        if not title.strip():
            raise BadRequestError("A quiz needs a title; it is what students see")
        existing = await self._quizzes.get_for_unit(unit_id=unit.id)
        if existing is not None:
            raise BadRequestError(
                "This week already has a quiz", data={"quizId": existing.id}
            )
        return await self._quizzes.create(
            space_id=space_id,
            unit_id=unit.id,
            title=title,
            due_at=due_at,
            created_by=actor_id,
        )

    async def update_quiz(
        self,
        *,
        space_id: int,
        quiz_id: int,
        title: str | None = None,
        due_at: datetime | None = None,
        clear_due_at: bool = False,
    ) -> Quiz:
        quiz = await self._get_quiz(space_id=space_id, quiz_id=quiz_id)
        fields: dict[str, object] = {}
        if title is not None:
            if not title.strip():
                raise BadRequestError("A quiz needs a title; it is what students see")
            fields["title"] = title
        if due_at is not None:
            fields["due_at"] = due_at
        elif clear_due_at:
            fields["due_at"] = None
        return await self._quizzes.update(quiz=quiz, **fields)

    async def delete_quiz(self, *, space_id: int, quiz_id: int) -> None:
        quiz = await self._get_quiz(space_id=space_id, quiz_id=quiz_id)
        await self._quizzes.soft_delete(quiz=quiz)

    async def add_question(
        self,
        *,
        space_id: int,
        quiz_id: int,
        kind: str,
        prompt: str,
        options: list[Any] | None,
        answer: Any,
        points: int,
    ) -> QuizQuestion:
        quiz = await self._get_quiz(space_id=space_id, quiz_id=quiz_id)
        shaped_options, shaped_answer = _check_question(
            kind=kind, prompt=prompt, options=options, answer=answer, points=points
        )
        return await self._questions.create(
            quiz_id=quiz.id,
            position=await self._questions.next_position(quiz_id=quiz.id),
            kind=kind,
            prompt=prompt,
            options=shaped_options,
            answer=shaped_answer,
            points=points,
        )

    async def update_question(
        self,
        *,
        space_id: int,
        quiz_id: int,
        question_id: int,
        kind: str | None = None,
        prompt: str | None = None,
        options: list[Any] | None = None,
        answer: Any = None,
        points: int | None = None,
    ) -> QuizQuestion:
        await self._get_quiz(space_id=space_id, quiz_id=quiz_id)
        question = await self._questions.get_in_quiz(
            quiz_id=quiz_id, question_id=question_id
        )
        if question is None:
            raise NotFoundError("Quiz question not found")
        next_kind = kind if kind is not None else question.kind
        next_prompt = prompt if prompt is not None else question.prompt
        next_options = options if options is not None else question.options
        next_answer = answer if answer is not None else question.answer
        next_points = points if points is not None else question.points
        shaped_options, shaped_answer = _check_question(
            kind=next_kind,
            prompt=next_prompt,
            options=next_options,
            answer=next_answer,
            points=next_points,
        )
        return await self._questions.update(
            question=question,
            kind=next_kind,
            prompt=next_prompt,
            options=shaped_options,
            answer=shaped_answer,
            points=next_points,
        )

    async def delete_question(
        self, *, space_id: int, quiz_id: int, question_id: int
    ) -> None:
        await self._get_quiz(space_id=space_id, quiz_id=quiz_id)
        question = await self._questions.get_in_quiz(
            quiz_id=quiz_id, question_id=question_id
        )
        if question is None:
            raise NotFoundError("Quiz question not found")
        await self._questions.soft_delete(question=question)

    # ------------------------------------------------------------------
    # 管理员：看全班与复核
    # ------------------------------------------------------------------

    async def submissions(self, *, space_id: int, quiz_id: int) -> dict[str, Any]:
        """这次小测的每一份作答：谁、什么时候交的、多少分、判完没有。"""
        quiz = await self._get_quiz(space_id=space_id, quiz_id=quiz_id)
        questions = await self._questions.list_for_quiz(quiz_id=quiz.id)
        max_score = sum(question.points for question in questions)
        attempts = await self._attempts.list_for_quiz(quiz_id=quiz.id)
        answers = await self._answers.list_for_attempts(
            attempt_ids=[attempt.id for attempt in attempts]
        )
        rows = [
            {
                "attemptId": attempt.id,
                "userId": attempt.user_id,
                "submittedAt": attempt.submitted_at,
                "gradedAt": attempt.graded_at,
                "score": _score(answers.get(attempt.id, [])),
                "maxScore": max_score,
            }
            for attempt in attempts
        ]
        return {
            "quiz": quiz,
            "questions": questions,
            "submissions": rows,
            "maxScore": max_score,
        }

    async def review_queue(
        self, *, space_id: int, quiz_id: int
    ) -> list[dict[str, Any]]:
        """等着人判的答案 —— 这次小测里所有 ``awarded_points`` 还是空的那些。"""
        quiz = await self._get_quiz(space_id=space_id, quiz_id=quiz_id)
        rows = await self._answers.list_ungraded_for_quiz(quiz_id=quiz.id)
        return [
            {
                "answerId": answer.id,
                "attemptId": attempt.id,
                "userId": attempt.user_id,
                "submittedAt": attempt.submitted_at,
                "questionId": question.id,
                "kind": question.kind,
                "prompt": question.prompt,
                "referenceAnswer": question.answer,
                "points": question.points,
                "response": answer.response,
            }
            for answer, question, attempt in rows
        ]

    async def grade_answer(
        self,
        *,
        space_id: int,
        quiz_id: int,
        answer_id: int,
        actor_id: int,
        points: int,
        comment: str = "",
    ) -> QuizAnswer:
        quiz = await self._get_quiz(space_id=space_id, quiz_id=quiz_id)
        answer = await self._answers.get_by_id(answer_id=answer_id)
        if answer is None:
            raise NotFoundError("Quiz answer not found")
        attempt = await self._attempts.get_by_id(attempt_id=answer.attempt_id)
        if attempt is None or attempt.quiz_id != quiz.id:
            raise NotFoundError("Quiz answer not found")
        question = await self._questions.get_in_quiz(
            quiz_id=quiz.id, question_id=answer.question_id
        )
        if question is None:
            raise NotFoundError("Quiz question not found")
        if is_objective(question.kind):
            raise BadRequestError(
                "This question is graded on submission; there is nothing to review",
                data={"questionId": question.id},
            )
        if points < 0 or points > question.points:
            raise BadRequestError(
                "The score has to fit the question",
                data={"points": points, "max": question.points},
            )
        now = datetime.now(UTC)
        answer = await self._answers.update(
            answer=answer,
            awarded_points=points,
            comment=comment,
            graded_by=actor_id,
            graded_at=now,
        )
        await self._settle_attempt(attempt=attempt)
        return answer

    # ------------------------------------------------------------------
    # 成员：看题、交卷、看分
    # ------------------------------------------------------------------

    async def quiz_for_unit(
        self, *, space_id: int, unit_id: int, viewer_id: int, can_teach: bool
    ) -> dict[str, Any]:
        unit = await self._get_unit(space_id=space_id, unit_id=unit_id)
        if not can_teach:
            _require_published(unit)
        quiz = await self._quizzes.get_for_unit(unit_id=unit.id)
        if quiz is None:
            return {"quiz": None, "unitId": unit.id}
        return await self._payload(
            quiz=quiz, unit=unit, viewer_id=viewer_id, can_teach=can_teach
        )

    async def quiz_ids_by_unit(self, *, unit_ids: list[int]) -> dict[int, int]:
        """「哪些周有小测」—— 单元列表给每一行标出来用的那一次查询。"""
        return await self._quizzes.quiz_ids_by_unit(unit_ids=unit_ids)

    async def quiz_by_id(
        self, *, space_id: int, quiz_id: int, viewer_id: int, can_teach: bool
    ) -> dict[str, Any]:
        quiz = await self._get_quiz(space_id=space_id, quiz_id=quiz_id)
        unit = await self._get_unit(space_id=space_id, unit_id=quiz.unit_id)
        if not can_teach:
            # 可见性只有一条：单元没发布，它的小测对成员不存在。
            _require_published(unit)
        return await self._payload(
            quiz=quiz, unit=unit, viewer_id=viewer_id, can_teach=can_teach
        )

    async def submit(
        self,
        *,
        space_id: int,
        quiz_id: int,
        user_id: int,
        answers: dict[int, Any],
    ) -> dict[str, Any]:
        """交卷。客观题在这一刻判完；主观题留下一行等着人判。

        ``answers`` 按题号给；**没给的题按「没答」记**（客观题 0 分、主观题躺进
        复核队列）—— 一道没答不该让整张卷子交不上。
        """
        quiz = await self._get_quiz(space_id=space_id, quiz_id=quiz_id)
        unit = await self._get_unit(space_id=space_id, unit_id=quiz.unit_id)
        _require_published(unit)
        _require_open(quiz)
        questions = await self._questions.list_for_quiz(quiz_id=quiz.id)
        known = {question.id for question in questions}
        unknown = set(answers) - known
        if unknown:
            raise BadRequestError(
                "These questions are not on this quiz",
                data={"questionIds": sorted(unknown)},
            )

        attempt = await self._attempts.get_for_user(quiz_id=quiz.id, user_id=user_id)
        if attempt is not None:
            # 重交 = 换一份答案，不追加版本（见 QuizAttempt 的说明）。
            await self._answers.clear_for_attempt(attempt_id=attempt.id)
            await self._attempts.update(
                attempt=attempt,
                submitted_at=datetime.now(UTC),
                graded_at=None,
            )
        else:
            attempt = await self._attempts.create(
                quiz_id=quiz.id,
                space_id=space_id,
                user_id=user_id,
                graded_at=None,
            )

        for question in questions:
            response = answers.get(question.id)
            awarded = (
                _grade_objective(question=question, response=response)
                if is_objective(question.kind)
                else None
            )
            await self._answers.create(
                attempt_id=attempt.id,
                question_id=question.id,
                response=response,
                awarded_points=awarded,
            )
        await self._settle_attempt(attempt=attempt)
        return await self._payload(
            quiz=quiz, unit=unit, viewer_id=user_id, can_teach=False
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _settle_attempt(self, *, attempt: QuizAttempt) -> QuizAttempt:
        """判完了就记时间：还有 ``awarded_points`` 为空的答案就不算判完。"""
        rows = await self._answers.list_for_attempt(attempt_id=attempt.id)
        pending = any(row.awarded_points is None for row in rows)
        return await self._attempts.update(
            attempt=attempt,
            graded_at=None if pending else (attempt.graded_at or datetime.now(UTC)),
        )

    async def _payload(
        self, *, quiz: Quiz, unit: TeachingUnit, viewer_id: int, can_teach: bool
    ) -> dict[str, Any]:
        questions = await self._questions.list_for_quiz(quiz_id=quiz.id)
        attempt = await self._attempts.get_for_user(quiz_id=quiz.id, user_id=viewer_id)
        answers = (
            await self._answers.list_for_attempt(attempt_id=attempt.id)
            if attempt is not None
            else []
        )
        payload: dict[str, Any] = {
            "quiz": quiz,
            "unit": unit,
            "canTeach": can_teach,
            "questions": questions,
            "myAttempt": attempt,
            "myAnswers": _answers_payload(answers),
            "maxScore": sum(question.points for question in questions),
        }
        if can_teach:
            # 管理员版那一屏同时要「全班交得怎么样」与「有什么等着我判」，一次给齐 ——
            # 三条接口分开问除了多两个往返没有任何好处。
            collected = await self.submissions(space_id=quiz.space_id, quiz_id=quiz.id)
            payload["submissions"] = collected["submissions"]
            payload["reviewQueue"] = await self.review_queue(
                space_id=quiz.space_id, quiz_id=quiz.id
            )
        return payload

    async def _get_quiz(self, *, space_id: int, quiz_id: int) -> Quiz:
        quiz = await self._quizzes.get_in_space(space_id=space_id, quiz_id=quiz_id)
        if quiz is None:
            raise NotFoundError("Quiz not found")
        return quiz

    async def _get_unit(self, *, space_id: int, unit_id: int) -> TeachingUnit:
        unit = await self._units.get_in_space(space_id=space_id, unit_id=unit_id)
        if unit is None:
            raise NotFoundError("Teaching unit not found")
        return unit


def _require_published(unit: TeachingUnit) -> None:
    """**可见性只有这一条**：单元没发布，它的小测对成员不存在（404，不是 403）。"""
    now = datetime.now(UTC)
    if unit.published_at is None or unit.published_at > now:
        raise NotFoundError("Quiz not found")


def _require_open(quiz: Quiz) -> None:
    if quiz.due_at is not None and quiz.due_at <= datetime.now(UTC):
        raise BadRequestError(
            "This quiz is closed", data={"dueAt": int(quiz.due_at.timestamp() * 1000)}
        )


def _score(answers: list[QuizAnswer]) -> int:
    """总分是这些行的和，现算 —— 不存第二份真相。"""
    return sum(answer.awarded_points or 0 for answer in answers)


def _attempt_payload(attempt: QuizAttempt) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "userId": attempt.user_id,
        "submittedAt": attempt.submitted_at,
        "gradedAt": attempt.graded_at,
    }


def _answers_payload(answers: list[QuizAnswer]) -> list[dict[str, Any]]:
    """成员看到的每一题：**只有得分与评语，没有答案键** —— 见模块说明。"""
    return [
        {
            "questionId": answer.question_id,
            "response": answer.response,
            "awardedPoints": answer.awarded_points,
            "comment": answer.comment,
            "needsReview": answer.awarded_points is None,
        }
        for answer in answers
    ]


# ----------------------------------------------------------------------
# 题型与判分 —— 客观题在交卷那一刻判完，规则只有这一处
# ----------------------------------------------------------------------


def _check_question(
    *,
    kind: str,
    prompt: str,
    options: list[Any] | None,
    answer: Any,
    points: int,
) -> tuple[list[Any], Any]:
    """把题型的形状检查做完并归一化答案键。

    **为什么要这么严**：答案键的形状错了不会报错，只会让这一题**永远判错**，
    而且从成员那边看不出来（他只知道自己是 0 分）。所以宁可当场 400。
    """
    if kind not in QUESTION_KINDS:
        raise BadRequestError("Unknown question kind", data={"kind": kind})
    if not prompt.strip():
        raise BadRequestError("A question needs a prompt")
    if points < 0:
        raise BadRequestError("Points cannot be negative", data={"points": points})
    clean_options = [str(option) for option in (options or [])]

    if kind in {SINGLE_CHOICE, MULTIPLE_CHOICE}:
        if len(clean_options) < 2:
            raise BadRequestError("A choice question needs at least two options")
        if kind == SINGLE_CHOICE:
            if not isinstance(answer, int) or isinstance(answer, bool):
                raise BadRequestError("A single choice answers with an option index")
            if not 0 <= answer < len(clean_options):
                raise BadRequestError(
                    "That option index is not on this question",
                    data={"answer": answer, "options": len(clean_options)},
                )
            return clean_options, answer
        if not isinstance(answer, list) or not answer:
            raise BadRequestError("A multiple choice answers with option indices")
        indices = sorted({int(index) for index in answer})
        if any(index < 0 or index >= len(clean_options) for index in indices):
            raise BadRequestError("That option index is not on this question")
        return clean_options, indices

    if kind == TRUE_FALSE:
        if not isinstance(answer, bool):
            raise BadRequestError("A true-or-false question answers with true or false")
        return [], answer

    if kind == FILL_BLANK:
        accepted = _accepted_strings(answer)
        if not accepted:
            raise BadRequestError(
                "A fill-in question needs at least one accepted answer"
            )
        return [], accepted

    # SHORT_ANSWER：机器不判分，``answer`` 只是写给管理员看的参考要点。
    if answer is None:
        return [], ""
    if isinstance(answer, str):
        return [], answer
    raise BadRequestError("A short answer's reference is text")


def _accepted_strings(answer: Any) -> list[str]:
    if isinstance(answer, str):
        return [answer] if answer.strip() else []
    if isinstance(answer, list):
        return [str(item) for item in answer if str(item).strip()]
    return []


def _normalize(text: Any) -> str:
    """填空的比对：去两端空白、不分大小写、把连续空白压成一个空格。

    判分宽松到「意思一样就算对」会开始吃同义词，所以只做到这里 —— 要更宽就是多
    写几个可接受答案。
    """
    return " ".join(str(text).strip().casefold().split())


def _grade_objective(*, question: QuizQuestion, response: Any) -> int:
    """客观题当场判分。**多选题全对才给分**（不做部分分）—— 部分分要定义「对一半
    算几成」，那是一个教学判断，不该由这里替管理员决定。"""
    kind = question.kind
    if kind == SINGLE_CHOICE:
        return question.points if response == question.answer else 0
    if kind == TRUE_FALSE:
        correct = isinstance(response, bool) and response == question.answer
        return question.points if correct else 0
    if kind == MULTIPLE_CHOICE:
        given = (
            sorted({int(index) for index in response})
            if isinstance(response, list)
            else []
        )
        expected = sorted({int(index) for index in (question.answer or [])})
        return question.points if given and given == expected else 0
    if kind == FILL_BLANK:
        if response is None or isinstance(response, bool):
            return 0
        accepted = {_normalize(item) for item in _accepted_strings(question.answer)}
        return question.points if _normalize(response) in accepted else 0
    raise BadRequestError("Unknown question kind", data={"kind": kind})
