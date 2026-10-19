"""资料库的读写：清单在记录表里 (models.LibraryFileRecord)，字节在 `blobs` 里。

列出、读、判断在不在，都是查这张表，再按那一行的 `location` / `blob_key` 去取字节，
不扫目录。写字节和记一行在同一次请求里做：先记，再写字节，请求结束时一起提交——
字节写失败了，这一行随事务回滚，不会留下一行说着一份不存在的文件。

名字里的 `/` 是文件夹。一个名字不能同时是一份文件和一个文件夹：`报告` 是文件时，
`报告/附录.md` 放不进来，反过来也一样。
"""

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path

import fitz
from sqlalchemy import delete, func, literal, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.sentences import say
from app.domain.block.models import Block, BlockKind
from app.domain.library import blobs, service
from app.domain.library.models import LibraryFileRecord
from app.domain.textfile import bytes_text_payload


async def _bytes_of(row: LibraryFileRecord) -> bytes:
    return await asyncio.to_thread(blobs.store(row.location).get, row.blob_key)


def _new_row(
    project_id: uuid.UUID,
    name: str,
    measured: tuple[int, str],
    *,
    added_by: str | None,
    room_id: uuid.UUID | None,
    source_path: str | None = None,
) -> LibraryFileRecord:
    record_id = uuid.uuid4()
    size, sha256 = measured
    return LibraryFileRecord(
        id=record_id,
        project_id=project_id,
        name=name,
        bytes=size,
        sha256=sha256,
        added_by=added_by,
        room_id=room_id,
        source_path=source_path,
        location=blobs.LOCAL,
        blob_key=blobs.new_key(project_id, record_id),
    )


async def _put(row: LibraryFileRecord, data: service.Content) -> None:
    # 资料库的字节都从这里落地，上限与房间文件是同一条（`service.refuse_oversize`）。
    service.refuse_oversize(row.bytes, row.name)
    await asyncio.to_thread(blobs.store(row.location).put, row.blob_key, data)


def _like_prefix(prefix: str) -> str:
    """`LIKE` 里按字面匹配 `prefix` 开头的名字。"""
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


def _current_where(project_id: uuid.UUID) -> tuple:
    """「现在这一份」的判据，给只挑几列、不取整行的查询共用。"""
    return (
        LibraryFileRecord.project_id == project_id,
        LibraryFileRecord.superseded_at.is_(None),
    )


def _current(project_id: uuid.UUID):
    return select(LibraryFileRecord).where(*_current_where(project_id))


async def _current_row(
    session: AsyncSession, project_id: uuid.UUID, name: str
) -> LibraryFileRecord | None:
    return await session.scalar(
        _current(project_id).where(LibraryFileRecord.name == name)
    )


async def _current_or_404(
    session: AsyncSession, project_id: uuid.UUID, name: str
) -> LibraryFileRecord:
    row = await _current_row(session, project_id, name)
    if row is None:
        raise NotFoundError(say("libraryFileNotFound"))
    return row


async def _taken(
    session: AsyncSession,
    project_id: uuid.UUID,
    name: str,
    ignore: frozenset[str] = frozenset(),
) -> bool:
    """`name` 放不下一份文件：有同名的文件，它是一个文件夹，或者它路径上的某一层
    已经是一份文件。`ignore` 是正要挪走的那些名字。"""
    parts = name.split("/")
    ancestors = ["/".join(parts[:i]) for i in range(1, len(parts))]
    clash = or_(
        LibraryFileRecord.name == name,
        LibraryFileRecord.name.like(_like_prefix(f"{name}/"), escape="\\"),
        LibraryFileRecord.name.in_(ancestors),
    )
    query = _current(project_id).where(clash)
    if ignore:
        query = query.where(LibraryFileRecord.name.not_in(ignore))
    return await session.scalar(query.limit(1)) is not None


async def read(session: AsyncSession, project_id: uuid.UUID, name: str) -> bytes:
    """资料库里这个名字现在那一份的字节。"""
    return await _bytes_of(await _current_or_404(session, project_id, name))


async def exists(session: AsyncSession, project_id: uuid.UUID, name: str) -> bool:
    return await _current_row(session, project_id, name) is not None


