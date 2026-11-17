"""学情分析（step 1 的底座）：这块板开不开、它的知识点清单、AI 建议、加入时的告知卡。

八条路由，一个概念 —— 这块板要对成员做学情分析，于是它得先说清分析什么（一份知识点
清单）、告诉每个人（一张告知卡），并由管理员按下开关：

- ``GET /spaces/{spaceId}/learning-analysis`` 开关 + 清单 + 「我能不能改」。**成员
  读得到**（``view``）：发题是成员的能力，「从清单添加」要能看见清单，所以读这一份
  不算管理入口；摆不摆管理入口由响应里的 ``canManage`` 决定，与资料库那条同一条界。
- ``PATCH /spaces/{spaceId}/learning-analysis`` 开 / 关（``edit_settings``）。
- ``POST /spaces/{spaceId}/learning-analysis/knowledge-points`` 往清单末尾加一条。
- ``PATCH .../knowledge-points/{pointId}`` 改一条的名字。
- ``DELETE .../knowledge-points/{pointId}`` 从清单里去掉（软删）。
- ``POST .../knowledge-points/suggestions`` AI 读题面给出建议（下面单独说）。
- ``GET /spaces/{spaceId}/learning-analysis/notice`` 进这块板时要不要弹告知卡（``view``）。
- ``POST .../notice`` 点了「我已阅读并知悉」（``view``，无 body）。
- ``GET .../learning-analysis/me`` 我的学习洞察（``view``）：只读发请求的人自己的那份。
- ``POST .../me/judgments/{judgmentId}/dispute`` 把自己的一条判断标成「不准」（``view``）。

三条判据全在 ``app.domain.space.learning_analysis_service`` 里，这一层只搬数据、拼
响应。**写全都 ``edit_settings``**，而路径参数必须叫 ``spaceId``：外层
``require_reviewed_space`` 与每条路由上的 ``Can(...)`` 都按这个名字取（见
``spaces_materials.py`` 的同一条说明）。

**AI 建议那一问在别处**：它要调网关、要记账（``app.domain.space.knowledge_suggestions``
第 5 层），而这一层只管把请求接住、把 ``Refused`` 翻成一个用户看得懂的非 200 信封。
问一次由发问的那个人付钱（空间没有自己的团队，账本里没有「空间付费」这一档）——同
问芝士的口径。

这个模块不 import 任何 ``app.domain.*.models``（``.importlinter`` 的 C2），也不 import
任何 repository（``tests/unit/test_domain_import_guard.py`` 的棘轮）。
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.refusal import refuse
from app.api.request_deps import AuthUser
from app.api.response import ok, typed_response
from app.api.space_deps import (
    get_space_learning_analysis_service,
    get_space_learning_insight_service,
    require_reviewed_space,
)
from app.api.space_payloads import SpaceRow
from app.api.write_access import Can
from app.db.session import get_db
from app.domain.space import knowledge_suggestions
from app.domain.space import permissions as space_seats
from app.domain.space.learning_analysis_service import SpaceLearningAnalysisService
from app.domain.space.learning_insight_service import SpaceLearningInsightService
from app.domain.space.permissions import Permission as SpacePermission

router = APIRouter(
    prefix="/spaces", tags=["Spaces"], dependencies=[Depends(require_reviewed_space)]
)

PointId = Annotated[int, Path(ge=0, alias="pointId")]
JudgmentId = Annotated[int, Path(ge=0, alias="judgmentId")]

#: 读那几条的落地：能看见这块板就行。成员「从清单添加」靠它。
SpaceReader = Annotated[SpaceRow, Can(SpacePermission.view)]
#: 写那几条的落地：这块板的设置归管理员。
SpaceManager = Annotated[SpaceRow, Can(SpacePermission.edit_settings)]


class PatchLearningAnalysisRequest(BaseModel):
    enabled: bool


class KnowledgePointRequest(BaseModel):
    name: str


class KnowledgeSuggestionRequest(BaseModel):
    """AI 建议的问：题目的名称与描述（纯文本）。缺省空串 —— 一块还没写完的题面
    也可以先问一句。"""

    name: str = ""
    description: str = ""


class LearningAnalysisSwitchOut(BaseModel):
    """开关的三格。``enabled`` 是判据；后两格只用于展示（谁开的、什么时候开的）。"""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool
    enabled_by: int | None = Field(alias="enabledBy")
    enabled_at: int | None = Field(alias="enabledAt")


class KnowledgePointOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    display_order: int = Field(alias="displayOrder")


class SpaceLearningAnalysisOut(BaseModel):
    """``GET`` 的载荷：开关 + 清单 + 我能不能改。"""

    model_config = ConfigDict(populate_by_name=True)

    learning_analysis: LearningAnalysisSwitchOut = Field(alias="learningAnalysis")
    knowledge_points: list[KnowledgePointOut] = Field(alias="knowledgePoints")
    can_manage: bool = Field(alias="canManage")


class LearningAnalysisSwitchReply(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    learning_analysis: LearningAnalysisSwitchOut = Field(alias="learningAnalysis")


class KnowledgePointReply(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    knowledge_point: KnowledgePointOut = Field(alias="knowledgePoint")


class KnowledgeSuggestionOut(BaseModel):
    """一条建议。``isNew`` 由服务端拿现有清单算，不由模型说了算。"""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    why: str
    confidence: Literal["high", "medium"]
    is_new: bool = Field(alias="isNew")


class KnowledgeSuggestionsReply(BaseModel):
    suggestions: list[KnowledgeSuggestionOut]


class LearningNoticeOut(BaseModel):
    """进这块板时那张告知卡的状态：要不要弹。"""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool
    needs_acknowledge: bool = Field(alias="needsAcknowledge")
    acknowledged: bool


class LearningNoticeAckOut(BaseModel):
    acknowledged: bool


@router.get(
    "/{spaceId}/learning-analysis",
    summary="Read a space's learning analysis",
    **typed_response(SpaceLearningAnalysisOut),
)
async def read_space_learning_analysis(
    space: SpaceReader,
    auth_user: AuthUser,
    service: SpaceLearningAnalysisService = Depends(
        get_space_learning_analysis_service
    ),
    db=Depends(get_db),
) -> dict:
    """开关、清单，以及我在这块板能不能改设置。

    ``canManage`` 与资料库那条同一个口径（``spaces_materials.list_space_materials``）：
    界面拿它决定摆不摆管理入口。判据是一次 ``edit_settings`` 的问 —— 管理员、板主，
    以及没有座位但管着平台的平台管理员都算。
    """
    return ok(
        {
            "learningAnalysis": await service.read_switch(space),
            "knowledgePoints": await service.list_points(space.id),
            "canManage": await space_seats.SPACE.allows(
                db, space.id, auth_user.user_id, SpacePermission.edit_settings
            ),
        }
    )


@router.patch(
    "/{spaceId}/learning-analysis",
    summary="Turn a space's learning analysis on or off",
    **typed_response(LearningAnalysisSwitchReply),
)
async def patch_space_learning_analysis(
    space: SpaceManager,
    payload: PatchLearningAnalysisRequest,
    auth_user: AuthUser,
    service: SpaceLearningAnalysisService = Depends(
        get_space_learning_analysis_service
    ),
    db=Depends(get_db),
) -> dict:
    """开 / 关。关掉不删任何东西：清单与已读记录都留着（见 service 的 docstring）。"""
    switch = await service.set_switch(
        space=space, actor_user_id=auth_user.user_id, enabled=payload.enabled
    )
    await db.commit()
    return ok({"learningAnalysis": switch})


@router.post(
    "/{spaceId}/learning-analysis/knowledge-points",
    summary="Add a knowledge point",
    status_code=status.HTTP_201_CREATED,
    **typed_response(KnowledgePointReply),
)
async def add_space_knowledge_point(
    space: SpaceManager,
    payload: KnowledgePointRequest,
    service: SpaceLearningAnalysisService = Depends(
        get_space_learning_analysis_service
    ),
    db=Depends(get_db),
) -> dict:
    point = await service.add_point(space_id=space.id, name=payload.name)
    await db.commit()
    return ok({"knowledgePoint": point})


@router.patch(
    "/{spaceId}/learning-analysis/knowledge-points/{pointId}",
    summary="Rename a knowledge point",
    **typed_response(KnowledgePointReply),
)
async def rename_space_knowledge_point(
    space: SpaceManager,
    point_id: PointId,
    payload: KnowledgePointRequest,
    service: SpaceLearningAnalysisService = Depends(
        get_space_learning_analysis_service
    ),
    db=Depends(get_db),
) -> dict:
    point = await service.rename_point(
        space_id=space.id, point_id=point_id, name=payload.name
    )
    await db.commit()
    return ok({"knowledgePoint": point})


@router.delete(
    "/{spaceId}/learning-analysis/knowledge-points/{pointId}",
    summary="Remove a knowledge point",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_space_knowledge_point(
    space: SpaceManager,
    point_id: PointId,
    service: SpaceLearningAnalysisService = Depends(
        get_space_learning_analysis_service
    ),
    db=Depends(get_db),
) -> None:
    """软删：从清单里去掉，行与名字留着 —— 已经归在它下面的题还显示这个名字。"""
    await service.remove_point(space_id=space.id, point_id=point_id)
    await db.commit()


@router.post(
    "/{spaceId}/learning-analysis/knowledge-points/suggestions",
    summary="Suggest knowledge points from a task's text",
    **typed_response(KnowledgeSuggestionsReply),
)
async def suggest_space_knowledge_points(
    space: SpaceManager,
    payload: KnowledgeSuggestionRequest,
    auth_user: AuthUser,
    service: SpaceLearningAnalysisService = Depends(
        get_space_learning_analysis_service
    ),
    db=Depends(get_db),
):
    """AI 读一遍题面，给出几条知识点（可能含清单里还没有的）。

    网关不可用、这个模型没计价、或者这个人没有额度，都不是错误而是一种「现在问不
    了」——照 ``app.api.refusal.refuse`` 答一个 503 / 429，前端按普通 apiError 弹。
    """
    existing = await service.list_points(space_id=space.id)
    result = await knowledge_suggestions.suggest(
        db,
        user_id=auth_user.user_id,
        name=payload.name,
        description=payload.description,
        existing_names=[point["name"] for point in existing],
    )
    if isinstance(result, knowledge_suggestions.Refused):
        return refuse(result.status, result.message, result.retry_after_s)
    return ok(
        {
            "suggestions": [
                {
                    "name": item.name,
                    "why": item.why,
                    "confidence": item.confidence,
                    "isNew": item.is_new,
                }
                for item in result
            ]
        }
    )


@router.get(
    "/{spaceId}/learning-analysis/notice",
    summary="Whether the learning notice needs acknowledging",
    **typed_response(LearningNoticeOut),
)
async def read_space_learning_notice(
    space: SpaceReader,
    auth_user: AuthUser,
    service: SpaceLearningAnalysisService = Depends(
        get_space_learning_analysis_service
    ),
) -> dict:
    """这块板开着、且这个人还没有确认记录，才要弹 —— 见 service 的 docstring。"""
    return ok(await service.notice_state(space=space, user_id=auth_user.user_id))


@router.post(
    "/{spaceId}/learning-analysis/notice",
    summary="Acknowledge the learning notice",
    **typed_response(LearningNoticeAckOut),
)
async def acknowledge_space_learning_notice(
    space: SpaceReader,
    auth_user: AuthUser,
    service: SpaceLearningAnalysisService = Depends(
        get_space_learning_analysis_service
    ),
    db=Depends(get_db),
) -> dict:
    """记下这次点击。幂等：再点一次不会有第二行，也不改第一次的时间。"""
    result = await service.acknowledge(space_id=space.id, user_id=auth_user.user_id)
    await db.commit()
    return ok(result)


class LearningEvidenceOut(BaseModel):
    """一条依据：这个成员自己的一条消息的摘录，以及它在哪道题里。"""

    model_config = ConfigDict(populate_by_name=True)

    block_id: str = Field(alias="blockId")
    task_id: int | None = Field(alias="taskId")
    task_name: str = Field(alias="taskName")
    quote: str
    created_at: int | None = Field(alias="createdAt")


class LearningJudgmentOut(BaseModel):
    """AI 对这个知识点的当前判断。``disputed``：本人标过「不准」。"""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    level: Literal["mastered", "shaky", "weak"]
    summary: str
    disputed: bool
    analyzed_at: int | None = Field(alias="analyzedAt")
    evidence: list[LearningEvidenceOut]


class LearningInsightPointOut(BaseModel):
    """清单里的一个知识点；``judgment`` 为空即「未涉及」。"""

    id: int
    name: str
    judgment: LearningJudgmentOut | None


class FrequentQuestionOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    knowledge_point_id: int | None = Field(alias="knowledgePointId")
    knowledge_point_name: str | None = Field(alias="knowledgePointName")
    kind: Literal["concept", "error", "answer", "verify", "other"]
    count: int
    example: str


class LearningSourceTaskOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: int = Field(alias="taskId")
    name: str
    messages_read: int = Field(alias="messagesRead")


class LearningSourcesOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    messages_read: int = Field(alias="messagesRead")
    tasks: list[LearningSourceTaskOut]


class MyLearningInsightOut(BaseModel):
    """「我的学习洞察」。``enabled`` 为假时这块板没开学情分析，其余都是空的。"""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool
    updated_at: int | None = Field(alias="updatedAt")
    knowledge_points: list[LearningInsightPointOut] = Field(alias="knowledgePoints")
    focus_knowledge_point_id: int | None = Field(alias="focusKnowledgePointId")
    frequent_questions: list[FrequentQuestionOut] = Field(alias="frequentQuestions")
    sources: LearningSourcesOut


class LearningDisputeOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    judgment_id: int = Field(alias="judgmentId")
    disputed: bool


@router.get(
    "/{spaceId}/learning-analysis/me",
    summary="Read my own learning insight in a space",
    **typed_response(MyLearningInsightOut),
)
async def read_my_learning_insight(
    space: SpaceReader,
    auth_user: AuthUser,
    service: SpaceLearningInsightService = Depends(get_space_learning_insight_service),
) -> dict:
    """只有本人：没有「看谁」这个参数，读的永远是发请求的人自己的那份。"""
    return ok(await service.mine(space=space, user_id=auth_user.user_id))


@router.post(
    "/{spaceId}/learning-analysis/me/judgments/{judgmentId}/dispute",
    summary="Mark one of my learning judgments as inaccurate",
    **typed_response(LearningDisputeOut),
)
async def dispute_my_learning_judgment(
    space: SpaceReader,
    judgment_id: JudgmentId,
    auth_user: AuthUser,
    service: SpaceLearningInsightService = Depends(get_space_learning_insight_service),
    db=Depends(get_db),
) -> dict:
    """标记不准：只能标自己的（别人的判断答 404，与不存在同一个样子）。"""
    result = await service.dispute(
        space=space, user_id=auth_user.user_id, judgment_id=judgment_id
    )
    await db.commit()
    return ok(result)
