"""A person's save of a task file, said in the task and to its AI teammate.

The teammate keeps what it read of a file in its context and writes from that.
When a person changes the file under it, its copy is stale, and a write from
that copy would quietly undo the person's edit. So the save leaves a line in
the task (who changed which lines, with the diff), and that line carries what
the teammate is told before its next tool call: re-read the file before
touching it.

Saves come in a run while a person types. A run on one file is one line: until
the teammate has read it, and for as long as the work that read it is still
going, the next save rewrites that line with everything changed since the run
began, and a new line starts only after that.

The person who edited is also credited on the task: their part goes into the
teammate's next commit, which names them as a co-author, unless they hand it
in themselves first (`machine.hand_in`), as a commit of their own.
"""

import difflib
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live_frames import show_once_committed
from app.core.sentences import say, with_keys
from app.domain.agent.announce import announce
from app.domain.block.models import (
    AGENT_NOTICE_META_KEY,
    Block,
    BlockKind,
    consumed_turn,
)
from app.domain.block.schemas import BlockOut
from app.domain.identity.handles import CHEESE_HANDLE
from app.domain.notice_meta import (
    EVENT_FILE_EDITED,
    SEVERITY_INFO,
    WHO_CHEESE,
    notice,
)
from app.domain.room_task.models import Task


def changed_lines(previous: str, content: str) -> tuple[int, int] | None:
    """The first and last line of `content` that differ from `previous`."""
    matcher = difflib.SequenceMatcher(
        None, previous.splitlines(), content.splitlines(), autojunk=False
    )
    spans = [
        (j1 + 1, max(j1 + 1, j2))
        for tag, _i1, _i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]
    if not spans:
        return None
    return spans[0][0], spans[-1][1]


#: On a line's meta: whose run of saves on which file the line stands for.
FILE_EDIT_META_KEY = "file_edit"

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _before(after: str, diff: str) -> str:
    """The text `diff` was taken from, given the text it led to. `diff` is a
    unified diff this module wrote, so it applies to `after` exactly."""
    lines = after.splitlines()
    out: list[str] = []
    at = 0
    old: list[str] | None = None
    for row in diff.splitlines():
        hunk = _HUNK.match(row)
        if hunk:
            if old is not None:
                out.extend(old)
            start = int(hunk.group(3))
            count = int(hunk.group(4) if hunk.group(4) is not None else 1)
            # A hunk that adds nothing names the line before it.
            first = start - 1 if count else start
            out.extend(lines[at:first])
            at = first + count
            old = []
        elif old is not None and row[:1] in (" ", "-"):
            old.append(row[1:])
    if old is not None:
        out.extend(old)
    out.extend(lines[at:])
    return "\n".join(out)


async def _open_line(
    session: AsyncSession, task: Task, who: str, path: str, working: uuid.UUID | None
) -> Block | None:
    """The line this person's run of saves on this file is still written on:
    their latest one, while the teammate has not read it or the work that read
    it is still going."""
    line = await session.scalar(
        select(Block)
        .where(
            Block.conversation_id == task.id,
            Block.kind == BlockKind.event,
            Block.meta["event_type"].as_string() == EVENT_FILE_EDITED,
            Block.meta[FILE_EDIT_META_KEY]["path"].as_string() == path,
            Block.meta[FILE_EDIT_META_KEY]["by"].as_string() == who,
        )
        .order_by(Block.created_at.desc(), Block.id.desc())
        .limit(1)
    )
    if line is None:
        return None
    read_in = consumed_turn(line)
    if read_in is None or (working is not None and read_in == str(working)):
        return line
    return None


