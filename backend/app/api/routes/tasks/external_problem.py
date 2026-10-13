"""这道题挂在 YOJ 上的题号，和每个领取者在那道题上过没过。

读写这一块的是出题人，或者这块板的管理员 —— 门和看名单、重提审核是同一条
（``may_teach_task``，见 ``app/auth/space_access.py``）。判据不在这儿重写。

**同步在这次请求里做完。** 一次同步是一道题、通常几页（每页 25 行，``MAX_PAGES``
封顶），而对面不通时是**第一次**请求超时就判失败 —— 最坏情况是一次超时，不是八十
次。拿这一点换掉的是「按一下就有结果」，前端不用再配一套轮询。

**谁挂的题号记的是用户 id**（``str(auth_user.user_id)``）：这条路上只有
``AuthUserInfo.user_id``，没有 handle。它只是留档，界面不显示。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.request_deps import DbSession
from app.api.response import Deleted, ok, typed_response
from app.api.task_deps import _require_task
from app.api.write_access import ROUTE_DECIDES
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.auth.space_access import may_teach_task
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.external_problem.schemas import TaskExternalProblemOut
from app.domain.external_problem.service import (
    ExternalProblemService,
    Student,
    SyncTarget,
    binding_view,
)
from app.domain.task.external_roster import student_roster

#: 这一组全是写路由，声明在 router 上：每条都自己判调用者（``_require_teacher``）。
router = APIRouter(prefix="/tasks", dependencies=[ROUTE_DECIDES])


class ExternalProblemIn(BaseModel):
    """挂题号的请求体。**形状由服务判**（``service.PROBLEM_NO`` 只收数字），这里只封
    一个长度上界，免得把一兆的字符串送进去。"""

    problemNo: str = Field(max_length=32)


async def _require_teacher(db: AsyncSession, *, task, user_id: int) -> None:
    """出题人或板管理员。判据是 ``may_teach_task``，这里只负责拒的时候说什么。

    ``task`` 不注类型：这道题的类型来自 ``_require_task``（题目域的服务），而路由
    不许导入 ``app.domain.*.models``（``.importlinter`` 的 C2）。
    """
    if not await may_teach_task(session=db, task=task, user_id=user_id):
        raise ForbiddenError(say("taskOwnerOnly"))


async def _view(service: ExternalProblemService, task_id: int) -> dict:
    """这道题的外站那一块。没挂题号时不用去查结果 —— 那边一定是空的。"""
    binding = await service.binding(task_id)
    if binding is None:
        return binding_view(None, [])
    return binding_view(binding, await service.results(task_id))


@router.get(
    "/{taskId}/external-problem",
    summary="Get External Problem Sync",
    **typed_response(TaskExternalProblemOut),
)
async def get_external_problem(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    db: DbSession,
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """题号、上次同不成功、以及每个领取者的结论。

    没挂题号时也回 200，回的是一块空的（``problemNo: ""``）—— 「这道题还没接外站」
    是一个答案，和名单那条路上的「这道题还没有人报名」一样，不是错误。
    """
    task = await _require_task(db, task_id)
    await _require_teacher(db, task=task, user_id=auth_user.user_id)
    return ok(await _view(ExternalProblemService(db), task_id))


@router.put(
    "/{taskId}/external-problem",
    summary="Set External Problem Number",
    **typed_response(TaskExternalProblemOut),
)
async def set_external_problem(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    body: ExternalProblemIn,
    db: DbSession,
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """挂上、或者换掉这道题在 YOJ 上的题号。返回换完之后的那一块。

    换成一个**另一个**题号会把上一次的结果删掉（``set_problem``）：那些数说的是另一
    道题，留着比空着更糟。同一个题号再挂一次是幂等的，结果原样留着。
    """
    task = await _require_task(db, task_id)
    await _require_teacher(db, task=task, user_id=auth_user.user_id)
    service = ExternalProblemService(db)
    await service.set_problem(
        task_id=task_id, problem_no=body.problemNo, by=str(auth_user.user_id)
    )
    view = await _view(service, task_id)
    await db.commit()
    return ok(view)


@router.delete(
    "/{taskId}/external-problem",
    summary="Clear External Problem",
    **typed_response(Deleted),
)
async def clear_external_problem(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    db: DbSession,
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """撤掉挂载：题号和结果一起没。

    只撤题号、把结果留成孤儿是更小的动作，但那种状态没人能修回来（没有题号就没法再
    同步一次），留着只是让一段读不懂的旧数据活着。
    """
    task = await _require_task(db, task_id)
    await _require_teacher(db, task=task, user_id=auth_user.user_id)
    await ExternalProblemService(db).clear_problem(task_id=task_id)
    await db.commit()
    return ok({"deleted": True})


@router.post(
    "/{taskId}/external-problem/sync",
    summary="Sync External Problem Now",
    **typed_response(TaskExternalProblemOut),
)
async def sync_external_problem(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    db: DbSession,
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """现在去外站看一眼，返回看完之后的那一块。

    同步失败也是 200，回的那一块上 ``status: "failed"`` 带着原因 —— 失败的是「外站
    这一趟没去成」，不是「你这个请求不对」。上一次成功的结论**留着**（见
    ``service.py`` 的取舍），所以界面要照着 ``status`` 说清这是几点的数。
    """
    task = await _require_task(db, task_id)
    await _require_teacher(db, task=task, user_id=auth_user.user_id)
    service = ExternalProblemService(db)
    binding = await service.binding(task_id)
    if binding is None:
        raise NotFoundError(say("externalProblemNotSet"))
    roster = await student_roster(db, task_id)
    await service.sync(
        [
            SyncTarget(
                task_id=task_id,
                problem_no=binding.problem_no,
                students=[
                    Student(member_id=member_id, student_no=student_no)
                    for member_id, student_no in roster
                ],
            )
        ]
    )
    view = await _view(service, task_id)
    await db.commit()
    return ok(view)
