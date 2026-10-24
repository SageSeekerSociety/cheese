"""What a task's AI teammate hears about its own task.

Whatever a person can see change on a task — where it stands on the board, who
has it, what it is called, what became of its card — its AI teammate learns
too. Otherwise the two talk past each other: someone says 「作废了，重新递一版」
to a teammate that still believes its card is waiting on review.

Who made the change decides where the teammate hears it.

- **The teammate itself** (it filed a card, reported its state, closed the
  task): the reply of the tool it called says what that did to the task. It
  does not come through here.
- **Anyone else** — a person, or the platform on its own: the code that makes
  the change says it here, in one of two kinds.

  - `act` — there is something for the teammate to do now. The instruction
    goes on the delivery ledger for the task's own session, which wakes it
    (`delivery.agent.record_task_instruction`), and the line people read lands
    in the task's conversation in the same transaction. The caller dispatches
    the ledger once it has committed (`delivery.agent.dispatch_pending`).
  - `inform` — nothing to do, but its picture of the task is out of date. The
    sentence rides on a block of the task's conversation
    (`AGENT_NOTICE_META_KEY`), goes into the teammate's next input whatever
    opens it, and is stamped read there (`block.history.notices_after`).
    Nothing is woken. Where the change already writes a line people read, the
    sentence goes on that line (`informing`); where it writes none, on a line
    the room does not show (`inform`) — people see the change where it was made.

The sentences are here, one function each, so what the teammate is told about
its task can be read in one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.adapters import platform_line
from app.domain.block.models import AGENT_NOTICE_META_KEY, Block
from app.domain.delivery_events import REPLACES, DeliveryEvent
from app.domain.event_about import EventAbout
from app.domain.notification.models import NotificationType
from app.domain.room_task.models import Task, TaskStatus

if TYPE_CHECKING:
    from app.domain.room_task.ports import Announce, RecordInstruction
    from app.domain.topic.models import Topic


# —— the two kinds ————————————————————————————————————————————————


async def act(
    session: AsyncSession,
    *,
    topic: Topic,
    task: Task | None,
    said: str,
    line: str,
    meta: dict,
    announce: Announce,
    record_instruction: RecordInstruction | None,
    replaces: str | None = None,
) -> Block | None:
    """Say ``line`` in the task's conversation (the room's, for a card filed for
    the room itself) and give ``said`` to the task's teammate as an instruction.

    Only an open task's teammate is woken: a closed task has nothing left to
    work on, so its people get the line and nobody is summoned.

    ``replaces`` names what the instruction is the current state of; an earlier
    one with the same name the teammate has not been handed yet is dropped for
    it (`delivery_events.REPLACES`)."""
    block = await announce(
        session,
        place_id=topic.id,
        task_id=task.id if task is not None else None,
        content=line,
        meta=meta,
    )
    if (
        block is not None
        and task is not None
        and task.status == TaskStatus.open
        and record_instruction is not None
    ):
        await record_instruction(
            session,
            DeliveryEvent(
                id=block.id,
                type=NotificationType.ROOM_NOTICE,
                payload={
                    "projectId": str(topic.project_id),
                    "topicId": str(topic.id),
                    **({REPLACES: replaces} if replaces else {}),
                },
                occurred_at=block.created_at,
            ),
            task_id=task.id,
            content=said,
        )
    return block


def informing(meta: dict | None, said: str) -> dict:
    """``meta`` for a line people read in the task's conversation, carrying
    ``said`` to the teammate's next input as well."""
    return {**(meta or {}), AGENT_NOTICE_META_KEY: said}


async def inform(session: AsyncSession, task: Task, said: str) -> None:
    """Tell ``task``'s teammate ``said`` in its next input, on a line the room
    does not show: what changed is already in front of people where they
    changed it (the board, the task header, the card)."""
    await platform_line(
        session,
        about=EventAbout.task,
        project_id=task.project_id,
        room_id=task.room_id,
        task_id=task.id,
        author="system",
        content=said,
        meta={"in_room": False, AGENT_NOTICE_META_KEY: said},
    )


# —— what it is told ——————————————————————————————————————————————