async def read_attachment(
    session: AsyncSession, project_id: uuid.UUID, room_id: uuid.UUID, path: str
) -> bytes:
    """一个附件的字节：资料库里那一份，或者只属于这个房间的那一份。"""
    name = service.library_name(path)
    if name is not None:
        return await read(session, project_id, name)
    return await asyncio.to_thread(service.read_room_file, project_id, room_id, path)


async def attachment_file(
    session: AsyncSession, project_id: uuid.UUID, room_id: uuid.UUID, path: str
) -> Path:
    """一个附件的字节在磁盘上的位置，给按段读它的人：播放器拖进度条只要那一段，
    两百兆的录屏不该为了一段整份读进内存。"""
    name = service.library_name(path)
    if name is not None:
        row = await _current_or_404(session, project_id, name)
        return await asyncio.to_thread(blobs.store(row.location).file, row.blob_key)
    return await asyncio.to_thread(service.room_file_path, project_id, room_id, path)


async def attachment_exists(
    session: AsyncSession, project_id: uuid.UUID, room_id: uuid.UUID, path: str
) -> bool:
    """这个地址今天还读得到吗：资料库里那一份可以被人删掉，引用它的消息还在。"""
    name = service.library_name(path)
    if name is not None:
        return await exists(session, project_id, name)
    return await asyncio.to_thread(service.room_file_exists, project_id, room_id, path)


def _split_attachment_paths(paths: list[str]) -> tuple[dict[str, str], list[str]]:
    """把一批地址分成资料库的名字和房间自己的路径：前者 name -> 地址,后者原样。"""
    names: dict[str, str] = {}
    room: list[str] = []
    for path in dict.fromkeys(paths):
        name = service.library_name(path)
        if name is None:
            room.append(path)
        else:
            names[name] = path
    return names, room


def _room_files_present(
    project_id: uuid.UUID, room_id: uuid.UUID, paths: list[str]
) -> set[str]:
    return {
        path for path in paths if service.room_file_exists(project_id, room_id, path)
    }


async def attachments_present(
    session: AsyncSession,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    paths: list[str],
) -> set[str]:
    """这批地址里今天还读得到的那些。

    资料库那半边一条查询问完；房间那半边一次线程池调用问完所有路径。一条待发
    消息可以带着好几个附件，逐个问会变成一串来回。
    """
    names, room = _split_attachment_paths(paths)
    present: set[str] = set()
    if names:
        rows = await session.execute(
            select(LibraryFileRecord.name).where(
                *_current_where(project_id),
                LibraryFileRecord.name.in_(list(names)),
            )
        )
        present.update(names[name] for name in rows.scalars())
    if room:
        present.update(
            await asyncio.to_thread(_room_files_present, project_id, room_id, room)
        )
    return present


def _read_attachment_bytes(
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    library: list[tuple[str, str, str]],
    room_paths: list[str],
) -> dict[str, bytes]:
    """一次线程池调用读完这批附件的字节(资料库的行先查好，这里只取字节)。"""
    found: dict[str, bytes] = {}
    for path, location, blob_key in library:
        found[path] = blobs.store(location).get(blob_key)
    for path in room_paths:
        found[path] = service.read_room_file(project_id, room_id, path)
    return found


async def read_attachments(
    session: AsyncSession,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    paths: list[str],
) -> dict[str, bytes]:
    """一批地址的字节：资料库一条查询拿所有行，存储读取和房间文件放同一个
    线程池调用里。缺一个就照单条读那样拒——调用方应当先问 `attachments_present`。"""
    names, room = _split_attachment_paths(paths)
    library: list[tuple[str, str, str]] = []
    if names:
        rows = await session.execute(
            _current(project_id).where(LibraryFileRecord.name.in_(list(names)))
        )
        for row in rows.scalars():
            library.append((names[row.name], row.location, row.blob_key))
        if len(library) != len(names):
            # 和单条读一样：这个地址没错，是东西不在了。
            raise NotFoundError(say("libraryFileNotFound"))
    if not library and not room:
        return {}
    return await asyncio.to_thread(
        _read_attachment_bytes, project_id, room_id, library, room
    )


