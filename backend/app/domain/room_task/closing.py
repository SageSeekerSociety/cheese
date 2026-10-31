"""A task whose last delivery landed closes once its AI teammate has written it up.

Accepting the last step does not close the task on the spot. The task stays
open, with `closing_since` set, while its AI teammate is told to bring the task
document up to date, say what came of the task, and close it with the close a
task's own session has (`cheese_task(action=close)`); to everyone looking it already
reads 已采纳 (`presentation`). It closes when that close is called — the one
fact here that says the write-up was done — or, without it, when the instruction
for it was given up on (`delivery.timer.give_up_stale`), or, whatever
happened to it, `CLOSE_AT_THE_LATEST` after it landed.

Neither of the platform's own closes — the instruction given up on, the
deadline — closes a task while something besides the write-up is being done or
asked for in it (`unfinished`): a session at work there that has not read the
write-up instruction, a message there still waiting to be read, or one read
into work that then failed. A close stops the work still running and drops the
request still waiting, and work begun after the delivery was handed over is
work that delivery does not contain. Such a task is left as it is, and either
goes on (`review.task_landing.go_on_while_working`) or closes at the first of
those moments that finds nothing left: the deadline is checked on every tick
of the task closing sweep (`review.pr_poll.sweep_closings`), so a task still
waiting past it closes on the first tick after its teammate's session stops.
Nothing holds it past `STOP_WORK_AFTER`: then it closes, stopping that work.
Each stopped session keeps a run record under its own seat (`_keep_stopped`),
and the room hears what was not delivered
(`review.room_notices.tell_room_undelivered`).

Every fact read here is one the platform can check: what a session's runner
declared it is doing (`session_states`), which input a session read
(`deliveries`, the blocks it consumed), which message is still waiting. That a
turn interval ended is not one: the platform cannot tell from it whether the
instruction that started it was followed.

Every way a task closes goes through `close_task` — its owner, its own
session, the instruction for it given up on, the deadline above — and so every
one of them stops the work still running in the task once the close commits.
A session the close did not reach is stopped by the session's holder once the
task has been closed for `CLOSING_TAIL`
(`agent.turn_inputs.stop_work_in_closed_tasks`).
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import Uuid, column, event, select, table
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import background
from app.core.db import after_own_commit
from app.core.sentences import say
from app.domain.block.authorship import participant_blocks
from app.domain.block.models import CONSUMED_TURN_META_KEY, Block, BlockKind
from app.domain.block.queries import queued_messages, read_failed_messages
from app.domain.notice_meta import EVENT_TURN_TIMEOUT, SEVERITY_WARN, WHO_HUMAN, notice
from app.domain.room_task import heard
from app.domain.room_task.checkouts import after_close
from app.domain.room_task.lock_order import (
    lock_task_for_change,
    lock_tasks_for_change,
)
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.ports import SweepCheckouts
from app.domain.room_task.services import said_title
from app.domain.run_record.service import record as keep_record

logger = logging.getLogger("cheesex.room_task")

#: Half an hour is how long an instruction to an AI teammate is retried
#: (`delivery.agent.GIVE_UP_AFTER`); a write-up takes a few minutes.
CLOSE_AT_THE_LATEST = timedelta(minutes=45)

#: Marks the instruction to write a finished task up, in the delivery's payload.
CLOSES_TASK = "closesTask"

#: How long past `CLOSE_AT_THE_LATEST` a task is left open for work its AI
#: teammate's session is still doing in it. A session that keeps working and
#: never stops would hold the task forever; past this the task closes and that
#: work is stopped, and the room hears what was not delivered.
STOP_WORK_AFTER = CLOSE_AT_THE_LATEST + timedelta(hours=2)

#: How far back the pending-message scan still starts a waiting message
#: (`AgentWorkRunner.ORPHAN_STALE_S`, which this module cannot import): one
#: older than this is not going to be read, so it does not hold a task.
QUEUED_FOR = timedelta(hours=2)

# The facts `unfinished` reads, as bare tables: the sessions' declared state
# belongs to the agent domain, the instructions and the inputs to the delivery
# domain, and both of those import this module.
_sessions = table(
    "session_states",
    column("conversation_id", Uuid),
    column("agent_handle"),
    column("worker_status"),
    column("changed_at"),
)
_instructions = table(
    "deliveries",
    column("conversation_id", Uuid),
    column("recipient_handle"),
    column("payload", JSONB),
    column("state"),
    column("recorded_at"),
)
_inputs = table(
    "native_inputs",
    column("conversation_id", Uuid),
    column("recipient_handle"),
    column("echoed_at"),
    column("block_ids", JSONB),
    column("held_block_ids", JSONB),
)

#: What a session's runner declares while its session is at work
#: (`harness.driven.lifecycle.RUNNING`, which this module cannot import).
_RUNNING = "running"


@dataclass(frozen=True)
class Unfinished:
    """Something a task still had going or waiting when it closed: what was
    being done or asked for (``prompt``), and the seat whose session the close
    stopped (``seat``). A message no session had read has no work to name."""

    prompt: str
    seat: str | None

    @property
    def gist(self) -> str:
        """``prompt`` on one line, short enough for a notice's detail row."""
        line = " ".join(self.prompt.split())
        return line if len(line) <= 120 else line[:119] + "…"


