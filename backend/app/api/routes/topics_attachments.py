"""The files a message carries: upload one, serve it, render it as a PDF.

First slice of `app/api/routes/topics.py` (arch review C-backend.md §3.3).
topics.py is 4,022 lines against a 1,500-line cap that only ratchets down. The
block that moves is the one concept "a file attached to a message": the upload
(`POST /topics/{topic_id}/attachments`), the bytes an `<img>` reads back (`GET
/topics/{topic_id}/attachments/raw`), the PDF a browser can draw in place of a
download for a Word or PowerPoint deliverable (`GET
/topics/{topic_id}/attachments/pdf`), and the web page the same container
renders for a Word file, a workbook or a deck (`GET
/topics/{topic_id}/attachments/html`), with the media-mime tables nothing else
in the tree names. The size ceilings are the landing layer's
(`library.max_bytes_for`).

Where the shared names went. The three names this module used to read from
topics.py now have their own homes: `clean_artifact_path` is a room-file rule in
`app.domain.project.room_files`, the ceiling it is read against is
`library.service.MAX_ARTIFACT_BYTES` (written down once, where the bytes land),
`source_bytes` is the API read-orchestration helper in
`app.api.topics_file_sources`, and the binding check became
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

import asyncio
import uuid
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.api.auth import (
    ActorResolverDep,
    MediaActorResolverDep,
    remember_for_media,
)
from app.api.byte_response import blob_response, media_bytes_response
from app.api.inert_headers import INERT_HEADERS, NO_SNIFF
from app.api.project_library import may_read_path
from app.api.request_deps import DbSession
from app.api.response import Page, ok, page, typed_response
from app.api.topics_deps import _actor_in_place
from app.api.topics_file_sources import source_bytes
from app.api.write_access import ROUTE_DECIDES
from app.core import raster_images, video_files
from app.core.config import settings
from app.core.errors import SystemBusyError, ValidationError
from app.core.model_images import model_media_type
from app.core.sentences import exception_text, say
from app.domain.documents.notes import slide_notes
from app.domain.library import in_room
from app.domain.library import records as library_records
from app.domain.library import service as library
from app.domain.library.shelf import Shelf
from app.domain.preview.office import (
    OfficeRenderFailed,
    OfficeRenderUnavailable,
    is_html_renderable,
    is_renderable,
    render_to_html,
    render_to_pdf,
    suffix_of,
)
from app.domain.project.room_files import clean_artifact_path
from app.domain.room_task.place import place_or_404
from app.domain.room_task.services import TaskService
from app.domain.textfile import content_version

router = APIRouter(prefix="/topics", tags=["topics"])


# ---- Chat attachments -----------------------------------------------------
# 发进对话里的文件跟着这段对话（#3828）：落在房间文件区一个独占的目录下
# （`uploads/<随机串>/<名字>`），谁读得到跟着这个频道或私聊走，不进资料库。要让项目
# 里的人都找得到，有人按「保存到资料库」（`/shown/save`），那是一个单独的动作。
#
# 从资料库里挑一份附上的，消息里带的就是它在资料库的地址 `library/<名字>`，不拷贝。
# 送上机器的那一份落在会话 home 的 `attachments/` 下（`agent/place.py`），不落在检出
# 目录里，芝士 收到的是机器报回来的绝对路径。

# Only inert raster images and video containers may render inline.
_IMAGE_MIME_EXT = raster_images.MEDIA_TYPE_SUFFIX
_EXT_MEDIA_MIME = {**raster_images.SUFFIX_MEDIA_TYPE, **video_files.SUFFIX_MEDIA_TYPE}

#: What a rendered page may do. It is a document, not an app, and it arrives
#: carrying inline styles and a little inline script of officecli's own: without
#: `style-src` it would lose every bit of its formatting, and without
#: `script-src` a workbook's sheet tabs would stop switching sheets.
#:
#: Everything else is closed. Those pages also reach for a font CDN, a formula
#: CDN and a WebGL library on the vendor's host, and a reader opening a room's
#: internal document should not be made to call out to any of them. What that
#: costs is the two features that need them: a formula falls back to its source
#: text and a 3D model does not draw, both of which the page's own script
#: already handles as failure cases. Fonts are not a cost at all — the stack
#: names Microsoft YaHei, PingFang SC and STHeiti, which the readers who open
#: Chinese documents have.
_PAGE_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "img-src data:; font-src data:"
)

#: The same policy for a page navigated to directly. `sandbox` is a header-only
#: directive — a meta tag cannot carry it, and browsers ignore it there — so it
#: rides in the header; the frame the panel actually uses is sandboxed by the
#: iframe element's own attribute (no `allow-same-origin`, so the page lands in
#: an opaque origin and cannot reach this one).
_PAGE_POLICY_HEADER = _PAGE_POLICY + "; sandbox allow-scripts"

_PAGE_POLICY_META = (
    f'<meta http-equiv="Content-Security-Policy" content="{_PAGE_POLICY}">'
).encode()


def _head_end(page: bytes) -> int:
    """Just past the opening `<head ...>` tag, or -1 when there is none.

    Case-insensitive, and tolerant of attributes, the way `_looks_like_html`
    reads the same bytes: a page that got this far is going to be served as
    HTML, and the one failure this tag exists to prevent is the policy silently
    not attaching — inside the frame it is the only policy there is.
    """
    at = page.lower().find(b"<head")
    if at < 0:
        return -1
    end = page.find(b">", at)
    return end + 1 if end >= 0 else -1


def _with_policy(page: bytes) -> bytes:
    """The policy in the page itself, not only in the response headers.

    The panel does not navigate to this route. It fetches the bytes with the
    Authorization header and hands them to a sandboxed frame, and a response's
    CSP does not follow those bytes into the frame — the meta tag does.
    """
    at = _head_end(page)
    if at < 0:
        return page
    return page[:at] + _PAGE_POLICY_META + page[at:]


class RoomAttachmentOut(BaseModel):
    """One file sent in a channel or private chat (`in_room.attachments`)."""

    path: str
    name: str
    mime: str | None
    added_by: str
    added_at: str
    in_library: bool


@router.get("/{topic_id}/attachments", **typed_response(Page[RoomAttachmentOut]))
async def list_attachments(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The files sent in this channel or private chat, its tasks and 支线
    included, the newest first, each once — and whether it is in the library
    yet. Saving one there is `POST /topics/{topic_id}/shown/save`."""
    place = await place_or_404(db, topic_id)
    await _actor_in_place(resolver, place)
    rows = await in_room.attachments(db, place.room_id)
    return ok(page(rows, len(rows)))


