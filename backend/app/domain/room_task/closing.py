"""A task whose last delivery landed closes once its AI teammate has written it up.

Accepting the last step does not close the task on the spot. The task stays
open, with `closing_since` set, while its AI teammate takes one more turn to
bring the task document up to date and say what came of the task; to everyone
looking it already reads 已采纳 (`presentation`). It closes when that turn ends
(`turns_ended`), or when the instruction for it ends without a turn running
(`delivery.agent.run_attempt`, `delivery.timer.give_up_stale`), or, whatever
happened to it, `CLOSE_AT_THE_LATEST` after it landed.

None of those closes a task while something besides the write-up is being
done or asked for in it (`unfinished`): a turn running there, or a message
there still waiting for its turn. A close stops the work still running and
drops the request still waiting, and work begun after the delivery was handed
over is work that delivery does not contain. Such a task is left as it is, and
either goes on (`review.task_landing.go_on_while_working`) or closes at the
first of those moments that finds nothing left: the deadline is checked on
every tick of the landing watch, so a task still waiting past it closes on the
first tick after its teammate stops. A turn that has said nothing for the
orphan sweep's limit does not hold it, and nothing holds it past
`STOP_WORK_AFTER`: then it closes, stopping that work, and the room hears what
was not delivered (`review.landing_watch`).

Closing after the turn rather than before keeps every rule about closed tasks
as it is: a closed task takes no turns and its document is read-only, and
nothing here needs an exception to either.

Every way a task closes goes through `close_task` — its owner, its own
session, the summary turn ending, the instruction for it given up on, the
deadline above — and so every one of them stops the work still running in the
task once the close commits. The summary turn ending leaves nothing running
that `unfinished` counts; a turn it did not count, gone silent, is stopped by
the session's holder once the task has been closed for `CLOSING_TAIL`
(`agent.turn_inputs.stop_work_in_closed_tasks`).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import Uuid, column, event, func, or_, select, table
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import background
from app.core.config import settings
from app.core.db import after_own_commit
from app.domain.block.models import Block
from app.domain.block.queries import queued_messages
from app.domain.room_task.checkouts import after_close
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.ports import SweepCheckouts

#: Half an hour is how long an instruction to an AI teammate is retried
#: (`delivery.agent.GIVE_UP_AFTER`); a summary turn is a few minutes.
CLOSE_AT_THE_LATEST = timedelta(minutes=45)

#: Marks the instruction whose turn ends the task, in the delivery's payload.
CLOSES_TASK = "closesTask"

#: How long past `CLOSE_AT_THE_LATEST` a task is left open for work its AI
#: teammate is still doing in it. A turn that has gone silent for the orphan
#: sweep's limit no longer counts (`unfinished`), but one that keeps talking
#: and never ends would hold the task forever; past this the task closes and
#: that work is stopped, and the room hears what was not delivered.
STOP_WORK_AFTER = CLOSE_AT_THE_LATEST + timedelta(hours=2)

#: How far back the pending-message scan still starts a waiting message
#: (`AgentWorkRunner.ORPHAN_STALE_S`, which this module cannot import): one
#: older than this is not going to get its turn, so it does not hold a task.
QUEUED_FOR = timedelta(hours=2)

# The facts `unfinished` reads, as bare tables: the turn intervals belong to
# the agent domain and the instructions to the delivery domain, and both of
# those import this module (`agent.liveness` reads `threads` the same way).
_turns = table(
    "agent_turns",
    column("id", Uuid),
    column("continuation_id", Uuid),
    column("conversation_id", Uuid),
    column("agent_handle"),
    column("author"),
    column("content"),
    column("started_at"),
    column("delivered_at"),
    column("stopped_at"),
)
_instructions = table(
    "deliveries",
    column("attempt_id", Uuid),
    column("conversation_id", Uuid),
    column("payload", JSONB),
)


async def unfinished(
    session: AsyncSession,
    task_id: uuid.UUID,
    *,
    since: datetime | None = None,
    by: str | None = None,
) -> list[str]:
    """What is still being done or asked for in ``task_id`` besides its
    write-up: the prompt of each turn open in it, then each message there
    waiting for a turn. Empty when there is none.

    A turn counts from the moment its prompt reached the session
    (`agent.liveness.running_tasks` asks the same of the intervals) until it
    stops; one a person began counts from the moment it is taken on, so
    their message waiting for the session behind another turn is not lost to
    a close. It counts while it has said something within the orphan sweep's
    own limit (`settings.turn_silence_timeout_s`), or started within it: an
    interval left open by a turn that died without closing it is not work. The
    write-up is the turn an instruction marked `CLOSES_TASK` started: the
    turn's id is that instruction's attempt, and a resumed one keeps it as its
    `continuation_id`. A waiting message is one the pending-message scan
    would still start (`block.queries.queued_messages`) and no open turn has
    taken.

    ``since``: only turns started after it. ``by``: only what is for that
    agent (`ResolvedAgent.handle`) and was not begun by the platform — turns
    of its own that a person or the agent itself started, and messages
    addressed to it. A turn not yet assembled has no seat on it yet, and one
    a person began then is taken as the teammate's."""
    now = datetime.now(UTC)
    query = select(
        _turns.c.id, _turns.c.continuation_id, _turns.c.started_at, _turns.c.content
    ).where(
        _turns.c.conversation_id == task_id,
        _turns.c.stopped_at.is_(None),
        or_(_turns.c.delivered_at.is_not(None), _turns.c.author != "system"),
    )
    if since is not None:
        query = query.where(_turns.c.started_at > since)
    if by is not None:
        query = query.where(
            or_(_turns.c.agent_handle == by, _turns.c.agent_handle.is_(None)),
            _turns.c.author != "system",
        )
    running = (await session.execute(query.order_by(_turns.c.started_at))).all()
    left = []
    if running:
        chains = {turn.id for turn in running} | {
            turn.continuation_id for turn in running
        }
        write_ups = set(
            await session.scalars(
                select(_instructions.c.attempt_id).where(
                    _instructions.c.attempt_id.in_(chains),
                    _instructions.c.conversation_id == task_id,
                    _instructions.c.payload[CLOSES_TASK].as_boolean().is_(True),
                )
            )
        )
        said = {
            turn_id: at
            for turn_id, at in (
                await session.execute(
                    select(Block.turn_id, func.max(Block.created_at))
                    .where(Block.turn_id.in_(chains))
                    .group_by(Block.turn_id)
                )
            ).all()
        }
        # A turn a message began keeps that message, not a copy, until it is
        # assembled: the message says what it is.
        asked = {
            block_id: content
            for block_id, content in (
                await session.execute(
                    select(Block.id, Block.content).where(
                        Block.id.in_([turn.id for turn in running if not turn.content])
                    )
                )
            ).all()
        }
        quiet_since = now - timedelta(seconds=settings.turn_silence_timeout_s)
        for turn in running:
            if turn.id in write_ups or turn.continuation_id in write_ups:
                continue
            last = max(
                [turn.started_at]
                + [said[t] for t in (turn.id, turn.continuation_id) if t in said]
            )
            if last >= quiet_since:
                left.append(turn.content or asked.get(turn.id) or "")
    waiting = list(await session.scalars(queued_messages(now - QUEUED_FOR, task_id)))
    if waiting:
        taken = set(
            await session.scalars(
                select(_turns.c.id).where(
                    _turns.c.id.in_([m.id for m in waiting]),
                    _turns.c.stopped_at.is_(None),
                )
            )
        )
        left += [
            message.content
            for message in waiting
            if message.id not in taken
            and (
                by is None
                or ((message.meta or {}).get("agent_recipient") or {}).get("handle")
                == by
            )
        ]
    return left


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
    is stopped through ``work``: a closed task is work that is over, and a turn
    left running in it goes on paying for the model while every write it
    makes is refused. ``work`` is None only when the turn that would be
    stopped is the one closing the task, or when nothing is left running
    (`turns_ended`).

    ``machines`` goes to `after_close` unchanged: the call that reaches a
    machine to remove this room's closed checkouts (`ports.SweepCheckouts`).

    Idempotent: closing a closed task keeps the first `closed_at` — the
    moment it stopped being live is a fact, not a re-statement of intent.
    """
    if conclusion is not None:
        task.conclusion = conclusion
    # A closed task is not waiting for a summary turn any more, so the window
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


async def _being_written_up(session: AsyncSession, task_id: uuid.UUID) -> bool:
    """Whether the turn writing ``task_id`` up has started and not yet stopped:
    the turn an instruction marked `CLOSES_TASK` started, or one resuming it
    (`unfinished` says how it is told apart)."""
    write_ups = select(_instructions.c.attempt_id).where(
        _instructions.c.conversation_id == task_id,
        _instructions.c.payload[CLOSES_TASK].as_boolean().is_(True),
    )
    running = await session.scalar(
        select(_turns.c.id)
        .where(
            _turns.c.conversation_id == task_id,
            _turns.c.stopped_at.is_(None),
            or_(_turns.c.id.in_(write_ups), _turns.c.continuation_id.in_(write_ups)),
        )
        .limit(1)
    )
    return running is not None


async def close_after_summary(
    session: AsyncSession,
    task_id: uuid.UUID,
    work: StopsWork | None,
    *,
    machines: SweepCheckouts | None = None,
) -> Task | None:
    """Close ``task_id`` if it is waiting for its summary turn, that turn is
    not still running, and nothing else is being done or asked for in it
    (`unfinished`, module docstring). Returns the task it closed; the pages
    showing it hear from `room_task.live`.

    An instruction's attempt is over once the session has taken the turn,
    which is long before the turn ends: closing then interrupted the write-up
    it was waiting for. The turn's own end closes the task (`turns_ended`).
    ``work`` is None only there, where what `unfinished` counts is nothing.
    ``machines`` is `close_task`'s."""
    task = await session.get(Task, task_id, with_for_update=True)
    if task is None or task.closing_since is None:
        return None
    if await _being_written_up(session, task.id):
        return None
    if await unfinished(session, task.id):
        return None
    return await close_task(session, task, work, machines=machines)


