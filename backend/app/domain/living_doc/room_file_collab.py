"""A room file several people edit at once: its live state and how a store
becomes a version of the file.

A room file is addressed by ``(room, path)`` and has no id, so the live editor
needs one made for it (``RoomFileCollab``) — that row's id is the name the
collaboration service holds it under. This module is the backend's side of
that name: open one (making the row), and land a store arriving from the
service as a new revision of the file.

The file's own revisions stay the record. What the service stores here is the
editing session's shared state, and every store becomes a revision through
``save_room_file`` — so the preview, the history and a restore all keep reading
one thing, the file, whether the last save came from one person in the preview,
芝士, or several people at once here.
"""

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.living_doc import collab
from app.domain.project import room_files
from app.domain.project.models import RoomFileCollab
from app.domain.project.ports import FileStore, WarmPreview


async def open_for(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    path: str,
    content: str,
) -> RoomFileCollab:
    """The file's live state, made if this is the first time anyone edits it
    together.

    ``content`` seeds a state that does not exist yet: the file's current
    Markdown, which the service converts and stores back before anyone types.
    That first store is the conversion, harmless to an unedited document.

    Two people opening the same file at once both find no row; the unique key
    on ``(room_id, path)`` settles it, and the loser re-reads what the winner
    made rather than failing. A save serialises writers on the file itself
    (`save_room_file`'s advisory lock); this is the same hazard one step
    earlier, on the row the editing session lives in.
    """
    row = await find(session, room_id=room_id, path=path)
    if row is not None:
        return row
    row = RoomFileCollab(
        project_id=project_id,
        room_id=room_id,
        path=path,
        state=b"",
        content=content,
        suggestions=[],
    )
    try:
        # A savepoint: losing the race must not take the caller's whole
        # transaction (and whatever else it did) down with it. Added inside
        # it: begin_nested() flushes pending objects first, which would put
        # the INSERT outside the savepoint.
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = await find(session, room_id=room_id, path=path)
        if existing is None:
            raise
        return existing
    return row


async def find(
    session: AsyncSession, *, room_id: uuid.UUID, path: str
) -> RoomFileCollab | None:
    return await session.scalar(
        select(RoomFileCollab).where(
            RoomFileCollab.room_id == room_id, RoomFileCollab.path == path
        )
    )


async def by_id(session: AsyncSession, collab_id: uuid.UUID) -> RoomFileCollab:
    row = await session.get(RoomFileCollab, collab_id)
    if row is None:
        raise NotFoundError(say("roomFileNotFound"))
    return row


async def room_file_collab_named(
    session: AsyncSession, name: str
) -> RoomFileCollab | None:
    """The row a service-side name (``roomfile:<id>``) names, or None when the
    name is not one."""
    try:
        collab_id = collab.room_file_collab_of(name)
    except ValueError:
        return None
    return await session.get(RoomFileCollab, collab_id)


async def store(
    session: AsyncSession,
    row: RoomFileCollab,
    *,
    state: bytes,
    content: str | None,
    actors: list[str],
    suggestions: list[dict],
    author_kind: str,
    warm_preview: WarmPreview,
    file_store: FileStore,
) -> dict:
    """Record one store: the live state here, and — when the text moved — a new
    revision of the file itself.

    ``content`` None stores the state alone (the service reconciled a state it
    already had). A store whose text is what the file already holds leaves the
    file untouched too: the state is kept so the next open gets it back, and no
    empty revision is written.

    ``file_store`` is the bytes' owner, handed in rather than imported: it is
    layer 4's `library.service`, and neither this module nor `room_files` may
    reach it directly (they are in the lower layers).
    """
    row.state = state
    row.suggestions = suggestions
    await session.flush()
    if content is None or content == row.content:
        return {"seq": None, "content_version": None, "path": row.path}
    encoded = content.encode("utf-8")
    if await asyncio.to_thread(
        file_store.room_file_exists, row.project_id, row.room_id, row.path
    ):
        current = await asyncio.to_thread(
            file_store.read_room_file, row.project_id, row.room_id, row.path
        )
        if current == encoded:
            # The file already reads this: the state is worth keeping, the
            # file is not worth rewriting.
            row.content = content
            await session.flush()
            return {"seq": None, "content_version": None, "path": row.path}
    writer = actors[0] if actors else "system"
    saved = await room_files.save_room_file(
        session,
        project_id=row.project_id,
        room_id=row.room_id,
        path=row.path,
        data=encoded,
        author=writer,
        author_kind=author_kind,
        source="editor",
        note=None,
        editor_key=f"collab:{row.id}",
        warm_preview=warm_preview,
        file_store=file_store,
    )
    row.content = content
    await session.flush()
    return {"seq": saved.seq, "content_version": saved.sha256, "path": row.path}
