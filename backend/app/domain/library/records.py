"""资料库那张记录表的读写 (models.LibraryFileRecord)。

磁盘上的字节由 `service` 管，这里只管「谁、在哪、什么时候」。写字节和记一行在同
一次请求里做：先记，再动磁盘，请求结束时一起提交——磁盘上动失败了，这一行随事务
回滚，不会留下一行说着一份不存在的文件。
"""

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import Block, BlockKind
from app.domain.library import service
from app.domain.library.models import LibraryFileRecord


def _row(
    project_id: uuid.UUID,
    name: str,
    data: bytes,
    *,
    added_by: str | None,
    room_id: uuid.UUID | None,
) -> LibraryFileRecord:
    return LibraryFileRecord(
        project_id=project_id,
        name=name,
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        added_by=added_by,
        room_id=room_id,
    )


async def add(
    session: AsyncSession,
    project_id: uuid.UUID,
    filename: str,
    data: bytes,
    added_by: str | None,
    room_id: uuid.UUID | None,
) -> str:
    """放进一份新资料，记下是谁在哪放的。撞名不覆盖：拿下一个 `(n)`。返回它的名字。"""
    name = await asyncio.to_thread(
        service.write_library_file, project_id, filename, data
    )
    session.add(_row(project_id, name, data, added_by=added_by, room_id=room_id))
    await session.flush()
    return name


async def replace(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    name: str,
    data: bytes,
    by: str,
) -> None:
    """把这个名字下的那一份换成新的字节。旧的那一份留在历史目录里，不是删掉。

    引用这个名字的旧消息从此读到的是新的一份——这正是「替换」的意思：人明确说了这
    是同一份文件的新版本。"""
    old = await asyncio.to_thread(service.read_library_file, project_id, name)
    current = await session.scalar(
        select(LibraryFileRecord).where(
            LibraryFileRecord.project_id == project_id,
            LibraryFileRecord.name == name,
            LibraryFileRecord.superseded_at.is_(None),
        )
    )
    if current is None:
        # 记录表之前就在的一份：先给它补一行，它被替换下来也有处可查。
        current = _row(project_id, name, old, added_by=None, room_id=None)
        session.add(current)
    current.superseded_at = datetime.now(UTC)
    await session.flush()
    session.add(_row(project_id, name, data, added_by=by, room_id=None))
    await session.flush()
    await asyncio.to_thread(service.keep_replaced, project_id, name, current.id)
    await asyncio.to_thread(service.overwrite_library_file, project_id, name, data)


async def remove(session: AsyncSession, *, project_id: uuid.UUID, name: str) -> None:
    """扔掉一份资料：它现在这一份、被替换下来的几版，连同记录一起。"""
    ids = list(
        await session.scalars(
            select(LibraryFileRecord.id).where(
                LibraryFileRecord.project_id == project_id,
                LibraryFileRecord.name == name,
            )
        )
    )
    await session.execute(
        delete(LibraryFileRecord).where(LibraryFileRecord.id.in_(ids))
    )
    await asyncio.to_thread(service.delete_library_file, project_id, name)
    await asyncio.to_thread(service.drop_history, project_id, ids)


async def current(
    session: AsyncSession, project_id: uuid.UUID
) -> dict[str, LibraryFileRecord]:
    """每个名字现在那一份的记录。记录表之前就在的文件不在这里面。"""
    rows = await session.scalars(
        select(LibraryFileRecord).where(
            LibraryFileRecord.project_id == project_id,
            LibraryFileRecord.superseded_at.is_(None),
        )
    )
    return {row.name: row for row in rows}


async def replaced_counts(
    session: AsyncSession, project_id: uuid.UUID
) -> dict[str, int]:
    """每个名字被替换过几次。"""
    rows = await session.execute(
        select(LibraryFileRecord.name, func.count())
        .where(
            LibraryFileRecord.project_id == project_id,
            LibraryFileRecord.superseded_at.is_not(None),
        )
        .group_by(LibraryFileRecord.name)
    )
    return {name: n for name, n in rows}


async def describe(
    session: AsyncSession,
    project_id: uuid.UUID,
    files: list[dict],
    room_titles: dict[uuid.UUID, str],
) -> list[dict]:
    """资料库清单上每一份的来源：谁、什么时候、在哪个房间，被几条消息引用、替换过
    几次。

    `room_titles` 是读者读得了的那些房间：房间名只写这些，引用也只数这些房间里的。
    记录表之前就在的文件没有行，来源取第一条带上它的附件消息。"""
    refs = [service.library_ref(f["path"]) for f in files]
    recorded = await current(session, project_id)
    replaced = await replaced_counts(session, project_id)
    first_sent: dict[str, Block] = {}
    for block in await session.scalars(
        select(Block)
        .where(
            Block.project_id == project_id,
            Block.kind == BlockKind.attachment,
            Block.content.in_(refs),
        )
        .order_by(Block.created_at)
    ):
        first_sent.setdefault(block.content, block)
    counts: dict[str, int] = {}
    if room_titles:
        for content, n in await session.execute(
            select(Block.content, func.count())
            .where(
                Block.project_id == project_id,
                Block.kind == BlockKind.attachment,
                Block.content.in_(refs),
                Block.topic_id.in_(list(room_titles)),
            )
            .group_by(Block.content)
        ):
            counts[content] = n

    def one(f: dict) -> dict:
        ref = service.library_ref(f["path"])
        row = recorded.get(f["path"])
        sent = first_sent.get(ref)
        room_id = row.room_id if row else (sent.topic_id if sent else None)
        added_at = row.created_at if row else (sent.created_at if sent else None)
        return {
            **f,
            "added_by": row.added_by if row else (sent.author if sent else None),
            "added_at": added_at.isoformat() if added_at else None,
            "room": (
                {"id": str(room_id), "title": room_titles[room_id]}
                if room_id is not None and room_id in room_titles
                else None
            ),
            "replaced": replaced.get(f["path"], 0),
            "references": counts.get(ref, 0),
        }

    return [one(f) for f in files]
