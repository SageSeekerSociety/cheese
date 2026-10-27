"""A room file's draft history, and the office editor that writes it.

Every write goes through `room_files.save_room_file`, so whichever door the
bytes came in by — the editor, `cheese show`, a restore — the state before it
stays restorable, and a writer that read an older version gets a conflict
instead of overwriting whoever saved in between.
"""

import asyncio
import logging
import uuid
from typing import Annotated
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.deps import get_broker, get_chat_service
from app.api.project_library import shelf_for
from app.api.request_deps import DbSession
from app.api.response import Page, ok, page, typed_response
from app.api.write_access import ROUTE_DECIDES, Can, InChannel
from app.auth.project_roles import may
from app.core.config import settings
from app.core.errors import (
    ConflictError,
    NotFoundError,
    UnprocessableEntityError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.agent.chat import ChatService
from app.domain.agent.file_edits import announce_editor_save
from app.domain.block.shown import add_shown_block
from app.domain.documents import catalogue, editor
from app.domain.identity.actor import Actor
from app.domain.identity.services import IdentityService
from app.domain.library import records as library_records
from app.domain.library import service as library
from app.domain.living_doc import collab, room_file_collab
from app.domain.preview import office
from app.domain.project import room_files
from app.domain.project.room_files import (
    ARTIFACT_MIME,
    artifact_kind_for,
    clean_artifact_path,
)
from app.domain.project_permissions import Permission
from app.domain.room_task.place import Place, place_or_404
from app.domain.textfile import content_version

logger = logging.getLogger("cheesex.room_files")

router = APIRouter(tags=["room-files"])


async def _in_room(
    db: AsyncSession,
    resolver: ActorResolver,
    topic_id: uuid.UUID,
) -> tuple[Place, Actor]:
    """The room and who is in it."""
    place = await place_or_404(db, topic_id)
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    return place, actor


def author_kind(actor: Actor) -> str:
    return "agent" if actor.via == "cheese" else "human"


def _room_path(raw: str) -> str:
    path = clean_artifact_path(raw)
    if library.library_name(path) is not None:
        raise ValidationError(say("libraryOriginalReadOnly"))
    if library.is_message_attachment(path):
        # 发进对话的那一份是原件：消息指着它，浏览器也按「这个地址的字节不会变」留着它。
        raise UnprocessableEntityError(say("attachmentOriginalReadOnly"))
    return path


def _bytes_response(data: bytes, name: str, version: str) -> Response:
    filename = quote(name, safe="")
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "no-store",
            "X-Cheese-Version": version,
        },
    )


class RoomFileOut(BaseModel):
    """文件区里的一份文件：路径、大小、最后一次改动的时间，以及最后是谁从哪条路
    放下的。

    作者是平台按写下来的那次动作记的（谁按的保存、谁交的），不是谁自己填的。三个
    字段都可能空：目录里的一份文件可能从没有过保存记录（清单列的是目录本身，字节
    可以由别的东西直接放进去），那时界面照列，只是说不出是谁写的。
    """

    path: str
    bytes: int
    modified_at: str
    #: 空 = 说不出来是谁写的（见上面）。键一直都在，值可以是 null。
    author: str | None
    #: ``"human"`` 或 ``"agent"``，空 = 同上。
    author_kind: str | None
    #: 最后那次保存从哪个门进来的：取值见 ``room_files.SOURCES``。空 = 同上。
    source: str | None
    #: 有人从这里把它保存到了资料库（#3828）。
    in_library: bool


class RoomFileListOut(Page[RoomFileOut]):
    """文件区的清单：``data`` 是文件，``truncated`` 说清单是不是全部。"""

    truncated: bool


class RoomFileRevisionOut(BaseModel):
    """一次保存留下的那一版：地址、第几版、版本号、大小、谁从哪条路存的。

    `room_files.revision_out` 那一份的形状——保存、恢复、编辑器回调都回它。版本是内容
    的哈希前 16 位，和读文件时拿到的是同一个 id（`domain/textfile.content_version`）。
    """

    id: str
    path: str
    seq: int
    version: str
    size: int
    author: str
    author_kind: str
    #: 这一版从哪个门进来的：取值见 ``room_files.SOURCES``。
    source: str
    note: str | None
    #: 在线编辑器（OnlyOffice）那次会话的钥匙；走别条路的保存是空的。
    editor_key: str | None
    created_at: str


