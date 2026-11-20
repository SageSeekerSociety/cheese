"""「数据 › 学情分析」：老师看全班整体（§ learning analysis, step 3）。

十条路由，前缀 ``/spaces/{spaceId}/learning-analysis/class``：

- ``GET .../overview`` 总览；``GET .../points`` 重难点（两条分界可用查询参数调）；
  ``GET .../points/{pointId}`` 知识点详情；``GET .../questions`` 高频问题；
  ``GET .../words`` 高频词与高频报错；``GET .../tasks`` 题目难度；
  ``GET .../behavior`` 提问行为；``GET .../trend`` 趋势；
- ``GET .../excerpts?cluster=`` 或 ``?misconception=`` 一组的匿名对话摘录；
- ``POST .../export`` 导出报告（PDF / Excel / Word），记下谁什么时候导出的。

**门是 ``view_analytics``**，与「数据」页其它分析同一道（管理员、板主、平台管理员）。
现有「学习」分页那四条（``spaces_analytics``）逐项目过 ``may_read_project``，因为它们
返回的是成员项目里的一条条原话；这十条返回的是全班整体，没有一行属于哪个项目，按
项目过门无从下手，所以和其它整体分析同一道门。摘录里「查看原文」的坐标仍逐项目判
（``SpaceLearningClassService._render_excerpts``）。

所有判据 —— 只有整体、不足 3 人不分析、没开就是空的 —— 都在
``app.domain.space.learning_class_service`` 里，这一层只搬数据。这个模块不 import 任何
``app.domain.*.models``（``.importlinter`` 的 C2），也不 import repository。
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import ActorResolverDep
from app.api.request_deps import AuthUser
from app.api.response import ok, typed_response
from app.api.space_deps import get_space_learning_class_service, require_reviewed_space
from app.api.space_payloads import SpaceRow
from app.api.write_access import Can
from app.core.errors import BadRequestError
from app.core.sentences import say
from app.db.session import get_db
from app.domain.space.learning_class_export import MEDIA_TYPES, render
from app.domain.space.learning_class_service import SpaceLearningClassService
from app.domain.space.permissions import Permission as SpacePermission

router = APIRouter(
    prefix="/spaces", tags=["Spaces"], dependencies=[Depends(require_reviewed_space)]
)

#: 这块板的分析归能看「数据」页的人。
SpaceAnalyst = Annotated[SpaceRow, Can(SpacePermission.view_analytics)]
PointId = Annotated[int, Path(ge=0, alias="pointId")]
Service = Annotated[
    SpaceLearningClassService, Depends(get_space_learning_class_service)
]

_C = ConfigDict(populate_by_name=True)


class CompletionOut(BaseModel):
    rate: float | None
    submitted: int
    expected: int


class TaskCompletionOut(BaseModel):
    model_config = _C
    task_id: int = Field(alias="taskId")
    name: str
    analysed: bool
    not_started: int | None = Field(alias="notStarted")
    in_progress: int | None = Field(alias="inProgress")
    submitted: int | None
    passed: int | None


class FocusPointOut(BaseModel):
    model_config = _C
    knowledge_point_id: int = Field(alias="knowledgePointId")
    name: str
    stuck: int
    involved: int


class LearningClassOverviewOut(BaseModel):
    """总览。``enabled`` 为假时这块板没开学情分析，其余为空。"""

    model_config = _C
    enabled: bool
    min_people: int = Field(alias="minPeople")
    updated_at: int | None = Field(alias="updatedAt")
    members: int
    completion: CompletionOut
    messages_this_week: int = Field(alias="messagesThisWeek")
    weak_points: int = Field(alias="weakPoints")
    tasks: list[TaskCompletionOut]
    focus: list[FocusPointOut]


class ClassPointOut(BaseModel):
    """一个知识点在全班的样子；``analysed`` 为假（涉及不足 3 人）时人数都是空。"""

    model_config = _C
    id: int
    name: str
    analysed: bool
    asked: int | None
    involved: int | None
    stuck: int | None
    stuck_rate: float | None = Field(alias="stuckRate")
    quadrant: Literal["both", "hard", "focus", "normal"] | None


class LearningClassPointsOut(BaseModel):
    model_config = _C
    enabled: bool
    min_people: int = Field(alias="minPeople")
    members: int
    asked_ratio: float = Field(alias="askedRatio")
    stuck_ratio: float = Field(alias="stuckRatio")
    asked_threshold: int = Field(alias="askedThreshold")
    points: list[ClassPointOut]


class PointRefOut(BaseModel):
    id: int
    name: str


class CodedQuoteOut(BaseModel):
    """一句代号例句：只标本空间固定代号，原话已遮掉姓名学号。"""

    model_config = _C
    code: str
    quote: str
    task_name: str | None = Field(default=None, alias="taskName")


class MisconceptionOut(BaseModel):
    id: int
    label: str
    people: int
    examples: list[CodedQuoteOut]


class MasteryOut(BaseModel):
    mastered: int
    shaky: int
    weak: int
    untouched: int


class AdviceOut(BaseModel):
    model_config = _C
    text: str
    generated_at: int | None = Field(alias="generatedAt")


class PracticeOutcomeOut(BaseModel):
    """做过巩固题的人里几人升了档（§ step 4）；不足 3 人时整格为空。"""

    practiced: int
    improved: int


class LastPushOut(BaseModel):
    """最近一次「推送巩固题」：什么时候、排上几人（不足 3 人时人数为空）。"""

    at: int | None
    recipients: int | None


class LearningClassPointDetailOut(BaseModel):
    model_config = _C
    enabled: bool
    min_people: int = Field(alias="minPeople")
    knowledge_point: PointRefOut = Field(alias="knowledgePoint")
    analysed: bool
    members: int
    involved: int | None
    stuck: int | None
    new_this_week: int | None = Field(alias="newThisWeek")
    tasks: list[str]
    mastery: MasteryOut | None
    misconceptions: list[MisconceptionOut]
    hidden_misconceptions: int = Field(alias="hiddenMisconceptions")
    advice: AdviceOut | None
    practice: PracticeOutcomeOut | None
    last_push: LastPushOut | None = Field(alias="lastPush")


class ClusterOut(BaseModel):
    model_config = _C
    id: int
    label: str
    knowledge_point_id: int | None = Field(alias="knowledgePointId")
    knowledge_point_name: str | None = Field(alias="knowledgePointName")
    people: int
    asks: int
    trend: Literal["up", "down", "new"] | None
    example: CodedQuoteOut | None


class LearningClassQuestionsOut(BaseModel):
    model_config = _C
    enabled: bool
    min_people: int = Field(alias="minPeople")
    total_questions: int = Field(alias="totalQuestions")
    total_clusters: int = Field(alias="totalClusters")
    hidden_clusters: int = Field(alias="hiddenClusters")
    clusters: list[ClusterOut]


class ExcerptMessageOut(BaseModel):
    role: Literal["member", "agent"]
    code: str | None
    text: str
    at: int | None


class ExcerptSourceOut(BaseModel):
    """「查看原文」的坐标：只有看的人读得了那个项目时才有。"""

    model_config = _C
    topic_id: str = Field(alias="topicId")
    project_id: str = Field(alias="projectId")
    block_id: str = Field(alias="blockId")


class ExcerptOut(BaseModel):
    model_config = _C
    code: str
    task_name: str = Field(alias="taskName")
    at: int | None
    messages: list[ExcerptMessageOut]
    source: ExcerptSourceOut | None


class LearningClassExcerptsOut(BaseModel):
    model_config = _C
    enabled: bool
    min_people: int = Field(alias="minPeople")
    analysed: bool
    title: str
    excerpts: list[ExcerptOut]


class WordOut(BaseModel):
    model_config = _C
    word: str
    count: int
    people: int
    is_new: bool = Field(alias="isNew")
    rising: bool


class ErrorLineOut(BaseModel):
    line: str
    count: int
    people: int


class LearningClassWordsOut(BaseModel):
    model_config = _C
    enabled: bool
    min_people: int = Field(alias="minPeople")
    window_days: int = Field(alias="windowDays")
    words: list[WordOut]
    errors: list[ErrorLineOut]


class TaskDifficultyOut(BaseModel):
    model_config = _C
    task_id: int = Field(alias="taskId")
    name: str
    analysed: bool
    claims: int | None
    completion_rate: float | None = Field(alias="completionRate")
    avg_turns: float | None = Field(alias="avgTurns")
    avg_hours: float | None = Field(alias="avgHours")
    stuck_rate: float | None = Field(alias="stuckRate")
    answer_seekers: int | None = Field(alias="answerSeekers")
    #: 挂着测试点的题：每人每阶段最新那一版提交的测试平均通过率（0–1），和读得出分的
    #: 份数；没有测试点、或不足 3 份时为 null。
    test_pass_rate: float | None = Field(default=None, alias="testPassRate")
    test_judged: int | None = Field(default=None, alias="testJudged")


class LearningClassTasksOut(BaseModel):
    model_config = _C
    enabled: bool
    min_people: int = Field(alias="minPeople")
    tasks: list[TaskDifficultyOut]


class KindShareOut(BaseModel):
    kind: Literal["concept", "error", "answer", "verify", "other"]
    count: int
    share: float


class DependencyOut(BaseModel):
    low: int
    mid: int
    high: int


class LearningClassBehaviorOut(BaseModel):
    """提问行为。``heat`` 是 7 行（周一到周日）× ``slots`` 列，不足 3 人的格子为空。"""

    model_config = _C
    enabled: bool
    min_people: int = Field(alias="minPeople")
    analysed: bool
    total_questions: int = Field(alias="totalQuestions")
    kinds: list[KindShareOut]
    slots: list[str]
    heat: list[list[int | None]]
    dependency: DependencyOut | None


class TrendSeriesOut(BaseModel):
    model_config = _C
    knowledge_point_id: int = Field(alias="knowledgePointId")
    name: str
    values: list[int | None]


class LearningClassTrendOut(BaseModel):
    model_config = _C
    enabled: bool
    min_people: int = Field(alias="minPeople")
    weeks: list[str]
    series: list[TrendSeriesOut]


class LearningExportRequest(BaseModel):
    """导出什么：格式、勾了哪几部分，以及要不要只导出一个知识点。"""

    model_config = _C
    format: Literal["pdf", "xlsx", "docx"]
    sections: list[Literal["tasks", "points", "advice", "excerpts"]]
    knowledge_point_id: int | None = Field(default=None, alias="knowledgePointId")


@router.get(
    "/{spaceId}/learning-analysis/class/overview",
    summary="Read the class overview of a space's learning analysis",
    **typed_response(LearningClassOverviewOut),
)
async def read_learning_class_overview(space: SpaceAnalyst, service: Service) -> dict:
    return ok(await service.overview(space))


@router.get(
    "/{spaceId}/learning-analysis/class/points",
    summary="Read where the class is stuck, per knowledge point",
    **typed_response(LearningClassPointsOut),
)
async def read_learning_class_points(
    space: SpaceAnalyst,
    service: Service,
    asked_ratio: Annotated[float | None, Query(ge=0, le=1, alias="askedRatio")] = None,
    stuck_ratio: Annotated[float | None, Query(ge=0, le=1, alias="stuckRatio")] = None,
) -> dict:
    """两条分界由页面调（查询参数），不存：问到的人占成员的比例、涉及的人里卡住的比例。"""
    return ok(
        await service.points(space, asked_ratio=asked_ratio, stuck_ratio=stuck_ratio)
    )


@router.get(
    "/{spaceId}/learning-analysis/class/points/{pointId}",
    summary="Read one knowledge point as the class sees it",
    **typed_response(LearningClassPointDetailOut),
)
async def read_learning_class_point(
    space: SpaceAnalyst, point_id: PointId, service: Service
) -> dict:
    return ok(await service.point_detail(space, point_id))


@router.get(
    "/{spaceId}/learning-analysis/class/questions",
    summary="Read the class's recurring questions",
    **typed_response(LearningClassQuestionsOut),
)
async def read_learning_class_questions(space: SpaceAnalyst, service: Service) -> dict:
    return ok(await service.questions(space))


@router.get(
    "/{spaceId}/learning-analysis/class/excerpts",
    summary="Read anonymous conversation excerpts of one group",
    **typed_response(LearningClassExcerptsOut),
)
async def read_learning_class_excerpts(
    space: SpaceAnalyst,
    service: Service,
    resolver: ActorResolverDep,
    cluster: Annotated[int | None, Query(ge=0)] = None,
    misconception: Annotated[int | None, Query(ge=0)] = None,
) -> dict:
    """一簇问题（``cluster``）或一种误解（``misconception``）的匿名摘录，二选一。"""
    actor = await resolver.resolve()
    return ok(
        await service.excerpts(
            space,
            viewer=actor.handle if actor.authenticated else None,
            cluster_id=cluster,
            misconception_id=misconception if cluster is None else None,
        )
    )


@router.get(
    "/{spaceId}/learning-analysis/class/words",
    summary="Read the class's frequent words and errors",
    **typed_response(LearningClassWordsOut),
)
async def read_learning_class_words(space: SpaceAnalyst, service: Service) -> dict:
    return ok(await service.words(space))


@router.get(
    "/{spaceId}/learning-analysis/class/tasks",
    summary="Read how hard each problem is for the class",
    **typed_response(LearningClassTasksOut),
)
async def read_learning_class_tasks(space: SpaceAnalyst, service: Service) -> dict:
    return ok(await service.tasks(space))


@router.get(
    "/{spaceId}/learning-analysis/class/behavior",
    summary="Read how the class asks the AI",
    **typed_response(LearningClassBehaviorOut),
)
async def read_learning_class_behavior(space: SpaceAnalyst, service: Service) -> dict:
    return ok(await service.behavior(space))


@router.get(
    "/{spaceId}/learning-analysis/class/trend",
    summary="Read how many are stuck on each knowledge point, week by week",
    **typed_response(LearningClassTrendOut),
)
async def read_learning_class_trend(space: SpaceAnalyst, service: Service) -> dict:
    return ok(await service.trend(space))


@router.post(
    "/{spaceId}/learning-analysis/class/export",
    summary="Export the class learning report",
    response_class=Response,
    responses={
        200: {"content": {media: {} for media in MEDIA_TYPES.values()}},
    },
)
async def export_learning_class_report(
    space: SpaceAnalyst,
    payload: LearningExportRequest,
    auth_user: AuthUser,
    service: Service,
    db=Depends(get_db),
) -> Response:
    """一份只含全班整体的报告文件。导出前记一笔：谁、什么时候、什么格式、哪几部分。"""
    sections: list[str] = [str(s) for s in dict.fromkeys(payload.sections)]
    if not sections:
        raise BadRequestError(say("learningExportEmpty"))
    report = await service.report(
        space, sections=sections, point_id=payload.knowledge_point_id
    )
    content = render(report, payload.format)
    await service.record_export(
        space_id=space.id,
        user_id=auth_user.user_id,
        format=payload.format,
        sections=sections,
        point_id=payload.knowledge_point_id,
    )
    await db.commit()
    return Response(
        content=content,
        media_type=MEDIA_TYPES[payload.format],
        headers={
            "Content-Disposition": (
                f"attachment; filename=learning-report-{space.id}.{payload.format}"
            )
        },
    )