async def read_attachment_text(
    session: AsyncSession, project_id: uuid.UUID, room_id: uuid.UUID, path: str
) -> dict:
    """同一个地址，读成文本(二进制的那一份照旧只回元数据和版本)。"""
    name = service.library_name(path)
    if name is None:
        return await asyncio.to_thread(
            service.read_room_text_file, project_id, room_id, path
        )
    row = await _current_row(session, project_id, name)
    if row is None:
        # 一条旧消息里的引用，而那份资料已经被扔掉了。说清是哪一种打不开：这个
        # 地址没错，是东西不在了。
        raise UnprocessableEntityError(say("libraryFileGone"))
    return bytes_text_payload(await _bytes_of(row), path)


#: 一份 PDF 读成文字时最多读多少页、留多少字：读的是芝士，它要的是要求和要点，
#: 不是整本书。
PDF_TEXT_PAGES = 40
PDF_TEXT_CHARS = 60_000


def _pdf_text(data: bytes) -> str | None:
    """PDF 里的文字层，一页一段；打不开、或者全是扫描图没有文字，是 None。"""
    try:
        with fitz.open(stream=data, filetype="pdf") as pdf:
            pages = [
                str(pdf[i].get_text()).strip()
                for i in range(min(pdf.page_count, PDF_TEXT_PAGES))
            ]
            more = pdf.page_count > PDF_TEXT_PAGES
    except Exception:  # a file that is not a readable PDF reads as binary
        return None
    text = "\n\n".join(page for page in pages if page)
    if not text:
        return None
    if more or len(text) > PDF_TEXT_CHARS:
        text = text[:PDF_TEXT_CHARS] + "\n\n（后面还有，没有读完）"
    return text


async def read_text(session: AsyncSession, project_id: uuid.UUID, name: str) -> dict:
    """资料库里一份文件读成文本：文本文件照原样，PDF 取它的文字层；别的二进制文件
    只回大小和版本。给芝士读资料用，读的人不必在哪个房间里。"""
    row = await _current_or_404(session, project_id, name)
    data = await _bytes_of(row)
    payload = bytes_text_payload(data, f"library/{name}")
    if payload["binary"] and name.lower().endswith(".pdf"):
        text = await asyncio.to_thread(_pdf_text, data)
        if text is not None:
            payload = {**payload, "binary": False, "content": text}
    return payload


async def stored(
    session: AsyncSession, project_id: uuid.UUID
) -> list[tuple[str, str, str]]:
    """现在每一份的名字、存储和键：给要在事务结束之后才搬字节的人（项目导出）。"""
    rows = await session.scalars(_current(project_id).order_by(LibraryFileRecord.name))
    return [(row.name, row.location, row.blob_key) for row in rows]


def read_blob(location: str, key: str) -> bytes:
    """``stored`` 交回的那一双 ``(存储, 键)`` 底下的字节。

    和 ``stored`` 是一对：行和字节分开给，是因为搬字节的时机在事务结束之后（见它
    的说明）。「存储 + 键」这双地址怎么读回字节是本包自己的事，不该让搬的人去认
    字节存放在哪儿。
    """
    return blobs.store(location).get(key)


async def add(
    session: AsyncSession,
    project_id: uuid.UUID,
    filename: str,
    data: service.Content,
    added_by: str | None,
    room_id: uuid.UUID | None,
    source_path: str | None = None,
) -> str:
    """放进一份新资料，记下是谁在哪放的。撞名不覆盖：拿下一个 `(n)`。返回它的名字。

    `filename` 可以带文件夹（`合同/报价.xlsx`），编号加在最后一层上。占名字就是插
    入那一行：同一时刻两份同名的上传，唯一索引让后到的那一份去拿下一个号。
    `source_path` 是从房间文件区里哪一份存过来的（`saved_from`）。"""
    measured = await asyncio.to_thread(service.measure, data)
    for attempt in range(1, 1000):
        name = service.next_name(filename, attempt)
        if await _taken(session, project_id, name):
            continue
        row = _new_row(
            project_id,
            name,
            measured,
            added_by=added_by,
            room_id=room_id,
            source_path=source_path,
        )
        try:
            async with session.begin_nested():
                session.add(row)
        except IntegrityError:
            continue
        await _put(row, data)
        return name
    raise UnprocessableEntityError(say("tooManySameNameFiles", name=filename))


