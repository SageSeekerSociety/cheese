"""The sealer's clock."""

from app.core.background import PeriodicJobs
from app.core.db import SessionFactory
from app.domain.session_record import service


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    jobs.register(
        "session record seal",
        service.SEAL_EVERY_S,
        lambda: service.seal(sessions),
    )
