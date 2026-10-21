"""Submission deadline clock."""

from app.core.background import PeriodicJobs
from app.core.db import SessionFactory
from app.domain.task_notices import sweep


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    jobs.register(
        "task deadline reminder", 3600, lambda: sweep.remind_deadlines(sessions)
    )
