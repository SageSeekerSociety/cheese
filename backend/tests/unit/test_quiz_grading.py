"""小测的两条判据，单独测：**答案键的形状**与**客观题的判分**。

它们值得单独测是因为**错的时候看不出来**：答案键形状写错不会报错，只会让这道题
永远判 0（学生只知道自己是 0 分）；部分给分要不要给，是一个教学判断，得钉死在
「多选题全对才给分」上，别哪天被顺手改成对一半给一半。

另外测一条纪律：**已经自动判完的题不能走复核接口改分** —— 那不是复核，是改卷。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.errors import BadRequestError
from app.domain.teaching.quiz_models import QuizQuestion
from app.domain.teaching.quiz_services import (
    QuizService,
    _check_question,
    _grade_objective,
)


def _question(kind: str, answer: Any, *, points: int = 2) -> QuizQuestion:
    return QuizQuestion(
        id=1,
        quiz_id=1,
        position=1,
        kind=kind,
        prompt="题",
        options=[],
        answer=answer,
        points=points,
        created_at=None,
        updated_at=None,
        deleted_at=None,
    )


# --- 答案键的形状 -------------------------------------------------------------


def test_a_choice_key_outside_its_options_is_refused():
    """答案写了个不存在的选项 —— 这题本来会永远判错，所以当场 400。"""
    with pytest.raises(BadRequestError):
        _check_question(
            kind="SINGLE_CHOICE", prompt="题", options=["甲", "乙"], answer=5, points=1
        )
    with pytest.raises(BadRequestError):
        _check_question(
            kind="SINGLE_CHOICE", prompt="题", options=["甲"], answer=0, points=1
        )


def test_a_true_or_false_key_must_be_a_bool():
    with pytest.raises(BadRequestError):
        _check_question(
            kind="TRUE_FALSE", prompt="题", options=[], answer="yes", points=1
        )


def test_a_fill_in_needs_at_least_one_accepted_answer():
    with pytest.raises(BadRequestError):
        _check_question(kind="FILL_BLANK", prompt="题", options=[], answer=[], points=1)


def test_an_unknown_kind_is_refused():
    with pytest.raises(BadRequestError):
        _check_question(kind="ESSAY", prompt="题", options=[], answer="x", points=1)


def test_a_multiple_choice_key_is_deduplicated_and_ordered():
    """重复的选项下标不是错，但存下来要是规范形状 —— 判分靠的是集合相等。"""
    _, answer = _check_question(
        kind="MULTIPLE_CHOICE",
        prompt="题",
        options=["甲", "乙", "丙"],
        answer=[2, 0, 0],
        points=3,
    )
    assert answer == [0, 2]


# --- 判分 ---------------------------------------------------------------------


def test_single_choice_and_true_or_false_are_all_or_nothing():
    assert _grade_objective(question=_question("SINGLE_CHOICE", 1), response=1) == 2
    assert _grade_objective(question=_question("SINGLE_CHOICE", 1), response=0) == 0
    assert _grade_objective(question=_question("TRUE_FALSE", True), response=True) == 2
    assert _grade_objective(question=_question("TRUE_FALSE", True), response=False) == 0
    # 没答不是「答错」以外的东西，但它也不该被当成 True。
    assert _grade_objective(question=_question("TRUE_FALSE", False), response=None) == 0
    assert _grade_objective(question=_question("TRUE_FALSE", False), response=0) == 0


def test_a_multiple_choice_needs_every_right_option_and_no_wrong_one():
    question = _question("MULTIPLE_CHOICE", [0, 2], points=4)
    assert _grade_objective(question=question, response=[2, 0]) == 4
    assert _grade_objective(question=question, response=[0]) == 0
    assert _grade_objective(question=question, response=[0, 1, 2]) == 0
    assert _grade_objective(question=question, response=[]) == 0


def test_a_fill_in_ignores_case_and_extra_spaces_but_nothing_else():
    question = _question("FILL_BLANK", ["int", "Integer"], points=3)
    assert _grade_objective(question=question, response="  int ") == 3
    assert _grade_objective(question=question, response="INTEGER") == 3
    assert _grade_objective(question=question, response="long") == 0
    # 没答不该被当成字符串 "None" 去比对。
    assert _grade_objective(question=question, response=None) == 0


# --- 已经判完的题不能走复核 ---------------------------------------------------


class _FakeQuizzes:
    def __init__(self, quiz: Any) -> None:
        self._quiz = quiz

    async def get_in_space(self, *, space_id: int, quiz_id: int) -> Any:
        return self._quiz


class _FakeQuestions:
    def __init__(self, question: Any) -> None:
        self._question = question

    async def get_in_quiz(self, *, quiz_id: int, question_id: int) -> Any:
        return self._question


class _FakeAnswers:
    def __init__(self, answer: Any) -> None:
        self._answer = answer
        self.updated: dict[str, Any] | None = None

    async def get_by_id(self, *, answer_id: int) -> Any:
        return self._answer

    async def update(self, *, answer: Any, **fields: Any) -> Any:
        self.updated = fields
        return answer


class _FakeAttempts:
    def __init__(self, attempt: Any) -> None:
        self._attempt = attempt

    async def get_by_id(self, *, attempt_id: int) -> Any:
        return self._attempt

    async def update(self, *, attempt: Any, **fields: Any) -> Any:
        return attempt

    async def list_for_quiz(self, *, quiz_id: int) -> list[Any]:
        return [self._attempt]


async def test_an_automatically_graded_answer_cannot_be_reviewed():
    quiz = SimpleNamespace(id=7, space_id=3)
    objective = SimpleNamespace(id=11, kind="SINGLE_CHOICE", points=2)
    answer = SimpleNamespace(id=21, attempt_id=31, question_id=11)
    attempt = SimpleNamespace(id=31, quiz_id=7)
    service = QuizService(
        quizzes=_FakeQuizzes(quiz),
        questions=_FakeQuestions(objective),
        attempts=_FakeAttempts(attempt),
        answers=_FakeAnswers(answer),
        units=SimpleNamespace(),
    )
    with pytest.raises(BadRequestError):
        await service.grade_answer(
            space_id=3, quiz_id=7, answer_id=21, actor_id=1, points=2
        )
