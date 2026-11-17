"""The space domain's clock: the nightly 学情分析 (§ learning analysis, step 2)."""

from app.core.background import PeriodicJobs
from app.core.db import SessionFactory
from app.domain.space import learning_insight_analysis


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    # Hourly: the job itself decides whether it is night yet
    # (``learning_analysis_run_hour``) and whether a board's run for today is
    # done, so a deploy or a missed tick only delays the night by an hour.
    jobs.register(
        "learning analysis nightly",
        3600,
        lambda: learning_insight_analysis.run_nightly(sessions),
    )
