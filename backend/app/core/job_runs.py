"""When each periodic job last ran, kept across restarts.

A deployment restarts the backend far more often than some jobs are due, so a
clock that starts over with every process never reaches them. Kept in the
database because the jobs run in whichever process holds the ownership lock
(`app.core.ownership`), one at a time, and the next holder reads what the last
one wrote.

**One tick, one lease.** A row is not only that clock: it is the claim on the
job's current tick. ``claim`` is one statement that takes the tick if the job is
due and nobody else's claim still stands, and says whether it did. A job that no
longer depends on the ownership lock (`PeriodicRunner` with ``owner_only``
false) is scheduled by every process — this, and not the lock, is then what
keeps one tick from being run twice.
"""

from datetime import datetime, timedelta

from sqlalchemy import DateTime, String, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, SessionFactory


class JobRun(Base):
    __tablename__ = "periodic_job_runs"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Which process took the job's latest tick, and how far that claim reaches.
    # NULL on a row nobody has claimed yet. They are what a person reading the
    # table — or a process deciding whether to take the next tick — learns about
    # who is running it.
    run_by: Mapped[str | None] = mapped_column(String(200))
    run_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobRuns:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def load(self, names: list[str], now: datetime) -> dict[str, datetime]:
        """Every named job's last run, in one transaction. A job never recorded
        is recorded at ``now``, so its first interval counts from this start."""
        if not names:
            return {}
        async with self._sessions() as session:
            await session.execute(
                insert(JobRun)
                .values([{"name": name, "last_run_at": now} for name in names])
                .on_conflict_do_nothing(index_elements=[JobRun.name])
            )
            rows = await session.execute(
                select(JobRun.name, JobRun.last_run_at).where(JobRun.name.in_(names))
            )
            last = {name: at for name, at in rows.tuples()}
            await session.commit()
        return last

    async def claim(
        self, name: str, *, interval_s: float, who: str, now: datetime
    ) -> bool:
        """Take ``name``'s tick for ``who``, if it is this process's to take.

        The whole decision is this one statement, so two processes claiming at
        once cannot both win: the row moves only if the job is due — its last
        run a full interval back — and no claim on it still stands. A claim
        reaches one interval ahead, so a run still going when its next tick
        comes round holds its claim and the other process skips that tick rather
        than overlapping it.

        A row nobody has recorded is inserted and won, so a job whose record was
        never loaded still runs rather than silently never running at all.
        """
        due_before = now - timedelta(seconds=interval_s)
        holds_until = now + timedelta(seconds=interval_s)
        take = (
            insert(JobRun)
            .values(name=name, last_run_at=now, run_by=who, run_until=holds_until)
            .on_conflict_do_update(
                index_elements=[JobRun.name],
                set_={"last_run_at": now, "run_by": who, "run_until": holds_until},
                where=(JobRun.last_run_at <= due_before)
                & ((JobRun.run_until.is_(None)) | (JobRun.run_until <= now)),
            )
            .returning(JobRun.name)
        )
        async with self._sessions() as session:
            won = (await session.execute(take)).scalar() is not None
            await session.commit()
        return won
