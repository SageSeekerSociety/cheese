"""Routine execution clock."""

from collections.abc import Callable
from typing import Any

from app.core.background import PeriodicJobs
from app.core.db import SessionFactory
from app.domain.routine import service


def register_periodic_jobs(
    jobs: PeriodicJobs,
    *,
    sessions: SessionFactory,
    chat: Any,
    get_runner: Callable[[], Any],
) -> None:
    jobs.register(
        "routines", 30, lambda: service.sweep(sessions, chat=chat, runner=get_runner())
    )
