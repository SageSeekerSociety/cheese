"""Machine-domain maintenance clocks."""

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.machine import warm

Sweeper = Callable[[], Awaitable[Any]]


def register_periodic_jobs(
    jobs: PeriodicJobs,
    *,
    machines: Sweeper,
    sandboxes: Sweeper,
    compute: Sweeper,
    sessions: SessionFactory,
) -> None:
    jobs.register(
        "cloud host pool sweep", settings.machine_enroll_interval_seconds, machines
    )
    jobs.register(
        "cloud sandbox lifecycle", settings.machine_enroll_interval_seconds, sandboxes
    )
    jobs.register(
        "cloud compute metering", settings.machine_enroll_interval_seconds, compute
    )
    jobs.register(
        "cloud warm pool",
        settings.machine_enroll_interval_seconds,
        lambda: warm.sweep_warm_pool(sessions),
    )
