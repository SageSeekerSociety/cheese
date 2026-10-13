"""Task-domain deadline clock."""

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.task import deadline_scheduler


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    jobs.register(
        "task deadline sweep",
        settings.task_deadline_sweep_interval_s,
        lambda: deadline_scheduler.sweep_expired_deadlines(sessions),
    )
