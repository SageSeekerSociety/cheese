"""How a routine's run is drawn where its message is: the line in the main
line that stands for one run (`service._fire`)."""

import uuid

from sqlalchemy import ARRAY, Uuid, any_, bindparam, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_facts import remember
from app.domain.routine.models import Routine, RoutineRun
from app.domain.thread.models import Thread

_ROUTINE_RUN_TABLES = frozenset({"routine_runs", "routines"})


def _can_cache(session: object) -> bool:
    return isinstance(session, AsyncSession)


async def runs_under(
    session: AsyncSession, block_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict]:
    """For each of these messages that stands for a run: the rule's title, how
    the run went (``queued`` or ``running`` until it is settled), the reason a
    run that did not succeed gives, the files it kept, when it ran, and the
    支线 it ran in. Keyed by the message."""
    if not block_ids:
        return {}
    rows = await session.execute(
        select(RoutineRun, Routine.title, Routine.owner_handle, Thread.id)
        .join(Routine, Routine.id == RoutineRun.routine_id)
        .outerjoin(Thread, Thread.root_block_id == RoutineRun.message_id)
        # One array parameter, not an IN list: the whole timeline of a busy
        # room is more ids than asyncpg binds in one statement.
        .where(
            RoutineRun.message_id
            == any_(bindparam(None, list(block_ids), type_=ARRAY(Uuid)))
        )
    )
    out: dict[uuid.UUID, dict] = {}
    owner_by_msg: dict[uuid.UUID, tuple[str, str | None]] = {}
    for run, title, owner_handle, thread_id in rows:
        assert run.message_id is not None
        owner_by_msg[run.message_id] = (title, owner_handle)
        out[run.message_id] = {
            "routine_id": str(run.routine_id),
            "run_id": str(run.id),
            "title": title,
            "status": run.status,
            "reason": "" if run.status == "succeeded" else run.error,
            "outputs": list(run.outputs or []),
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "thread_id": str(thread_id) if thread_id else None,
        }
    if _can_cache(session):
        for bid in dict.fromkeys(block_ids):
            remember(
                session,
                ("routine_run_owner_title", bid),
                owner_by_msg.get(bid),
                tables=_ROUTINE_RUN_TABLES,
            )
    return out
