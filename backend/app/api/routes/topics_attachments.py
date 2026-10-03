"""The files a message carries: upload one, serve it, render it as a PDF.

First slice of `app/api/routes/topics.py` (arch review C-backend.md §3.3).
topics.py is 4,022 lines against a 1,500-line cap that only ratchets down. The
block that moves is the one concept "a file attached to a message": the upload
(`POST /topics/{topic_id}/attachments`), the bytes an `<img>` reads back (`GET
/topics/{topic_id}/attachments/raw`), and the PDF a browser can draw in place of
a download for a Word or PowerPoint deliverable (`GET
/topics/{topic_id}/attachments/pdf`), with the two image-mime tables and the
size ceiling nothing else in the tree names.

Where the shared names went. The three names this module used to read from
topics.py now have their own homes: `clean_artifact_path` and
`MAX_ARTIFACT_BYTES` are the room-file rules in `app.domain.project.room_files`,
`source_bytes` is the API read-orchestration helper in
`app.api.routes.topics_file_sources`, and the binding check became
`TaskService.require_source_in_room`. `DbSession` is still imported from
topics.py, the shape `admin_models.py` uses for it. A helper two groups share
does not belong to either, so it moves out of topics.py rather than staying
there -- and there is no import cycle, because none of the new homes imports
this module.

Ordering. This module sorts after `topics.py` (`.` < `_`), so its router mounts
after that file's. Nothing registered earlier can shadow these paths: no
`/topics/{a}/{b}` route exists anywhere, so `/topics/{topic_id}/attachments/raw`
and `/attachments/pdf` have no parameterized route to lose to, and
`POST /topics/{topic_id}/attachments` is the only POST of that shape in the tree.
A request per path confirms each still reaches its own handler.

The new module mounts itself: `app.main._discover_routers` includes every
module-level APIRouter under `app.api.routes`, so the same
`APIRouter(prefix="/topics", tags=["topics"])` is all it takes.
"""

import uuid
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import Response

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.api.routes.topics import DbSession
from app.api.routes.topics_file_sources import source_bytes
from app.core.config import settings
from app.core.errors import SystemBusyError, ValidationError
from app.domain.block.notice_text import exception_text, say
from app.domain.library import records as library_records
from app.domain.library import service as library
from app.domain.preview.office import (
    OfficeRenderFailed,
    OfficeRenderUnavailable,
    is_renderable,
    render_to_pdf,
)
from app.domain.project.room_files import (
    MAX_ARTIFACT_BYTES,
    clean_artifact_path,
)
from app.domain.room_task.services import TaskService
from app.domain.textfile import content_version
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["topics"])


# ---- Chat attachments -----------------------------------------------------
# 用户挑出来或拖进来的文件落进项目的资料库 (`library.write_library_file`)，按原名寻址，
# 所有房间都能引用。消息里带的就是它自己那个地址 `library/<名字>`——**不拷贝**：
# 一份资料在这个项目里只有一份字节。送上机器的那一份落在会话 home 的
# `attachments/` 下（`agent/place.py`），不落在检出目录里，芝士 收到的是机器报回来
# 的绝对路径。
#
# 剪贴板里贴进来的那张图**不进资料库**：资料库的前提是「名字就是身份」，而剪贴板里
# 的截图没有名字，`image.png` 是浏览器替它编的。它只属于这条消息，所以落在房间文件
# 区一个独占的目录下。

# Only these image types may render inline; other files require download.
_IMAGE_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
_EXT_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


