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

## 规则与来源

房间里的文件从哪来、叫什么算合法，是这一组规则，而不是某一条路由的内部：
`ARTIFACT_MIME` 说一种渲染类型对应哪个 mime，`artifact_kind_for` 从扩展名推类型，
`clean_artifact_path` 拦住越界路径；它们共用的那个上限跟着落地的字节走，在
``library.service.MAX_ARTIFACT_BYTES`` 那边，这里不再写第二遍。

规则和来源分开：**规则**（这里）是纯函数和常量，谁读都行、不需要 session；**来源**
（字节从哪个仓库读出来 —— 房间自己的、某个任务分支的、资料库的）认 `db` 和
项目/房间/任务的寻址方式，是 API 侧的读编排，留在 `app/api/routes` 那边。两边读同
一份 `clean_artifact_path`，路径合法与否只有一个答案。

## 字节放在哪儿

本包读写的那几份字节 —— 房间里的文件、它每一版的历史、交出去那一版留存的快照 ——
都是资料库的事：落地的在 `app/domain/library/service.py`，那几行记录在
`app/domain/library/records.py` 里，两个都在本包上面两层。所以两件事都**由调用方
递进来**：``FileStore`` 收读写字节的每一次调用，``LibraryFiles`` 收「留一份进资料
库」和「导出时把库里的字节搬出来」。

