"""The 支线 recycling clock.

一条死掉的支线满 `FAILURE_GIVES_UP_AFTER` 就被收起来（`thread/recycle.py`）。这是
它唯一的入口：没有人在等的时刻会主动去收一条支线。

「谁在跑」由调用方注入（`app/periodic_jobs.py` 递 `agent.session_state` 那一个）：
`thread` 读不了上面那一层的表（`.importlinter` 的 C5），而这一句话是会话那一域的事实。
"""

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.thread.recycle import Running, recycle_due


def register_periodic_jobs(
    jobs: PeriodicJobs, *, sessions: SessionFactory, running: Running
) -> None:
    jobs.register(
        "thread recycling",
        settings.thread_recycle_interval_s,
        lambda: recycle_due(sessions, running=running),
    )
