"""When each periodic job last ran, kept across restarts.

A deployment restarts the backend far more often than some jobs are due, so a
clock that starts over with every process never reaches them. Kept in the
database because the jobs run in whichever process holds the ownership lock
(`app.core.ownership`), one at a time, and the next holder reads what the last
one wrote.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, SessionFactory


class JobRun(Base):
    __tablename__ = "periodic_job_runs"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JobRuns:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def last_run(self, name: str) -> datetime | None:
        async with self._sessions() as session:
            return await session.scalar(
                select(JobRun.last_run_at).where(JobRun.name == name)
            )

    async def record(self, name: str, at: datetime) -> None:
        async with self._sessions() as session:
            await session.execute(
                insert(JobRun)
                .values(name=name, last_run_at=at)
                .on_conflict_do_update(
                    index_elements=[JobRun.name], set_={"last_run_at": at}
                )
            )
            await session.commit()
