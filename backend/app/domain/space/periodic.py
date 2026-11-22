"""The space domain's clock: 学情分析's two halves (§ learning analysis, step 2)
and the practice sets teachers push (step 4)."""

from app.core.background import PeriodicJobs
from app.core.db import SessionFactory
from app.domain.space import learning_insight_analysis, learning_practice_service


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    # Every minute: whoever has words past their cursor and has stopped
    # talking. A member's judgment appears within a minute or two of the
    # conversation ending, not the next morning.
    jobs.register(
        "learning analysis recent",
        60,
        lambda: learning_insight_analysis.run_recent(sessions),
    )
    # Hourly: the cross-member side — grouping questions into clusters and
    # writing teaching advice — reads a whole class per call, so it stays off
    # the minute's clock. Both passes spend the same board's daily cap.
    jobs.register(
        "learning class aggregates",
        3600,
        lambda: learning_insight_analysis.run_class_aggregates(sessions),
    )
    # The practice sets teachers pushed (§ step 4) are written off the request,
    # a few at a time, under the same daily cap as the night; a member who
    # opens one first gets it written on the spot instead.
    jobs.register(
        "learning practice queue",
        900,
        lambda: learning_practice_service.run_queued(sessions),
    )
