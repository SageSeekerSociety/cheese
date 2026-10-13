"""Subscription usage ingestion clock."""

from pathlib import Path

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.usage import subscription_ingest


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    usage_log = settings.subscription_usage_log.strip()
    jobs.register(
        "subscription usage ingest",
        settings.subscription_ingest_interval_s if usage_log else 0,
        lambda: subscription_ingest.ingest_once(sessions, Path(usage_log)),
    )
