"""Serialize prompt preparation and live offers on the same recipient seat."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar

_owner = ContextVar("seat_admission_owner", default=None)


@asynccontextmanager
async def seat_admission(lock):
    task = asyncio.current_task()
    if _owner.get() == (lock, task):
        yield
        return
    async with lock:
        token = _owner.set((lock, task))
        try:
            yield
        finally:
            _owner.reset(token)
