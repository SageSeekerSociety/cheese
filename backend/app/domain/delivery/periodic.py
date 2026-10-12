"""Delivery-domain recovery and alarm clocks."""

from collections.abc import Callable
from typing import Any

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.delivery import ledger, overdue, timer


def register_periodic_jobs(
    jobs: PeriodicJobs,
    *,
    sessions: SessionFactory,
    chat: Any,
    get_runner: Callable[[], Any],
) -> None:
    jobs.register(
        "delivery resend",
        settings.delivery_resend_interval_s,
        lambda: ledger.resend_unsent_deliveries(sessions),
    )
    jobs.register(
        "timed deliveries",
        30,
        lambda: timer.deliver_due(sessions, chat=chat, runner=get_runner()),
    )
    jobs.register(
        "overdue deliveries",
        settings.delivery_overdue_check_interval_s,
        lambda: overdue.report_overdue(sessions),
    )
