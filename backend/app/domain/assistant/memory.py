"""What a person's own 芝士 remembers about them.

It is the platform's one memory, the files a project's 芝士 keep
(``memory.files``), in a scope of its own: ``personal``, held by the person
and in no project. One memory is one file (a name, a line saying what it is
for, a type, and the thing itself); ``MEMORY.md`` indexes them, one line each.
The index goes with the start of every new conversation (``prompt.about``),
and a memory's text is read when it is needed.

Only the person's own 芝士, in their own panel, reads and writes it: a
project's 芝士 never does, since every read there names its project, and a
routine's run does not write it, since nobody is there and all it reads is
other people's words. The person reads, edits and deletes it on their settings
page, and can turn it off (``AssistantRules.memory``): kept, but neither used
nor added to.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory.files import (
    INDEX_NAME,
    MemoryFile,
    MemoryFileError,
    MemoryFileScope,
    fit_index,
    parse_index,
    parse_memory_file,
)
from app.domain.memory.files_store import MemoryFileStore
from app.domain.memory.models import MemoryFileRecord
from app.domain.memory.writers import MemoryWriter

SCOPE = MemoryFileScope.personal


@dataclass(frozen=True)
class Entry:
    """One memory as the settings page shows it."""

    path: str
    memory: MemoryFile
    updated_at: str


def path_of(name: str) -> str:
    return f"{name}.md"


async def index(session: AsyncSession, handle: str) -> str | None:
    """The index as the start of a conversation says it, cut to its budget;
    None when nothing is remembered."""
    text = await MemoryFileStore(session).index_text(None, SCOPE, handle)
    if text is None:
        return None
    fitted, _ = fit_index(text)
    return fitted.strip() or None


async def entries(session: AsyncSession, handle: str) -> list[Entry]:
    """Every memory, in the index's order; one whose file no longer reads as a
    memory is left out rather than shown half."""
    store = MemoryFileStore(session)
    rows = {r.path: r for r in await store.list(None, SCOPE, handle)}
    index_row = rows.pop(INDEX_NAME, None)
    order = [e.path for e in parse_index(index_row.content)] if index_row else []
    order += sorted(p for p in rows if p not in order)
    out: list[Entry] = []
    for path in order:
        row = rows.get(path)
        if row is None:
            continue
        try:
            memory = parse_memory_file(row.content)
        except MemoryFileError:
            continue
        out.append(Entry(path, memory, row.updated_at.isoformat()))
    return out


async def read(session: AsyncSession, handle: str, name: str) -> MemoryFile | None:
    row = await MemoryFileStore(session).get(None, SCOPE, handle, path_of(name))
    if row is None:
        return None
    try:
        return parse_memory_file(row.content)
    except MemoryFileError:
        return None


async def save(
    session: AsyncSession,
    handle: str,
    memory: MemoryFile,
    *,
    by: str,
) -> str | None:
    """Write one memory and its index line, new or over the one of that name.
    Returns what the file said before (None when it is new), for undoing;
    the caller commits. A memory over its limits is refused
    (``MemoryFileLimit``), with what to shorten."""
    path = path_of(memory.name)
    return await put(session, handle, path, memory.text(), memory, by=by)


async def put(
    session: AsyncSession,
    handle: str,
    path: str,
    content: str | None,
    memory: MemoryFile | None = None,
    *,
    by: str,
) -> str | None:
    """Make the file at ``path`` say ``content``, or be gone when None, and
    keep the index in step. Returns what it said before."""
    store = MemoryFileStore(session)
    row = await store.get(None, SCOPE, handle, path, for_update=True)
    before = row.content if row is not None else None
    if content is None:
        if row is not None:
            await store.forget(
                project_id=None,
                scope=SCOPE,
                owner_handle=handle,
                paths=[path],
                updated_by=by,
                writer=MemoryWriter.assistant,
            )
        return before
    if memory is None:
        memory = parse_memory_file(content)
    await store.write(
        project_id=None,
        scope=SCOPE,
        owner_handle=handle,
        path=path,
        content=content,
        updated_by=by,
        expected_version=None if row is None else row.version,
        writer=MemoryWriter.assistant,
    )
    await _index_line(store, handle, path, memory.index_line(path), by=by)
    return before


async def _index_line(
    store: MemoryFileStore, handle: str, path: str, line: str, *, by: str
) -> None:
    """The index points at ``path`` with ``line``: in its old place when it
    had one, at the end when it is new."""
    row = await store.get(None, SCOPE, handle, INDEX_NAME, for_update=True)
    lines = row.content.splitlines() if row is not None else []
    pointed = [i for i, e in enumerate(lines) if f"]({path})" in e]
    if pointed:
        lines[pointed[0]] = line
        for i in reversed(pointed[1:]):
            del lines[i]
    else:
        lines.append(line)
    text = "\n".join(lines) + "\n"
    if row is not None and row.content == text:
        return
    await store.write(
        project_id=None,
        scope=SCOPE,
        owner_handle=handle,
        path=INDEX_NAME,
        content=text,
        updated_by=by,
        expected_version=None if row is None else row.version,
        writer=MemoryWriter.assistant,
    )


async def clear(session: AsyncSession, handle: str) -> int:
    """Forget everything; how many memories there were. The caller commits."""
    count = len(await entries(session, handle))
    await session.execute(
        delete(MemoryFileRecord).where(
            MemoryFileRecord.project_id.is_(None),
            MemoryFileRecord.scope == SCOPE,
            MemoryFileRecord.owner_handle == handle,
        )
    )
    await session.flush()
    return count


def made(name: str, description: str, kind: str, body: str) -> MemoryFile:
    """A memory from its parts, checked as its file is; ``MemoryFileError``
    says what is wrong with it."""
    text = (
        "---\n"
        f"name: {name.strip()}\n"
        f"description: {' '.join(description.split())}\n"
        f"type: {kind.strip()}\n"
        "---\n\n"
        f"{body.strip()}\n"
    )
    return parse_memory_file(text)
