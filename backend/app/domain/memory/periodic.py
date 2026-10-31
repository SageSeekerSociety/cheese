"""Memory change-history retention clock."""

from app.core.background import PeriodicJobs
from app.core.db import SessionFactory
from app.domain.memory import history


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    # Hourly: the window is 30 days, so an hour late is nothing; one indexed
    # delete on `created_at` per run.
    jobs.register(
        "memory history retention",
        3600,
        lambda: history.purge_expired(sessions),
    )
