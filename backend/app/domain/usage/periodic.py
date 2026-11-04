"""Usage clocks: subscription ingestion, and the warning before credits run out."""

from pathlib import Path

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.usage import alerts, subscription_ingest
from app.domain.usage.ports import TurnStarts


def register_periodic_jobs(
    jobs: PeriodicJobs,
    *,
    sessions: SessionFactory,
    turn_starts: TurnStarts,
    team_people: alerts.TeamRoster,
    project_people: alerts.ProjectRoster,
    notify: alerts.Notify,
) -> None:
    """``turn_starts`` 是本包够不着的那个读，谁在门口谁递进来（`app.periodic_jobs`
    在接线时递的是 `app.domain.block.queries.turn_starts`）。"""
    usage_log = settings.subscription_usage_log.strip()
    jobs.register(
        "subscription usage ingest",
        settings.subscription_ingest_interval_s if usage_log else 0,
        lambda: subscription_ingest.ingest_once(
            sessions, Path(usage_log), turn_starts=turn_starts
        ),
    )

    # 额度快用完的提醒（`usage.alerts`）：名册和投递是上面几层的，由
    # `app.periodic_jobs` 递进来。0 关掉。
    jobs.register(
        "credit warnings",
        settings.credit_warning_interval_s,
        lambda: alerts.sweep(
            sessions,
            team_people=team_people,
            project_people=project_people,
            notify=notify,
        ),
    )