async def turns_ended(
    session: AsyncSession,
    conversations: list[uuid.UUID],
    *,
    machines: SweepCheckouts | None = None,
) -> None:
    """Turns stopped in these conversations, in ``session``: each of them that
    is a task waiting for its summary turn closes if that was the write-up, or
    the last thing besides it (`close_after_summary`). Every way a turn ends
    records it through `agent.repositories.AgentTurnRepository`, which calls
    this, so a write-up that ends, fails, times out or dies with its session
    closes its task when its interval does.

    ``machines`` is `close_task`'s. This is the path most tasks close by, so a
    caller that leaves it out leaves the room's closed checkouts on the
    machines it works on; `AgentTurnRepository` hands in the sweep this
    package implements."""
    if not conversations:
        return
    waiting = list(
        await session.scalars(
            select(Task.id).where(
                Task.id.in_(set(conversations)), Task.closing_since.is_not(None)
            )
        )
    )
    for task_id in waiting:
        await close_after_summary(session, task_id, None, machines=machines)


@dataclass(frozen=True)
class Overdue:
    """A task `close_overdue` closed, and what was still being done or asked
    for in it when it did (`unfinished`): empty unless the task was closed on
    work at `STOP_WORK_AFTER`, which stopped that work undelivered."""

    task: Task
    undelivered: list[str]


async def close_overdue(
    session: AsyncSession, work: StopsWork, *, machines: SweepCheckouts | None = None
) -> list[Overdue]:
    """Close every task still waiting for a summary turn it is not getting,
    unless something else is being done or asked for in it — and, past
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
    for task_id in overdue:
        task = await session.get(Task, task_id, with_for_update=True)
        if task is None or task.closing_since is None:
            continue
        left = await unfinished(session, task.id)
        if left and task.closing_since >= now - STOP_WORK_AFTER:
            continue
        closed.append(
            Overdue(await close_task(session, task, work, machines=machines), left)
        )
    return closed
