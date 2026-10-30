"""Routine execution clock: a room's routines, and a person's 芝士's."""

import time
from collections.abc import Callable
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.background import PeriodicJobs
from app.core.db import SessionFactory
from app.core.redis import get_redis_client
from app.domain import happenings
from app.domain.assistant import memory_tidy
from app.domain.routine import personal, service

#: How often a person's routines are swept when nothing wakes them.
ROUTINES_EVERY_S = 30


def register_periodic_jobs(
    jobs: PeriodicJobs,
    *,
    sessions: SessionFactory,
    chat: Any,
    get_runner: Callable[[], Any],
    assistants: Callable[[], tuple[Any, Any]] | None = None,
) -> None:
    """``assistants`` gives the session host and the questions read on it,
    which a person's 芝士 answers with; without it their routines wait."""
    jobs.register(
        "routines", 30, lambda: service.sweep(sessions, chat=chat, runner=get_runner())
    )

    swept = 0.0

    async def assistant_routines() -> object:
        nonlocal swept
        if assistants is None:
            return None
        people, consumptions = assistants()
        # The application's session maker: a person's 芝士 asks with it.
        factory = cast(async_sessionmaker[AsyncSession], sessions)
        # Woken by something that happened, only that is read; the rest of the
        # sweep keeps to the clock.
        if time.monotonic() - swept < ROUTINES_EVERY_S - 5:
            return await personal.react(factory, people, consumptions, get_redis_client)
        swept = time.monotonic()
        return await personal.tick(factory, people, consumptions, get_redis_client)

    jobs.register(
        "assistant routines",
        ROUTINES_EVERY_S,
        assistant_routines,
        woken_by=happenings.waiter(get_redis_client),
    )
    jobs.register(
        "assistant memory tidy",
        3600,
        lambda: memory_tidy.sweep(cast(async_sessionmaker[AsyncSession], sessions)),
    )
