"""Reading and writing `ratchet_snapshots` — the only code that touches the table."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ratchet.models import RatchetSnapshot


def _now() -> datetime:
    return datetime.now(UTC)


class RatchetSnapshots:
    """The archive. Writes are idempotent per CI run; reads are newest-first.

    ``store_many`` is the only write, and it is deliberately the ONLY one: a
    collected snapshot is an observation, not state this platform owns, so
    nothing here updates or deletes one. A wrong row is fixed by fixing the
    collector and letting the next commit write a new one — the archive stays a
    record of what was said at the time.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def known_run_ids(self, repo: str, run_ids: list[int]) -> set[int]:
        """Which of these CI runs are already stored."""
        if not run_ids:
            return set()
        rows = await self._session.execute(
            select(RatchetSnapshot.workflow_run_id).where(
                RatchetSnapshot.repo == repo,
                RatchetSnapshot.workflow_run_id.in_(run_ids),
            )
        )
        return {run_id for (run_id,) in rows}

    async def store_many(self, rows: list[dict]) -> int:
        """Insert the rows whose CI run is not stored yet; return how many.

        `ON CONFLICT DO NOTHING` on ``(repo, workflow_run_id)`` rather than a
        read-then-write: two pulls can overlap (the periodic job and the page's
        refresh button), and the loser must insert nothing rather than fail the
        pull. ``ingested_at`` is stamped here so a caller does not have to.
        """
        if not rows:
            return 0
        now = _now()
        values = [{**row, "ingested_at": now} for row in rows]
        result = await self._session.execute(
            insert(RatchetSnapshot)
            .values(values)
            .on_conflict_do_nothing(constraint="uq_ratchet_snapshots_run")
            .returning(RatchetSnapshot.id)
        )
        inserted = len(list(result))
        await self._session.commit()
        return inserted

    async def newest(self, repo: str, limit: int) -> list[RatchetSnapshot]:
        """The most recent ``limit`` points, newest first.

        Ordered by when the checks RAN (`collected_at`), falling back to when
        the row was ingested for a run that never wrote a snapshot — a failed
        collection still belongs in the series at the moment it happened.
        """
        rows = await self._session.scalars(
            select(RatchetSnapshot)
            .where(RatchetSnapshot.repo == repo)
            .order_by(
                func.coalesce(
                    RatchetSnapshot.collected_at, RatchetSnapshot.ingested_at
                ).desc()
            )
            .limit(limit)
        )
        return list(rows)

    async def count(self, repo: str) -> int:
        total = await self._session.scalar(
            select(func.count())
            .select_from(RatchetSnapshot)
            .where(RatchetSnapshot.repo == repo)
        )
        return int(total or 0)