地址由本包给出（项目、房间、路径、卡），交回的都是内置类型：本包认不出也不该认得
资料库那几行，也认不出字节存在哪张盘上。所以调用方把那两个模块本身递进来即可，
不需要适配（`ports.py` 里写明为什么形状正好对得上）。
"""

import asyncio
import hashlib
import uuid
from pathlib import PurePosixPath

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.notice_meta import (
    EVENT_LIBRARY_SAVED,
    SEVERITY_INFO,
    WHO_PLATFORM,
    notice,
)
from app.domain.project.models import RoomFileRevision
from app.domain.project.ports import (
    Announce,
    FileStore,
    LibraryFiles,
    WarmPreview,
)
from app.domain.textfile import content_version

#: Which door a revision's bytes came in by.
SOURCES = ("baseline", "upload", "template", "ai", "editor", "restore", "scheduled")


async def save_to_library(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    path: str,
    by: str,
    said_in: uuid.UUID | None = None,
    announce: Announce,
    file_store: FileStore,
    library_files: LibraryFiles,
) -> str:
    """把房间里的一份文件留进资料库，返回它在那里的名字。``said_in`` 是按下保存
    的那段对话（任务或支线）；在那里说一声，不在主线。

    三样都是递进来的：``file_store`` 读出房间里的那份字节、``library_files`` 把它
    留进资料库（都在 `library` 里，比本包高两层），说那一句的 `announce` 在
    `agent.announce`（第 5 层，`ports.py`）。

    撞名不覆盖，跟着资料库自己的规矩走：两次保存就是两份，各自留着 —— 谁也说不准
    第二份是第一份的新版，还是另一样同名的东西。
    """
    leaf = PurePosixPath(path).name
    if not leaf:
        raise ValidationError(say("notARoomFile"))
    data = await asyncio.to_thread(file_store.read_room_file, project_id, room_id, path)
    name = await library_files.add(
        session,
        project_id=project_id,
        filename=leaf,
        data=data,
        added_by=by,
        room_id=room_id,
        source_path=path,
    )
    await announce(
        session,
        place_id=room_id,
        task_id=said_in,
        content=say("librarySaved", name=name),
        meta=notice(
            EVENT_LIBRARY_SAVED,
            severity=SEVERITY_INFO,
            # 一个人已经存好了，没有谁要再动手：不是「等人处理」那一档。
            who=WHO_PLATFORM,
            detail=say("librarySavedDetail", actor=by, file=leaf),
            detail_label=say("labelSaveToLibrary"),
        ),
    )
    return name


def current_version(
    project_id: uuid.UUID, room_id: uuid.UUID, path: str, *, file_store: FileStore
) -> str | None:
    """The version a reader holds: the content hash, or None when there is no file."""
    if not file_store.room_file_exists(project_id, room_id, path):
        return None
    return content_version(file_store.read_room_file(project_id, room_id, path))


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
    warm_preview: WarmPreview,
    file_store: FileStore,
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
        file_store.room_file_exists, project_id, room_id, path
    )
    before = (
        await asyncio.to_thread(file_store.read_room_file, project_id, room_id, path)
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
            say("fileChangedSinceRead"),
            data={"path": path, "version": now_version, "base_version": base_version},
        )
    # A file that is already here can be over the ceiling: it went in before
    # the gate existed. The history only holds what the ceiling allows, so that
    # earlier state is not recorded — the save itself still goes through (its
    # own bytes face the gate like any other). Failing here instead would leave
    # such a file impossible to save at all: every attempt would hit this first.
    if (
        latest is None
        and before is not None
        and len(before) <= file_store.MAX_FILE_BYTES
    ):
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
            warm_preview=warm_preview,
            file_store=file_store,
        )
    if before is not None and before == data and latest is not None:
        return latest
    await asyncio.to_thread(file_store.write_room_file, project_id, room_id, path, data)
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
        warm_preview=warm_preview,
        file_store=file_store,
    )


async def latest_by_path(
    session: AsyncSession, room_id: uuid.UUID
) -> dict[str, RoomFileRevision]:
    """这个房间里每一份文件最后保存的是哪一版，按路径。

    文件区那一页列的是目录本身（走文件系统），一份文件可能从没经过
    :func:`save_room_file`——所以作者和来源要照这张表补齐，缺的那些照列，只是说不出
    是谁写的。
    """
    rows = (
        (
            await session.execute(
                select(RoomFileRevision)
                .where(RoomFileRevision.room_id == room_id)
                .order_by(RoomFileRevision.path, RoomFileRevision.seq.desc())
                .distinct(RoomFileRevision.path)
            )
        )
        .scalars()
        .all()
    )
    return {row.path: row for row in rows}


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
    warm_preview: WarmPreview,
    file_store: FileStore,
) -> RoomFileRevision:
    digest = await asyncio.to_thread(
        file_store.write_revision_blob, project_id, room_id, data
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
    warm_preview(data, path)
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
        raise NotFoundError(say("versionNotFound"))
    return row


def revision_bytes(row: RoomFileRevision, *, file_store: FileStore) -> bytes:
    return file_store.read_revision_blob(row.project_id, row.room_id, row.sha256)


async def restore_revision(
    session: AsyncSession,
    *,
    row: RoomFileRevision,
    by: str,
    author_kind: str,
    warm_preview: WarmPreview,
    file_store: FileStore,
) -> RoomFileRevision:
    """Put an earlier state back as the file's newest one.

    History is not rewound: the restore is a new revision, so the state it
    replaced stays one click away too. A delivered version lives in the accept
    card's own snapshot and is not reachable from here at all.
    """
    data = await asyncio.to_thread(revision_bytes, row, file_store=file_store)
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
        warm_preview=warm_preview,
        file_store=file_store,
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


# 芝士 → UI rendering (spec §9.1): an artifact is a file the AI explicitly points
# at + how to render it. The type comes from the tool call, never from parsing
# prose. MVP renders html/svg in the preview window; more types are additive.
ARTIFACT_MIME = {
    "html": "text/html",
    "svg": "image/svg+xml",
    # A deliverable is not always a web page. A room that writes a report, a
    # budget or a deck produces one of these, and until the platform accepted
    # them the only way to hand one over was to describe where it sat in the
    # worktree — which the person in the room cannot open.
    "pdf": "application/pdf",
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "md": "text/markdown",
    "csv": "text/csv",
    "png": "image/png",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    # Source code and plain text. The preview draws them with line numbers and
    # highlighting from the extension (frontend `lib/fileKind.ts`); what the
    # content domain sends is the bytes as text, never as a page to run.
    "text": "text/plain",
    # 运行环境预览: the artifact is a RUNNING app on the machine this place's turn
    # lives on, reached over the preview tunnel that machine dialled out. HOW to
    # run it — and on which port — is the agent's judgment; the platform only
    # carries what answers there.
    "app": "application/x-cheesex-app",
    # MCP Apps: the artifact points at a THIRD party's HTML View, a `ui://`
    # resource on a remote MCP server the project declares. Its ``content`` is a
    # JSON pointer ``{server, uri}`` (see ``app.domain.remote_mcp.apps``); the
    # platform fetches the bytes through that server's connection, never from a
    # room file, and the preview host serves them under a stricter policy.
    "mcp-app": "text/html;profile=mcp-app",
}

#: Which artifact kind a filename implies, when the caller named none.
#:
#: Asking the agent to restate in a flag what the extension already says is a
#: rule it can get wrong, and the wrong answer here is silent: `report.docx`
#: declared as html reaches the panel as a mis-typed blob rather than an error.
#: An unknown extension still falls back to html, which is what every caller
#: predating this table sent.
_ARTIFACT_KIND_BY_SUFFIX = {
    ".html": "html",
    ".htm": "html",
    ".svg": "svg",
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".md": "md",
    ".markdown": "md",
    ".csv": "csv",
    ".png": "png",
    ".jpg": "jpg",
    ".jpeg": "jpg",
    ".gif": "gif",
    ".webp": "webp",
    # Source code and plain text: the same suffixes the frontend draws with its
    # code viewer (`lib/fileKind.ts`, CODE_TYPES). Before this they fell through
    # to html and a `.py` reached the content domain as a page.
    ".py": "text",
    ".pyi": "text",
    ".js": "text",
    ".mjs": "text",
    ".cjs": "text",
    ".jsx": "text",
    ".ts": "text",
    ".mts": "text",
    ".cts": "text",
    ".tsx": "text",
    ".vue": "text",
    ".java": "text",
    ".kt": "text",
    ".c": "text",
    ".h": "text",
    ".cpp": "text",
    ".cc": "text",
    ".cxx": "text",
    ".hpp": "text",
    ".hh": "text",
    ".cs": "text",
    ".go": "text",
    ".rs": "text",
    ".rb": "text",
    ".php": "text",
    ".swift": "text",
    ".lua": "text",
    ".r": "text",
    ".sh": "text",
    ".bash": "text",
    ".zsh": "text",
    ".json": "text",
    ".yaml": "text",
    ".yml": "text",
    ".toml": "text",
    ".ini": "text",
    ".sql": "text",
    ".css": "text",
    ".scss": "text",
    ".less": "text",
    ".xml": "text",
    ".diff": "text",
    ".patch": "text",
    ".txt": "text",
    ".log": "text",
}


def artifact_kind_for(path: str) -> str:
    """The kind `path`'s extension implies; `html` when it implies none."""
    suffix = path.rsplit("/", 1)[-1]
    dot = suffix.rfind(".")
    return _ARTIFACT_KIND_BY_SUFFIX.get(suffix[dot:].lower() if dot > 0 else "", "html")


def clean_artifact_path(raw: str) -> str:
    """A workspace-relative pointer — reject absolute paths, traversal, and .git.
    The file itself is read later via the guarded workspace reader.

    Returned in its one spelling, without ``.`` segments or empty ones: the
    rules that read a path's first segment (a library original, a file sent in
    the conversation) must see the same path the file is written to, and
    ``./uploads/x`` or ``uploads//x`` lands on ``uploads/x``."""
    given = (raw or "").strip()
    path = "/".join(part for part in given.split("/") if part not in ("", "."))
    if not path:
        raise ValidationError(say("pathEmpty"))
    parts = path.split("/")
    if given.startswith("/") or ".." in parts or ".git" in parts:
        raise ValidationError(say("pathMustBeRelative"))
    return path
