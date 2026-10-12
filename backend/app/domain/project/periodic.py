"""Project-owned Forge maintenance clocks."""

from app.core.background import PeriodicJobs
from app.core.db import SessionFactory
from app.domain.project import forge


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    jobs.register(
        "forge event subscriptions",
        300,
        lambda: forge.reconcile_repository_webhooks(sessions),
    )
    jobs.register(
        "forge orphan account sweep",
        3600,
        lambda: forge.sweep_orphan_accounts(sessions),
    )
