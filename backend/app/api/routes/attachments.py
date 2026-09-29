"""通用附件：上传一个文件，再按 id 读回去。

**读这一侧判给谁** —— 这张表（``app/domain/attachment/models.py``）只有
``id / type / url / meta``，**没有归属列**，所以判据只能问别的域：

- **挂在某道题上的文件**（``task_attachment`` 里有一行活关联）：判据照抄题内那条路，
  一个字不另立 —— 出题人 / 板管理员 / 已经领取的人（``may_teach_task`` 或
  ``is_participant``）。从前这里一行判断都没有，等于给 `#1786` 立的闸门开了扇边门：
  id 是小整数、可枚举，知道一个 id 就拿到了别人题目材料的原始字节，而同一个文件里的
  ``DELETE`` 反倒是 owner-gated。
- **不挂在任何题上的文件**：上传者本人；此外**图片**对登录用户放行，其余只有上传者。
  图片这一条是前端契约，不是宽容：公告与讨论里嵌的图（``AttachmentImage`` 节点带的是
  ``attachmentId``）由**每个看得见那段内容的人**通过这条路由解析成 url
  （``frontend/src/plugins/tiptap/extensions/image/ImageView.vue``、
  ``frontend/src/views/spaces/detail/Discussions.vue``），掐掉它板上所有人看到的都是
  裂图。而 ``type=file`` 的散件（交作业的材料、建题前先传上去的文件）没有任何一处前端
  跨用户读它，所以收紧到上传者本人。

**这是缺口，不是完整答案**：散图仍然对任何登录用户可读 —— 富文本里嵌的 id 反查不到
容器，「这张图属于哪段内容」在这张表上无法回答（存储那把 url 本来就是公开可猜的字节
路径，见 ``routes/uploads.py`` 顶部）。要真正收紧，得给 ``attachment`` 一个归属列，
或者让嵌入方把容器 id 一起带上。``tests/integration/test_attachment_read_authz.py``
把现状钉住了，换判据的人必须显式改掉那条测试。
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Path, UploadFile
from fastapi.responses import Response

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.config import settings
from app.core.errors import BadRequestError, ForbiddenError, UnprocessableEntityError
from app.core.storage import get_storage_backend
from app.db.session import get_db
from app.domain.attachment.models import Attachment, AttachmentType
from app.domain.attachment.repositories import AttachmentRepository
from app.domain.attachment.services import AttachmentService
from app.domain.task.attachment_service import TaskAttachmentService

router = APIRouter(prefix="/attachments", tags=["Attachments"])

VALID_TYPES = {"image", "video", "audio", "file"}

TYPE_MIME_PREFIXES = {
    "image": ["image/"],
    "video": ["video/"],
    "audio": ["audio/"],
    "file": ["application/", "text/", "image/", "video/", "audio/"],
}


async def get_attachment_service(db=Depends(get_db)) -> AttachmentService:
    repo = AttachmentRepository(session=db)
    storage = get_storage_backend()
    return AttachmentService(repo=repo, storage=storage)


async def get_task_attachment_service(db=Depends(get_db)) -> TaskAttachmentService:
    """题目那一域的 service：通用路由要问它「这个文件挂在哪道题上、谁能读」。"""
    return TaskAttachmentService(session=db, storage=get_storage_backend())


async def _ensure_may_read(
    *,
    attachment: Attachment,
    user_id: int,
    service: AttachmentService,
    task_attachments: TaskAttachmentService,
) -> None:
    """通用读路由的判据，见模块顶部那两段 —— 三条路各对应一句话。"""
    if service.is_uploader(attachment, user_id):
        return

    on_a_task = await task_attachments.may_read_file(
        attachment_id=attachment.id, user_id=user_id
    )
    if on_a_task is True:
        return
    if on_a_task is False:
        # 挂在题上的文件：与题内那条下载路由**同一句话**，只是少了「是哪道题」。
        raise ForbiddenError(
            "Only the publisher, a board manager or a participant "
            "can download this attachment"
        )
    if attachment.type == AttachmentType.IMAGE.value:
        return
    raise ForbiddenError("Only the uploader can read this attachment")


@router.post(
    "",
    summary="Upload Attachment",
    status_code=201,
)
async def upload_attachment(
    file: UploadFile = File(...),
    type: str = Form(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: AttachmentService = Depends(get_attachment_service),
    db=Depends(get_db),
) -> dict:
    if type not in VALID_TYPES:
        raise BadRequestError(f"Invalid type: {type}")

    file_mime = file.content_type or "application/octet-stream"
    if type != "file":
        valid_prefixes = TYPE_MIME_PREFIXES.get(type, [])
        if not any(file_mime.startswith(prefix) for prefix in valid_prefixes):
            raise UnprocessableEntityError(
                f"MIME type {file_mime} does not match type {type}"
            )

    attachment = await service.upload(
        file=file.file,
        filename=file.filename or "unknown",
        content_type=file.content_type,
        uploader_id=auth_user.user_id,
        attachment_type=type,
    )
    # Commit before answering: the id handed back is attached to a task on the
    # client's next request, which must find the row. ``get_db`` commits in its
    # teardown, which FastAPI runs after the response has gone out.
    await db.commit()
    return {
        "code": 201,
        "message": "Attachment uploaded successfully",
        "data": {"id": attachment.id, "url": attachment.url, "type": attachment.type},
    }


@router.get(
    "/limits",
    summary="Attachment Upload Limits",
)
async def get_attachment_limits(
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """What an upload will be refused for — asked before the upload, not after.

    Declared above ``/{attachmentId}`` on purpose: paths match in registration
    order, and a literal segment parked after a path parameter is unreachable.

    The number is read from the same setting the upload itself reads
    (``settings.attachment_max_bytes``, enforced in ``AttachmentService.upload``),
    so this cannot drift from what is actually enforced unless someone reports a
    second source — which is the thing this route exists to prevent.

    Same door as the upload: ``require_auth_user``, nothing else. A signed-in
    person learns the shape of an upload they are about to make; nobody else.
    """
    return {
        "code": 200,
        "message": "OK",
        "data": {"maxFileBytes": settings.attachment_max_bytes},
    }


@router.get(
    "/{attachmentId}",
    summary="Get Attachment Detail",
)
async def get_attachment_detail(
    attachmentId: int = Path(..., ge=0),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: AttachmentService = Depends(get_attachment_service),
    task_attachments: TaskAttachmentService = Depends(get_task_attachment_service),
) -> dict:
    attachment = await service.get(attachmentId)
    await _ensure_may_read(
        attachment=attachment,
        user_id=auth_user.user_id,
        service=service,
        task_attachments=task_attachments,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"attachment": attachment.to_dict()},
    }


@router.get(
    "/{attachmentId}/download",
    summary="Download Attachment",
)
async def download_attachment(
    attachmentId: int = Path(..., ge=0),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: AttachmentService = Depends(get_attachment_service),
    task_attachments: TaskAttachmentService = Depends(get_task_attachment_service),
) -> Response:
    await _ensure_may_read(
        attachment=await service.get(attachmentId),
        user_id=auth_user.user_id,
        service=service,
        task_attachments=task_attachments,
    )
    content, filename, content_type = await service.download(attachmentId)
    # ``filename*=UTF-8''…`` 而不是裸引号：中文文件名直接写进 header 会让
    # Starlette 按 latin-1 编码时报错（下载一个中文名的材料变成 500），名字
    # 里的引号还会把引号那段提前闭合、把文件名变成 header 语法。
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
    "/{attachmentId}",
    summary="Delete Attachment",
    status_code=204,
)
async def delete_attachment(
    attachmentId: int = Path(..., ge=0),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: AttachmentService = Depends(get_attachment_service),
) -> None:
    await service.delete(attachmentId, user_id=auth_user.user_id)