class LiveEditorOut(BaseModel):
    """开实时编辑器要的那点东西：一份文档名、一张票、能不能改，加上它的正文。

    `enabled` 为假时只有 `reason`（``not_configured``、``unsupported``），和
    `open_in_editor` 同一个形状，界面按同一个办法说人话。`content` 是这份文件此刻
    的 Markdown：编辑器拿它当起点，协作服务那边还没有活状态时也拿它做第一次转换。
    """

    enabled: bool
    reason: str | None = None
    document: str | None = None
    ticket: str | None = None
    read_only: bool | None = None
    version: str | None = None
    content: str | None = None


@router.get("/topics/{topic_id}/files", **typed_response(RoomFileListOut))
async def list_files(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """这个房间自己的文件：路径、大小、最后一次改动、是谁写的，最近改的在最前。

    这个房间的文件区此前只能按路径取一份（`files/raw`）：要打开一份，得先知道它叫
    什么名字。定时任务交回的结果、芝士交付的文档、有人放进来的文件都落在同一个目录
    下，这份清单就是那个目录本身，也是界面上看得见它的那一处。

    作者不在目录里，在保存记录里（`room_file_revisions`）：列完目录照最后一次保存
    补齐，没保存过的那几份照列。

    `truncated` 为真时说清单不是全部：超过上限的那部分没有列出来。`in_library` 说
    这一份有没有人保存到资料库（`POST /topics/{topic_id}/shown/save`）。
    """
    place, _ = await _in_room(db, resolver, topic_id)
    items, total = await asyncio.to_thread(
        library.list_room_files, place.project_id, place.room_id
    )
    saved = await room_files.latest_by_path(db, place.room_id)
    kept = await library_records.saved_from(db, place.room_id)
    rows: list[dict] = []
    for item in items:
        last = saved.get(item["path"])
        rows.append(
            {
                **item,
                # 记「原本就在的那一版」时不署人（作者是空串）：一份文件在有人动它之前
                # 就是那样，说不出来是谁写的。空串在这里按「没有」交出去。
                "author": (last.author_handle or None) if last is not None else None,
                "author_kind": last.author_kind if last is not None else None,
                "source": last.source if last is not None else None,
                "in_library": item["path"] in kept,
            }
        )
    return ok({**page(rows, total), "truncated": total > len(items)})


@router.get("/topics/{topic_id}/files/raw")
async def room_file_raw(
    topic_id: uuid.UUID, path: str, db: DbSession, resolver: ActorResolverDep
) -> Response:
    """The file as it is now, with its version — what `cheese pull` reads."""
    place, actor = await _in_room(db, resolver, topic_id)
    clean = clean_artifact_path(path)
    name = library.library_name(clean)
    if name is not None:
        shelf = await shelf_for(db, place.project_id)
        data = await library_records.read(db, shelf, name)
    else:
        data = await asyncio.to_thread(
            library.read_room_file, place.project_id, place.room_id, clean
        )
    return _bytes_response(data, clean.rsplit("/", 1)[-1], content_version(data))


@router.get("/topics/{topic_id}/files/revisions")
async def list_file_revisions(
    topic_id: uuid.UUID, path: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    place, _ = await _in_room(db, resolver, topic_id)
    clean = clean_artifact_path(path)
    rows = await room_files.list_revisions(db, place.room_id, clean)
    current = await asyncio.to_thread(
        room_files.current_version,
        place.project_id,
        place.room_id,
        clean,
        file_store=library,
    )
    items = [room_files.revision_out(r) for r in rows]
    return ok({**page(items, len(items)), "path": clean, "version": current})


@router.get("/topics/{topic_id}/files/revisions/{revision_id}/raw")
async def file_revision_raw(
    topic_id: uuid.UUID,
    revision_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> Response:
    place, _ = await _in_room(db, resolver, topic_id)
    row = await room_files.revision_or_404(db, place.room_id, revision_id)
    data = await asyncio.to_thread(room_files.revision_bytes, row, file_store=library)
    stem, dot, ext = row.path.rsplit("/", 1)[-1].rpartition(".")
    name = f"{stem}（第{row.seq}版）{dot}{ext}" if dot else f"{ext}（第{row.seq}版）"
    return _bytes_response(data, name, row.sha256[:16])


@router.post("/topics/{topic_id}/files/revisions/{revision_id}/restore")
async def restore_file_revision(
    at: Annotated[InChannel, Can(Permission.edit_content, at="topic_id")],
    revision_id: uuid.UUID,
    db: DbSession,
) -> dict:
    """Put an earlier save back. The current state becomes one more revision
    back, so a restore is itself undoable."""
    place, actor = at.place, at.actor
    row = await room_files.revision_or_404(db, place.room_id, revision_id)
    made = await room_files.restore_revision(
        db,
        row=row,
        by=actor.handle,
        author_kind=author_kind(actor),
        warm_preview=office.prewarm,
        file_store=library,
    )
    await db.commit()
    return ok(room_files.revision_out(made))


@router.post("/topics/{topic_id}/files/copy")
async def copy_into_room(
    at: Annotated[InChannel, Can(Permission.edit_content, at="topic_id")],
    body: dict,
    db: DbSession,
) -> dict:
    """Make an editable copy of a file in this room.

    The usual source is the project library: an uploaded original stays as it
    was given, and the room works on its own copy — which is also how a user's
    file becomes the template for a new one without being overwritten.
    """
    place, actor = at.place, at.actor
    source = clean_artifact_path(str(body.get("source") or ""))
    target = _room_path(str(body.get("path") or ""))
    if artifact_kind_for(target) not in ARTIFACT_MIME:
        raise ValidationError(say("unsupportedFileType"))
    name = library.library_name(source)
    if name is not None:
        shelf = await shelf_for(db, place.project_id)
        data = await library_records.read(db, shelf, name)
    else:
        data = await asyncio.to_thread(
            library.read_room_file, place.project_id, place.room_id, source
        )
    if await asyncio.to_thread(
        library.room_file_exists, place.project_id, place.room_id, target
    ):
        raise ConflictError(say("roomFileNameTaken"), data={"path": target})
    made = await room_files.save_room_file(
        db,
        project_id=place.project_id,
        room_id=place.room_id,
        path=target,
        data=data,
        author=actor.handle,
        author_kind=author_kind(actor),
        source="upload",
        note=f"从 {source} 复制",
        warm_preview=office.prewarm,
        file_store=library,
    )
    shown = await add_shown_block(
        db,
        project_id=place.project_id,
        conversation_id=place.room_id,
        path=target,
        author=actor.handle,
        mime=ARTIFACT_MIME[artifact_kind_for(target)],
    )
    # Live, like a published message: the reader is usually in the room
    # while 芝士 works, and the card has to appear then, not on the next
    # reload.
    await get_broker().publish(
        str(place.room_id),
        {"type": "assistant_block", "block": shown.model_dump(mode="json")},
    )
    await db.commit()
    return ok({"path": target, "version": made.sha256[:16]})


@router.get("/topics/{topic_id}/files/templates")
async def list_templates(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    await _in_room(db, resolver, topic_id)
    items = [t.as_dict() for t in catalogue.TEMPLATES]
    return ok(page(items, len(items)))


@router.post("/topics/{topic_id}/files/new")
async def new_from_template(
    at: Annotated[InChannel, Can(Permission.edit_content, at="topic_id")],
    body: dict,
    db: DbSession,
) -> dict:
    """A new room document, copied from a standard template.

    The name must keep the template's format: a 「报告」 saved as .xlsx would be a
    file that no program opens as what its name says.
    """
    place, actor = at.place, at.actor
    template = catalogue.find(str(body.get("template") or ""))
    if template is None:
        raise ValidationError(say("templateNotFound"))
    target = _room_path(str(body.get("path") or ""))
    if not target.lower().endswith(f".{template.suffix}"):
        raise ValidationError(
            say(
                "templateSuffixRequired", template=template.name, suffix=template.suffix
            )
        )
    if await asyncio.to_thread(
        library.room_file_exists, place.project_id, place.room_id, target
    ):
        raise ConflictError(say("roomFileNameTaken"), data={"path": target})
    made = await room_files.save_room_file(
        db,
        project_id=place.project_id,
        room_id=place.room_id,
        path=target,
        data=await asyncio.to_thread(catalogue.template_bytes, template),
        author=actor.handle,
        author_kind=author_kind(actor),
        source="template",
        note=f"从「{template.name}」模板新建",
        warm_preview=office.prewarm,
        file_store=library,
    )
    shown = await add_shown_block(
        db,
        project_id=place.project_id,
        conversation_id=place.room_id,
        path=target,
        author=actor.handle,
        mime=ARTIFACT_MIME[artifact_kind_for(target)],
    )
    # Live, like a published message: the reader is usually in the room
    # while 芝士 works, and the card has to appear then, not on the next
    # reload.
    await get_broker().publish(
        str(place.room_id),
        {"type": "assistant_block", "block": shown.model_dump(mode="json")},
    )
    await db.commit()
    return ok({"path": target, "version": made.sha256[:16]})


@router.get("/topics/{topic_id}/files/editor")
async def open_in_editor(
    topic_id: uuid.UUID, path: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The signed config the browser opens the office editor with.

    When it cannot open, ``reason`` is a code (``not_configured``,
    ``unsupported``, ``library_original``, ``attachment_original``) that the
    screen words in its
    reader's language (``work.room.fileEditor.unavailable.<code>``)."""
    place, actor = await _in_room(db, resolver, topic_id)
    clean = clean_artifact_path(path)
    if not editor.enabled():
        return ok({"enabled": False, "reason": "not_configured"})
    if editor.document_type(clean) is None:
        return ok({"enabled": False, "reason": "unsupported"})
    if library.library_name(clean) is not None:
        return ok({"enabled": False, "reason": "library_original", "copyable": True})
    if library.is_message_attachment(clean):
        return ok({"enabled": False, "reason": "attachment_original", "copyable": True})
    version = await asyncio.to_thread(
        room_files.current_version,
        place.project_id,
        place.room_id,
        clean,
        file_store=library,
    )
    if version is None:
        raise NotFoundError(say("roomFileNotFound"))
    config = editor.editor_config(
        project_id=place.project_id,
        room_id=place.room_id,
        path=clean,
        version=version,
        handle=actor.handle,
        display_name=actor.handle,
        can_edit=actor.authenticated
        and await may(db, place.project_id, actor.handle, Permission.edit_content),
    )
    return ok(
        {
            "enabled": True,
            "editable": editor.editable(clean),
            "api_url": settings.office_editor_url.rstrip("/")
            + "/web-apps/apps/api/documents/api.js",
            "version": version,
            "config": config,
        }
    )


@router.post("/topics/{topic_id}/files/text", **typed_response(RoomFileRevisionOut))
async def write_text_file(
    at: Annotated[InChannel, Can(Permission.edit_content, at="topic_id")],
    body: dict,
    db: DbSession,
) -> dict:
    """Save an edit to a room file that is text.

    The counterpart of `write_file` on the workspace side, for the room's own
    files rather than a task's worktree. The preview panel edits a markdown
    file where it is shown, and what it saves has to land as a new version of
    the file everyone reads — not as a copy somewhere else.

    `version` is the one the caller read (the room-file read hands back
    `content_version`, the same id this route compares against). It is required,
    not optional: a save that skipped the read has nothing to compare against and
    would overwrite whoever saved in between — the one thing this route exists to
    prevent. The same rule the workspace side enforces (`taskFileReadBeforeSave`).
    """
    place, actor = at.place, at.actor
    path = _room_path(str(body.get("path") or ""))
    content = body.get("content")
    if not isinstance(content, str):
        raise ValidationError(say("contentRequired"))
    expected = str(body.get("version") or "")
    if not expected:
        raise ValidationError(say("taskFileReadBeforeSave"))
    if not await asyncio.to_thread(
        library.room_file_exists, place.project_id, place.room_id, path
    ):
        raise NotFoundError(say("roomFileNotFound"))
    # 这是「文本」那条路：一份二进制（docx、表格、图片）也能有这个地址，把 UTF-8 写进
    # 去就把它毁了（旧版还在历史里，但文件本身没了）。读的人拿到的 `binary` 为真，
    # 那也正是「这一份不是文本」；这里照同一条读法问一次，不靠面板那一层只给 markdown
    # 开按钮。同一次读也把版本带回来——比的就是读它的人手上那一版。
    #
    # 判 `binary`，不是判 `content` 是不是 null：大过 `MAX_TEXT_BYTES` 的文本（1 MB）
    # 正文也不交出来（`too_large`），但它是文本、写得回去——`save_room_file` 收得下
    # 它。`binary` 和 `too_large` 在 `domain/textfile.py` 里是分开的两件事。
    shown = await asyncio.to_thread(
        library.read_room_text_file, place.project_id, place.room_id, path
    )
    if shown.get("binary"):
        raise UnprocessableEntityError(say("roomFileNotText"))
    version = shown.get("version")
    if version is None:
        raise NotFoundError(say("roomFileNotFound"))
    if expected != version:
        raise ConflictError(
            say("roomFileMovedOn"), data={"version": version, "path": path}
        )
    made = await room_files.save_room_file(
        db,
        project_id=place.project_id,
        room_id=place.room_id,
        path=path,
        data=content.encode("utf-8"),
        author=actor.handle,
        author_kind=author_kind(actor),
        source="editor",
        base_version=version,
        note=str(body.get("note") or "") or None,
        warm_preview=office.prewarm,
        file_store=library,
    )
    await db.commit()
    return ok(room_files.revision_out(made))


@router.get("/office-editor/files/{link}")
async def editor_fetches_file(link: str) -> Response:
    """The document, for the editor. The link token is the only credential."""
    try:
        target = editor.read_link(link)
    except (editor.EditorRefused, editor.EditorUnavailable) as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    data = await asyncio.to_thread(
        library.read_room_file, target.project_id, target.room_id, target.path
    )
    return _bytes_response(data, target.path.rsplit("/", 1)[-1], content_version(data))


@router.get("/topics/{topic_id}/files/collab", **typed_response(LiveEditorOut))
async def open_live_editor(
    topic_id: uuid.UUID,
    path: str,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """A ticket that opens this room file in the live editor, where several
    people edit it at once.

    The counterpart of `open_in_editor` for the office files: same shape, same
    gate (opening a room is enough to read; changing it needs
    `edit_content`). The file gets a live state made for it on first open, and
    its id is what the collaboration service names the document by.

    Only text is served here: the office kinds go through OnlyOffice, whose
    edits land as whole-file saves, not as a shared live document. A file that
    is not there is a 404; the ones that cannot open live answer `enabled` false
    with a `reason` the screen words
    (`work.room.fileEditor.unavailable.<reason>`): `not_configured`,
    `unsupported` (a binary), `too_large` (text over the preview's ceiling,
    whose text is never read into a body).
    """
    place, actor = await _in_room(db, resolver, topic_id)
    clean = _room_path(path)
    if not collab.enabled():
        return ok({"enabled": False, "reason": "not_configured"})
    if not await asyncio.to_thread(
        library.room_file_exists, place.project_id, place.room_id, clean
    ):
        raise NotFoundError(say("roomFileNotFound"))
    shown = await asyncio.to_thread(
        library.read_room_text_file, place.project_id, place.room_id, clean
    )
    if shown.get("binary"):
        return ok({"enabled": False, "reason": "unsupported"})
    content = shown.get("content")
    if content is None:
        # Text over the ceiling is not read into a body at all (`too_large`).
        # Opening it live would seed the state with text nobody read, and the
        # service's first store would write that back over the file.
        return ok({"enabled": False, "reason": "too_large"})
    can_edit = actor.authenticated and await may(
        db, place.project_id, actor.handle, Permission.edit_content
    )
    agent = await IdentityService(db).is_agent(actor.handle)
    row = await room_file_collab.open_for(
        db,
        project_id=place.project_id,
        room_id=place.room_id,
        path=clean,
        content=content,
    )
    await db.commit()
    name = collab.room_file_name(row.id)
    return ok(
        {
            "enabled": True,
            "document": name,
            "ticket": collab.sign_ticket(
                name=name,
                handle=actor.handle,
                agent=agent,
                read_only=not can_edit,
                suggest_only=False,
            ),
            "read_only": not can_edit,
            "version": shown.get("version"),
            "content": content,
        }
    )


#: https://api.onlyoffice.com/docs/docs-api/usage-api/callback-handler/
_MUST_SAVE = (2, 6)
_SAVE_FAILED = (3, 7)


@router.post("/office-editor/callback/{link}", dependencies=[ROUTE_DECIDES])
async def editor_saves_file(
    link: str,
    request: Request,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """The editor telling us a document changed: 2 when the last person closed
    it, 6 on each explicit save. Any answer but `{"error": 0}` makes the editor
    tell the person their work was not saved, so that is only said when true."""
    try:
        target = editor.read_link(link)
        body = await request.json()
        payload = editor.verify_callback(body, authorization)
    except (editor.EditorRefused, editor.EditorUnavailable, ValueError) as exc:
        logger.warning("office editor callback refused: %s", exc)
        return JSONResponse({"error": 1, "message": str(exc)}, status_code=403)
    status = payload.get("status")
    if status in _SAVE_FAILED:
        logger.error("office editor failed to save %s: %s", target.path, payload)
        return JSONResponse({"error": 0})
    if status not in _MUST_SAVE:
        return JSONResponse({"error": 0})
    try:
        url = editor.internal_download_url(str(payload.get("url") or ""))
        async with httpx.AsyncClient(timeout=60) as client:
            got = await client.get(url)
            got.raise_for_status()
            data = got.content
    except (editor.EditorRefused, httpx.HTTPError) as exc:
        logger.error("office editor result unreachable for %s: %s", target.path, exc)
        return JSONResponse({"error": 1})
    users = payload.get("users") or []
    author = str(users[0]) if users else target.handle
    aside: str | None = None
    if await room_files.saved_by_session(
        db, target.room_id, target.path, target.key, data
    ):
        return JSONResponse({"error": 0})
    try:
        await room_files.save_room_file(
            db,
            project_id=target.project_id,
            room_id=target.room_id,
            path=target.path,
            data=data,
            author=author,
            author_kind="human",
            source="editor",
            base_version=target.version,
            editor_key=target.key,
            warm_preview=office.prewarm,
            file_store=library,
        )
    except UnprocessableEntityError as exc:
        # Over the ceiling: a refusal, and the editor has to hear why. Left to
        # itself it arrives as a 422 whose body is not `{"error": 0}` — "not
        # saved" without a reason. Answer in the shape it reads instead.
        logger.warning("office editor save refused: %s", exc)
        await db.rollback()
        return JSONResponse({"error": 1, "message": str(exc)}, status_code=403)
    except ConflictError:
        # Somebody else saved this file after the editor opened it. Writing
        # over it would drop their change without anyone seeing; dropping this
        # one would lose the person's edit. Keep both: theirs stays the file,
        # this one lands beside it and is named so.
        stem, dot, ext = target.path.rpartition(".")
        mark = f"（{author} 的修改）"
        aside = f"{stem}{mark}.{ext}" if dot else f"{target.path}{mark}"
        await db.rollback()
        try:
            await room_files.save_room_file(
                db,
                project_id=target.project_id,
                room_id=target.room_id,
                path=aside,
                data=data,
                author=author,
                author_kind="human",
                source="editor",
                note=f"保存时 {target.path} 已被别人改过，这一份另存在这里",
                editor_key=target.key,
                warm_preview=office.prewarm,
                file_store=library,
            )
        except UnprocessableEntityError as exc:
            # Same ceiling, same answer — the copy beside it is a file too.
            logger.warning("office editor save refused: %s", exc)
            await db.rollback()
            return JSONResponse({"error": 1, "message": str(exc)}, status_code=403)
        place = await place_or_404(db, target.room_id)
        shown = await add_shown_block(
            db,
            project_id=place.project_id,
            conversation_id=place.room_id,
            path=aside,
            author=author,
            mime=ARTIFACT_MIME[artifact_kind_for(aside)],
        )
        # Live, like a published message: the reader is usually in the room
        # while 芝士 works, and the card has to appear then, not on the next
        # reload.
        await get_broker().publish(
            str(place.room_id),
            {"type": "assistant_block", "block": shown.model_dump(mode="json")},
        )
    # The AI teammate may hold the version it read; tell it before its next
    # step, the same as a person's save of a task file.
    said = await announce_editor_save(
        db, room_id=target.room_id, who=author, path=target.path, aside=aside
    )
    await db.commit()
    if said is not None:
        line, told = said
        await chat.notify_running_session(target.room_id, told, blocks=[line.id])
    return JSONResponse({"error": 0})
