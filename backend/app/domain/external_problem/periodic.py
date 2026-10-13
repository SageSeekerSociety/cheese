"""每天把挂过题号的题同步一遍。

老师点「同步」是给人用的那一半；这一半是给「老师没点，但学生昨晚做出来了」用的。
两者跑的是同一段代码（`service.sync`），差别只在名单从哪来：接口那边手上就有，
这里靠 ``student_roster`` 现取。

名单是**传进来的**，不是这里 import 的（照 `domain/task/periodic.py` 那些一样，
依赖由 `app/periodic_jobs.py` 装配）：这个域就不必认识题目域和用户域。
"""

import logging
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.external_problem.service import (
    ExternalProblemService,
    Student,
    SyncTarget,
)

#: ``(会话, 题目 id) -> [(报名记录 id, 用户名), …]``。
StudentRoster = Callable[[AsyncSession, int], Awaitable[Sequence[tuple[int, str]]]]


async def sync_all(sessions: SessionFactory, *, student_roster: StudentRoster) -> None:
    """一次跑完所有挂过题号的题。一道题没成不影响别的题。"""
    async with sessions() as session:
        service = ExternalProblemService(session)
        bindings = await service.all_bindings()
        targets = []
        for binding in bindings:
            roster = await student_roster(session, binding.task_id)
            targets.append(
                SyncTarget(
                    task_id=binding.task_id,
                    problem_no=binding.problem_no,
                    students=[
                        Student(member_id=member_id, student_no=student_no)
                        for member_id, student_no in roster
                    ],
                )
            )
        outcomes = await service.sync(targets)
        await session.commit()
        for outcome in outcomes:
            if outcome.status != "ok":
                # 一轮里某个题没成只是个日志：账号没配、外站改了版，都会在下一次
                # 或者老师点同步时再暴露一次，不该把整轮掀了
                # （`core/background.py` 里一轮抛出去也只是记一笔）。
                logging.getLogger("app.periodic").warning(
                    "external problem sync failed for task %s: %s",
                    outcome.task_id,
                    outcome.error,
                )


def register_periodic_jobs(
    jobs: PeriodicJobs, *, sessions: SessionFactory, student_roster: StudentRoster
) -> None:
    jobs.register(
        "external problem sync",
        settings.external_problem_sync_interval_s,
        lambda: sync_all(sessions, student_roster=student_roster),
    )
