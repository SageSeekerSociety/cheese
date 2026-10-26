"""题目的材料：题目资料库（`Shelf.of_task`）里出题人放进来的文件。

列出、下载、放进、替换、删掉。字节不经过这里上传，和资料库是同一条分段上传的路
（`library.records`）：先领一次上传的凭据（``POST /tasks/{id}/files/uploads``），
按段领地址（``…/uploads/part``），浏览器把每一段连同它的 MD5 直接放进存储，再拿凭据、
整份的 SHA-256 和每段的 MD5 来登记。放进、替换、删掉一份，题目记一版
（`app.domain.task.package`）。起始代码走同一条上传的路，登记在 ``PUT /tasks/{id}/starter``。

看得见这道题就能看到清单；下载要是出题人、空间管理员或参与者（申请通过的人）（`package` 里的
三条判据）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.library_bytes import library_file_response
from app.api.response import ok, typed_response
from app.api.task_deps import _require_task
from app.api.write_access import needs
from app.auth import task_roles as task_seats
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.auth.task_roles import Permission as TaskPermission
from app.core.errors import ValidationError
from app.core.sentences import say
from app.db.session import get_db
from app.domain.library import records as library_records
from app.domain.library import service as library
from app.domain.task import package
from app.domain.user.services import user_service

router = APIRouter(prefix="/tasks")

TaskId = Annotated[int, Path(ge=1, alias="taskId")]


class TaskFileOut(BaseModel):
    """一份材料：名字（也是它的地址）、多大、什么时候放进来的（毫秒）。"""

    name: str
    size: int
    addedAt: int


class TaskFilesOut(BaseModel):
    files: list[TaskFileOut]
    #: 起始代码（#4767 第二节）：领题建项目时作为仓库第一个提交的那份 .zip；没有是 null。
    starter: TaskFileOut | None
    #: 这个人能不能下载；不能的只看到名字。
    canDownload: bool


class TaskFileUploadSlotIn(BaseModel):
    name: str
    size: int


class TaskFileUploadSlot(BaseModel):
    """一次上传的凭据（``upload``）和它怎么切：每段 ``part_bytes`` 个字节，一共
    ``parts`` 段，最后一段可以短。"""

    upload: str
    part_bytes: int
    parts: int


class TaskFileUploadPartIn(BaseModel):
    """第 ``part`` 段（从 1 数）的字节的 MD5，十六进制。"""

    upload: str
    part: int = Field(ge=1)
    md5: str


class TaskFileUploadPart(BaseModel):
    """把这一段原样 PUT 到 ``url``（以 ``/`` 开头的是本后端的地址），带上
    ``headers``。"""

    url: str
    headers: dict[str, str]


class TaskFileUploaded(BaseModel):
    """传完的一次上传：凭据，整份的 SHA-256，每段的 MD5（按段的顺序）。"""

    upload: str
    sha256: str
    parts: list[str]


class TaskFileAddIn(TaskFileUploaded):
    filename: str


class TaskFileReplaceIn(TaskFileUploaded):
    pass


def _received(task_id: int, body: TaskFileUploaded) -> library_records.Upload:
    return library_records.received(
        package.shelf(task_id), body.upload, body.sha256, body.parts
    )


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
    "/{taskId}/files",
    summary="List Task Files",
    **typed_response(TaskFilesOut),
    dependencies=[needs(TaskPermission.view, on="taskId")],
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
    rows = await package.files(db, task.id)
    starter = await package.starter_file(db, task.id)
    return ok(
        {
            "files": [_file(row) for row in rows],
            "starter": _file(starter) if starter is not None else None,
            "canDownload": await task_seats.allows(
                db, task, auth_user.user_id, TaskPermission.download
            ),
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


@router.get(
    "/{taskId}/files/raw",
    summary="Download Task File",
    **BYTES,
    dependencies=[needs(TaskPermission.view, on="taskId")],
)
async def download_task_file(
    task_id: TaskId,
    name: str,
    request: Request,
    version: Annotated[int | None, Query(ge=1)] = None,
    preview_pdf: bool = False,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> Response:
    """一份材料的字节。先过题目详情那三道闸，再看是不是出题人、空间管理员或参与者（申请通过的人）。

    `version`：第几版，取那一版手上的这份文件当时的字节——只认那一版记下的文件；不给
    就是现在这一份。`preview_pdf`：Office 文档转成 PDF，给题目页上的预览。"""
    task = await _require_task(db, task_id)
    await package.refuse_unless_downloader(db, task, auth_user.user_id)
    name = _name(name)
    row_id = (
        (await package.version_file(db, task.id, version, name)).id
        if version is not None
        else None
    )
    return await library_file_response(
        db, package.shelf(task.id), name, row_id, preview_pdf, request
    )


class TaskVersionAuthorOut(BaseModel):
    id: int
    username: str
    nickname: str


class TaskVersionFileOut(BaseModel):
    """一版里的一份文件，按 ``/files/raw?name=&version=<第几版>`` 取。这份材料后来
    整份删掉了的，``size`` 是 null，只剩名字，取不到。"""

    name: str
    size: int | None


class TaskVersionOut(BaseModel):
    number: int
    #: 记下这一版的时刻（毫秒）。
    at: int
    author: TaskVersionAuthorOut | None
    #: 这一版改了什么（`TaskPackageRevision.changes`）。
    changes: dict
    #: 这一版手上的文件；记版本时还没记文件的那几版是 null。
    files: list[TaskVersionFileOut] | None


class TaskVersionsOut(BaseModel):
    versions: list[TaskVersionOut]
    #: 这个人能不能下载各版的文件；不能的只看到名字。
    canDownload: bool


@router.get(
    "/{taskId}/versions",
    summary="List Task Versions",
    **typed_response(TaskVersionsOut),
    dependencies=[needs(TaskPermission.view, on="taskId")],
)
async def list_task_versions(
    task_id: TaskId,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """题目的历史版本，新的在前：第几版、什么时候、谁改的、改了什么、那一版手上的文件。

    看得见这道题就看得到（题目详情那三道闸），和材料清单一样；各版文件的下载走
    ``/files/raw?version=``，判据和下载现在的材料是同一条。"""
    task = await _require_task(db, task_id)
    versions = await package.history(db, task.id)
    author_ids = list(
        {v.revision.author_id for v in versions if v.revision.author_id is not None}
    )
    users = user_service(db)
    handles = await users.get_handles_by_ids(author_ids)
    profiles = await users.get_users_by_ids(author_ids)

    def author(user_id: int | None) -> dict | None:
        handle = handles.get(user_id) if user_id is not None else None
        if user_id is None or handle is None:
            return None
        profile = profiles.get(user_id)
        nickname = profile.nickname if profile and profile.nickname else handle
        return {"id": user_id, "username": handle, "nickname": nickname}

    return ok(
        {
            "versions": [
                {
                    "number": v.revision.number,
                    "at": int(v.revision.created_at.timestamp() * 1000),
                    "author": author(v.revision.author_id),
                    "changes": v.revision.changes or {},
                    "files": None
                    if v.files is None
                    else [
                        {
                            "name": held.name,
                            "size": held.row.bytes if held.row else None,
                        }
                        for held in v.files
                    ],
                }
                for v in versions
            ],
            "canDownload": await task_seats.allows(
                db, task, auth_user.user_id, TaskPermission.download
            ),
        }
    )


@router.post(
    "/{taskId}/files/uploads",
    summary="Task File Upload Slot",
    dependencies=[needs(TaskPermission.teach, on="taskId")],
    **typed_response(TaskFileUploadSlot),
)
async def task_file_upload_slot(
    task_id: TaskId,
    body: TaskFileUploadSlotIn,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """领一次上传的凭据（出题人本人或空间管理员）。超过上限的不给。"""
    task = await _require_task(db, task_id)
    if body.size <= 0:
        raise ValidationError(say("emptyFile"))
    name = library.clean_upload_name(body.name)
    return ok(
        await library_records.upload_slot(package.shelf(task.id), name, body.size)
    )


@router.post(
    "/{taskId}/files/uploads/part",
    summary="Task File Upload Part",
    dependencies=[needs(TaskPermission.teach, on="taskId")],
    **typed_response(TaskFileUploadPart),
)
async def task_file_upload_part(
    task_id: TaskId,
    body: TaskFileUploadPartIn,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """一次上传里一段的地址：只收这一段，字节的 MD5 对不上的存储不收。"""
    task = await _require_task(db, task_id)
    return ok(
        await library_records.upload_part(
            package.shelf(task.id), body.upload, body.part, body.md5
        )
    )


@router.post(
    "/{taskId}/files",
    summary="Add Task File",
    status_code=201,
    dependencies=[needs(TaskPermission.teach, on="taskId")],
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
    upload = _received(task.id, body)
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
    dependencies=[needs(TaskPermission.teach, on="taskId")],
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
    upload = _received(task.id, body)
    name = _name(name)
    await package.replace_file(db, task, by=auth_user.user_id, name=name, data=upload)
    await db.commit()
    return ok({"name": name, "size": upload.size})


@router.delete(
    "/{taskId}/files",
    summary="Remove Task File",
    dependencies=[needs(TaskPermission.teach, on="taskId")],
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
    await package.remove_file(db, task, by=auth_user.user_id, name=_name(name))
    await db.commit()
    return ok({"removed": True})


@router.put(
    "/{taskId}/starter",
    summary="Set Task Starter Code",
    dependencies=[needs(TaskPermission.teach, on="taskId")],
    **typed_response(TaskFilePlacedOut),
)
async def set_task_starter(
    task_id: TaskId,
    body: TaskFileUploaded,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """把传上来的一份 .zip 设成起始代码（和材料同一条分段上传），题目记一版。

    当场查它能不能解开、有没有越出目录的路径或链接、大小和文件数在不在上限内，不合格
    的说清楚为什么。已有一份的，旧的留作历史；已经领了题的项目不受影响。"""
    task = await _require_task(db, task_id)
    upload = _received(task.id, body)
    verb = await package.set_starter(db, task, by=auth_user.user_id, data=upload)
    await package.record(
        db, task, by=auth_user.user_id, changes={package.STARTER_CHANGE: verb}
    )
    await db.commit()
    return ok({"name": package.STARTER, "size": upload.size})


@router.delete(
    "/{taskId}/starter",
    summary="Remove Task Starter Code",
    dependencies=[needs(TaskPermission.teach, on="taskId")],
    **typed_response(TaskFileRemovedOut),
)
async def remove_task_starter(
    task_id: TaskId,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """不要起始代码了，题目记一版；之后领题的人拿到的是一个由平台初始化的仓库。"""
    task = await _require_task(db, task_id)
    await package.remove_starter(db, task)
    await package.record(
        db,
        task,
        by=auth_user.user_id,
        changes={package.STARTER_CHANGE: package.REMOVED},
    )
    await db.commit()
    return ok({"removed": True})