async def saved_from(session: AsyncSession, room_id: uuid.UUID) -> set[str]:
    """这个房间文件区里已经存进资料库的那几份的路径（`add` 的 `source_path`）。

    资料库里那一份后来被替换、改名都还算：它还在资料库里（替换记的是新的一行，存进来
    的那一行成了旧版本，名字跟着改名一起改）。删掉了就不算。"""
    now = aliased(LibraryFileRecord)
    rows = await session.scalars(
        select(LibraryFileRecord.source_path).where(
            LibraryFileRecord.room_id == room_id,
            LibraryFileRecord.source_path.is_not(None),
            select(now.id)
            .where(
                now.project_id == LibraryFileRecord.project_id,
                now.name == LibraryFileRecord.name,
                now.superseded_at.is_(None),
            )
            .exists(),
        )
    )
    return {path for path in rows if path}


async def replace(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    name: str,
    data: service.Content,
    by: str,
) -> None:
    """把这个名字下的那一份换成新的字节。旧的那一份留着，不是删掉。

    引用这个名字的旧消息从此读到的是新的一份——这正是「替换」的意思：人明确说了这
    是同一份文件的新版本。

    「现在这一份」是**读出来再写回去**的一对动作（`superseded_at` 为空的那一行只有
    一行，见 `uq_library_files_current`），两个人同时替换同一份时，两边都会读到自己
    是唯一的那一版。所以先按 project:name 取一把锁，把这个名字上的替换排成队。
    `restore`（把旧的一版复制回来）走的也是这里，一并排上。"""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"library-file:{project_id}:{name}"},
    )
    current = await _current_or_404(session, project_id, name)
    measured = await asyncio.to_thread(service.measure, data)
    try:
        current.superseded_at = datetime.now(UTC)
        await session.flush()
        row = _new_row(project_id, name, measured, added_by=by, room_id=None)
        session.add(row)
        await session.flush()
    except IntegrityError:
        # 锁只管走这条路的替换；不走这条路的写入（比如同时放进一份新资料）仍然能撞
        # 上「一个名字只有一份是现在这一份」。撞上就是并发冲突，不是故障：这一次没
        # 换成就直说，别把 500 摆给人看。事务到这里就作废了，由 `get_db` 回滚。
        raise ConflictError(say("libraryFileBeingReplaced", name=name)) from None
    await _put(row, data)


async def _rows_under(
    session: AsyncSession, project_id: uuid.UUID, path: str
) -> tuple[list[LibraryFileRecord], bool]:
    """`path` 名下的每一行（被替换下来的几版也算），以及它是不是一个文件夹。"""
    rows = list(
        await session.scalars(
            select(LibraryFileRecord).where(
                LibraryFileRecord.project_id == project_id,
                LibraryFileRecord.name == path,
            )
        )
    )
    if any(row.superseded_at is None for row in rows):
        return rows, False
    rows = list(
        await session.scalars(
            select(LibraryFileRecord).where(
                LibraryFileRecord.project_id == project_id,
                LibraryFileRecord.name.like(_like_prefix(f"{path}/"), escape="\\"),
            )
        )
    )
    if not any(row.superseded_at is None for row in rows):
        raise NotFoundError(say("libraryFileNotFound"))
    return rows, True


async def remove(session: AsyncSession, *, project_id: uuid.UUID, name: str) -> None:
    """扔掉一份资料，或一个文件夹和它里面的一切：现在那一份、被替换下来的几版，
    连同记录一起。"""
    rows, _ = await _rows_under(session, project_id, name)
    await session.execute(
        delete(LibraryFileRecord).where(
            LibraryFileRecord.id.in_([row.id for row in rows])
        )
    )
    for row in rows:
        await asyncio.to_thread(blobs.store(row.location).delete, row.blob_key)