@router.post("/{topic_id}/attachments", dependencies=[ROUTE_DECIDES])
async def upload_attachment(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    file: UploadFile | None = File(None),
    library_path: str | None = Form(None),
) -> dict:
    """Attach a file to a message being written in this room.

    Either a new upload (`file`), which stays in this room's files, or one the
    资料库 already holds (`library_path`). Both answer the {path, mime} the
    client references when it sends the message."""
    # A task's id reaches its room's roster, files and documents.
    topic = (await place_or_404(db, topic_id)).room
    await resolver.require_verified_caller(
        project_id=topic.project_id, topic_id=topic_id
    )
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    topic_id = topic.id
    if (file is None) == (library_path is None):
        raise ValidationError(say("attachmentOneSource"))
    if library_path is not None:
        name = clean_artifact_path(library_path)
        # 找一次它的字节：既确认它真的在，也把大小告诉输入栏。一个字节都不读、不写。
        ref = library.library_ref(name)
        reads_task = await may_read_path(db, topic.project_id, ref)
        stored = await library_records.stored_file(
            db, Shelf.of_project(topic.project_id, with_task=reads_task), name
        )
        suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        mime = _EXT_MEDIA_MIME.get(suffix, "application/octet-stream")
        return ok({"path": ref, "mime": mime, "bytes": stored.size})
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
        if ext is not None:
            # The browser's word for it is a claim; the bytes decide. One that
            # does not decode is kept as a plain file, and never handed to a
            # model as a picture.
            mime = (
                await asyncio.to_thread(model_media_type, file.file)
                or "application/octet-stream"
            )
            ext = _IMAGE_MIME_EXT.get(mime)
        name = library.clean_upload_name(file.filename)
        suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        video_mime = video_files.SUFFIX_MEDIA_TYPE.get(suffix)
        if video_mime:
            mime = video_mime
            ext = None
        elif mime in video_files.MEDIA_TYPE_SUFFIX:
            ext = video_files.MEDIA_TYPE_SUFFIX[mime]
            mime = video_files.SUFFIX_MEDIA_TYPE[ext]
        if ext and not name.lower().endswith(ext):
            name += ext
        # The multipart parser has already spooled the body to a temporary
        # file; the bytes are measured there and copied from there, never read
        # into memory whole — a phone recording runs to a couple of hundred MB.
        # The ceiling follows the final name, so a video is held to the video
        # ceiling and everything else to the general one.
        data = file.file
        size = library.size_of(data)
        if not size:
            raise ValidationError(say("emptyFile"))
        library.refuse_oversize(size, name)
        path = f"{library.MESSAGE_ATTACHMENT_PREFIX}/{uuid.uuid4().hex}/{name}"
        await asyncio.to_thread(
            library.write_room_file, topic.project_id, topic_id, path, data
        )
        return ok({"path": path, "mime": mime, "bytes": size})


