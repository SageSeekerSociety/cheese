"""叫醒链观察者的钟（`domain/wake_watch/observe.py`）。

`owner_only`（本文件的 `jobs.register` 不给那个参数，就是默认的真）是这件事的关键：
只有握着归属锁的那一个进程跑它，也就是 API 那一侧、不在任何工作机上。观察者和被
观察的东西住同一台机器，就会跟着一起停，那正是要发现的那件事。

补跳（`domain/wake_watch/catchup.py`）由这里的 `wake_watch_catchup` 开关决定要不要
接上：关掉就回到影子期 —— 只判、只记，判据一个字不变。开关在配置里，所以不改代码
也能把补跳停掉，重启一次就行。
"""

from functools import partial

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.wake_watch import catchup, observe


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    summon = partial(catchup.summon, sessions) if settings.wake_watch_catchup else None
    jobs.register(
        "wake watch",
        settings.wake_watch_interval_s,
        lambda: observe.watch(sessions, summon=summon),
    )