async def unfinished(
    session: AsyncSession,
    task_id: uuid.UUID,
    *,
    since: datetime | None = None,
    by: str | None = None,
    seat: str | None = None,
) -> list[str]:
    """What is still being done or asked for in ``task_id`` besides its
    write-up: the prompts of `unfinished_work`. Empty when there is none."""
    return [
        left.prompt
        for left in await unfinished_work(
            session, task_id, since=since, by=by, seat=seat
        )
    ]


async def unfinished_work(
    session: AsyncSession,
    task_id: uuid.UUID,
    *,
    since: datetime | None = None,
    by: str | None = None,
    seat: str | None = None,
) -> list[Unfinished]:
    """What is still being done or asked for in ``task_id`` besides its
    write-up, each item with the seat whose session is doing it.

    Work: a seat whose session its runner declares at work in the task
    (`session_states`), each seat on its own. Without ``since``, that is work
    besides the write-up unless that seat is the one that read the write-up
    instruction given since the task began closing: before it has, and for
    every other seat, it is doing something else. With ``since``, it counts
    only when that session, while at work this time, read a message for ``by``
    sent after ``since`` — a request a person, or the teammate itself, made
    after the delivery went up, which the delivery does not contain. A message
    the platform wrote is not such a request, nor is one the session read and
    answered before it last stopped.

    Asked for: a message for ``by`` (any agent's when None) the pending-message
    scan would still start (`block.queries.queued_messages`), and one that was
    read into work that then failed (`block.queries.read_failed_messages`):
    read is not answered.

    ``by``: only messages for that agent (`ResolvedAgent.handle`, the handle a
    message names its recipient by); ``seat``: only that agent's session, by
    the handle it acts under (the one its runner registers,
    `agent_instance_handle`). The two are the same agent in the two spellings
    the two facts are kept in."""
    now = datetime.now(UTC)
    left = await _at_work(session, task_id, since=since, by=by, seat=seat)
    left += [
        Unfinished(message.content, None)
        for message in await session.scalars(queued_messages(now - QUEUED_FOR, task_id))
        if _for(message, by)
    ]
    left += [
        Unfinished(message.content, None)
        for message in await session.scalars(
            read_failed_messages(now - QUEUED_FOR, task_id)
        )
        if _for(message, by)
    ]
    return left


async def _at_work(
    session: AsyncSession,
    task_id: uuid.UUID,
    *,
    since: datetime | None,
    by: str | None,
    seat: str | None,
) -> list[Unfinished]:
    """The seats whose session is at work in ``task_id`` on something besides
    its write-up (`unfinished_work`), each judged on its own: one seat having
    read the write-up says nothing about what another is doing."""
    query = select(_sessions.c.agent_handle, _sessions.c.changed_at).where(
        _sessions.c.conversation_id == task_id,
        _sessions.c.worker_status == _RUNNING,
    )
    if seat is not None:
        query = query.where(_sessions.c.agent_handle == seat)
    running = (await session.execute(query)).all()
    if not running:
        return []
    if since is None:
        wrote_up = await _write_up_readers(session, task_id)
        prompt = await _last_read(session, task_id)
        return [
            Unfinished(prompt, handle)
            for handle, _running_since in running
            if handle not in wrote_up
        ]
    left = []
    for handle, running_since in running:
        asked = await _asked_since(
            session, task_id, handle, by, since=since, read_since=running_since
        )
        if asked is not None:
            left.append(Unfinished(asked, handle))
    return left