def _uploaded(path: str) -> bool:
    """这个地址是不是一次上传落下的那一份（`uploads/<随机串>/<名字>`）。

    造这种地址的有两处，两处都是现取一个 `uuid4().hex` 当一层目录：发进对话里的
    文件（这个模块的上传分支），和芝士送一份工作区外的文件（
    `claude_code/remote_execution/send_user_file.py` 的 `send_user_file_paths`）。
    随机串把这一份钉死在一次上传上，同一地址下的字节写下之后不会再变。
    """
    parts = path.split("/")
    return (
        len(parts) >= 3
        and parts[0] == "uploads"
        and len(parts[1]) == 32
        and all(c in "0123456789abcdef" for c in parts[1])
    )


def _cache_control_of(path: str) -> str:
    """一条消息里的一个附件，浏览器可以留多久。

    **同一个地址下的字节会变的那些，不许浏览器自己答。** 资料库里那一份是这样
    （替换就是「这个人说了这是同一份文件的新版本」），房间里摆出来的那一份也是这样
    （芝士重做一版，路径还是那一个）。两份都照资料库自己的字节端点
    （`routes/library.py`）的规矩来，每次问一句「变了没有」——留一小时的话，新的一版
    发出来之后整整一小时里，点开画着的还是旧的。

    发进来的那一份不是这样：`uploads/<随机串>/<名字>` 里的随机串把它钉死在某一次
    上传上，字节写下之后不会再变，留一小时是有意的。
    """
    if library.library_name(path) is not None:
        return "private, no-cache"
    if _uploaded(path):
        return "private, max-age=3600"
    return "private, no-cache"


@router.get("/{topic_id}/attachments/raw")
async def attachment_raw(
    topic_id: uuid.UUID,
    path: str,
    db: DbSession,
    resolver: MediaActorResolverDep,
    request: Request,
    download: bool = False,
    task: uuid.UUID | None = None,
    source: Literal["live", "committed"] = "live",
) -> Response:
    """Authorized raster images and video bytes, never executable HTML/SVG.

    A room's own file and a library file are served with byte ranges, so a
    player seeking through a long recording reads only the part it shows. A video read with the
    ``Authorization`` header leaves a cookie for this path behind
    (``remember_for_media``): the ``<video>`` element's own range requests
    cannot carry the header, and the cookie is how they prove the same viewer.
    """
    # A task's id reaches its room's roster, files and documents.
    topic = (await place_or_404(db, topic_id)).room
    if download:
        await resolver.require_verified_caller(
            project_id=topic.project_id, topic_id=topic_id
        )
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    room_id = topic.id
    clean = clean_artifact_path(path)
    suffix = "." + clean.rsplit(".", 1)[-1].lower() if "." in clean else ""
    mime = _EXT_MEDIA_MIME.get(suffix)
    if mime is None and not download:
        raise ValidationError(say("attachmentMediaOnly"))
    if task is not None:
        await TaskService(db).require_source_in_room(room_id, task)
    filename = quote(clean.rsplit("/", 1)[-1], safe="")
    media_type = (
        "application/octet-stream" if download else (mime or "application/octet-stream")
    )
    headers = {
        "Content-Disposition": (
            f"attachment; filename*=UTF-8''{filename}" if download else "inline"
        ),
        **INERT_HEADERS,
        "Cache-Control": (
            "no-store" if task or source == "committed" else _cache_control_of(clean)
        ),
    }
    if task is None and source == "live":
        stored = await library_records.attachment_file(
            db,
            topic.project_id,
            room_id,
            clean,
            with_task=await may_read_path(db, topic.project_id, clean),
        )
        response: Response
        if isinstance(stored, library_records.Stored):
            response = blob_response(
                stored.location,
                stored.key,
                stored.size,
                media_type,
                headers,
                request.headers.get("range"),
                request.headers.get("if-range"),
            )
        else:
            response = FileResponse(stored, media_type=media_type, headers=headers)
    else:
        data = await source_bytes(
            db,
            topic.project_id,
            room_id,
            clean,
            task,
            source,
            with_task=await may_read_path(db, topic.project_id, clean),
        )
        response = media_bytes_response(
            data,
            media_type,
            headers,
            request.headers.get("range"),
            request.headers.get("if-range"),
        )
    if media_type.startswith("video/"):
        remember_for_media(response, request)
    return response


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
    # A task's id reaches its room's roster, files and documents.
    topic = (await place_or_404(db, topic_id)).room
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    topic_id = topic.id
    clean = clean_artifact_path(path)
    if not is_renderable(clean):
        raise ValidationError(say("previewFormatUnsupported"))
    if task is not None:
        await TaskService(db).require_source_in_room(topic_id, task)
    data = await source_bytes(
        db,
        topic.project_id,
        topic_id,
        clean,
        task,
        source,
        with_task=await may_read_path(db, topic.project_id, clean),
    )
    if len(data) > library.MAX_ARTIFACT_BYTES:
        raise ValidationError(
            say("previewTooLarge", mb=library.MAX_ARTIFACT_BYTES // (1024 * 1024))
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
            **INERT_HEADERS,
            "Cache-Control": "no-store",
            "X-Cheese-Source-Version": content_version(data),
        },
    )


