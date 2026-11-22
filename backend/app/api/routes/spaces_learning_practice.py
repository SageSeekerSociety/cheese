"""学情分析第 4 步：巩固练习、复习提醒、讲解，与老师的「推送巩固题」。

成员本人的六条（门是 ``view``，同「我的学习洞察」：读写的永远是发请求的人自己的行，
没有「看谁」这个参数；别人的一组题答 404，与不存在同一个样子）：

- ``GET .../learning-analysis/me/practice`` 没做完的几组、复习提醒、今天还能要几次；
- ``POST .../me/practice`` 对一个知识点「生成 3 道巩固题」（``fresh`` 为「换一组」，
  ``source=review`` 为复习时「再做一组」）；
- ``GET .../me/practice/{practiceId}`` 读一组（答过的题才带答案和讲解）；
- ``POST .../me/practice/{practiceId}/answers`` 答一道，当场交回对错和讲解；
- ``POST .../me/practice/{practiceId}/complete`` 「做完了」：并进判断、排下一次复习；
- ``POST .../me/explanation`` 「先看一段讲解」（判断没变时读缓存）。

老师的一条（门是 ``view_analytics``，与知识点详情同一道：按钮就在那一页上，能看那一
页的人就能推）：

- ``POST .../learning-analysis/class/points/{pointId}/push`` 给卡在这个知识点上的成员
  各排一组；只报人数，卡住的不足 3 人不推。

要问模型的三条（出题、讲解）可能答 429（今天的次数用完、没额度）或 503（网关不可
用），照 ``app.api.refusal.refuse`` 的信封，前端按普通 apiError 弹。判据都在
``app.domain.space.learning_practice_service``；这一层不 import models 与 repository。
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, ConfigDict, Field

from app.api.refusal import refuse
from app.api.request_deps import AuthUser
from app.api.response import ok, typed_response
from app.api.space_deps import (
    get_space_learning_practice_service,
    require_reviewed_space,
)
from app.api.space_payloads import SpaceRow
from app.api.write_access import Can
from app.db.session import get_db
from app.domain.space.knowledge_suggestions import Refused
from app.domain.space.learning_practice_service import SpaceLearningPracticeService
from app.domain.space.permissions import Permission as SpacePermission

router = APIRouter(
    prefix="/spaces", tags=["Spaces"], dependencies=[Depends(require_reviewed_space)]
)

_C = ConfigDict(populate_by_name=True)

SpaceReader = Annotated[SpaceRow, Can(SpacePermission.view)]
SpaceAnalyst = Annotated[SpaceRow, Can(SpacePermission.view_analytics)]
PracticeId = Annotated[int, Path(ge=0, alias="practiceId")]
PointId = Annotated[int, Path(ge=0, alias="pointId")]
Service = Annotated[
    SpaceLearningPracticeService, Depends(get_space_learning_practice_service)
]
Level = Literal["mastered", "shaky", "weak"]


class StartPracticeRequest(BaseModel):
    model_config = _C
    knowledge_point_id: int = Field(alias="knowledgePointId", ge=0)
    #: ``self`` 自己点的，``review`` 复习提醒里「再做一组」。
    source: Literal["self", "review"] = "self"
    #: 「换一组」：这个知识点上出好了没做完的那组换掉。
    fresh: bool = False


class AnswerRequest(BaseModel):
    question: int = Field(ge=0)
    choice: int = Field(ge=0)


class ExplanationRequest(BaseModel):
    model_config = _C
    knowledge_point_id: int = Field(alias="knowledgePointId", ge=0)


class OpenPracticeOut(BaseModel):
    model_config = _C
    id: int
    knowledge_point_id: int = Field(alias="knowledgePointId")
    knowledge_point_name: str = Field(alias="knowledgePointName")
    source: Literal["self", "pushed", "review"]
    #: 出好了；为假是老师推送、还排着队。
    ready: bool
    answered: int
    total: int
    created_at: int | None = Field(alias="createdAt")


class ReviewOut(BaseModel):
    model_config = _C
    knowledge_point_id: int = Field(alias="knowledgePointId")
    knowledge_point_name: str = Field(alias="knowledgePointName")
    #: 该复习的那一天（平台日历，YYYY-MM-DD）。
    due_on: str = Field(alias="dueOn")
    streak: int


class PracticeOverviewOut(BaseModel):
    model_config = _C
    enabled: bool
    daily_limit: int = Field(alias="dailyLimit")
    used_today: int = Field(alias="usedToday")
    open: list[OpenPracticeOut]
    #: 已经到期（今天或更早）的复习。
    due: list[ReviewOut]
    #: 还没到期的下一次复习。
    upcoming: ReviewOut | None


class PracticeQuestionOut(BaseModel):
    """一道题。``chosen`` 为空即还没答；答过之后才带 ``answer`` 与 ``explanation``。"""

    stem: str
    options: list[str]
    chosen: int | None
    correct: bool | None
    answer: int | None
    explanation: str | None


class PracticeSetOut(BaseModel):
    model_config = _C
    id: int
    knowledge_point_id: int = Field(alias="knowledgePointId")
    knowledge_point_name: str = Field(alias="knowledgePointName")
    source: Literal["self", "pushed", "review"]
    ready: bool
    done: bool
    questions: list[PracticeQuestionOut]
    correct: int | None
    level_before: Level | None = Field(alias="levelBefore")
    level_after: Level | None = Field(alias="levelAfter")
    created_at: int | None = Field(alias="createdAt")
    completed_at: int | None = Field(alias="completedAt")


class PracticeAnswerOut(BaseModel):
    question: int
    chosen: int
    correct: bool
    answer: int
    explanation: str


class PracticeResultOut(BaseModel):
    model_config = _C
    practice_id: int = Field(alias="practiceId")
    correct: int
    total: int
    level_before: Level | None = Field(alias="levelBefore")
    level_after: Level | None = Field(alias="levelAfter")
    next_review_on: str | None = Field(alias="nextReviewOn")


class ExplanationOut(BaseModel):
    model_config = _C
    knowledge_point_id: int = Field(alias="knowledgePointId")
    text: str
    generated_at: int | None = Field(alias="generatedAt")


class PushOut(BaseModel):
    model_config = _C
    #: 推了没有：卡住的不足 ``minPeople`` 人时不推。
    pushed: bool
    #: 新排上一组的人数；不足 ``minPeople`` 人时为空（只说「不足 3 人」）。
    recipients: int | None
    min_people: int = Field(alias="minPeople")


def _answer(result: dict | Refused):
    if isinstance(result, Refused):
        return refuse(result.status, result.message, result.retry_after_s)
    return ok(result)


@router.get(
    "/{spaceId}/learning-analysis/me/practice",
    summary="Read my practice sets and review reminders",
    **typed_response(PracticeOverviewOut),
)
async def read_my_practice(
    space: SpaceReader, auth_user: AuthUser, service: Service
) -> dict:
    return ok(await service.overview(space, user_id=auth_user.user_id))


@router.post(
    "/{spaceId}/learning-analysis/me/practice",
    summary="Generate a practice set on one knowledge point",
    **typed_response(PracticeSetOut),
)
async def start_my_practice(
    space: SpaceReader,
    payload: StartPracticeRequest,
    auth_user: AuthUser,
    service: Service,
):
    """出 3 道巩固题（或交回这个知识点上没做完的那组）。要问模型：每人每天、每块板
    每天各有上限，到了答 429。"""
    return _answer(
        await service.start(
            space,
            user_id=auth_user.user_id,
            point_id=payload.knowledge_point_id,
            source=payload.source,
            fresh=payload.fresh,
        )
    )


@router.get(
    "/{spaceId}/learning-analysis/me/practice/{practiceId}",
    summary="Read one of my practice sets",
    **typed_response(PracticeSetOut),
)
async def read_my_practice_set(
    space: SpaceReader, practice_id: PracticeId, auth_user: AuthUser, service: Service
) -> dict:
    return ok(
        await service.read(space, user_id=auth_user.user_id, practice_id=practice_id)
    )


@router.post(
    "/{spaceId}/learning-analysis/me/practice/{practiceId}/answers",
    summary="Answer one question of my practice set",
    **typed_response(PracticeAnswerOut),
)
async def answer_my_practice(
    space: SpaceReader,
    practice_id: PracticeId,
    payload: AnswerRequest,
    auth_user: AuthUser,
    service: Service,
    db=Depends(get_db),
) -> dict:
    """第一次选的定下来；再答同一道交回第一次的结果。"""
    result = await service.answer(
        space,
        user_id=auth_user.user_id,
        practice_id=practice_id,
        question=payload.question,
        choice=payload.choice,
    )
    await db.commit()
    return ok(result)


@router.post(
    "/{spaceId}/learning-analysis/me/practice/{practiceId}/complete",
    summary="Finish my practice set and fold it into my learning insight",
    **typed_response(PracticeResultOut),
)
async def complete_my_practice(
    space: SpaceReader,
    practice_id: PracticeId,
    auth_user: AuthUser,
    service: Service,
    db=Depends(get_db),
) -> dict:
    result = await service.complete(
        space, user_id=auth_user.user_id, practice_id=practice_id
    )
    await db.commit()
    return ok(result)


@router.post(
    "/{spaceId}/learning-analysis/me/explanation",
    summary="Read a short explanation of one knowledge point for me",
    **typed_response(ExplanationOut),
)
async def explain_for_me(
    space: SpaceReader,
    payload: ExplanationRequest,
    auth_user: AuthUser,
    service: Service,
):
    return _answer(
        await service.explain(
            space, user_id=auth_user.user_id, point_id=payload.knowledge_point_id
        )
    )


@router.post(
    "/{spaceId}/learning-analysis/class/points/{pointId}/push",
    summary="Push practice sets to the members stuck on a knowledge point",
    **typed_response(PushOut),
)
async def push_learning_practice(
    space: SpaceAnalyst,
    point_id: PointId,
    auth_user: AuthUser,
    service: Service,
    db=Depends(get_db),
) -> dict:
    """不收名单、不回名单：系统自己找卡住的人，老师只看到人数。"""
    result = await service.push(space, point_id=point_id, actor_id=auth_user.user_id)
    await db.commit()
    return ok(result)
