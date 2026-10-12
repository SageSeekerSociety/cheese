"""Notification-domain queue and digest clocks."""

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.notification import digest, maintenance, push_delivery


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    jobs.register(
        "notification email drain",
        settings.notification_email_drain_interval_s,
        lambda: maintenance.drain_email_queue(sessions),
    )
    jobs.register(
        "notification push drain",
        settings.notification_push_drain_interval_s,
        lambda: push_delivery.drain_push_queue(sessions),
    )
    jobs.register(
        "notification digest",
        settings.notification_digest_interval_s,
        lambda: digest.run_notification_digests(sessions),
    )
