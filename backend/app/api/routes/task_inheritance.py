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
是在 ``.importlinter`` 的 C3 棘轮上多记一条跨域边 —— 那张表是「解开这个结要付的
代价」，只为**必需**的边记账（``task.teaching`` 已经为过滤课件记了一条），所以资源
清单这一层由路由来做，不再往上加第二条。

**这个模块不 import 任何 ``app.domain.*.models``**（``.importlinter`` 的 C2），也不
import 任何 repository（``tests/unit/test_domain_import_guard.py`` 的棘轮）：两条判据
都由域服务在它那一侧做完，这里只搬数据、拼响应。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.api.routes.spaces import teaching_to_api
from app.api.routes.tasks._common import _require_task
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import NotFoundError
from app.core.storage import get_storage_backend
from app.db.session import get_db
from app.domain.space.material_service import (
    SpaceMaterialService,
    member_readable_material_ids,
)
from app.domain.task.inheritance import TaskInheritance, for_task
from app.domain.task.services import ensure_task_readable

router = APIRouter(prefix="/tasks", tags=["Tasks"])


def _teaching_to_api(teaching, source: str | None, material_ids: list[int]) -> dict:
    """合成后的教学指导摆成接口形状 —— 与空间、项目集、题目那几份读回来的同一套
    camelCase（``teaching_to_api``）；``materialIds`` 换成过滤后的那份，再加一格
    ``source``。

    ``material_ids`` 由调用方过滤后传入（见 ``get_task_inheritance``）：指导里
    点名、但只在「仅管理员」档里的课件，不该出现在这份「会继承什么」的预览里 ——
    否则点名的 id 说会继承、下面的清单里却没有，同一份响应自相矛盾。
    """
    return {
        **teaching_to_api(teaching),
        "materialIds": list(material_ids),
        # 这一份来自哪一层：space / category / task / project，或 null（四层都没
        # 说）。界面据此写「来自项目集」，不是一个只有结果的字符串。
        "source": source,
    }


def _to_api(
    found: TaskInheritance, materials: list[dict], material_ids: list[int]
) -> dict:
    return {
        "taskId": found.task_id,
        "spaceId": found.space_id,
        "resourcePack": found.resource_pack,
        "teaching": _teaching_to_api(
            found.teaching, found.teaching_source, material_ids
        ),
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

    **判据是题目详情那三道闸**（``ensure_task_readable``）：未过审 403、看不见
    404、超出本板上限 404 —— 不是更宽的那一条 ``can_view_task``。后者在题目没开
    可见范围时对任何登录用户都放行，于是「还没过审」和「超出上限」的题会在这里
    把资源包、合成后的指导连同资料名一起交出去。**这份清单不比题本身更公开**：
    它给的是这道题会交出去的东西（算力额度就在里面），而「有没有这份清单」本身
    就是那道题的探针。``/attachments`` 当年正是只走了 ``can_view_task`` 才漏的，
    别再犯第二次。

    资料清单对**非成员**是空的，不是失败：一道没开可见范围的题任何登录用户都看得
    见，但不是每个人都在这块板里，而 ``list_for_space`` 对非成员抛 ``NotFoundError``
    —— 那不是错误，只是「这题带的资料你一份也拿不到」。
    """
    task = await _require_task(db, task_id)
    await ensure_task_readable(session=db, task=task, user_id=auth_user.user_id)

    found = await for_task(session=db, task_id=task_id, user_id=auth_user.user_id)
    if found is None:
        raise NotFoundError("Task not found", data={"type": "task", "id": task_id})

    try:
        # ``list_for_space`` 答的是 ``{"materials": [...], "canManage": bool}`` ——
        # 它自己按档位过滤：非管理员拿不到「仅管理员」那一档。
        listed = (
            await SpaceMaterialService(
                session=db, storage=get_storage_backend()
            ).list_for_space(space_id=found.space_id, user_id=auth_user.user_id)
        )["materials"]
    except NotFoundError:
        listed = []

    # One judgement decides both conclusions, so they cannot diverge: a 课件 that
    # lives only in a board's「仅管理员」tier is neither one the guidance may name
    # (`teaching.materialIds`) nor one that will be brought along (`materials`).
    # The criterion is the shared `member_readable_material_ids` — the same one
    # `for_project` filters through on the read path.
    readable = await member_readable_material_ids(
        db,
        material_ids=[*found.teaching.material_ids, *(m["id"] for m in listed)],
    )
    material_ids = [mid for mid in found.teaching.material_ids if mid in readable]
    materials = [item for item in listed if item["id"] in readable]

    # 裸响应体，不套 ``{"code", "message", "data"}`` —— ``/tasks`` 这一族
    # （详情、附件清单、参与）都是裸的，``TasksApi`` 也是这样读的。同一个
    # router 前缀下两种信封会让 `result.data.…` 有时对有时错。
    return _to_api(found, materials, material_ids)
