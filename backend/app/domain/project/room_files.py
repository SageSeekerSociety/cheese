"""房间里那份文件上的「保存到资料库」(#1085 结论四)。

房间里的文件不是项目产物：用户传一份文件进来让 芝士 改，改完在那个房间里拿走，事
情就结束了。但有时候他想把它留下 —— 不是要交出去，是**以后还要用**：另一个房间里
提到它、下一轮拿它当素材。留下来是**一个动作**，按了才算，不猜、不自动升。

留在哪里由那句话决定：留着要用的东西是**资料**，所以它进资料库，按原名寻址、撞名
加 `(2)`、所有房间读得到，和用户自己上传的那些并排。

**它不上产物清单，也不进 git**，两件事都不是省略：

- 清单上的一项要「会交给项目外的人」（结论三）。一份留着以后用的文件不满足它，为
  了让它上榜就得凭一次按钮伪造一条交付记录 —— 那一刻谁也没把它交给任何人。
- 成品不进库（结论五）。房间里摆出来的东西多半是构建产物，把它提交进用户的主干，
  既违反那一条，也违反「不属于用户代码库的东西，不写进用户的仓库」。

真正「文件本身就是源」的那条路走正常交付：芝士 在任务分支上改、递卡、人采纳合并，
二进制从那个口进 git，不从这个按钮进。
"""

import asyncio
import hashlib
import uuid
from pathlib import PurePosixPath

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.agent.announce import announce
from app.domain.agent.platform_notices import (
    EVENT_LIBRARY_SAVED,
    SEVERITY_INFO,
    WHO_HUMAN,
    notice,
)
from app.domain.library import service as library
from app.domain.project.models import RoomFileRevision
from app.domain.textfile import content_version

#: Which door a revision's bytes came in by.
SOURCES = ("baseline", "upload", "ai", "editor", "restore", "scheduled")


async def save_to_library(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    path: str,
    by: str,
) -> str:
    """把房间里的一份文件留进资料库，返回它在那里的名字。

    撞名不覆盖，跟着资料库自己的规矩走：两次保存就是两份，各自留着 —— 谁也说不准
    第二份是第一份的新版，还是另一样同名的东西。
    """
    leaf = PurePosixPath(path).name
    if not leaf:
        raise ValidationError("这不是房间里的一份文件")
    data = await asyncio.to_thread(library.read_room_file, project_id, room_id, path)
    name = await asyncio.to_thread(library.write_library_file, project_id, leaf, data)
    await announce(
        session,
        place_id=room_id,
        content=f"{name} 已存进资料库",
        meta=notice(
            EVENT_LIBRARY_SAVED,
            severity=SEVERITY_INFO,
            who=WHO_HUMAN,
            detail=f"{by} 把这个房间里的 {leaf} 留进了资料库，每个房间都引用得到",
            detail_label="保存到资料库",
        ),
    )
    return name


def current_version(project_id: uuid.UUID, room_id: uuid.UUID, path: str) -> str | None:
    """The version a reader holds: the content hash, or None when there is no file."""
    if not library.room_file_exists(project_id, room_id, path):
        return None
    return content_version(library.read_room_file(project_id, room_id, path))


async def save_room_file(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    path: str,
    data: bytes,
    author: str,
    author_kind: str,
    source: str,
    note: str | None = None,
    base_version: str | None = None,
    editor_key: str | None = None,
) -> RoomFileRevision:
    """Write a room file and keep the state it replaces restorable.

    `base_version` is the version the writer read before editing. When the file
    has moved since, somebody else saved in between, and writing would drop
    their work without anyone seeing it — so it is a conflict, never an
    overwrite. A writer that is creating the file, or that knowingly replaces
    it (a restore), passes None.

    The first save of a file that existed before history did records that
    earlier state first, so the very first edit is already undoable.
    """
    if source not in SOURCES:
        raise ValidationError(f"unknown revision source {source!r}")
    # One writer per (room, path) at a time: the version check and the seq
    # below are only true while nobody else is between them.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"room-file:{room_id}:{path}"},
    )
    existing = await asyncio.to_thread(
        library.room_file_exists, project_id, room_id, path
    )
    before = (
        await asyncio.to_thread(library.read_room_file, project_id, room_id, path)
        if existing
        else None
    )
    now_version = content_version(before) if before is not None else None
    latest = await _latest(session, room_id, path)
    own_earlier_save = (
        editor_key is not None
        and latest is not None
        and latest.editor_key == editor_key
    )
    if (
        base_version
        and now_version
        and base_version != now_version
        and not own_earlier_save
    ):
        raise ConflictError(
            "这份文件在你读取之后被人改过，先取最新的一版再改",
            data={"path": path, "version": now_version, "base_version": base_version},
        )
    if latest is None and before is not None:
        latest = await _record(
            session,
            project_id=project_id,
            room_id=room_id,
            path=path,
            data=before,
            seq=1,
            author="",
            author_kind="unknown",
            source="baseline",
            note=None,
            editor_key=None,
        )
    if before is not None and before == data and latest is not None:
        return latest
    await asyncio.to_thread(library.write_room_file, project_id, room_id, path, data)
    return await _record(
        session,
        project_id=project_id,
        room_id=room_id,
        path=path,
        data=data,
        seq=(latest.seq if latest else 0) + 1,
        author=author,
        author_kind=author_kind,
        source=source,
        note=(note or "").strip() or None,
        editor_key=editor_key,
    )


