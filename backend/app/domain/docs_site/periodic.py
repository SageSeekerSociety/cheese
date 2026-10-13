"""Docs-site retention clock."""

from app.core.background import PeriodicJobs
from app.core.db import SessionFactory
from app.domain.docs_site import assistant


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    jobs.register(
        "docs question retention",
        86400,
        lambda: assistant.purge_old_questions(sessions),
    )
