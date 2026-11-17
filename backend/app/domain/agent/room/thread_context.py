"""What a session in a channel is told about where it is: the channel's main
line, or one of its 支线 (``turn/intake/preparation/assembly.py``)."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.domain.agent.teammate_runs import teammate_runs
from app.domain.block.authorship import speaker
from app.domain.block.queries import awaiting_an_answer, latest_messages
from app.domain.prompts import piece
from app.domain.review.queries import latest_cards_by_task
from app.domain.room_task import presentation
from app.domain.room_task.models import TaskStatus
from app.domain.thread.reads import routine_runs_of, said_before
from app.domain.topic.models import Topic

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: How many of the main line's messages a session in the channel opens with:
#: what was being talked about before what it is handed.
THREAD_CONTEXT_MESSAGES = 10


def _line(block) -> str:
    return f"[id={block.id}] [{speaker(block)}] {block.content}"


async def channel_context(session: "AsyncSession", room: Topic, first) -> str:
    """What a session in a channel's main line is told about where it is: the
    channel, what it may do there, and what the main line said just before
    ``first``, the oldest message it is handed. The channel's tasks are a
    section of their own (`channel_tasks`)."""
    parts = [piece("context/channel", title=room.title)]
    earlier = (
        await said_before(session, first, limit=THREAD_CONTEXT_MESSAGES)
        if first is not None
        else []
    )
    if earlier:
        parts += ["", "在这之前，主线上说的是：", *map(_line, earlier)]
    return "\n".join(parts)


async def thread_context(
    session: "AsyncSession", room: Topic, root, *, routine_run: bool = False
) -> str:
    """What a 支线's session is told about where it is: the channel, the
    message the 支线 hangs under and what the main line said just before it.
    A routine's run is told that this turn may keep what it produces, and that
    the 支线 keeps nothing afterwards. The channel's tasks are a section of
    their own (`channel_tasks`)."""
    run = (await routine_runs_of(session, [root.id])).get(root.id)
    earlier = await said_before(session, root, limit=THREAD_CONTEXT_MESSAGES)
    if run:
        parts = [
            piece(
                "context/routine_run", title=room.title, routine=run.get("title", "")
            ),
            piece(
                "context/routine_run_now"
                if routine_run
                else "context/routine_run_after"
            ),
        ]
    else:
        parts = [
            piece("context/thread", title=room.title),
            "",
            "支线挂在主线的这条消息下面：",
            _line(root),
        ]
    if earlier:
        parts += ["", "这条消息之前，主线上说的是：", *map(_line, earlier)]
    return "\n".join(parts)


async def channel_tasks(
    session: "AsyncSession", room: Topic, root, *, names: Mapping[str, str]
) -> str:
    """The channel's tasks still open, as a session in its main line or in one
    of its 支线 reads them: number, title, the state its page shows (one of
    the seven status words), who asked for it and which AI teammate works it,
    members written as the roster names them (``names``, handle to name). In
    a 支线 (``root`` is the message it hangs under) the ones made from that
    message are marked: they are this conversation's own.

    A section of the session opening that is told again when it changes
    (`TRACKED_SECTIONS`): a task made while the session was already open
    otherwise never reached it, and the teammate, asked to do the work, made a
    second task for the same thing. Only the platform's facts are here, never
    a summary somebody wrote."""
    # deferred-import: tests replace this name on app.domain.room_task.services
    from app.domain.room_task.services import TaskService

    open_tasks = [
        task
        for task in await TaskService(session).list_in_room(room.id)
        if task.status == TaskStatus.open
    ]
    if not open_tasks:
        return piece("context/tasks_none")
    states = await _states(session, open_tasks)
    lead = (
        piece("context/tasks_rule") + piece("context/tasks_marks")
        if root is not None
        else piece("context/tasks_look")
    )
    return piece("context/tasks_head") + "\n".join(
        [
            lead,
            *(
                _task_line(task, states[task.id], names)
                + ("（从这条支线的消息建的）" if _made_from(task, root) else "")
                for task in open_tasks
            ),
        ]
    )


#: The seven status words (design-system §8.10), by the phrase a task's page
#: shows (`presentation.task_presentation`); the frontend's `taskState.STATUS`
#: is the same table.
_STATUS_WORDS = {
    "started": "未开始",
    "draft": "未开始",
    "running": "进行中",
    "waiting": "进行中",
    "bounced": "进行中",
    "idle": "进行中",
    "awaiting_answer": "待回答",
    "blocked": "待协助",
    "gate_running": "待审阅",
    "awaiting_checks": "待审阅",
    "fixing_checks": "待审阅",
    "resolving_conflict": "待审阅",
    "updating_branch": "待审阅",
    "awaiting_review": "待审阅",
    "checks_failed": "待审阅",
    "deploying": "待审阅",
    "deploy_blocked": "待审阅",
    "accepted": "已完成",
    "completed": "已完成",
    "closed": "已关闭",
    "archived": "已关闭",
}


async def _states(session: "AsyncSession", tasks) -> dict:
    """What each task's page says of it now (`presentation.task_presentation`)."""
    ids = [task.id for task in tasks]
    cards = await latest_cards_by_task(session, ids)
    asked = await awaiting_an_answer(session, ids)
    runs = await teammate_runs(session, tasks)
    now = datetime.now(UTC)
    return {
        task.id: presentation.task_presentation(
            presentation.facts_for_task(
                task,
                cards.get(task.id),
                awaiting_answer=task.id in asked,
                teammate_runs=runs.get(task.id),
            ),
            now=now,
        ).phrase
        for task in tasks
    }


def member(handle: str | None, names: Mapping[str, str]) -> str:
    """A member as a session reads one: 「名字（handle: x）」."""
    if not handle:
        return ""
    return f"{names.get(handle, handle)}（handle: {handle}）"


async def task_summary(session: "AsyncSession", task, names: Mapping[str, str]) -> str:
    """One task as the main line looks into it (`cheese_task(action=look)`):
    its line in the channel's list, where its work is, its latest delivery
    and the last thing said in it. Only the platform's facts."""
    phrase = (await _states(session, [task]))[task.id]
    lines = [_task_line(task, phrase, names)]
    if task.branch_name:
        lines.append(piece("context/look_branch", branch=task.branch_name))
    cards = await latest_cards_by_task(session, [task.id])
    card = cards.get(task.id)
    if card is not None:
        lines.append(
            piece(
                "context/look_delivery",
                subject=card.change_subject or "",
                status=str(card.status),
            )
        )
    said = await latest_messages(session, task.id, 1)
    if said:
        lines.append(piece("context/look_last", line=_line(said[-1])))
    return "\n".join(lines)


def _task_line(task, phrase: str, names: Mapping[str, str]) -> str:
    word = _STATUS_WORDS.get(phrase, "进行中")
    reviewer = task.reviewer_handle or task.owner_handle
    state = (
        piece("context/state_review", who=member(reviewer, names))
        if word == "待审阅" and reviewer
        else word
    )
    proposer = task.reporter_handle or task.owner_handle
    return piece(
        "context/task_line",
        number=f"#{task.number}" if task.number is not None else "",
        title=task.title,
        state=state,
        proposer=member(proposer, names) or "未记录",
        teammate=member(task.agent_handle, names) or "未指定",
    )


def _made_from(task, root) -> bool:
    return root is not None and task.upgraded_from_block_id == root.id