@router.post("/{topic_id}/attachments")
async def upload_attachment(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    file: UploadFile | None = File(None),
    library_path: str | None = Form(None),
    origin: str | None = Form(None),
) -> dict:
    """Attach a file to a message being written in this room.

    Either a new upload (`file`) or one the 资料库 already holds
    (`library_path`). Both end the same way: a copy in this room's files, and
    the {path, mime} the client references when it sends the message.

    `origin="clipboard"` says the bytes came off the clipboard — they stay in
    this room, because a pasted screenshot has no name of its own to be filed
    under."""
    topic = await TopicService(db).get_or_404(topic_id)
    await resolver.require_verified_caller(
        project_id=topic.project_id, topic_id=topic_id
    )
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    if (file is None) == (library_path is None):
        raise ValidationError(say("attachmentOneSource"))
    if library_path is not None:
        name = clean_artifact_path(library_path)
        # 读一次：既确认它真的在，也把大小告诉输入栏。一个字节都不写。
        data = library.read_library_file(topic.project_id, name)
        suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        mime = _EXT_IMAGE_MIME.get(suffix, "application/octet-stream")
        return ok({"path": library.library_ref(name), "mime": mime, "bytes": len(data)})
    else:
        assert file is not None
        mime = (
            (file.content_type or "application/octet-stream")
            .split(";")[0]
            .strip()
            .lower()
        )
        ext = _IMAGE_MIME_EXT.get(mime)
        if mime.startswith("image/") and ext is None:
            mime = "application/octet-stream"
        data = await file.read(MAX_ATTACHMENT_BYTES + 1)
        if not data:
            raise ValidationError(say("emptyFile"))
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ValidationError(say("attachmentTooLarge"))
        name = library.clean_upload_name(file.filename)
        if ext and not name.lower().endswith(ext):
            name += ext
        if origin == "clipboard":
            path = f"uploads/{uuid.uuid4().hex}/{name}"
            library.write_room_file(topic.project_id, topic_id, path, data)
            return ok({"path": path, "mime": mime, "bytes": len(data)})
        name = await library_records.add(
            db, topic.project_id, name, data, actor.handle, topic_id
        )
    return ok({"path": library.library_ref(name), "mime": mime, "bytes": len(data)})


@router.get("/{topic_id}/attachments/raw")
async def attachment_raw(
    topic_id: uuid.UUID,
    path: str,
    db: DbSession,
    resolver: ActorResolverDep,
    download: bool = False,
    task: uuid.UUID | None = None,
    source: Literal["live", "committed"] = "live",
) -> Response:
    """Raw bytes of an image attachment, for <img src=…>. Extension-whitelisted
    to images so this can never serve executable HTML from the worktree."""
    topic = await TopicService(db).get_or_404(topic_id)
    if download:
        await resolver.require_verified_caller(
            project_id=topic.project_id, topic_id=topic_id
        )
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    clean = clean_artifact_path(path)
    suffix = "." + clean.rsplit(".", 1)[-1].lower() if "." in clean else ""
    mime = _EXT_IMAGE_MIME.get(suffix)
    if mime is None and not download:
        raise ValidationError(say("attachmentImageOnly"))
    if task is not None:
        await TaskService(db).require_source_in_room(topic_id, task)
    data = await source_bytes(db, topic.project_id, topic_id, clean, task, source)
    filename = quote(clean.rsplit("/", 1)[-1], safe="")
    return Response(
        content=data,
        media_type="application/octet-stream" if download else mime,
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{filename}" if download else "inline"
            ),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": (
                "no-store" if task or source == "committed" else "private, max-age=3600"
            ),
        },
    )


@router.get("/{topic_id}/attachments/pdf")
async def attachment_as_pdf(
    topic_id: uuid.UUID,
    path: str,
    db: DbSession,
    resolver: ActorResolverDep,
    task: uuid.UUID | None = None,
    source: Literal["live", "committed"] = "live",
) -> Response:
    """A Word or PowerPoint deliverable, converted so a browser can show it.

    Browsers draw PDF and nothing else in this family, so this is what stands
    between "看得见的成果" and a download button on a tab labelled 预览.

    Spreadsheets are not here on purpose: paginating a sheet breaks the columns
    apart and throws away the cell addresses, which are the only thing anyone can
    point at afterwards. Those are drawn from the original bytes instead.
    """
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    clean = clean_artifact_path(path)
    if not is_renderable(clean):
        raise ValidationError(say("previewFormatUnsupported"))
    if task is not None:
        await TaskService(db).require_source_in_room(topic_id, task)
    data = await source_bytes(db, topic.project_id, topic_id, clean, task, source)
    if len(data) > MAX_ARTIFACT_BYTES:
        raise ValidationError(
            say("previewTooLarge", mb=MAX_ARTIFACT_BYTES // (1024 * 1024))
        )
    try:
        pdf = await render_to_pdf(data, clean, settings.office_render_endpoint)
    except OfficeRenderUnavailable as exc:
        # 503 (SystemBusyError is this codebase's 503), not 500: the renderer is
        # absent or unreachable, which the panel reports as its own state and
        # pairs with the download — a different sentence from "这个文件转换不了",
        # which is about the file and will not improve on a retry.
        raise SystemBusyError(exception_text(exc)) from exc
    except OfficeRenderFailed as exc:
        raise ValidationError(exception_text(exc)) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "no-store",
            "X-Cheese-Source-Version": content_version(data),
        },
    )