async def move(
    session: AsyncSession, *, project_id: uuid.UUID, source: str, target: str
) -> dict[str, str]:
    """把一份资料或一个文件夹改名 / 挪到 `target`，返回每个旧名字变成了什么。

    字节不动，只改名字；被替换下来的几版跟着走。引用旧名字的消息——附件和正文里
    的 `<&library/…>`——一起改过去：消息里的那枚 chip 指的是这一份资料，它换了个
    名字，还是它。"""
    if target == source:
        return {}
    if target.startswith(f"{source}/"):
        raise UnprocessableEntityError(say("libraryMoveIntoItself"))
    rows, folder = await _rows_under(session, project_id, source)
    renamed = {
        row.name: target + row.name[len(source) :] if folder else target for row in rows
    }
    moving = frozenset(renamed)
    for row in rows:
        if row.superseded_at is None and await _taken(
            session, project_id, renamed[row.name], ignore=moving
        ):
            raise UnprocessableEntityError(
                say("libraryNameTaken", name=renamed[row.name])
            )
    for row in rows:
        row.name = renamed[row.name]
    await session.flush()
    await _follow_references(session, project_id, source, target, folder)
    return {old: new for old, new in renamed.items()}


async def _follow_references(
    session: AsyncSession,
    project_id: uuid.UUID,
    source: str,
    target: str,
    folder: bool,
) -> None:
    old = service.library_ref(source)
    new = service.library_ref(target)
    in_project = Block.project_id == project_id
    attachments = (Block.kind == BlockKind.attachment) & in_project
    if folder:
        await session.execute(
            update(Block)
            .where(
                attachments, Block.content.like(_like_prefix(f"{old}/"), escape="\\")
            )
            .values(
                content=literal(f"{new}/") + func.substr(Block.content, len(old) + 2)
            )
        )
    else:
        await session.execute(
            update(Block).where(attachments, Block.content == old).values(content=new)
        )
    end = "/" if folder else ">"
    chip_old, chip_new = f"<&{old}{end}", f"<&{new}{end}"
    await session.execute(
        update(Block)
        .where(
            in_project,
            Block.kind != BlockKind.attachment,
            Block.content.like(f"%{_like_prefix(chip_old)}", escape="\\"),
        )
        .values(content=func.replace(Block.content, chip_old, chip_new))
    )


async def versions(
    session: AsyncSession, project_id: uuid.UUID, name: str
) -> list[dict]:
    """一份资料的每一版，新的在前。版本号按放进来的先后从 1 数。"""
    rows = list(
        await session.scalars(
            select(LibraryFileRecord)
            .where(
                LibraryFileRecord.project_id == project_id,
                LibraryFileRecord.name == name,
            )
            .order_by(LibraryFileRecord.created_at, LibraryFileRecord.id)
        )
    )
    if not rows:
        raise NotFoundError(say("libraryFileNotFound"))
    return [
        {
            "id": str(row.id),
            "version": number,
            "bytes": row.bytes,
            "added_by": row.added_by,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "current": row.superseded_at is None,
        }
        for number, row in reversed(list(enumerate(rows, start=1)))
    ]


async def _version_row(
    session: AsyncSession, project_id: uuid.UUID, name: str, version_id: uuid.UUID
) -> LibraryFileRecord:
    row = await session.get(LibraryFileRecord, version_id)
    if row is None or row.project_id != project_id or row.name != name:
        raise NotFoundError(say("libraryVersionNotFound"))
    return row


async def version_bytes(
    session: AsyncSession, project_id: uuid.UUID, name: str, version_id: uuid.UUID
) -> bytes:
    """某一版的字节。"""
    return await _bytes_of(await _version_row(session, project_id, name, version_id))


async def restore(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    name: str,
    version_id: uuid.UUID,
    by: str,
) -> None:
    """把旧的一版恢复成现在这一份：**复制**成新的一版，和替换走同一条路。历史只增不
    减，被恢复的那一版和它之后的几版都还在。"""
    row = await _version_row(session, project_id, name, version_id)
    if row.superseded_at is None:
        raise UnprocessableEntityError(say("libraryVersionIsCurrent"))
    data = await _bytes_of(row)
    await replace(session, project_id=project_id, name=name, data=data, by=by)
