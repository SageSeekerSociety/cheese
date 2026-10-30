"""建题：唯一的 POST /tasks。"""

from fastapi import APIRouter, Depends

from app.api.response import ok
from app.api.routes.tasks._common import CreateTaskRequest
from app.api.routes.tasks._writes import create_task_from
from app.api.write_access import ROUTE_DECIDES
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db

router = APIRouter(prefix="/tasks")


@router.post("", summary="Create Task", dependencies=[ROUTE_DECIDES])
async def create_task(
    payload: CreateTaskRequest,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """Create a new task (simplified port of Kotlin TaskService.createTask).

    NOTE:
    - 权限：调用者必须是 ``space`` 的成员（在成员名册里，或所有者在管理员关系里），
      否则 403 —— 从前这条 route 只要求提供 space 并验证 category 归属，任何登录
      用户都能往别人的板里发题；中间一度收成「只有管理员能发」，重设计后放开为
      「板里的人都能发、所有者与管理员审」（见 ``_create_task_entity`` 里的
      ``space_seats`` 的 ``publish``）；
    - submissionSchema 与 PATCH 一样写入提交表单；topics 只插关系行，不做校验。
    """
    return ok(
        await create_task_from(payload, db=db, user_id=auth_user.user_id),
        "Task created successfully.",
    )
