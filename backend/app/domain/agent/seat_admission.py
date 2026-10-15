"""Serialize prompt preparation and live offers on the same recipient seat."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from app.domain.agent.admission import Lease

#: The seat this task is already inside the admission of. A ``Lease`` is taken
#: once per seat rather than once per caller, so a turn that takes its own seat
#: a second time — the first input's admission, then the turn that admission
#: opened — would otherwise wait for itself until the lease lapsed. Nested
#: takes by the same task yield instead.
_owner: ContextVar[tuple[Lease | asyncio.Lock, asyncio.Task[object] | None] | None] = (
    ContextVar("seat_admission_owner", default=None)
)


@asynccontextmanager
async def seat_admission(lease: Lease | asyncio.Lock) -> AsyncIterator[None]:
    """Hold ``lease`` for the seat, re-entrant within one task.

    A bare ``asyncio.Lock`` is still taken: it is what a double for "this seat
    is never really taken" hands over, and nothing about admission needs the
    cross-process half to be there."""
    task = asyncio.current_task()
    if _owner.get() == (lease, task):
        yield
        return
    async with lease:
        token = _owner.set((lease, task))
        try:
            yield
        finally:
            _owner.reset(token)