async def _asked_since(
    session: AsyncSession,
    task_id: uuid.UUID,
    seat: str,
    by: str | None,
    *,
    since: datetime,
    read_since: datetime,
) -> str | None:
    """The newest message for ``by`` that a person (or the teammate itself)
    sent after ``since`` and that ``seat``'s session read while it has been at
    work this time (since its runner last declared it running): what it is
    working on that the delivery does not contain. None when there is none —
    a message it read and answered before it last stopped is done with."""
    batches = (
        await session.execute(
            select(_inputs.c.block_ids, _inputs.c.held_block_ids).where(
                _inputs.c.conversation_id == task_id,
                _inputs.c.recipient_handle == seat,
                _inputs.c.echoed_at >= read_since,
            )
        )
    ).all()
    ids = {uuid.UUID(block) for batch in batches for part in batch for block in part}
    if not ids:
        return None
    asked = [
        message
        for message in await session.scalars(
            select(Block)
            .where(
                Block.id.in_(ids),
                Block.kind == BlockKind.message,
                Block.created_at > since,
                participant_blocks(),
            )
            .order_by(Block.created_at)
        )
        if _for(message, by)
    ]
    return asked[-1].content if asked else None


async def _write_up_readers(session: AsyncSession, task_id: uuid.UUID) -> set[str]:
    """The seats that read the write-up instruction given since the task began
    closing: each one's echo settled its delivery
    (`delivery.receipts.record_receipt`)."""
    task = await session.get(Task, task_id)
    if task is None or task.closing_since is None:
        return set()
    return set(
        await session.scalars(
            select(_instructions.c.recipient_handle).where(
                _instructions.c.conversation_id == task_id,
                _instructions.c.payload[CLOSES_TASK].as_boolean().is_(True),
                _instructions.c.recorded_at >= task.closing_since,
                _instructions.c.state == "received",
            )
        )
    )


async def _last_read(session: AsyncSession, task_id: uuid.UUID) -> str:
    """The last message a session in ``task_id`` read: what its work is about,
    as near as the platform can say."""
    return (
        await session.scalar(
            select(Block.content)
            .where(
                Block.conversation_id == task_id,
                Block.kind == BlockKind.message,
                Block.meta[CONSUMED_TURN_META_KEY].as_string().is_not(None),
            )
            .order_by(Block.created_at.desc())
            .limit(1)
        )
        or ""
    )


def _for(message: Block, by: str | None) -> bool:
    """Whether a message is for ``by``'s work (any agent's when None)."""
    return (
        by is None
        or ((message.meta or {}).get("agent_recipient") or {}).get("handle") == by
    )


class StopsWork(Protocol):
    """What stops the work a teammate still has running in a conversation."""

    async def stop_work(self, conversation_id: uuid.UUID) -> None: ...


async def close_task(
    session: AsyncSession,
    task: Task,
    work: StopsWork | None,
    *,
    conclusion: str | None = None,
    machines: SweepCheckouts | None = None,
) -> Task:
    """Close ``task``; with a `conclusion` it is done, without one it was put
    down. Once ``session`` commits, whatever a teammate is still doing in it
    is stopped through ``work``: a closed task is work that is over, and a
    session left working in it goes on paying for the model while every write
    it makes is refused. ``work`` is None only when the session that would be
    stopped is the one closing the task.

    ``machines`` goes to `after_close` unchanged: the call that reaches a
    machine to remove this room's closed checkouts (`ports.SweepCheckouts`).

    Idempotent: closing a closed task keeps the first `closed_at` — the
    moment it stopped being live is a fact, not a re-statement of intent.
    """
    if conclusion is not None:
        task.conclusion = conclusion
    # A closed task is not waiting for its write-up any more, so the window
    # goes with it. Left behind, it would stay on a task its owner reopens and
    # read there as 已采纳 while the task is open — the one state that is
    # listed in neither 「未完成」 nor 「已关闭」.
    task.closing_since = None
    if task.status is not TaskStatus.closed:
        task.status = TaskStatus.closed
        task.closed_at = datetime.now(UTC)
        after_close(session, task.room_id, machines=machines)
        if work is not None:
            _stop_after_commit(session, task.id, work)
    await session.flush()
    return task


