"""资料库的清单，一页一页地取。

两种看法：

- 一层一层地看（`dir`）：这一层直接装着的文件夹在前，然后是文件。文件夹是名字里
  `/` 前面那一段，按前缀从记录表里聚出来，带着里面有几份、最近一份什么时候放进来。
- 平铺地找（`flat`、`q` / `kind`）：整个资料库里名字对得上、类型对得上的文件，
  不给条件就是全部，新放进来的在前。

每一行带一个位置（`app.core.rank`），下一页从上一页最后一行的位置之后接着取。文档
不在这里：它们在文档表里自己翻页，用的是同一种位置，资料库页把两串并成一张表。
"""

import uuid
from datetime import datetime

from sqlalchemy import ColumnElement, and_, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rank
from app.core.errors import NotFoundError, UnprocessableEntityError
from app.core.sentences import say
from app.domain.block.models import Block, BlockKind
from app.domain.conversation.services import of_rooms
from app.domain.library import service
from app.domain.library.models import LibraryFileRecord as Row

FOLDERS = 0
ITEMS = 1

#: 资料库页那几个类型筛选按后缀认；「其他」是哪一种都不是的。
KIND_SUFFIXES: dict[str, tuple[str, ...]] = {
    "doc": (
        "doc", "docx", "odt", "rtf", "md", "markdown", "txt",
        "ppt", "pptx", "odp", "pages", "key",
    ),
    "sheet": ("xls", "xlsx", "csv", "ods", "numbers"),
    "pdf": ("pdf",),
    "image": ("png", "jpg", "jpeg", "gif", "webp", "svg", "heic"),
}  # fmt: skip
KINDS = (*KIND_SUFFIXES, "other")


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _current(project_id: uuid.UUID) -> ColumnElement[bool]:
    return and_(Row.project_id == project_id, Row.superseded_at.is_(None))


def _of_kind(kind: str) -> ColumnElement[bool]:
    suffix = func.lower(func.substring(Row.name, r"\.([^./]+)$"))
    if kind == "other":
        known = [ext for exts in KIND_SUFFIXES.values() for ext in exts]
        return or_(suffix.is_(None), suffix.not_in(known))
    return suffix.in_(KIND_SUFFIXES[kind])


def _matching(q: str) -> ColumnElement[bool]:
    return Row.name.ilike(f"%{_escape(q)}%", escape="\\")


async def _describe(
    session: AsyncSession,
    project_id: uuid.UUID,
    rooms: dict[uuid.UUID, dict],
    rows: list[Row],
) -> list[dict]:
    """每一份带上来源：谁、什么时候、在哪个房间，被几条消息引用、替换过几次。

    `rooms` 是读者读得了的那些房间：房间名只写这些，引用也只数这些房间里的。"""
    names = [row.name for row in rows]
    refs = [service.library_ref(name) for name in names]
    replaced: dict[str, int] = {}
    counts: dict[str, int] = {}
    if names:
        replaced = {
            name: n
            for name, n in await session.execute(
                select(Row.name, func.count())
                .where(
                    Row.project_id == project_id,
                    Row.superseded_at.is_not(None),
                    Row.name.in_(names),
                )
                .group_by(Row.name)
            )
        }
    if rooms and refs:
        counts = {
            content: n
            for content, n in await session.execute(
                select(Block.content, func.count())
                .where(
                    Block.project_id == project_id,
                    Block.kind == BlockKind.attachment,
                    Block.content.in_(refs),
                    of_rooms(Block.conversation_id, rooms),
                )
                .group_by(Block.content)
            )
        }
    return [
        {
            "type": "file",
            "path": row.name,
            "bytes": row.bytes,
            "modified": row.created_at.timestamp(),
            "added_by": row.added_by,
            "added_at": row.created_at.isoformat(),
            "room": rooms.get(row.room_id) if row.room_id is not None else None,
            "replaced": replaced.get(row.name, 0),
            "references": counts.get(service.library_ref(row.name), 0),
            "rank": rank.encode(ITEMS, row.created_at, str(row.id)),
        }
        for row in rows
    ]


async def _folders(
    session: AsyncSession,
    project_id: uuid.UUID,
    prefix: str,
    after: rank.Rank | None,
    limit: int,
) -> list[dict]:
    rest = func.substr(Row.name, len(prefix) + 1)
    name = func.split_part(rest, "/", 1).label("name")
    grouped = (
        select(
            name,
            func.count().label("count"),
            func.max(Row.created_at).label("latest"),
        )
        .where(
            _current(project_id),
            Row.name.like(f"{_escape(prefix)}%", escape="\\"),
            func.strpos(rest, "/") > 0,
        )
        .group_by(name)
        .subquery()
    )
    by_bytes = grouped.c.name.collate("C")
    query = select(grouped).order_by(grouped.c.latest.desc(), by_bytes)
    if after is not None:
        query = query.where(
            or_(
                grouped.c.latest < after.at,
                and_(grouped.c.latest == after.at, by_bytes > after.tie),
            )
        )
    return [
        {
            "type": "folder",
            "path": f"{prefix}{folder.name}",
            "name": folder.name,
            "count": folder.count,
            "modified": folder.latest.timestamp(),
            "rank": rank.encode(FOLDERS, folder.latest, folder.name),
        }
        for folder in await session.execute(query.limit(limit))
    ]


