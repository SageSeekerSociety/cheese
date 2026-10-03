"""题目附件：列表、上传、下载、删除。"""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Path, UploadFile
from fastapi.responses import Response

from app.api.routes.tasks._common import (
    _require_task,
    _task_attachment_service,
)

# 给了题目的「给 AI 队友的指导」(#944)：请求体沿用 项目集 PATCH 那个严格模型，
# 读写与引用校验在 app.api.task_teaching 里，接口形状在 app.api.task_serialization。
from app.api.task_serialization import (
    _task_attachment_to_api,
)
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db
from app.domain.task.services import (
    ensure_task_readable,
)

router = APIRouter(prefix="/tasks")


@router.get(
    "/{taskId}/attachments",
    summary="List Task Attachments",
)
async def list_task_attachments(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """清单对**看得见这道题的人**都可见，能不能下载单独用一个标志告诉前端。

    判据放在服务里，两件事一起算：看得见才给清单（否则 403），能不能下载按
    「出题人 / 板管理员 / 已领取者」。前端只据此决定那行显示「下载」还是
    「领取这道题之后才能下载」，不自己猜。

    「看得见这道题」用的是题目详情那三道闸（``ensure_task_readable``），不是
    服务里那条更宽的 ``can_view_task``：后者在题目没开可见范围时对任何登录用户
    都放行，于是未审批（403）与超出板上限（404）的题会在这里把材料清单交出去。
    清单不比题更公开 —— 文件名常常就是答案，而「有没有清单」本身就是那道题的
    探针。
    """
    task = await _require_task(db, task_id)
    await ensure_task_readable(session=db, task=task, user_id=auth_user.user_id)
    files, links, can_download = await _task_attachment_service(db).list_for_task(
        task=task, user_id=auth_user.user_id
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "attachments": [
                _task_attachment_to_api(attachment=attachment, link=link)
                for attachment, link in zip(files, links, strict=True)
            ],
            "canDownload": can_download,
        },
    }


@router.post(
    "/{taskId}/attachments",
    summary="Upload Task Attachment",
    status_code=201,
)
async def upload_task_attachment(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    file: Annotated[UploadFile, File(...)],
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """传一个文件并挂到这道题上：出题人本人或板管理员。

    大小不在这里判：这条路由与 ``POST /attachments`` 走的是同一个
    ``AttachmentService.upload``，单份文件的上限在那一处判一次就够
    （``settings.attachment_max_bytes``），报出去的也是那个数
    （``GET /attachments/limits``）。
    """
    task = await _require_task(db, task_id)
    attachment, link = await _task_attachment_service(db).add(
        task=task,
        user_id=auth_user.user_id,
        file=file.file,
        filename=file.filename or "unnamed",
        content_type=file.content_type,
    )
    return {
        "code": 201,
        "message": "Created",
        "data": {
            "attachment": _task_attachment_to_api(attachment=attachment, link=link)
        },
    }


@router.get(
    "/{taskId}/attachments/{attachmentId}/download",
    summary="Download Task Attachment",
)
async def download_task_attachment(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    attachment_id: Annotated[int, Path(ge=1, alias="attachmentId")],
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> Response:
    """下载一道题的材料。

    先过题目详情那三道闸（``ensure_task_readable``），再看「你来不来得到」：
    一道 403 / 404 的题，它的材料连「拿不到（403）」这个回答都不该给 —— 对读者
    来说这道题不存在，回答里不该有它的 id 之外的任何东西。
    """
    task = await _require_task(db, task_id)
    await ensure_task_readable(session=db, task=task, user_id=auth_user.user_id)
    content, filename, content_type = await _task_attachment_service(db).download(
        task=task,
        user_id=auth_user.user_id,
        attachment_id=attachment_id,
    )
    # ``filename*=UTF-8''…`` 而不是裸引号：中文文件名直接写进 header 会让
    # Starlette 按 latin-1 编码时报错（下载一个中文名的材料变成 500）。
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": (
                "attachment; filename*=UTF-8''" + quote(filename, safe="")
            )
        },
    )


@router.delete(
    "/{taskId}/attachments/{attachmentId}",
    summary="Remove Task Attachment",
    status_code=204,
)
async def remove_task_attachment(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    attachment_id: Annotated[int, Path(ge=1, alias="attachmentId")],
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> None:
    await _task_attachment_service(db).remove(
        task=await _require_task(db, task_id),
        user_id=auth_user.user_id,
        attachment_id=attachment_id,
    )
