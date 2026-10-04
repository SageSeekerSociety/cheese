"""资料库：用户给这个项目的文件，按原名，每个房间都引用得到。

字节在磁盘上（`app.domain.library.service`），谁、在哪、什么时候放进来的记在记录表里
（`app.domain.library.records`）。读的是人和芝士；放进、替换、扔掉的只有人。
"""

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.place import project_reader, readable_rooms
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.identity.actor import Actor
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
    topic: str = "",
    preview_pdf: bool = False,
) -> Response:
    """一份资料的字节。给下载，也给 `cheese library get`——芝士 要读一份没有被这条
    消息带上的资料时，只能自己来取（那时带着它干活的那个话题，见 `authorized_place`）。

    `preview_pdf`：Office 文档转成 PDF，给资料库页预览。

    不让浏览器凭缓存直接用：一份资料可以被「替换为新版本」，同一个地址下的字节会
    变。"""
    await ProjectService(db).get_or_404(project_id)
    await project_reader(db, resolver, project_id, topic)
    name = _library_path(path)
    data = library.read_library_file(project_id, name)
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


@router.get("/{project_id}/library")
async def list_library(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep, topic: str = ""
) -> dict:
    """资料库：用户给这个项目的文件，按原名，每个房间都引用得到。

    Project-level on purpose — 「上周那份预算表」is a sentence someone says in a
    room that has never seen that file."""
    await ProjectService(db).get_or_404(project_id)
    actor = await project_reader(db, resolver, project_id, topic)
    files = library.list_library_files(project_id)
    rooms = await readable_rooms(db, resolver, actor, project_id)
    listed = await library_records.describe(db, project_id, files, rooms)
    return ok(page(listed, len(listed)))


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
) -> dict:
    """在资料库页上直接放进一份文件。撞名不覆盖：拿下一个 `(n)`。"""
    actor = await _library_keeper(project_id, db, resolver)
    data = await _upload_bytes(file)
    name = await library_records.add(
        db,
        project_id=project_id,
        filename=library.clean_upload_name(file.filename),
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
    """扔掉一份资料。

    这条路不收 `topic`：读资料库的是人和 芝士，扔掉它的只有人。一轮里铸出来的凭据
    过不了 `authorize_project`，所以 芝士 连同它自己正在读的那一份都删不掉。"""
    await _library_keeper(project_id, db, resolver)
    await library_records.remove(db, project_id=project_id, name=_library_path(path))
    await db.commit()
    return ok({"deleted": True})
