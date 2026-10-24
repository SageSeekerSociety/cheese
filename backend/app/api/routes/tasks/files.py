"""题目的材料：题目资料库（`Shelf.of_task`）里出题人放进来的文件。

列出、下载、放进、替换、删掉。字节不经过这里上传：先领一个上传地址
（``POST /tasks/{id}/files/uploads``），浏览器把字节直接放进存储，再拿领到的凭据来
登记。放进、替换、删掉一份，题目记一版（`app.domain.task.package`）。

看得见这道题就能看到清单；下载要是出题人、空间管理员或已经领取的人（`package` 里的
三条判据）。
"""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.byte_response import blob_response
from app.api.response import ok, typed_response
from app.api.task_deps import _require_task
from app.api.write_access import ROUTE_DECIDES
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import ValidationError
from app.core.sentences import say
from app.db.session import get_db
from app.domain.library import records as library_records
from app.domain.library import service as library
from app.domain.task import package
from app.domain.task.services import ensure_task_readable

router = APIRouter(prefix="/tasks")

TaskId = Annotated[int, Path(ge=1, alias="taskId")]


class TaskFileOut(BaseModel):
    """一份材料：名字（也是它的地址）、多大、什么时候放进来的（毫秒）。"""

    name: str
    size: int
    addedAt: int


class TaskFilesOut(BaseModel):
    files: list[TaskFileOut]
    #: 这个人能不能下载；不能的只看到名字。
    canDownload: bool


class TaskFileUploadSlotIn(BaseModel):
    name: str
    size: int


class TaskFileUploadSlot(BaseModel):
    """把字节原样 PUT 到 ``url``（以 ``/`` 开头的是本后端的地址），再拿 ``upload``
    来登记。"""

    url: str
    upload: str


class TaskFileAddIn(BaseModel):
    upload: str
    filename: str


class TaskFileReplaceIn(BaseModel):
    upload: str


class TaskFilePlacedOut(BaseModel):
    name: str
    size: int


class TaskFileRemovedOut(BaseModel):
    removed: bool


def _file(row) -> dict:
    return {
        "name": row.name,
        "size": row.bytes,
        "addedAt": int(row.created_at.timestamp() * 1000),
    }


def _name(raw: str) -> str:
    name = (raw or "").strip()
    if not name or name.startswith("/") or ".." in name.split("/"):
        raise ValidationError(say("libraryPathRelative"))
    return name


@router.get(
    "/{taskId}/files", summary="List Task Files", **typed_response(TaskFilesOut)
)
async def list_task_files(
    task_id: TaskId,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """看得见这道题的人都拿得到清单；``canDownload`` 说这个人能不能下载。

    「看得见」用的是题目详情那三道闸（``ensure_task_readable``）：清单不比题更公开，
    文件名常常就是答案。"""
    task = await _require_task(db, task_id)
    await ensure_task_readable(session=db, task=task, user_id=auth_user.user_id)
    rows = await package.files(db, task.id)
    return ok(
        {
            "files": [_file(row) for row in rows],
            "canDownload": await package.may_download(db, task, auth_user.user_id),
        }
    )


#: 一份材料的字节，原样给出。
BYTES = {
    "response_class": Response,
    "responses": {
        200: {
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            }
        }
    },
}


@router.get("/{taskId}/files/raw", summary="Download Task File", **BYTES)
async def download_task_file(
    task_id: TaskId,
    name: str,
    request: Request,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> Response:
    """一份材料的字节。先过题目详情那三道闸，再看是不是出题人、空间管理员或领取的人。"""
    task = await _require_task(db, task_id)
    await ensure_task_readable(session=db, task=task, user_id=auth_user.user_id)
    await package.refuse_unless_downloader(db, task, auth_user.user_id)
    name = _name(name)
    stored = await library_records.stored_file(db, package.shelf(task.id), name)
    filename = quote(name.rsplit("/", 1)[-1], safe="")
    return blob_response(
        stored.location,
        stored.key,
        stored.size,
        "application/octet-stream",
        {
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            # 同一个名字可以被替换成新的一份。
            "Cache-Control": "private, no-cache",
        },
        request.headers.get("range"),
        request.headers.get("if-range"),
    )


@router.post(
    "/{taskId}/files/uploads",
    summary="Task File Upload Slot",
    dependencies=[ROUTE_DECIDES],
    **typed_response(TaskFileUploadSlot),
)
async def task_file_upload_slot(
    task_id: TaskId,
    body: TaskFileUploadSlotIn,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """领一个上传地址（出题人本人或空间管理员）。超过上限的不给地址。"""
    task = await _require_task(db, task_id)
    await package.refuse_unless_teacher(db, task, auth_user.user_id)
    if body.size <= 0:
        raise ValidationError(say("emptyFile"))
    name = library.clean_upload_name(body.name)
    return ok(
        await library_records.upload_slot(package.shelf(task.id), name, body.size)
    )


@router.post(
    "/{taskId}/files",
    summary="Add Task File",
    status_code=201,
    dependencies=[ROUTE_DECIDES],
    **typed_response(TaskFilePlacedOut),
)
async def add_task_file(
    task_id: TaskId,
    body: TaskFileAddIn,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """把传上来的一份登记成这道题的材料，题目记一版。撞名不覆盖：拿下一个 `(n)`。"""
    task = await _require_task(db, task_id)
    await package.refuse_unless_teacher(db, task, auth_user.user_id)
    upload = await library_records.received(package.shelf(task.id), body.upload)
    name = await package.add_file(
        db,
        task,
        by=auth_user.user_id,
        filename=library.clean_upload_name(body.filename),
        data=upload,
    )
    await package.record(
        db, task, by=auth_user.user_id, changes=package.files_changed(added=[name])
    )
    await db.commit()
    return ok({"name": name, "size": upload.size})


@router.put(
    "/{taskId}/files",
    summary="Replace Task File",
    dependencies=[ROUTE_DECIDES],
    **typed_response(TaskFilePlacedOut),
)
async def replace_task_file(
    task_id: TaskId,
    name: str,
    body: TaskFileReplaceIn,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """把一份材料换成传上来的新版本，旧的留作历史，题目记一版。"""
    task = await _require_task(db, task_id)
    await package.refuse_unless_teacher(db, task, auth_user.user_id)
    upload = await library_records.received(package.shelf(task.id), body.upload)
    name = _name(name)
    await package.replace_file(db, task, by=auth_user.user_id, name=name, data=upload)
    await db.commit()
    return ok({"name": name, "size": upload.size})


@router.delete(
    "/{taskId}/files",
    summary="Remove Task File",
    dependencies=[ROUTE_DECIDES],
    **typed_response(TaskFileRemovedOut),
)
async def remove_task_file(
    task_id: TaskId,
    name: str,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """扔掉一份材料（连同被替换下来的几版），题目记一版。"""
    task = await _require_task(db, task_id)
    await package.refuse_unless_teacher(db, task, auth_user.user_id)
    await package.remove_file(db, task, by=auth_user.user_id, name=_name(name))
    await db.commit()
    return ok({"removed": True})