@router.get("/{topic_id}/attachments/notes")
async def attachment_notes(
    topic_id: uuid.UUID,
    path: str,
    db: DbSession,
    resolver: ActorResolverDep,
    task: uuid.UUID | None = None,
    source: Literal["live", "committed"] = "live",
) -> dict:
    """A deck's speaker notes, one entry per slide.

    A slide's notes are not on the slide, and the preview panel is handed the
    *converted PDF* — so this is the one thing about a deck the panel cannot
    read off what it holds. It arrives as its own small JSON rather than as part
    of the conversion response: the PDF is cached by content and read on every
    page change, while the notes are a few kilobytes read once per file.

    Authorization is the PDF route's, exactly: this reads the same bytes for the
    same reader, so a second, weaker path to them would be a hole rather than a
    shortcut.
    """
    # A task's id reaches its room's roster, files and documents.
    topic = (await place_or_404(db, topic_id)).room
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    topic_id = topic.id
    clean = clean_artifact_path(path)
    if suffix_of(clean) not in (".pptx", ".ppt"):
        raise ValidationError(say("previewFormatUnsupported"))
    if task is not None:
        await TaskService(db).require_source_in_room(topic_id, task)
    data = await source_bytes(
        db,
        topic.project_id,
        topic_id,
        clean,
        task,
        source,
        with_task=await may_read_path(db, topic.project_id, clean),
    )
    if len(data) > library.MAX_ARTIFACT_BYTES:
        raise ValidationError(
            say("previewTooLarge", mb=library.MAX_ARTIFACT_BYTES // (1024 * 1024))
        )
    # Reading a zip is CPU work on bytes that may be 200MB; keep it off the loop
    # the same way the comparison does.
    notes = await asyncio.to_thread(slide_notes, data)
    return ok(
        {
            "notes": notes or [],
            "readable": notes is not None,
            "version": content_version(data),
        }
    )


@router.get("/{topic_id}/attachments/html")
async def attachment_as_html(
    topic_id: uuid.UUID,
    path: str,
    db: DbSession,
    resolver: ActorResolverDep,
    task: uuid.UUID | None = None,
    source: Literal["live", "committed"] = "live",
) -> Response:
    """A Word file, a workbook or a deck as one web page.

    The PDF above answers what the document looks like. This answers what it is
    made of: every element on the page carries the address officecli's own
    `set`/`add`/`remove` take — `/body/p[7]`, `/数据/B2`,
    `/slide[1]/shape[@id=2]` — so "改这一格" names one cell to the reader who
    said it and to whoever has to change it. A chart arrives as a chart, and a
    header row keeps the fill it is drawn with.

    A workbook is here and not in the PDF route, for the reason it is not there:
    this is the shape a sheet can be shown in without losing its cell addresses.
    """
    # A task's id reaches its room's roster, files and documents.
    topic = (await place_or_404(db, topic_id)).room
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    topic_id = topic.id
    clean = clean_artifact_path(path)
    if not is_html_renderable(clean):
        raise ValidationError(say("previewFormatUnsupported"))
    if task is not None:
        await TaskService(db).require_source_in_room(topic_id, task)
    data = await source_bytes(
        db,
        topic.project_id,
        topic_id,
        clean,
        task,
        source,
        with_task=await may_read_path(db, topic.project_id, clean),
    )
    if len(data) > library.MAX_ARTIFACT_BYTES:
        raise ValidationError(
            say("previewTooLarge", mb=library.MAX_ARTIFACT_BYTES // (1024 * 1024))
        )
    try:
        page = await render_to_html(data, clean, settings.office_render_endpoint)
    except OfficeRenderUnavailable as exc:
        # 503, like the PDF route: the renderer is absent or unreachable, which
        # the panel reports as its own state, unlike "这份文件转换不了".
        raise SystemBusyError(exception_text(exc)) from exc
    except OfficeRenderFailed as exc:
        raise ValidationError(exception_text(exc)) from exc
    return Response(
        content=_with_policy(page),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": "inline",
            **NO_SNIFF,
            "Content-Security-Policy": _PAGE_POLICY_HEADER,
            "Cache-Control": "no-store",
            "X-Cheese-Source-Version": content_version(data),
        },
    )