async def page(
    session: AsyncSession,
    project_id: uuid.UUID,
    rooms: dict[uuid.UUID, dict],
    *,
    dir: str = "",
    flat: bool = False,
    q: str = "",
    kind: str | None = None,
    after: str | None = None,
    limit: int = 50,
) -> tuple[list[dict], str | None]:
    """一页清单和下一页的游标（没有下一页时是 None）。

    `flat`、或者给了 `q` / `kind`，就是整个资料库平铺地找，`dir` 不管；否则是
    `dir` 这一层。"""
    if kind is not None and kind not in KINDS:
        raise UnprocessableEntityError(say("libraryKindUnknown", kind=kind))
    cursor = rank.decode(after) if after else None
    flat = flat or bool(q) or kind is not None
    prefix = f"{dir}/" if dir and not flat else ""
    entries: list[dict] = []
    if not flat and (cursor is None or cursor.group == FOLDERS):
        entries = await _folders(session, project_id, prefix, cursor, limit + 1)
    if len(entries) <= limit:
        where = [_current(project_id)]
        if flat:
            where += [_matching(q) if q else true(), _of_kind(kind) if kind else true()]
        else:
            rest = func.substr(Row.name, len(prefix) + 1)
            where += [
                Row.name.like(f"{_escape(prefix)}%", escape="\\"),
                func.strpos(rest, "/") == 0,
            ]
        if cursor is not None and cursor.group == ITEMS:
            where.append(
                or_(
                    Row.created_at < cursor.at,
                    and_(Row.created_at == cursor.at, Row.id > uuid.UUID(cursor.tie)),
                )
            )
        rows = list(
            await session.scalars(
                select(Row)
                .where(*where)
                .order_by(Row.created_at.desc(), Row.id)
                .limit(limit + 1 - len(entries))
            )
        )
        entries += await _describe(session, project_id, rooms, rows)
    if len(entries) <= limit:
        return entries, None
    entries = entries[:limit]
    return entries, entries[-1]["rank"]


async def one(
    session: AsyncSession,
    project_id: uuid.UUID,
    rooms: dict[uuid.UUID, dict],
    name: str,
) -> dict:
    """一份资料的那一行（地址上点名的那一份，不一定在已经取回来的那几页里）。"""
    row = await session.scalar(
        select(Row).where(_current(project_id), Row.name == name)
    )
    if row is None:
        raise NotFoundError(say("libraryFileNotFound"))
    [described] = await _describe(session, project_id, rooms, [row])
    return described


async def folders(session: AsyncSession, project_id: uuid.UUID) -> list[str]:
    """资料库里的每一个文件夹（整条路径），给「移动到」挑。"""
    deepest = func.regexp_replace(Row.name, "/[^/]*$", "")
    found: set[str] = set()
    for folder in await session.scalars(
        select(deepest)
        .where(_current(project_id), func.strpos(Row.name, "/") > 0)
        .distinct()
    ):
        parts = folder.split("/")
        found.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    return sorted(found)


async def matching(
    session: AsyncSession, project_id: uuid.UUID, q: str, *, limit: int, offset: int = 0
) -> list[dict]:
    """名字里有 `q` 的那些（项目里的搜索用），新放进来的在前。"""
    rows = await session.scalars(
        select(Row)
        .where(_current(project_id), _matching(q))
        .order_by(Row.created_at.desc(), Row.id)
        .offset(offset)
        .limit(limit)
    )
    return [
        {"path": row.name, "bytes": row.bytes, "modified": row.created_at.timestamp()}
        for row in rows
    ]


async def count_matching(session: AsyncSession, project_id: uuid.UUID, q: str) -> int:
    return (
        await session.scalar(
            select(func.count()).where(_current(project_id), _matching(q))
        )
        or 0
    )


async def added_since(
    session: AsyncSession, project_id: uuid.UUID, since: datetime
) -> list[tuple[str, datetime]]:
    """`since` 之后放进来的每一份：名字和什么时候（例行任务的「资料库新增」）。"""
    rows = await session.execute(
        select(Row.name, Row.created_at)
        .where(_current(project_id), Row.created_at >= since)
        .order_by(Row.created_at)
    )
    return [(name, at) for name, at in rows]
