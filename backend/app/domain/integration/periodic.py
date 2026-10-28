"""Integration-domain clock: keeping people's Feishu authorizations alive."""

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.background import PeriodicJobs
from app.core.db import SessionFactory
from app.domain.integration.service import renew_feishu


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    jobs.register(
        "feishu authorization renewal",
        3600,
        lambda: renew_feishu(cast(async_sessionmaker[AsyncSession], sessions)),
    )
