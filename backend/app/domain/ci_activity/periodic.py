"""CI activity sweep clock."""

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.ci_activity import ingest


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    interval = settings.ci_activity_sweep_interval_s
    if interval > 0:
        interval = max(interval, int(ingest.RUN_SLICE.total_seconds()))
    jobs.register(
        "ci activity sweep",
        interval,
        lambda: ingest.sweep(sessions),
        forge_bound=True,
    )
