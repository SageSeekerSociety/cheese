"""The sealer's and the retention sweeps' clocks."""

from app.core.background import PeriodicJobs
from app.core.db import SessionFactory
from app.domain.session_record import retention, service


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    jobs.register(
        "session record seal",
        service.SEAL_EVERY_S,
        lambda: service.seal(sessions),
    )
    jobs.register(
        "session record retention",
        retention.EVERY_S,
        lambda: retention.sweep(sessions),
    )
