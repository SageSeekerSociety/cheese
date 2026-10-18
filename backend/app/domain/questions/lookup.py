"""questions 领域里「这道题存在吗」的那一处判断。

`QuestionsService` 与 `AnswersService` 原先各抄了一份「题不存在就 404」；收进这里，
两处只留一行转发。软删除的题算不存在，那条判定在 `QuestionRepository.get_by_id`
里，取的是仓储而不是 session，调用方手里拿的正好是它。
"""

from app.core.errors import NotFoundError
from app.domain.questions.repositories import QuestionRepository


async def ensure_question_exists(
    question_repo: QuestionRepository, question_id: int
) -> None:
    """题不存在就 404；存在则什么也不返回。"""
    question = await question_repo.get_by_id(question_id)
    if question is None:
        raise NotFoundError("Question not found", data={"id": question_id})
