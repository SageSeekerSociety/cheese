"""A person's save of a task file, said in the task and to its AI teammate.

The teammate keeps what it read of a file in its context and writes from that.
When a person changes the file under it, its copy is stale, and a write from
that copy would quietly undo the person's edit. So the save leaves a line in
the task (who changed which lines, with the diff), and that line carries what
the teammate is told before its next tool call: re-read the file before
touching it.

The person who edited is also credited on the task: their part goes into the
teammate's next commit, which names them as a co-author.
"""

import difflib

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.agent.platform_notices import (
    EVENT_FILE_EDITED,
    SEVERITY_INFO,
    WHO_CHEESE,
    notice,
)
from app.domain.block.models import AGENT_NOTICE_META_KEY, Block
from app.domain.identity.handles import CHEESE_HANDLE
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


async def announce_edit(
    session: AsyncSession,
    *,
    task: Task,
    who: str,
    path: str,
    previous: str | None,
    content: str,
) -> tuple[Block, str] | None:
    """Say the edit in the task and credit its author: the line, and what the AI
    teammate is told about it. None when nothing changed."""
    span = changed_lines(previous, content) if previous is not None else None
    if previous is not None and span is None:
        return None
    lines = (
        ""
        if span is None
        else say(
            "taskFileEditedLines",
            lines=str(span[0]) if span[0] == span[1] else f"{span[0]}–{span[1]}",
        )
    )
    agent = task.agent_handle or CHEESE_HANDLE
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
    block = await announce(
        session,
        place_id=task.room_id,
        task_id=task.id,
        content=say(
            "taskFileEdited",
            who=f"<@{who}>",
            path=path,
            lines=lines,
            agent=f"<@{agent}>",
        ),
        meta=meta,
    )
    return (block, for_agent) if block is not None else None