def _stop_after_commit(session: AsyncSession, task_id: uuid.UUID, work: StopsWork):
    """Stop ``task_id``'s work once ``session`` commits; a rollback stops
    nothing, since the task it would stop is still open."""
    pending: dict[uuid.UUID, StopsWork] | None = session.info.get("closing_tasks")
    if pending is None:
        pending = session.info["closing_tasks"] = {}

        def committed() -> None:
            for closed, stopper in list(pending.items()):
                background.spawn(
                    stopper.stop_work(closed), name="stop a closed task's work"
                )
            pending.clear()

        def rolled_back(_session, transaction) -> None:
            # Only the session's own rollback undoes the close. A savepoint
            # rolled back on its own — a courtesy line that could not be said —
            # leaves the close standing, and its commit still stops the work.
            if transaction is not None and transaction.parent is not None:
                return
            pending.clear()

        after_own_commit(session, committed, once=False)
        event.listen(session.sync_session, "after_soft_rollback", rolled_back)
    pending[task_id] = work


async def close_after_summary(
    session: AsyncSession,
    task_id: uuid.UUID,
    work: StopsWork | None,
    *,
    machines: SweepCheckouts | None = None,
) -> Task | None:
    """Close ``task_id`` without its write-up, once the instruction for it was
    given up on: if it is still waiting for one and nothing else is being done
    or asked for in it (`unfinished`, module docstring). Returns the task it
    closed; the pages showing it hear from `room_task.live`. ``machines`` is
    `close_task`'s."""
    # Under the task lock order (`lock_order`): a message being sent in the
    # task holds its conversation's numbering until it commits, so it is seen
    # waiting below rather than stopped by the close.
    task = await lock_task_for_change(session, task_id)
    if task is None or task.closing_since is None:
        return None
    if await unfinished(session, task.id):
        return None
    await heard.inform(session, task, heard.task_closed_unasked())
    return await close_task(session, task, work, machines=machines)


@dataclass(frozen=True)
class Overdue:
    """A task `close_overdue` closed, and what was still being done or asked
    for in it when it did (`unfinished_work`): empty unless the task was
    closed on work at `STOP_WORK_AFTER`, which stopped that work undelivered.
    Each item names the seat whose session it was, so the stopped work is not
    lost with the task."""

    task: Task
    undelivered: list[Unfinished]


async def close_overdue(
    session: AsyncSession, work: StopsWork, *, machines: SweepCheckouts | None = None
) -> list[Overdue]:
    """Close every task still waiting for a write-up it is not getting, unless
    something else is being done or asked for in it — and, past
    `STOP_WORK_AFTER`, that one too. ``machines`` is `close_task`'s."""
    now = datetime.now(UTC)
    overdue = list(
        await session.scalars(
            select(Task.id).where(
                Task.closing_since.is_not(None),
                Task.closing_since < now - CLOSE_AT_THE_LATEST,
            )
        )
    )
    closed = []
    # Every one of them at once, before any is looked at (`lock_order`); as in
    # `close_after_summary`, a message being sent in one is seen, not stopped.
    for task in await lock_tasks_for_change(session, overdue):
        if task.closing_since is None:
            continue
        left = await unfinished_work(session, task.id)
        if left and task.closing_since >= now - STOP_WORK_AFTER:
            continue
        await heard.inform(session, task, heard.task_closed_late())
        closed.append(
            Overdue(await close_task(session, task, work, machines=machines), left)
        )
        await _keep_stopped(session, task, left)
    return closed


async def _keep_stopped(
    session: AsyncSession, task: Task, stopped: list[Unfinished]
) -> None:
    """Keep a run record under each stopped session's own seat.

    The room hears one line about the task (`review.room_notices`), and one
    line is right for a room. The stopped work needs a record of its own: it
    was stopped without being finished, and once the task is closed nothing
    else in it will say whose work that was.

    A message nobody read is nobody's work and gets no record; the room line
    still names it. Best effort: a record cannot undo the close it is about, so
    a write that fails is logged and the close stands.
    """
    if not any(work.seat is not None for work in stopped):
        return
    try:
        async with session.begin_nested():
            for work in stopped:
                if work.seat is None:
                    continue
                await keep_record(
                    session,
                    conversation_id=task.id,
                    content=say("taskClosedUndelivered", title=said_title(task)),
                    meta=notice(
                        EVENT_TURN_TIMEOUT,
                        severity=SEVERITY_WARN,
                        who=WHO_HUMAN,
                        detail=say("taskClosedUndeliveredDetail", items=[work.gist]),
                        detail_label=say("labelDetails"),
                    ),
                    seat=work.seat,
                )
    except Exception:  # the close stands; this is its record
        logger.warning(
            "could not keep the stopped work of task %s", task.id, exc_info=True
        )
