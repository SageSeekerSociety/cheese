"""Task-domain clocks: deadlines, and starter code waiting to be pushed."""

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.task import deadline_scheduler, starter_jobs


def register_periodic_jobs(
    jobs: PeriodicJobs,
    *,
    sessions: SessionFactory,
    starter_settled: starter_jobs.Settled | None = None,
) -> None:
    jobs.register(
        "task deadline sweep",
        settings.task_deadline_sweep_interval_s,
        lambda: deadline_scheduler.sweep_expired_deadlines(sessions),
    )
    # A claim waits on this to see its repository's first commit, so it ticks
    # often; a tick with nothing pending is one indexed read.
    jobs.register(
        "starter code pushes",
        10,
        lambda: starter_jobs.push_due(sessions, settled=starter_settled),
    )
