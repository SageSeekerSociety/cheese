"""引导模式的两项保护落在 AI 队友的回复上（§ learning analysis, step 4）。

题目（或它的空间）选了「引导为主」并打开了开关时：

- **提示阶梯**：AI 队友被要求每条帮忙的回复开头标出自己在第几级（``[[hint:N]]``，
  ``prompts/pieces/opening/hint_ladder.md``）。这里读出这个标记、从消息里去掉（成员看
  不到它），记一条 ``hint`` 事件；
- **输出检查**：回复里一段围栏代码块超过 ``OUTPUT_CHECK_LINES`` 行，就换成一句提示，
  记一条 ``output_check`` 事件。判据是启发式的（长代码块），不认识「本题的目标代码」——
  题目里没有一处写着目标文件，引导为主时一段长代码本身就是要拦的东西。

**只碰引导为主的题。** 一条回复里既没有标记、也没有长代码块，这里一次查询都不做，原样
放行；有的话才去读这个项目出自哪道题、那道题的教学配置（``protocol.resolve``，与每一轮
开场读的是同一条继承链），配置没打开就原样放行 —— 非引导模式、非课程项目的回复一个字
都不改。

落点是 AI 发言唯一的入口（``chat_send`` → ``api/routes/topics_messages``）：先筛，再落库，
落库之后用那条消息的 id 记事件。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.models import Project
from app.domain.task.models import Task, TaskGuideEvent
from app.domain.task.teaching import protocol_for_task

#: 一段围栏代码块有这么多行（不算空行）就算「大段代码」，换成提示。
OUTPUT_CHECK_LINES = 12

_HINT_MARK = re.compile(r"^\s*\[\[hint:([1-4])\]\][ \t]*\n?")
#: 一段围栏代码块：``` 或 ~~~ 开头的一行到配对的那一行；没合上的一段算到消息末尾。
_FENCE = re.compile(
    r"(?ms)^[ \t]*(?P<fence>`{3,}|~{3,})[^\n]*\n(?P<body>.*?)(?:^[ \t]*(?P=fence)[ \t]*$|\Z)"
)


def _replacement(lines: int) -> str:
    return (
        f"（这里原本有一段 {lines} 行的代码。这道题按「引导为主」辅导，完整代码不直接"
        "给出 —— 说说你打算怎么写、卡在哪一步，我们一步步来。）"
    )


@dataclass(frozen=True)
class Screened:
    """一条回复筛过之后的样子，和要记下的事。"""

    text: str
    #: 这条回复标的提示阶梯级数；没标（或阶梯没开）为空。
    hint: int | None = None
    #: 被换掉的每段代码各有几行。
    checked: list[int] = field(default_factory=list)
    #: 这个项目出自的题目；筛过配置时才有，记事件用。
    task_id: int | None = None

    @property
    def changed(self) -> bool:
        return self.hint is not None or bool(self.checked)


def screen(text: str, *, ladder: bool, check: bool) -> Screened:
    """Strip the ladder mark and replace long code blocks — only the guards
    that are on. Pure: what a reply turns into depends on nothing else."""
    hint: int | None = None
    if ladder:
        mark = _HINT_MARK.match(text)
        if mark is not None:
            hint = int(mark.group(1))
            text = text[mark.end() :]
    checked: list[int] = []
    if check:

        def swap(block: re.Match[str]) -> str:
            lines = sum(1 for line in block.group("body").splitlines() if line.strip())
            if lines < OUTPUT_CHECK_LINES:
                return block.group(0)
            checked.append(lines)
            return _replacement(lines)

        text = _FENCE.sub(swap, text)
    return Screened(text=text, hint=hint, checked=checked)


async def _teaching(session: AsyncSession, project_id: uuid.UUID):
    """The teaching in force for this project and the 题目 it came from, or
    ``None`` when it is no 题目's project."""
    project = await session.get(Project, project_id)
    task_id = getattr(project, "external_task_id", None)
    if project is None or task_id is None:
        return None
    task = await session.get(Task, task_id)
    if task is None:
        return None
    protocol = await protocol_for_task(session, task, project=project)
    return task.id, protocol.teaching


async def screen_reply(
    session: AsyncSession, *, project_id: uuid.UUID, text: str
) -> Screened:
    """An AI teammate's reply as it is to be posted in this project.

    Nothing that could be touched — no mark, no long block — returns at once,
    without a query. Otherwise the project's 题目 and its teaching decide which
    guard, if any, applies.
    """
    if not screen(text, ladder=True, check=True).changed:
        return Screened(text=text)
    found = await _teaching(session, project_id)
    if found is None:
        return Screened(text=text)
    task_id, teaching = found
    if not (teaching.ladder or teaching.checks_output):
        return Screened(text=text)
    screened = screen(text, ladder=teaching.ladder, check=teaching.checks_output)
    return Screened(
        text=screened.text,
        hint=screened.hint,
        checked=screened.checked,
        task_id=task_id,
    )


async def record(
    session: AsyncSession,
    *,
    screened: Screened,
    project_id: uuid.UUID,
    block_id: uuid.UUID | None,
    user_id: int | None,
) -> None:
    """Write down what the guards did to one posted reply. The caller commits."""
    if screened.task_id is None or not screened.changed:
        return
    now = datetime.now(UTC)
    rows = []
    if screened.hint is not None:
        rows.append(("hint", screened.hint))
    rows.extend(("output_check", lines) for lines in screened.checked)
    for kind, level in rows:
        session.add(
            TaskGuideEvent(
                task_id=screened.task_id,
                project_id=project_id,
                user_id=user_id,
                block_id=block_id,
                kind=kind,
                level=level,
                created_at=now,
            )
        )
    await session.flush()
