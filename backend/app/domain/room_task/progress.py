"""What a task's AI teammate says about where its work stands, and the one
thing the platform adds beside it.

The board used to infer 「运行中」 from open turn intervals, which put a
teammate waiting on a build or a background command back in 「进行中」 the
moment its turn returned. Once its session stops, only the teammate knows
whether it is still on the work, so it says so (`cheese_task(action=state)`), and the
board shows what it said (`presentation.task_presentation`) — except where a
checkable fact speaks: a card in review, or the task's teammate's session
running in a run it has not reported in.

Two writers, two columns each, and neither touches the other's:

- the teammate's report: `set_progress` — state, note, when, who, and the run
  of its session it was made in, as the session's runner names it (the
  ``X-Cheese-Run`` header its platform call carries, `RUN_HEADER`). Only while
  no card waits on the task's review (`PUT /topics/{task}/task/progress`
  refuses it then): the card says where the task stands. The platform's own
  actions write the same columns: a card filed or the task reopened clears the
  report (`clear`), a card returned or voided puts the task back to work
  (`back_to_work`). Each of those tells the teammate (`heard`); filing is its
  own act, and its tool's reply says so;
- the session ending after that report: `note_agent_left` — the board puts
  「已离开」 beside the report, until the same teammate's session runs in the
  task again (`note_agent_back`). It never rewrites the report: a teammate that
  said 「在等 CI」 and then left is still the last word on that work. Both take
  the teammate that reported (`progress_set_by`): a task's conversation may
  hold other agents' sessions too, and one of those ending must not say the
  task's teammate left. The teammate is not told: the mark is about its own
  session being gone, and the session coming back is what takes it away, so by
  the time it could read a word of it the mark is no longer there.
"""

import re
import uuid
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.room_task.live import rows_written
from app.domain.room_task.models import Task, TaskProgress, TaskStatus

#: The header a platform call names its session's run in. The same name as
#: `agent.executor_transport.RUN_HEADER`, which sends it: that file ships to
#: the session's host alone and imports nothing of this one.
RUN_HEADER = "X-Cheese-Run"

#: The note's width: one line on the task header. The column is this wide.
NOTE_MAX_CHARS = 200


def clear(task: Task) -> None:
    """What the teammate reported is over: a card was filed (the card says
    where the task stands now, 等审), or a person reopened the task (开启).
    「已离开」 was written beside that report, so it goes with it."""
    task.progress_state = None
    task.progress_note = None
    task.progress_set_at = None
    task.progress_set_by = None
    _forget_run(task)
    task.agent_left_at = None


def back_to_work(task: Task, *, at: datetime) -> None:
    """The card was returned or voided: the task is back in its teammate's
    hands (在做). Nobody reported it, so nobody is named as having said so and
    nobody's session is being watched (``progress_set_by`` stays empty): the
    mark that sat beside the report the card cleared went with it."""
    task.progress_state = TaskProgress.working
    task.progress_note = None
    task.progress_set_at = at
    task.progress_set_by = None
    _forget_run(task)


def _forget_run(task: Task) -> None:
    task.progress_run_harness = None
    task.progress_run_epoch = None
    task.progress_run_seq = None


#: ``harness/epoch/seq``, the harness no wider than its column.
_RUN = re.compile(r"([a-z0-9-]{1,32})/([0-9]{1,18})/([0-9]{1,18})")


def parse_run(raw: str | None) -> tuple[str, int, int] | None:
    """The run a platform call says it is made in (`RUN_HEADER`,
    ``harness/epoch/seq``), or None when it names none or names it in another
    shape."""
    found = _RUN.fullmatch(raw or "")
    if found is None:
        return None
    harness, epoch, seq = found.groups()
    return harness, int(epoch), int(seq)


def set_progress(
    task: Task,
    state: TaskProgress,
    *,
    note: str | None,
    by: str,
    at: datetime,
    run: tuple[str, int, int] | None,
) -> None:
    """Record the teammate's report, and the run of its session it was made
    in as its runner named it (``parse_run``; None when the call named none).
    Reporting again means it is back, so the 「已离开」 beside an older report
    goes."""
    task.progress_state = state
    task.progress_note = (note or "").strip() or None
    task.progress_set_at = at
    task.progress_set_by = by
    if run is None:
        _forget_run(task)
    else:
        (
            task.progress_run_harness,
            task.progress_run_epoch,
            task.progress_run_seq,
        ) = run
    task.agent_left_at = None


async def note_agent_left(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    by: str,
    at: datetime,
) -> bool:
    """The session of ``by`` ended: mark ``by``'s last report, leaving the
    report as it was. ``conversation_id`` is the task's own conversation, whose
    id is the task's. An open task only — a closed one says how it ended. When
    the report is not ``by``'s (another agent working in the same task's
    conversation, its exit is not the teammate's) nothing is marked: the mark
    would name the teammate that did not leave. Says whether anything was
    marked; a conversation that is not a task marks nothing."""
    room_id = await session.scalar(
        update(Task)
        .where(
            Task.id == conversation_id,
            Task.status == TaskStatus.open,
            Task.progress_set_by == by,
        )
        .values(agent_left_at=at)
        .returning(Task.room_id)
    )
    if room_id is None:
        return False
    rows_written(session, conversation_id, room_id)
    return True


async def note_agent_back(
    session: AsyncSession, conversation_id: uuid.UUID, *, by: str
) -> bool:
    """``by``'s session is running in this task again: the 「已离开」 beside
    ``by``'s last report goes, and the report stays. Another agent's session
    running beside that report leaves the mark as it was — ``by`` is still
    gone. Says whether there was one to take away."""
    room_id = await session.scalar(
        update(Task)
        .where(
            Task.id == conversation_id,
            Task.status == TaskStatus.open,
            Task.progress_set_by == by,
            Task.agent_left_at.is_not(None),
        )
        .values(agent_left_at=None)
        .returning(Task.room_id)
    )
    if room_id is None:
        return False
    rows_written(session, conversation_id, room_id)
    return True
