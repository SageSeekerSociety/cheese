"""资料库：用户给这个项目的文件，按原名，每个房间都引用得到。

清单、名字和来源在记录表里，字节按每一行的键存放（`app.domain.library.records`）。
名字里的 `/` 是文件夹。读的是人和芝士；放进、替换、挪动、扔掉的只有人。
"""

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.place import readable_rooms
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.core.db import get_db
from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.identity.actor import Actor
from app.domain.library import listing as library_listing
from app.domain.library import records as library_records
from app.domain.library import service as library
from app.domain.preview import office
from app.domain.project.services import ProjectService

router = APIRouter(prefix="/projects", tags=["library"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

#: 资料库页上一次放进来的文件多大为止；和对话里上传附件是同一条线。
MAX_LIBRARY_UPLOAD_BYTES = 10 * 1024 * 1024


@router.get("/{project_id}/library/raw")
async def library_file_raw(
    project_id: uuid.UUID,
    path: str,
    db: DbSession,
    resolver: ActorResolverDep,
    preview_pdf: bool = False,
    version: uuid.UUID | None = None,
) -> Response:
    """一份资料的字节。给下载，也给 `cheese library get`——芝士 要读一份没有被这条
    消息带上的资料时，只能自己来取。

    `preview_pdf`：Office 文档转成 PDF，给资料库页预览。

    `version`：版本列表里那一版的 id，下载被替换下来的旧版用；不给就是现在这一版。

    不让浏览器凭缓存直接用：一份资料可以被「替换为新版本」，同一个地址下的字节会
    变。"""
    await ProjectService(db).get_or_404(project_id)
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    name = _library_path(path)
    data = (
        await library_records.version_bytes(db, project_id, name, version)
        if version is not None
        else await library_records.read(db, project_id, name)
    )
    if preview_pdf:
        data = await office.preview_pdf(data, name)
    filename = quote(name.rsplit("/", 1)[-1], safe="")
    return Response(
        content=data,
        media_type="application/pdf" if preview_pdf else "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, no-cache",
        },
    )


def _library_path(raw: str) -> str:
    """资料库里那一份的名字——它就是地址，所以这里只挡不是名字的东西。"""
    name = (raw or "").strip()
    if not name or name.startswith("/") or ".." in name.split("/"):
        raise ValidationError(say("libraryPathRelative"))
    return name


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
    versions = await library_records.versions(db, project_id, _library_path(path))
    return ok({"versions": versions})


@router.post("/{project_id}/library/restore")
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
        db, project_id=project_id, name=name, version_id=version, by=actor.handle
    )
    await db.commit()
    return ok({"path": name})


@router.get("/{project_id}/library")
async def list_library(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    dir: str = "",
    q: str = "",
    kind: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    """资料库：用户给这个项目的文件，按原名，每个房间都引用得到。一页一页地给。

    不给 `q` / `kind` 时是 `dir` 那一层：文件夹在前，然后是文件；给了就是整个资料
    库里对得上的文件。`next` 是下一页的游标，没有下一页时为 null。

    Project-level on purpose — 「上周那份预算表」is a sentence someone says in a
    room that has never seen that file."""
    actor = await _reader(project_id, db, resolver)
    rooms = await readable_rooms(db, resolver, actor, project_id)
    entries, after = await library_listing.page(
        db,
        project_id,
        rooms,
        dir=dir.strip("/"),
        q=q.strip(),
        kind=kind,
        after=cursor,
        limit=limit,
    )
    return ok({"data": entries, "next": after})


@router.get("/{project_id}/library/file")
async def library_file(
    project_id: uuid.UUID, path: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """一份资料的那一行：地址上点名的那一份不一定在已经取回来的那几页里。"""
    actor = await _reader(project_id, db, resolver)
    rooms = await readable_rooms(db, resolver, actor, project_id)
    return ok(await library_listing.one(db, project_id, rooms, _library_path(path)))


@router.get("/{project_id}/library/folders")
async def library_folders(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """资料库里的每一个文件夹（整条路径），给「移动到」挑。"""
    await _reader(project_id, db, resolver)
    return ok({"folders": await library_listing.folders(db, project_id)})


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
    await resolver.authorize_project(actor, project_id=project_id)
    return actor


async def _upload_bytes(file: UploadFile) -> bytes:
    data = await file.read(MAX_LIBRARY_UPLOAD_BYTES + 1)
    if not data:
        raise ValidationError(say("emptyFile"))
    if len(data) > MAX_LIBRARY_UPLOAD_BYTES:
        raise ValidationError(say("libraryUploadTooLarge"))
    return data


@router.post("/{project_id}/library")
async def upload_library_file(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    file: UploadFile = File(...),
    folder: str | None = Form(None),
) -> dict:
    """在资料库页上直接放进一份文件，`folder` 给了就放进那个文件夹（没有就新建）。
    撞名不覆盖：拿下一个 `(n)`。"""
    actor = await _library_keeper(project_id, db, resolver)
    data = await _upload_bytes(file)
    filename = library.clean_upload_name(file.filename)
    if folder and folder.strip("/ "):
        filename = f"{library.clean_library_path(folder)}/{filename}"
    name = await library_records.add(
        db,
        project_id=project_id,
        filename=filename,
        data=data,
        added_by=actor.handle,
        room_id=None,
    )
    await db.commit()
    return ok({"path": name, "bytes": len(data)})


@router.put("/{project_id}/library")
async def replace_library_file(
    project_id: uuid.UUID,
    path: str,
    db: DbSession,
    resolver: ActorResolverDep,
    file: UploadFile = File(...),
) -> dict:
    """把一份资料换成新版本。旧的那一份留着；引用这个名字的消息读到的是新的。"""
    actor = await _library_keeper(project_id, db, resolver)
    data = await _upload_bytes(file)
    name = _library_path(path)
    await library_records.replace(
        db, project_id=project_id, name=name, data=data, by=actor.handle
    )
    await db.commit()
    return ok({"path": name, "bytes": len(data)})


@router.delete("/{project_id}/library")
async def delete_library_file(
    project_id: uuid.UUID, path: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """扔掉一份资料，或者一个文件夹连同它里面的一切。

    读资料库的是人和 芝士，扔掉它的只有人。一轮里铸出来的凭据在 `authorize_project`
    那里只读得进来，所以 芝士 连同它自己正在读的那一份都删不掉。"""
    await _library_keeper(project_id, db, resolver)
    await library_records.remove(db, project_id=project_id, name=_library_path(path))
    await db.commit()
    return ok({"deleted": True})


@router.post("/{project_id}/library/move", dependencies=[ROUTE_DECIDES])
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
        project_id=project_id,
        source=_library_path(path),
        target=library.clean_library_path(to),
    )
    await db.commit()
    return ok({"moved": moved})
