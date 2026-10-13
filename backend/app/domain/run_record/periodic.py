"""Run-record retention clock."""

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.run_record import service


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    jobs.register(
        "run record expiry",
        settings.run_record_expiry_interval_s,
        lambda: service.purge_expired(sessions),
    )
