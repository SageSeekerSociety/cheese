"""Serialize prompt preparation and live offers on the same recipient seat."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
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
async def seat_admission(
    lease: Lease | asyncio.Lock,
    on_queued: Callable[[], Awaitable[object]] | None = None,
) -> AsyncIterator[None]:
    """Hold ``lease`` for the seat, re-entrant within one task.

    ``on_queued`` is told once, before the wait, when the seat this take wants
    is held by another turn — in this process or in another one. A turn that
    waits for a seat says so in its own room, the way one that waits for a
    place among the project's turns does: the wait is otherwise what a person
    reads as nothing happening.

    A bare ``asyncio.Lock`` is still taken and is never told about: it is what
    a double for "this seat is never really taken" hands over, and nothing
    about admission needs the cross-process half to be there."""
    task = asyncio.current_task()
    if _owner.get() == (lease, task):
        yield
        return
    if on_queued is not None and isinstance(lease, Lease) and await lease.taken():
        await on_queued()
    async with lease:
        token = _owner.set((lease, task))
        try:
            yield
        finally:
            _owner.reset(token)
