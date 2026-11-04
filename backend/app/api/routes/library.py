"""资料库：用户给这个项目的文件，按原名，每个房间都引用得到。

清单、名字和来源在记录表里，字节按每一行的键存放（`app.domain.library.records`）。
名字里的 `/` 是文件夹。读的是人和芝士；放进、替换、挪动、扔掉的只有人。从题目来的
项目还有一个只读的 `题目/`：题目的材料和题目说明（`app.domain.library.mount`），这里
每一条读的路都读得到它，写的路都拒绝它。
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.library_bytes import library_file_response
from app.api.place import readable_rooms
from app.api.project_library import shelf_for
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.identity.actor import Actor
from app.domain.library import listing as library_listing
from app.domain.library import records as library_records
from app.domain.library import service as library
from app.domain.library.shelf import Shelf
from app.domain.project.services import ProjectService
from app.domain.project_permissions import Permission
from app.domain.team.services import team_service
from app.domain.user.services import user_by_handle

router = APIRouter(prefix="/projects", tags=["library"])


class LibraryRoom(BaseModel):
    """一份资料是在哪个房间给出来的（`room_ref`）：房间的 id 和名字。"""

    id: str
    title: str


class LibraryFileOut(BaseModel):
    """清单里的一份文件（`library_listing._describe` 写的那一行）。"""

    type: Literal["file"]
    path: str
    bytes: int
    modified: float
    added_by: str | None
    added_at: str
    room: LibraryRoom | None
    replaced: int
    references: int
    #: 从题目来的那一份（`题目/` 底下）：只能读。
    readonly: bool
    rank: str


class LibraryTextOut(BaseModel):
    """一份资料读成的文本（`library_records.read_text`）：读不成文本时
    `content` 是 None，`binary` 或 `too_large` 说为什么。"""

    path: str
    bytes: int
    binary: bool
    too_large: bool
    content: str | None
    version: str


class LibraryFoldersOut(BaseModel):
    """资料库里的每一个文件夹（整条路径），给「移动到」挑。"""

    folders: list[str]


class LibraryLinkOut(BaseModel):
    """一份资料的下载地址，几分钟内开始取都有效；以 ``/`` 开头的是本后端的地址。"""

    url: str
    path: str
    bytes: int


class LibraryMoved(BaseModel):
    """每个旧名字变成了什么（`library_records.move`）；名字没变时是空的。"""

    moved: dict[str, str]


@router.get("/{project_id}/library/raw")
async def library_file_raw(
    project_id: uuid.UUID,
    path: str,
    db: DbSession,
    resolver: ActorResolverDep,
    request: Request,
    preview_pdf: bool = False,
    version: uuid.UUID | None = None,
) -> Response:
    """一份资料的字节，给下载。机器取一份资料走 ``/library/link``：它从存储直接取，
    不经过这里。

    `preview_pdf`：Office 文档转成 PDF，给资料库页预览。

    `version`：版本列表里那一版的 id，下载被替换下来的旧版用；不给就是现在这一版。

    不让浏览器凭缓存直接用：一份资料可以被「替换为新版本」，同一个地址下的字节会
    变。"""
    await ProjectService(db).get_or_404(project_id)
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    return await library_file_response(
        db,
        await shelf_for(db, project_id),
        _library_path(path),
        version,
        preview_pdf,
        request,
    )


def _shelf(project_id: uuid.UUID) -> Shelf:
    # 写的那几条路用它：`题目/` 底下谁都写不进去（`mount.refuse_write`），
    # 读的路一律取 `shelf_for` 给的那一份（`题目/` 有没有，看项目的申请）。
    return Shelf.of_project(project_id, with_task=False)


def _library_path(raw: str) -> str:
    """资料库里那一份的名字——它就是地址，所以这里只挡不是名字的东西。"""
    name = (raw or "").strip()
    if not name or name.startswith("/") or ".." in name.split("/"):
        raise ValidationError(say("libraryPathRelative"))
    return name


@router.get("/{project_id}/library/link", **typed_response(LibraryLinkOut))
async def library_file_link(
    project_id: uuid.UUID, path: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """一份资料的下载地址，几分钟内开始取都有效：给 `cheese library get`，字节从
    存储直接到机器上，不经过这里，也不整份读进谁的内存。文件存在本服务器盘上时，
    地址是本后端自己的一条（``/files/outgoing/…``），照样按段给。`题目/` 底下的
    那几份照 `shelf_for` 取，和下载那一条一样。"""
    await _reader(project_id, db, resolver)
    shelf = await shelf_for(db, project_id)
    name = _library_path(path.strip().removeprefix("library/"))
    stored, url = await library_records.download_link(db, shelf, name)
    return ok({"url": url, "path": name, "bytes": stored.size})


@router.get("/{project_id}/library/versions")
async def list_library_versions(
    project_id: uuid.UUID,
    path: str,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """一份资料的每一版（新的在前）：版本号、什么时候、谁放进来的、多大。"""
    await ProjectService(db).get_or_404(project_id)
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    versions = await library_records.versions(
        db, await shelf_for(db, project_id), _library_path(path)
    )
    return ok({"versions": versions})


@router.post("/{project_id}/library/restore", dependencies=[ROUTE_DECIDES])
async def restore_library_version(
    project_id: uuid.UUID,
    path: str,
    version: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """把旧的一版恢复成现在这一份：复制成新的一版，历史只增不减。只有人能做。"""
    actor = await _library_keeper(project_id, db, resolver)
    name = _library_path(path)
    await library_records.restore(
        db, shelf=_shelf(project_id), name=name, version_id=version, by=actor.handle
    )
    await db.commit()
    return ok({"path": name})


@router.get("/{project_id}/library")
async def list_library(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    dir: str = "",
    flat: bool = False,
    q: str = "",
    kind: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    """资料库：用户给这个项目的文件，按原名，每个房间都引用得到。一页一页地给。

    不给 `flat` / `q` / `kind` 时是 `dir` 那一层：文件夹在前，然后是文件；给了就是
    整个资料库里对得上的文件（只给 `flat` 是全部）。`next` 是下一页的游标，
    没有下一页时为 null。

    Project-level on purpose — 「上周那份预算表」is a sentence someone says in a
    room that has never seen that file."""
    actor = await _reader(project_id, db, resolver)
    rooms = await readable_rooms(db, resolver, actor, project_id)
    entries, after = await library_listing.page(
        db,
        await shelf_for(db, project_id),
        rooms,
        dir=dir.strip("/"),
        flat=flat,
        q=q.strip(),
        kind=kind,
        after=cursor,
        limit=limit,
    )
    return ok({"data": entries, "next": after})


@router.get("/{project_id}/library/file", **typed_response(LibraryFileOut))
async def library_file(
    project_id: uuid.UUID, path: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """一份资料的那一行：地址上点名的那一份不一定在已经取回来的那几页里。"""
    actor = await _reader(project_id, db, resolver)
    rooms = await readable_rooms(db, resolver, actor, project_id)
    shelf = await shelf_for(db, project_id)
    return ok(await library_listing.one(db, shelf, rooms, _library_path(path)))


@router.get("/{project_id}/library/text", **typed_response(LibraryTextOut))
async def library_file_text(
    project_id: uuid.UUID, path: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """一份资料读成文本，给芝士读（`cheese_library(action=read)` 读 `library/` 开头的地址）：
    文本文件照原样，PDF 取文字层，别的只回大小。和房间无关，所以没有房间的文档芝士
    （项目总览、资料库里的文档）也读得到项目给它的材料。"""
    await _reader(project_id, db, resolver)
    name = _library_path(path.strip().removeprefix("library/"))
    found = await library_records.read_text(db, await shelf_for(db, project_id), name)
    return ok(LibraryTextOut.model_validate(found).model_dump())


@router.get("/{project_id}/library/folders", **typed_response(LibraryFoldersOut))
async def library_folders(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """资料库里的每一个文件夹（整条路径），给「移动到」挑。"""
    await _reader(project_id, db, resolver)
    return ok({"folders": await library_listing.folders(db, _shelf(project_id))})


async def _reader(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> Actor:
    await ProjectService(db).get_or_404(project_id)
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    return actor


async def _library_keeper(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> Actor:
    """往资料库里放、换、扔东西的只有人：一轮里铸出来的凭据过不了
    `authorize_project`。"""
    await ProjectService(db).get_or_404(project_id)
    actor = await resolver.require_verified_caller(project_id=project_id)
    await resolver.authorize_project(
        actor, project_id=project_id, permission=Permission.edit_content
    )
    return actor


class LibraryUploadSlotIn(BaseModel):
    """要放进来的那一份叫什么、多大：大小定下了一共收多少字节、切成几段。"""

    name: str
    size: int


class LibraryUploadSlot(BaseModel):
    """一次上传的凭据（``upload``）和它怎么切：每段 ``part_bytes`` 个字节，一共
    ``parts`` 段，最后一段可以短。每一段的地址另领（``…/uploads/part``）。"""

    upload: str
    part_bytes: int
    parts: int


class LibraryUploadPartIn(BaseModel):
    """第 ``part`` 段（从 1 数）的字节的 MD5，十六进制。"""

    upload: str
    part: int = Field(ge=1)
    md5: str


class LibraryUploadPart(BaseModel):
    """把这一段的字节原样 PUT 到 ``url``（以 ``/`` 开头的是本后端的地址），带上
    ``headers``。"""

    url: str
    headers: dict[str, str]


class LibraryUploaded(BaseModel):
    """一次传完的上传：领凭据时拿到的 ``upload``，整份的 SHA-256，和每一段的
    MD5（按段的顺序），都是十六进制。"""

    upload: str
    sha256: str
    parts: list[str]


class LibraryAddIn(LibraryUploaded):
    """登记一份已经传上来的文件，``folder`` 给了就放进那个文件夹。"""

    filename: str
    folder: str | None = None


class LibraryReplaceIn(LibraryUploaded):
    pass


def _received(shelf: Shelf, body: LibraryUploaded) -> library_records.Upload:
    return library_records.received(shelf, body.upload, body.sha256, body.parts)


@router.post(
    "/{project_id}/library/uploads",
    dependencies=[ROUTE_DECIDES],
    **typed_response(LibraryUploadSlot),
)
async def library_upload_slot(
    project_id: uuid.UUID,
    body: LibraryUploadSlotIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """领一次上传的凭据。字节不经过这里：浏览器按段领地址，直接放进存储，再来登记
    （放进新的一份是 ``POST /library``，换成新版本是 ``PUT /library``）。超过上限的
    不给。"""
    await _library_keeper(project_id, db, resolver)
    if body.size <= 0:
        raise ValidationError(say("emptyFile"))
    name = library.clean_upload_name(body.name)
    return ok(await library_records.upload_slot(_shelf(project_id), name, body.size))


@router.post(
    "/{project_id}/library/uploads/part",
    dependencies=[ROUTE_DECIDES],
    **typed_response(LibraryUploadPart),
)
async def library_upload_part(
    project_id: uuid.UUID,
    body: LibraryUploadPartIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """一次上传里一段的地址：只收这一段，字节的 MD5 对不上的存储不收。"""
    await _library_keeper(project_id, db, resolver)
    return ok(
        await library_records.upload_part(
            _shelf(project_id), body.upload, body.part, body.md5
        )
    )


@router.post("/{project_id}/library", dependencies=[ROUTE_DECIDES])
async def upload_library_file(
    project_id: uuid.UUID,
    body: LibraryAddIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """把传上来的一份登记进资料库，`folder` 给了就放进那个文件夹（没有就新建）。
    撞名不覆盖：拿下一个 `(n)`。"""
    actor = await _library_keeper(project_id, db, resolver)
    upload = _received(_shelf(project_id), body)
    filename = library.clean_upload_name(body.filename)
    if body.folder and body.folder.strip("/ "):
        filename = f"{library.clean_library_path(body.folder)}/{filename}"
    name = await library_records.add(
        db,
        _shelf(project_id),
        filename=filename,
        data=upload,
        added_by=actor.handle,
        room_id=None,
    )
    await db.commit()
    return ok({"path": name, "bytes": upload.size})


@router.put("/{project_id}/library", dependencies=[ROUTE_DECIDES])
async def replace_library_file(
    project_id: uuid.UUID,
    path: str,
    body: LibraryReplaceIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """把一份资料换成传上来的新版本。旧的那一份留着；引用这个名字的消息读到的是新的。"""
    actor = await _library_keeper(project_id, db, resolver)
    upload = _received(_shelf(project_id), body)
    name = _library_path(path)
    await library_records.replace(
        db, shelf=_shelf(project_id), name=name, data=upload, by=actor.handle
    )
    await db.commit()
    return ok({"path": name, "bytes": upload.size})


@router.delete("/{project_id}/library", dependencies=[ROUTE_DECIDES])
async def delete_library_file(
    project_id: uuid.UUID, path: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """扔掉一份资料，或者一个文件夹连同它里面的一切。

    读资料库的是人和 芝士，扔掉它的只有人。一轮里铸出来的凭据在 `authorize_project`
    那里只读得进来，所以 芝士 连同它自己正在读的那一份都删不掉。"""
    await _library_keeper(project_id, db, resolver)
    await library_records.remove(db, shelf=_shelf(project_id), name=_library_path(path))
    await db.commit()
    return ok({"deleted": True})


@router.post(
    "/{project_id}/library/move",
    dependencies=[ROUTE_DECIDES],
    **typed_response(LibraryMoved),
)
async def move_library_file(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    path: Annotated[str, Body()],
    to: Annotated[str, Body()],
) -> dict:
    """把一份资料或一个文件夹改名、挪到别的文件夹：`to` 是它的新名字。

    字节不动；引用旧名字的消息跟着改过去。新名字被占了就拒绝，不替人编号——挪动
    是人有意给它起的名字，悄悄变成 `(2)` 就不是那个名字了。"""
    await _library_keeper(project_id, db, resolver)
    moved = await library_records.move(
        db,
        shelf=_shelf(project_id),
        source=_library_path(path),
        target=library.clean_library_path(to),
    )
    await db.commit()
    return ok({"moved": moved})


# --- 团队的资料库 ----------------------------------------------------------------
#
# 一个团队自己的资料库，一个人自己的就是他名下那个团队的。和项目的资料库一样读写，
# 只是没有房间：没有哪条消息引用它，清单上也就没有来源房间和引用数。团队的成员都能
# 读、能放、能删。个人芝士碰用户自己的资料库用它自己的工具（`/assistant/agent/library`），
# 没有哪样工具调这里。

teams = APIRouter(prefix="/teams", tags=["library"])


class LibraryFolderOut(BaseModel):
    """清单里的一个文件夹（`library_listing._folders` 写的那一行）。"""

    type: Literal["folder"]
    path: str
    name: str
    count: int
    modified: float
    #: `题目` 那个文件夹和它底下的：只能读。
    readonly: bool
    rank: str


class LibraryPageOut(BaseModel):
    """一页清单：文件夹在前，然后是文件；``next`` 是下一页的游标。"""

    data: list[LibraryFolderOut | LibraryFileOut]
    next: str | None


class LibraryVersionOut(BaseModel):
    id: str
    version: int
    bytes: int
    added_by: str | None
    created_at: str | None
    current: bool


class LibraryVersionsOut(BaseModel):
    versions: list[LibraryVersionOut]


class LibraryPlacedOut(BaseModel):
    """放进、换成新版本之后它叫什么、多大。"""

    path: str
    bytes: int


class LibraryRestoredOut(BaseModel):
    path: str


class LibraryDeletedOut(BaseModel):
    deleted: bool


#: 一份资料的字节，原样给出。
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


class LibraryRoomOut(BaseModel):
    """团队的资料库占了多少、一共能放多少（字节）。"""

    used: int
    total: int


async def _team_member(
    team_id: int, db: DbSession, resolver: ActorResolverDep
) -> Actor:
    """团队的成员本人，别人一律不行。"""
    actor = await resolver.resolve()
    resolver.reject_failed_credential(actor)
    if not actor.authenticated:
        raise AuthenticationRequiredError(say("libraryTeamMembersOnly"))
    user = await user_by_handle(db, actor.handle)
    if (
        actor.via == "cheese"
        or user is None
        or not await team_service(db).is_team_member(team_id, user.id)
    ):
        raise ForbiddenError(say("libraryTeamMembersOnly"))
    return actor


def _team_shelf(team_id: int) -> Shelf:
    return Shelf.of_team(team_id)


@teams.get("/{team_id}/library", **typed_response(LibraryPageOut))
async def list_team_library(
    team_id: int,
    db: DbSession,
    resolver: ActorResolverDep,
    dir: str = "",
    flat: bool = False,
    q: str = "",
    kind: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    """团队的资料库，一页一页地给，和项目的资料库一样翻。"""
    await _team_member(team_id, db, resolver)
    entries, after = await library_listing.page(
        db,
        _team_shelf(team_id),
        {},
        dir=dir.strip("/"),
        flat=flat,
        q=q.strip(),
        kind=kind,
        after=cursor,
        limit=limit,
    )
    return ok({"data": entries, "next": after})


@teams.get("/{team_id}/library/room", **typed_response(LibraryRoomOut))
async def team_library_room(
    team_id: int, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """占了多少、一共能放多少：资料库页上的用量。"""
    await _team_member(team_id, db, resolver)
    used = await library_records.used(db, _team_shelf(team_id))
    return ok({"used": used, "total": library_records.capacity()})


@teams.get("/{team_id}/library/file", **typed_response(LibraryFileOut))
async def team_library_file(
    team_id: int, path: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    await _team_member(team_id, db, resolver)
    return ok(
        await library_listing.one(db, _team_shelf(team_id), {}, _library_path(path))
    )


@teams.get("/{team_id}/library/folders", **typed_response(LibraryFoldersOut))
async def team_library_folders(
    team_id: int, db: DbSession, resolver: ActorResolverDep
) -> dict:
    await _team_member(team_id, db, resolver)
    return ok({"folders": await library_listing.folders(db, _team_shelf(team_id))})


@teams.get("/{team_id}/library/raw", **BYTES)
async def team_library_file_raw(
    team_id: int,
    path: str,
    db: DbSession,
    resolver: ActorResolverDep,
    request: Request,
    preview_pdf: bool = False,
    version: uuid.UUID | None = None,
) -> Response:
    """一份资料的字节，和项目资料库的那一条一样（`library_file_raw`）。"""
    await _team_member(team_id, db, resolver)
    return await library_file_response(
        db, _team_shelf(team_id), _library_path(path), version, preview_pdf, request
    )


@teams.get("/{team_id}/library/versions", **typed_response(LibraryVersionsOut))
async def list_team_library_versions(
    team_id: int, path: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    await _team_member(team_id, db, resolver)
    versions = await library_records.versions(
        db, _team_shelf(team_id), _library_path(path)
    )
    return ok({"versions": versions})


@teams.post(
    "/{team_id}/library/restore",
    dependencies=[ROUTE_DECIDES],
    **typed_response(LibraryRestoredOut),
)
async def restore_team_library_version(
    team_id: int,
    path: str,
    version: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    actor = await _team_member(team_id, db, resolver)
    shelf = _team_shelf(team_id)
    name = _library_path(path)
    stored = await library_records.stored_version(db, shelf, name, version)
    await library_records.refuse_past_room(db, shelf, stored.size)
    await library_records.restore(
        db, shelf=shelf, name=name, version_id=version, by=actor.handle
    )
    await db.commit()
    return ok({"path": name})


@teams.post(
    "/{team_id}/library/uploads",
    dependencies=[ROUTE_DECIDES],
    **typed_response(LibraryUploadSlot),
)
async def team_library_upload_slot(
    team_id: int,
    body: LibraryUploadSlotIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """领一个上传地址，和项目资料库一样；放不下的这里就不给。"""
    await _team_member(team_id, db, resolver)
    if body.size <= 0:
        raise ValidationError(say("emptyFile"))
    shelf = _team_shelf(team_id)
    name = library.clean_upload_name(body.name)
    await library_records.refuse_past_room(db, shelf, body.size)
    return ok(await library_records.upload_slot(shelf, name, body.size))


@teams.post(
    "/{team_id}/library/uploads/part",
    dependencies=[ROUTE_DECIDES],
    **typed_response(LibraryUploadPart),
)
async def team_library_upload_part(
    team_id: int,
    body: LibraryUploadPartIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    await _team_member(team_id, db, resolver)
    return ok(
        await library_records.upload_part(
            _team_shelf(team_id), body.upload, body.part, body.md5
        )
    )


@teams.post(
    "/{team_id}/library",
    dependencies=[ROUTE_DECIDES],
    **typed_response(LibraryPlacedOut),
)
async def upload_team_library_file(
    team_id: int, body: LibraryAddIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await _team_member(team_id, db, resolver)
    shelf = _team_shelf(team_id)
    upload = _received(shelf, body)
    filename = library.clean_upload_name(body.filename)
    if body.folder and body.folder.strip("/ "):
        filename = f"{library.clean_library_path(body.folder)}/{filename}"
    name = await library_records.add(
        db, shelf, filename=filename, data=upload, added_by=actor.handle, room_id=None
    )
    await db.commit()
    return ok({"path": name, "bytes": upload.size})


@teams.put(
    "/{team_id}/library",
    dependencies=[ROUTE_DECIDES],
    **typed_response(LibraryPlacedOut),
)
async def replace_team_library_file(
    team_id: int,
    path: str,
    body: LibraryReplaceIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    actor = await _team_member(team_id, db, resolver)
    shelf = _team_shelf(team_id)
    upload = _received(shelf, body)
    name = _library_path(path)
    await library_records.replace(
        db, shelf=shelf, name=name, data=upload, by=actor.handle
    )
    await db.commit()
    return ok({"path": name, "bytes": upload.size})


@teams.delete(
    "/{team_id}/library",
    dependencies=[ROUTE_DECIDES],
    **typed_response(LibraryDeletedOut),
)
async def delete_team_library_file(
    team_id: int, path: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    await _team_member(team_id, db, resolver)
    await library_records.remove(
        db, shelf=_team_shelf(team_id), name=_library_path(path)
    )
    await db.commit()
    return ok({"deleted": True})


@teams.post(
    "/{team_id}/library/move",
    dependencies=[ROUTE_DECIDES],
    **typed_response(LibraryMoved),
)
async def move_team_library_file(
    team_id: int,
    db: DbSession,
    resolver: ActorResolverDep,
    path: Annotated[str, Body()],
    to: Annotated[str, Body()],
) -> dict:
    await _team_member(team_id, db, resolver)
    moved = await library_records.move(
        db,
        shelf=_team_shelf(team_id),
        source=_library_path(path),
        target=library.clean_library_path(to),
    )
    await db.commit()
    return ok({"moved": moved})
