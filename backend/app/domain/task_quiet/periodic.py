"""Task quietness reminder clock."""

from app.core.background import PeriodicJobs
from app.core.db import SessionFactory
from app.domain.task_quiet import sweep


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    jobs.register(
        "quiet task reminder", 3600, lambda: sweep.remind_quiet_tasks(sessions)
    )
