"""建题：唯一的 POST /tasks。"""

from fastapi import APIRouter, Depends

from app.api.routes.tasks._common import (
    CreateTaskRequest,
    _create_task_entity,
    _task_attachment_service,
)

# 给了题目的「给 AI 队友的指导」(#944)：请求体沿用 项目集 PATCH 那个严格模型，
# 读写与引用校验在 app.api.task_teaching 里，接口形状在 app.api.task_serialization。
from app.api.task_serialization import (
    _enrich_task_models,
    _task_to_api_model,
)
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db

router = APIRouter(prefix="/tasks")


@router.post(
    "",
    summary="Create Task",
)
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
      ``may_publish_in_space``）；
    - submissionSchema 与 PATCH 一样写入提交表单；topics 只插关系行，不做校验。
    """
    task = await _create_task_entity(
        payload=payload,
        db=db,
        creator_user_id=auth_user.user_id,
    )

    if payload.attachment_ids:
        # 材料是随题一起发出去的，所以挂在这里做：题目已经建好（``_create_task_entity``
        # 里 flush 过），权限那道门也已经在同一个请求里过了一次。文件先由调用者经
        # ``POST /attachments`` 传上来，这里只认 id —— PDF 批量发布那条路（附件在服务
        # 端手上）走的是 ``attach_uploaded_to_tasks``，同一批校验，只是能挂到多道题上。
        await _task_attachment_service(db).attach_uploaded(
            task=task,
            user_id=auth_user.user_id,
            attachment_ids=payload.attachment_ids,
        )

    # 写到这里就完了 —— 题目、话题关系行、提交表单、材料都已落库，下面全是读。
    # 先提交再构造响应：``get_db`` 的提交在 ``yield`` 的退出码里，而那段跑在响应
    # 发出**之后**（FastAPI 0.137 的 ``request_stack`` 在 ``await response(...)``
    # 之后才关），不在这里提交，客户端拿到响应时这道题还没落地，紧接着来读它的
    # 请求就找不到（同 ``spaces.create_space``，合并队列 run 36296605673 实测）。
    await db.commit()

    task_model = _task_to_api_model(task)
    task_model = (await _enrich_task_models(db, [task_model], space_id=task.space_id))[
        0
    ]

    return {
        "code": 200,
        "message": "Task created successfully.",
        "data": {
            "task": task_model,
        },
    }