#: Where a returned or voided card leaves the task: back in its teammate's
#: hands (`progress.back_to_work`), able to report again.
BACK_TO_WORK = (
    "看板上这件任务回到了「在做」，cheese_task(action=state) 又能报了"
    "（你递卡之前报的那一条在递卡时已经清掉）。"
)

#: The closed-task ending of an instruction: the line still goes out, but there
#: is nothing left to work on.
TASK_CLOSED = "原任务已关闭或不存在；如需继续修改，请由新任务承接。"


def card_voided(
    *, by: str, reason: str, task_id, pr: int | None, open_task: bool
) -> str:
    """A person voided the card waiting on this task's review."""
    why = f"理由：{reason}" if reason else "没写理由。"
    if not open_task:
        return f"@{by} 作废了任务 {task_id} 的验收卡。{why}\n{TASK_CLOSED}"
    kept = f"PR #{pr} 没有关，还开着；重新递卡会沿用它。" if pr is not None else ""
    return (
        f"@{by} 作废了任务 {task_id} 的验收卡。{why}\n"
        f"这一版不再有人审阅，也不会因为这张卡被采纳。{kept}{BACK_TO_WORK}\n"
        "接下来：照理由需要改，就先执行 "
        f'cd "$(cheese worktree {task_id})" 进入任务目录，改完重新递卡；'
        "看不出要改什么、或者拿不准为什么作废，先在对话里问清楚，别猜着重做。"
    )


def reviewer_changed(*, by: str | None, reviewer: str) -> str:
    who = f"@{by} 把" if by else "平台把"
    return f"{who}这件任务的验收卡改由 @{reviewer} 审阅，卡照旧在等审阅。"


def accept_revoked(*, by: str, pr: int | None) -> str:
    what = f"PR #{pr} 的采纳记录" if pr is not None else "这次交付的采纳记录"
    return f"@{by} 撤回了{what}。撤回的只是记录，已经合并的代码没有撤。"


def task_closed(*, by: str, conclusion: str | None) -> str:
    ended = f"，结论：{conclusion}" if conclusion else "，没有留结论"
    return (
        f"@{by} 关闭了这件任务{ended}。"
        "你在这里还在跑的活已经停下；之后不再改动项目、不递卡。"
    )


def task_closed_late() -> str:
    """The last step landed and the write-up was not done in time
    (`closing.close_overdue`)."""
    return (
        "平台关闭了这件任务：最后一步采纳之后，写总结、关闭任务的那一步没有在"
        "时限内做完。你在这里还在跑的活已经停下。"
    )


def task_closed_unasked() -> str:
    """The last step landed and the instruction to write it up never reached
    the teammate (`closing.close_after_summary`)."""
    return (
        "平台关闭了这件任务：最后一步已经采纳，叫你写总结、关闭任务的那条指令"
        "一直没能交到你手上，平台没再等，直接关了。"
    )


def task_reopened(*, by: str, task_id, new_branch: bool) -> str:
    moved = (
        "工作目录换到了一条从项目最新代码切出的新分支：动手之前先执行 "
        f'cd "$(cheese worktree {task_id})"。'
        if new_branch
        else ""
    )
    return (
        f"@{by} 重新打开了这件任务，你之前报的状态已经清掉；"
        f"开工时再用 cheese_task(action=state) 报。{moved}"
    )


def task_renamed(*, by: str | None, title: str, previous: str) -> str:
    who = f"@{by} 把" if by else "平台把"
    if not previous:
        return f"{who}这件任务命名为「{title}」。"
    return f"{who}这件任务改名为「{title}」（原来是「{previous}」）。"


def owner_changed(*, by: str, owner: str) -> str:
    return f"@{by} 把这件任务交给了 @{owner}，现在由 @{owner} 负责。"


def contributors_changed(*, by: str, added: list[str], removed: list[str]) -> str:
    parts = []
    if added:
        parts.append("加了协作者 " + "、".join(f"@{h}" for h in added))
    if removed:
        parts.append("去掉了协作者 " + "、".join(f"@{h}" for h in removed))
    if removed == [by] and not added:
        return f"@{by} 退出了这件任务的协作者。"
    return f"@{by} " + "，".join(parts) + "。"


def teammate_changed(*, by: str, seat: str | None) -> str:
    who = f"<@{seat}>" if seat else "所在频道的 AI 队友"
    return f"@{by} 把这件任务改由 {who} 来做。"
