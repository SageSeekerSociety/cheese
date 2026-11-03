"""资料库的清单，一页一页地取。

两种看法：

- 一层一层地看（`dir`）：这一层直接装着的文件夹在前，然后是文件。文件夹是名字里
  `/` 前面那一段，按前缀从记录表里聚出来，带着里面有几份、最近一份什么时候放进来。
- 平铺地找（`flat`、`q` / `kind`）：整个资料库里名字对得上、类型对得上的文件，
  不给条件就是全部，新放进来的在前。

每一行带一个位置（`app.core.rank`），下一页从上一页最后一行的位置之后接着取。文档
不在这里：它们在文档表里自己翻页，用的是同一种位置，资料库页把两串并成一张表。

从题目派生的项目，清单里还有一个只读的 `题目/`（`mount`）：那道题目的资料库现在的
每一份，地址前面加上 `题目/`，行上带 `readonly`。平铺地找、项目里的搜索，两边一起找，
按同一种位置并起来。
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
from app.domain.library import mount, service
from app.domain.library.models import LibraryFileRecord as Row
from app.domain.library.shelf import Shelf

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


def _current(shelf: Shelf, hidden: int | None = None) -> ColumnElement[bool]:
    """这个资料库现在的每一份；挂着题目时（`hidden`，`mount.mounted`），项目自己在 `题目/` 底下的
    那几行不算（`mount.project_rows`）。"""
    return and_(
        shelf.holds(),
        Row.superseded_at.is_(None),
        mount.project_rows(shelf, hidden),
    )


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
    shelf: Shelf,
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
                    shelf.holds(),
                    Row.superseded_at.is_not(None),
                    Row.name.in_(names),
                )
                .group_by(Row.name)
            )
        }
    if rooms and refs and shelf.project_id is not None:
        counts = {
            content: n
            for content, n in await session.execute(
                select(Block.content, func.count())
                .where(
                    Block.project_id == shelf.project_id,
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
            "readonly": False,
            "rank": rank.encode(ITEMS, row.created_at, str(row.id)),
        }
        for row in rows
    ]


def _mounted(entry: dict) -> dict:
    """题目那一边的一行，在项目的清单上：地址前面加 `题目/`，只读。"""
    return {**entry, "path": mount.shown(entry["path"]), "readonly": True}


async def _folders(
    session: AsyncSession,
    shelf: Shelf,
    prefix: str,
    after: rank.Rank | None,
    limit: int,
    hidden: int | None = None,
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
            _current(shelf, hidden),
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
            "readonly": False,
            "rank": rank.encode(FOLDERS, folder.latest, folder.name),
        }
        for folder in await session.execute(query.limit(limit))
    ]


async def _task_folder(
    session: AsyncSession, task_id: int, after: str | None
) -> list[dict]:
    """项目最上面一层的那个 `题目` 文件夹：里面有几份、最近一份什么时候放进来。排在
    `after` 之前（上一页已经给过）的不再给。"""
    count, latest = (
        await session.execute(
            select(func.count(), func.max(Row.created_at)).where(
                _current(Shelf.of_task(task_id))
            )
        )
    ).one()
    if not count:
        return []
    position = rank.encode(FOLDERS, latest, mount.FOLDER)
    if after is not None and position <= after:
        return []
    return [
        {
            "type": "folder",
            "path": mount.FOLDER,
            "name": mount.FOLDER,
            "count": count,
            "modified": latest.timestamp(),
            "readonly": True,
            "rank": position,
        }
    ]


async def _items(
    session: AsyncSession,
    shelf: Shelf,
    rooms: dict[uuid.UUID, dict],
    where: list[ColumnElement[bool]],
    cursor: rank.Rank | None,
    limit: int,
) -> list[dict]:
    """这个资料库里对得上 `where` 的文件，新放进来的在前，从 `cursor` 之后取。"""
    if cursor is not None and cursor.group == ITEMS:
        where = [
            *where,
            or_(
                Row.created_at < cursor.at,
                and_(Row.created_at == cursor.at, Row.id > uuid.UUID(cursor.tie)),
            ),
        ]
    rows = list(
        await session.scalars(
            select(Row)
            .where(*where)
            .order_by(Row.created_at.desc(), Row.id)
            .limit(limit)
        )
    )
    return await _describe(session, shelf, rooms, rows)


async def page(
    session: AsyncSession,
    shelf: Shelf,
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
    `dir` 这一层。`dir` 是 `题目` 或它底下的一层时，看的是挂在这里的题目资料库。"""
    if kind is not None and kind not in KINDS:
        raise UnprocessableEntityError(say("libraryKindUnknown", kind=kind))
    cursor = rank.decode(after) if after else None
    flat = flat or bool(q) or kind is not None
    task_id = await mount.task_of(session, shelf)
    hidden = await mount.mounted(session, shelf)
    if task_id is not None and not flat and mount.under(dir):
        entries, more = await page(
            session,
            Shelf.of_task(task_id),
            {},
            dir=dir[len(mount.FOLDER) :].strip("/"),
            after=after,
            limit=limit,
        )
        return [_mounted(entry) for entry in entries], more
    prefix = f"{dir}/" if dir and not flat else ""
    entries: list[dict] = []
    if not flat and (cursor is None or cursor.group == FOLDERS):
        entries = await _folders(session, shelf, prefix, cursor, limit + 1, hidden)
        if task_id is not None and not prefix:
            entries = sorted(
                entries + await _task_folder(session, task_id, after),
                key=lambda entry: entry["rank"],
            )[: limit + 1]
    if len(entries) <= limit:
        wanted = limit + 1 - len(entries)
        if flat:
            match = [_matching(q) if q else true(), _of_kind(kind) if kind else true()]
            found = await _items(
                session,
                shelf,
                rooms,
                [_current(shelf, hidden), *match],
                cursor,
                wanted,
            )
            if task_id is not None:
                task_shelf = Shelf.of_task(task_id)
                found += [
                    _mounted(entry)
                    for entry in await _items(
                        session,
                        task_shelf,
                        {},
                        [_current(task_shelf), *match],
                        cursor,
                        wanted,
                    )
                ]
                found = sorted(found, key=lambda entry: entry["rank"])[:wanted]
        else:
            rest = func.substr(Row.name, len(prefix) + 1)
            found = await _items(
                session,
                shelf,
                rooms,
                [
                    _current(shelf, hidden),
                    Row.name.like(f"{_escape(prefix)}%", escape="\\"),
                    func.strpos(rest, "/") == 0,
                ],
                cursor,
                wanted,
            )
        entries += found
    if len(entries) <= limit:
        return entries, None
    entries = entries[:limit]
    return entries, entries[-1]["rank"]


