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
`clean_artifact_path` 拦住越界路径，`MAX_ARTIFACT_BYTES` 是它们共用的上限。

规则和来源分开：**规则**（这里）是纯函数和常量，谁读都行、不需要 session；**来源**
（字节从哪个仓库读出来 —— 房间自己的、某个任务分支的、资料库的）认 `db` 和
项目/房间/任务的寻址方式，是 API 侧的读编排，留在 `app/api/routes` 那边。两边读同
一份 `clean_artifact_path`，路径合法与否只有一个答案。
"""

import asyncio
import hashlib
import uuid
from pathlib import PurePosixPath

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.agent.platform_notices import (
    EVENT_LIBRARY_SAVED,
    SEVERITY_INFO,
    WHO_HUMAN,
    notice,
)
from app.domain.library import records as library_records
from app.domain.library import service as library
from app.domain.preview import office
from app.domain.project.models import RoomFileRevision
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
) -> str:
    """把房间里的一份文件留进资料库，返回它在那里的名字。``said_in`` 是按下保存
    的那段对话（任务或支线）；在那里说一声，不在主线。

    撞名不覆盖，跟着资料库自己的规矩走：两次保存就是两份，各自留着 —— 谁也说不准
    第二份是第一份的新版，还是另一样同名的东西。
    """
    leaf = PurePosixPath(path).name
    if not leaf:
        raise ValidationError(say("notARoomFile"))
    data = await asyncio.to_thread(library.read_room_file, project_id, room_id, path)
    name = await library_records.add(
        session,
        project_id=project_id,
        filename=leaf,
        data=data,
        added_by=by,
        room_id=room_id,
    )
    await announce(
        session,
        place_id=room_id,
        task_id=said_in,
        content=say("librarySaved", name=name),
        meta=notice(
            EVENT_LIBRARY_SAVED,
            severity=SEVERITY_INFO,
            who=WHO_HUMAN,
            detail=say("librarySavedDetail", actor=by, file=leaf),
            detail_label=say("labelSaveToLibrary"),
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
            say("fileChangedSinceRead"),
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
    office.prewarm(data, path)
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
    # 运行环境预览: the artifact is a RUNNING app on the machine this place's turn
    # lives on, reached over the preview tunnel that machine dialled out. HOW to
    # run it — and on which port — is the agent's judgment; the platform only
    # carries what answers there.
    "app": "application/x-cheesex-app",
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
}

#: Ceiling on a published artifact, matching the chat attachment limit —
#: both are "a file a person will open in this room", and a report that is too
#: big to send as an attachment is too big to publish as a deliverable. The
#: number itself lives with the layer that lands the bytes
#: (``library.MAX_FILE_BYTES``), together with the attachment and library caps.
MAX_ARTIFACT_BYTES = library.MAX_FILE_BYTES


def artifact_kind_for(path: str) -> str:
    """The kind `path`'s extension implies; `html` when it implies none."""
    suffix = path.rsplit("/", 1)[-1]
    dot = suffix.rfind(".")
    return _ARTIFACT_KIND_BY_SUFFIX.get(suffix[dot:].lower() if dot > 0 else "", "html")


def clean_artifact_path(raw: str) -> str:
    """A workspace-relative pointer — reject absolute paths, traversal, and .git.
    The file itself is read later via the guarded workspace reader."""
    path = (raw or "").strip()
    if not path:
        raise ValidationError(say("pathEmpty"))
    parts = path.split("/")
    if path.startswith("/") or ".." in parts or ".git" in parts:
        raise ValidationError(say("pathMustBeRelative"))
    return path