async def _latest(
    session: AsyncSession, room_id: uuid.UUID, path: str
) -> RoomFileRevision | None:
    return (
        await session.execute(
            select(RoomFileRevision)
            .where(RoomFileRevision.room_id == room_id, RoomFileRevision.path == path)
            .order_by(RoomFileRevision.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _record(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    path: str,
    data: bytes,
    seq: int,
    author: str,
    author_kind: str,
    source: str,
    note: str | None,
    editor_key: str | None,
) -> RoomFileRevision:
    digest = await asyncio.to_thread(
        library.write_revision_blob, project_id, room_id, data
    )
    row = RoomFileRevision(
        project_id=project_id,
        room_id=room_id,
        path=path,
        seq=seq,
        sha256=digest,
        size=len(data),
        author_handle=author,
        author_kind=author_kind,
        source=source,
        note=note,
        editor_key=editor_key,
    )
    session.add(row)
    await session.flush()
    return row


async def saved_by_session(
    session: AsyncSession,
    room_id: uuid.UUID,
    path: str,
    editor_key: str,
    data: bytes,
) -> bool:
    """Whether this editor session already saved exactly these bytes.

    The editor posts its content again when the last person closes it, even
    when every change was already saved with 「保存」. If the file has moved on
    since (芝士 saved after that), that repeat looks like a conflicting save —
    it is not; it is a copy of something already kept.
    """
    digest = hashlib.sha256(data).hexdigest()
    found = await session.execute(
        select(RoomFileRevision.id)
        .where(
            RoomFileRevision.room_id == room_id,
            RoomFileRevision.path == path,
            RoomFileRevision.editor_key == editor_key,
            RoomFileRevision.sha256 == digest,
        )
        .limit(1)
    )
    return found.first() is not None


async def list_revisions(
    session: AsyncSession, room_id: uuid.UUID, path: str
) -> list[RoomFileRevision]:
    return list(
        (
            await session.execute(
                select(RoomFileRevision)
                .where(
                    RoomFileRevision.room_id == room_id, RoomFileRevision.path == path
                )
                .order_by(RoomFileRevision.seq.desc())
            )
        ).scalars()
    )


async def revision_or_404(
    session: AsyncSession, room_id: uuid.UUID, revision_id: uuid.UUID
) -> RoomFileRevision:
    row = await session.get(RoomFileRevision, revision_id)
    if row is None or row.room_id != room_id:
        raise NotFoundError("没有这一版")
    return row


def revision_bytes(row: RoomFileRevision) -> bytes:
    return library.read_revision_blob(row.project_id, row.room_id, row.sha256)


async def restore_revision(
    session: AsyncSession,
    *,
    row: RoomFileRevision,
    by: str,
    author_kind: str,
) -> RoomFileRevision:
    """Put an earlier state back as the file's newest one.

    History is not rewound: the restore is a new revision, so the state it
    replaced stays one click away too. A delivered version lives in the accept
    card's own snapshot and is not reachable from here at all.
    """
    data = await asyncio.to_thread(revision_bytes, row)
    return await save_room_file(
        session,
        project_id=row.project_id,
        room_id=row.room_id,
        path=row.path,
        data=data,
        author=by,
        author_kind=author_kind,
        source="restore",
        note=f"恢复到第 {row.seq} 版",
    )


def revision_out(row: RoomFileRevision) -> dict:
    return {
        "id": str(row.id),
        "path": row.path,
        "seq": row.seq,
        "version": row.sha256[:16],
        "size": row.size,
        "author": row.author_handle,
        "author_kind": row.author_kind,
        "source": row.source,
        "note": row.note,
        "editor_key": row.editor_key,
        "created_at": row.created_at.isoformat(),
    }
