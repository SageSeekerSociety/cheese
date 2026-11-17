"""评测的钟：排着的评测有空位就领走去跑（``dispatch.run_due``）。

学生交了作业在等结果，所以它走得勤；没有排着的评测时一次 tick 只是一次带索引的读。
出了结果要告诉学生，那句话是 ``task_notices`` 说的，而它在评测之上一层 —— 由
``app.periodic_jobs`` 递进来（``judged``），这里不 import 它。
"""

from app.core.background import PeriodicJobs
from app.core.db import SessionFactory
from app.domain.judge import dispatch


def register_periodic_jobs(
    jobs: PeriodicJobs,
    *,
    sessions: SessionFactory,
    judged: dispatch.Judged | None = None,
) -> None:
    jobs.register(
        "judge runs",
        5,
        lambda: dispatch.run_due(sessions, judged=judged),
    )