async def one(
    session: AsyncSession,
    shelf: Shelf,
    rooms: dict[uuid.UUID, dict],
    name: str,
) -> dict:
    """一份资料的那一行（地址上点名的那一份，不一定在已经取回来的那几页里）。"""
    at, inner = await mount.resolve(session, shelf, name)
    hidden = await mount.mounted(session, shelf) if at == shelf else None
    row = await session.scalar(
        select(Row).where(_current(at, hidden), Row.name == inner)
    )
    if row is None:
        raise NotFoundError(say("libraryFileNotFound"))
    if at == shelf:
        [described] = await _describe(session, shelf, rooms, [row])
        return described
    [described] = await _describe(session, at, {}, [row])
    return _mounted(described)


async def folders(session: AsyncSession, shelf: Shelf) -> list[str]:
    """资料库里的每一个文件夹（整条路径），给「移动到」挑。`题目/` 不在里面：那里
    放不进东西。"""
    hidden = await mount.mounted(session, shelf)
    deepest = func.regexp_replace(Row.name, "/[^/]*$", "")
    found: set[str] = set()
    for folder in await session.scalars(
        select(deepest)
        .where(_current(shelf, hidden), func.strpos(Row.name, "/") > 0)
        .distinct()
    ):
        parts = folder.split("/")
        found.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    return sorted(found)


def _found(rows, shown=lambda name: name) -> list[dict]:
    return [
        {
            "path": shown(row.name),
            "bytes": row.bytes,
            "modified": row.created_at.timestamp(),
            "at": (row.created_at, row.id),
        }
        for row in rows
    ]


async def matching(
    session: AsyncSession, shelf: Shelf, q: str, *, limit: int, offset: int = 0
) -> list[dict]:
    """名字里有 `q` 的那些（项目里的搜索用），新放进来的在前；挂着题目的项目连
    `题目/` 底下的一起找（读的人看得到它的时候）。"""
    task_id = await mount.task_of(session, shelf)
    hidden = await mount.mounted(session, shelf)

    async def of(where: ColumnElement[bool]):
        return await session.scalars(
            select(Row)
            .where(where, _matching(q))
            .order_by(Row.created_at.desc(), Row.id)
            .limit(offset + limit)
        )

    found = _found(await of(_current(shelf, hidden)))
    if task_id is not None:
        found += _found(await of(_current(Shelf.of_task(task_id))), mount.shown)
    found.sort(key=lambda hit: (-hit["at"][0].timestamp(), hit["at"][1]))
    return [
        {key: value for key, value in hit.items() if key != "at"}
        for hit in found[offset : offset + limit]
    ]


async def count_matching(session: AsyncSession, shelf: Shelf, q: str) -> int:
    task_id = await mount.task_of(session, shelf)
    hidden = await mount.mounted(session, shelf)
    where = _current(shelf, hidden)
    if task_id is not None:
        where = or_(where, _current(Shelf.of_task(task_id)))
    return await session.scalar(select(func.count()).where(where, _matching(q))) or 0


async def added_since(
    session: AsyncSession, shelf: Shelf, since: datetime
) -> list[tuple[str, datetime]]:
    """`since` 之后放进来的每一份：名字和什么时候（例行任务的「资料库新增」）。"""
    rows = await session.execute(
        select(Row.name, Row.created_at)
        .where(_current(shelf), Row.created_at >= since)
        .order_by(Row.created_at)
    )
    return [(name, at) for name, at in rows]
