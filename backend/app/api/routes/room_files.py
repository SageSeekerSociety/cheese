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
from app.api.request_deps import DbSession
from app.api.response import Page, ok, page, typed_response
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
from app.domain.library import records as library_records
from app.domain.library import service as library
from app.domain.preview import office
from app.domain.project import room_files
from app.domain.project.room_files import (
    ARTIFACT_MIME,
    artifact_kind_for,
    clean_artifact_path,
)
from app.domain.room_task.place import Place
from app.domain.textfile import content_version
from app.domain.topic.services import TopicService

logger = logging.getLogger("cheesex.room_files")

router = APIRouter(tags=["room-files"])


async def _in_room(
    db: AsyncSession, resolver: ActorResolver, topic_id: uuid.UUID
) -> tuple[Place, Actor]:
    place = await TopicService(db).place_or_404(topic_id)
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


class RoomFileListOut(Page[RoomFileOut]):
    """文件区的清单：``data`` 是文件，``truncated`` 说清单是不是全部。"""

    truncated: bool


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

    `truncated` 为真时说清单不是全部：超过上限的那部分没有列出来。
    """
    place, _ = await _in_room(db, resolver, topic_id)
    items, total = await asyncio.to_thread(
        library.list_room_files, place.project_id, place.room_id
    )
    saved = await room_files.latest_by_path(db, place.room_id)
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
            }
        )
    return ok({**page(rows, total), "truncated": total > len(items)})


@router.get("/topics/{topic_id}/files/raw")
async def room_file_raw(
    topic_id: uuid.UUID, path: str, db: DbSession, resolver: ActorResolverDep
) -> Response:
    """The file as it is now, with its version — what `cheese pull` reads."""
    place, _ = await _in_room(db, resolver, topic_id)
    clean = clean_artifact_path(path)
    name = library.library_name(clean)
    if name is not None:
        data = await library_records.read(db, place.project_id, name)
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
    topic_id: uuid.UUID,
    revision_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Put an earlier save back. The current state becomes one more revision
    back, so a restore is itself undoable."""
    place, actor = await _in_room(db, resolver, topic_id)
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
    topic_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Make an editable copy of a file in this room.

    The usual source is the project library: an uploaded original stays as it
    was given, and the room works on its own copy — which is also how a user's
    file becomes the template for a new one without being overwritten.
    """
    place, actor = await _in_room(db, resolver, topic_id)
    source = clean_artifact_path(str(body.get("source") or ""))
    target = _room_path(str(body.get("path") or ""))
    if artifact_kind_for(target) not in ARTIFACT_MIME:
        raise ValidationError(say("unsupportedFileType"))
    name = library.library_name(source)
    if name is not None:
        data = await library_records.read(db, place.project_id, name)
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
    topic_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """A new room document, copied from a standard template.

    The name must keep the template's format: a 「报告」 saved as .xlsx would be a
    file that no program opens as what its name says.
    """
    place, actor = await _in_room(db, resolver, topic_id)
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
    ``unsupported``, ``library_original``) that the screen words in its
    reader's language (``work.room.fileEditor.unavailable.<code>``)."""
    place, actor = await _in_room(db, resolver, topic_id)
    clean = clean_artifact_path(path)
    if not editor.enabled():
        return ok({"enabled": False, "reason": "not_configured"})
    if editor.document_type(clean) is None:
        return ok({"enabled": False, "reason": "unsupported"})
    if library.library_name(clean) is not None:
        return ok({"enabled": False, "reason": "library_original", "copyable": True})
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
        can_edit=actor.authenticated,
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


#: https://api.onlyoffice.com/docs/docs-api/usage-api/callback-handler/
_MUST_SAVE = (2, 6)
_SAVE_FAILED = (3, 7)


@router.post("/office-editor/callback/{link}")
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
        place = await TopicService(db).place_or_404(target.room_id)
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
        await chat.notify_running_turn(target.room_id, told, blocks=[line.id])
    return JSONResponse({"error": 0})
