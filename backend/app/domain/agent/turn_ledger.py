"""The runner's smallest turn helpers, apart from the runner.

`runtime.py` is past its size cap (`.claude/rules/architecture.md`): each of
these is a couple of statements about a turn's row, the loop it runs on or the
pool that admits it, with nothing of the runner's own flow in it. The logger
keeps its `cheesex.runtime` name so their lines still read as the runner's.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from app.domain.agent import turn_inputs
from app.domain.agent.admission import Pool
from app.domain.agent.repositories import AgentTurnRepository

logger = logging.getLogger("cheesex.runtime")


def fire_on_done(callback: Callable[[], None]) -> None:
    """Run a `submit(on_done=...)` hook without letting it escape into the loop.

    A done-callback that raises does not fail the turn (that already finished) —
    it lands in the loop's exception handler as an unattributed error. Swallow
    and log instead, so a bookkeeping bug in a caller stays a bookkeeping bug.
    """
    try:
        callback()
    except Exception:  # noqa: BLE001 — a hook must never break the runner
        logger.exception("submit on_done hook failed")


async def open_turn(session_factory, **fields) -> None:
    async with session_factory() as session:
        await AgentTurnRepository(session).open(**fields)
        await session.commit()


async def stamp_delivery(session_factory, turn_id: uuid.UUID) -> None:
    """Record that the transport accepted this turn's prompt (the ledger's own
    helper; mid-turn bookkeeping that must never kill a working turn)."""
    await turn_inputs.stamp_delivery_fact(session_factory, turn_id=turn_id, at=utcnow())


async def close_turns(session_factory, turn_ids) -> None:
    async with session_factory() as session:
        await AgentTurnRepository(session).close(turn_ids, utcnow())
        await session.commit()


def project_pool(project_id: uuid.UUID | str, limit: int) -> Pool:
    """A project's turns: at most ``max_concurrent_turns`` run at once."""
    return Pool(f"project-turns:{project_id}", limit)


def utcnow() -> datetime:
    return datetime.now(UTC)