async def announce_edit(
    session: AsyncSession,
    *,
    task: Task,
    who: str,
    path: str,
    previous: str | None,
    content: str,
    working: uuid.UUID | None = None,
) -> tuple[Block, str, bool] | None:
    """Say the edit in the task and credit its author: the line, what the AI
    teammate is told about it, and whether the teammate has yet to hear of this
    run of saves. None when nothing changed.

    `working` is the work the teammate is doing in the task right now, if any.
    """
    if previous is not None and changed_lines(previous, content) is None:
        return None
    line = await _open_line(session, task, who, path, working)
    detail = (line.meta or {}).get("detail") if line is not None else None
    if line is not None and previous is not None and detail:
        # The run's first text, so the line says everything changed since then.
        previous = _before(previous, detail)
    span = changed_lines(previous, content) if previous is not None else None
    lines = (
        ""
        if span is None
        else say(
            "taskFileEditedLines",
            lines=str(span[0]) if span[0] == span[1] else f"{span[0]}–{span[1]}",
        )
    )
    diff = (
        "\n".join(
            difflib.unified_diff(
                previous.splitlines(),
                content.splitlines(),
                fromfile=f"{path}（修改前）",
                tofile=f"{path}（修改后）",
                lineterm="",
            )
        )
        if previous is not None
        else ""
    )
    for_agent = (
        f"{who} 刚在任务目录里改了 {path}"
        + (f"（{lines}）" if lines else "")
        + "。你此前读到的这个文件已经过期，动它之前先重新读取；不要用旧内容写回，"
        "那会把这次修改冲掉。下次提交时把这部分一起提交，提交信息里会署上 "
        f"{who} 为合作者。" + (f"\n```diff\n{diff}\n```" if diff else "")
    )
    if who != task.owner_handle and who not in task.contributor_handles:
        task.contributor_handles = [*task.contributor_handles, who]
    meta = notice(
        EVENT_FILE_EDITED,
        severity=SEVERITY_INFO,
        who=WHO_CHEESE,
        detail=diff or None,
        detail_label=say("labelDocEditDiff"),
    )
    meta[AGENT_NOTICE_META_KEY] = for_agent
    meta[FILE_EDIT_META_KEY] = {"path": path, "by": who}
    said = say("taskFileEdited", who=f"<@{who}>", path=path, lines=lines)
    if line is not None:
        unheard = consumed_turn(line) is None
        line.content = said
        line.meta = with_keys({**(line.meta or {}), **meta}, content=said)
        await session.flush()
        show_once_committed(
            session,
            line.conversation_id,
            {
                "type": "block_updated",
                "block": BlockOut.model_validate(line).model_dump(mode="json"),
            },
        )
        return line, for_agent, unheard
    block = await announce(
        session,
        place_id=task.room_id,
        task_id=task.id,
        content=said,
        meta=meta,
    )
    return (block, for_agent, True) if block is not None else None


async def announce_editor_save(
    session: AsyncSession,
    *,
    room_id: uuid.UUID,
    who: str,
    path: str,
    aside: str | None,
) -> tuple[Block, str] | None:
    """A person's save of a room document in the Office editor, said in the room
    and to its AI teammate. The editor writes Office files whole, so there is no
    diff to show; what the teammate needs is that its copy is stale. `aside` is
    where the save landed when the file had changed after the editor opened it.
    """
    agent = f"<@{CHEESE_HANDLE}>"
    if aside is None:
        for_agent = (
            f"{who} 刚在编辑器里保存了 {path}。你此前读到的这份已经过期，动它之前先"
            "重新读取；不要用旧内容写回，那会把这次修改冲掉。"
        )
        content = say("roomFileEditorSaved", who=f"<@{who}>", path=path, agent=agent)
    else:
        for_agent = (
            f"{who} 在编辑器里改了 {path}，但保存时这份文件已经被改过，{who} 的这一"
            f"份另存为 {aside}。两份都在，先读这两份，需要的话合成一份。"
        )
        content = say(
            "roomFileEditorSavedAside",
            who=f"<@{who}>",
            path=path,
            aside=aside,
            agent=agent,
        )
    meta = notice(EVENT_FILE_EDITED, severity=SEVERITY_INFO, who=WHO_CHEESE)
    meta[AGENT_NOTICE_META_KEY] = for_agent
    block = await announce(session, place_id=room_id, content=content, meta=meta)
    return (block, for_agent) if block is not None else None
