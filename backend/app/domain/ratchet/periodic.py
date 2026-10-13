"""Architecture-ratchet snapshot ingestion clock."""

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.ratchet import ingest


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    jobs.register(
        "ratchet ingest",
        settings.ratchet_ingest_interval_s,
        lambda: ingest.ingest_snapshots(sessions),
    )
