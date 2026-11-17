"""The space domain's clock: the nightly 学情分析 (§ learning analysis, step 2)
and the practice sets teachers push (step 4)."""

from app.core.background import PeriodicJobs
from app.core.db import SessionFactory
from app.domain.space import learning_insight_analysis, learning_practice_service


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    # Hourly: the job itself decides whether it is night yet
    # (``learning_analysis_run_hour``) and whether a board's run for today is
    # done, so a deploy or a missed tick only delays the night by an hour.
    jobs.register(
        "learning analysis nightly",
        3600,
        lambda: learning_insight_analysis.run_nightly(sessions),
    )
    # The practice sets teachers pushed (§ step 4) are written off the request,
    # a few at a time, under the same daily cap as the night; a member who
    # opens one first gets it written on the spot instead.
    jobs.register(
        "learning practice queue",
        900,
        lambda: learning_practice_service.run_queued(sessions),
    )
