"""叫醒链观察者的钟（`domain/wake_watch/observe.py`）。

`owner_only`（本文件的 `jobs.register` 不给那个参数，就是默认的真）是这件事的关键：
只有握着归属锁的那一个进程跑它，也就是 API 那一侧、不在任何工作机上。观察者和被
观察的东西住同一台机器，就会跟着一起停，那正是要发现的那件事。
"""

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.wake_watch import observe


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    jobs.register(
        "wake watch",
        settings.wake_watch_interval_s,
        lambda: observe.watch(sessions),
    )
