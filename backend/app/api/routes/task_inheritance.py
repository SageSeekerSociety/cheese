"""``GET /tasks/{taskId}/inheritance`` —— 建项目之前，这道题会交出什么 (#944)。

**为什么是一条新路由文件。** 「领这道题会拿到什么」是一个新的读问题，不是现有某条
路由的字段。把它塞进 ``GET /tasks/{id}`` 会让那道接口在每次列表、每次预览时都多算
一遍资料清单（那是一串额外查询），而这只在按下「领取」的那一步需要。``tasks.py``
也已经贴着文件行数上限，新代码没有落脚的地方。

**分两层，因为两条来源在两个域。** 协议与指导由
``app.domain.task.inheritance.for_task`` 给（它复用 ``protocol.resolve()`` 合成四层
指导，连「来自哪一层」都是那一个循环带出来的）。资料是 ``app.domain.space`` 那一域
的行，只能经 ``SpaceMaterialService`` 拿 —— 路由 import 一个域服务是允许的（
``routes/spaces_materials.py`` 就是这么用的），而 ``app.domain.task`` 去 import 它就
是跨域的新边，``.importlinter`` 的 C3 不许新增。

**这个模块不 import 任何 ``app.domain.*.models``**（``.importlinter`` 的 C2），也不
import 任何 repository（``tests/unit/test_domain_import_guard.py`` 的棘轮）：两条判据
都由域服务在它那一侧做完，这里只搬数据、拼响应。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import NotFoundError
from app.core.storage import get_storage_backend
from app.db.session import get_db
from app.domain.space.material_service import SpaceMaterialService
from app.domain.task.inheritance import TaskInheritance, for_task

router = APIRouter(prefix="/tasks", tags=["Tasks"])

#: 「会被带上的资料」只列这一档 —— 名字取自
#: ``app.domain.space.models.SpaceMaterialVisibility``。管理员看得到「仅管理员」
#: 那一档的行，但那几行**不会**跟着项目走（项目成员打不开），把它们列进「会被
#: 带上」是骗人（符露夀 2026-10-02 定：这一档不进清单）。
_MEMBERS_VISIBILITY = "members"


def _teaching_to_api(teaching, source: str | None) -> dict:
    """合成后的教学指导摆成接口形状 —— 字段名与 ``TeachingRequest`` 同一套
    camelCase，前端读回来的一份和它写进去的一份因此长得一样。"""
    return {
        "systemPrompt": teaching.system_prompt,
        "currentWeek": teaching.current_week,
        "allowedTopics": list(teaching.allowed_topics),
        "avoidInCode": list(teaching.avoid_in_code),
        "materialIds": list(teaching.material_ids),
        "knowledgeIds": list(teaching.knowledge_ids),
        # 这一份来自哪一层：space / category / task / project，或 null（四层都没
        # 说）。界面据此写「来自项目集」，不是一个只有结果的字符串。
        "source": source,
    }


def _to_api(found: TaskInheritance, materials: list[dict]) -> dict:
    return {
        "taskId": found.task_id,
        "spaceId": found.space_id,
        "resourcePack": found.resource_pack,
        "teaching": _teaching_to_api(found.teaching, found.teaching_source),
        "materials": materials,
    }


@router.get(
    "/{taskId}/inheritance",
    summary="Get Task Inheritance",
)
async def get_task_inheritance(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """建项目会继承的资源包、合成后的教学指导（含来源层）、会被带上的资料。

    与 ``GET /tasks/{id}`` 同一条可见性判据：看不见这题就 404，不是 403 —— 一块你
    不在的板子上的题不该被确认存在（``TaskVisibilityService.can_view_task``）。

    资料清单对**非成员**是空的，不是失败：一道没开可见范围的题任何登录用户都看得
    见，但不是每个人都在这块板里，而 ``list_for_space`` 对非成员抛 ``NotFoundError``
    —— 那不是错误，只是「这题带的资料你一份也拿不到」。
    """
    found = await for_task(session=db, task_id=task_id, user_id=auth_user.user_id)
    if found is None:
        raise NotFoundError("Task not found", data={"type": "task", "id": task_id})

    try:
        listed = await SpaceMaterialService(
            session=db, storage=get_storage_backend()
        ).list_for_space(space_id=found.space_id, user_id=auth_user.user_id)
    except NotFoundError:
        listed = []
    materials = [
        item for item in listed if item.get("visibility") == _MEMBERS_VISIBILITY
    ]

    # 裸响应体，不套 ``{"code", "message", "data"}`` —— ``/tasks`` 这一族
    # （详情、附件清单、参与）都是裸的，``TasksApi`` 也是这样读的。同一个
    # router 前缀下两种信封会让 `result.data.…` 有时对有时错。
    return _to_api(found, materials)
