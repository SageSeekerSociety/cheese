"""Reading and writing the turn intervals in :mod:`app.domain.agent.models`."""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.models import AgentTurn


@dataclass(frozen=True, slots=True)
class TurnRecord:
    """One open interval, as the orphan sweep reads it.

    A record, not the ORM row, because the sweep runs long enough to await
    several probes and post several events — holding a session open across all
    of that to keep an object attached would be a transaction that exists only
    to serve attribute access.
    """

    turn_id: uuid.UUID
    topic_id: uuid.UUID
    continuation_id: uuid.UUID
    author: str
    content: str
    is_resume: bool
    resendable: bool
    started_at: datetime
    delivered_at: datetime | None

    @property
    def delivered(self) -> bool:
        """Did the transport accept this turn's prompt? The sweep's whole
        attach-versus-re-send decision turns on this one fact."""
        return self.delivered_at is not None

    def age_s(self, now: datetime) -> float:
        return (now - self.started_at).total_seconds()


class AgentTurnRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def open(
        self,
        *,
        turn_id: uuid.UUID,
        topic_id: uuid.UUID,
        continuation_id: uuid.UUID,
        author: str,
        content: str,
        is_resume: bool,
        resendable: bool,
        started_at: datetime,
    ) -> None:
        self._session.add(
            AgentTurn(
                id=turn_id,
                topic_id=topic_id,
                continuation_id=continuation_id,
                author=author,
                content=content,
                is_resume=is_resume,
                resendable=resendable,
                started_at=started_at,
            )
        )

    async def mark_delivered(self, turn_id: uuid.UUID, at: datetime) -> None:
        """Stamp the moment the transport accepted this turn's write.

        Only the first one counts: a re-delivered prompt does not move the
        moment the session first heard the task.
        """
        await self._session.execute(
            update(AgentTurn)
            .where(AgentTurn.id == turn_id, AgentTurn.delivered_at.is_(None))
            .values(delivered_at=at)
        )

    async def close(self, turn_ids: Iterable[uuid.UUID], at: datetime) -> None:
        """End these intervals. Closing is not deleting — the ids stay readable
        next to the blocks that carry them."""
        ids = list(turn_ids)
        if not ids:
            return
        await self._session.execute(
            update(AgentTurn)
            .where(AgentTurn.id.in_(ids), AgentTurn.stopped_at.is_(None))
            .values(stopped_at=at)
        )

    async def close_for_topic(self, topic_id: uuid.UUID, at: datetime) -> int:
        """End every DELIVERED open interval on one topic; returns how many.

        What the harness's Stop acts on: it says the session finished, not which
        turn id the platform had filed that under — and after a restart those
        are not the same thing, because the coroutine holding the id is gone.

        Delivered, because the interval is 投喂 → Stop and a turn that was never
        fed cannot be what this Stop is ending. A turn spends its first seconds
        (or minutes, if the box has to boot) between opening its interval and
        reaching the transport; a Stop from the previous conversation landing in
        that window would otherwise close it, and a turn with no open interval is
        invisible to every future sweep — the silent death this table exists to
        end. Its own coroutine closes it by id, delivered or not.
        """
        result = await self._session.execute(
            update(AgentTurn)
            .where(
                AgentTurn.topic_id == topic_id,
                AgentTurn.stopped_at.is_(None),
                AgentTurn.delivered_at.is_not(None),
            )
            .values(stopped_at=at)
        )
        # UPDATE returns a CursorResult, which has rowcount at runtime.
        return result.rowcount or 0  # type: ignore[attr-defined]

    async def open_turns(self) -> list[TurnRecord]:
        """Every interval still open, oldest first."""
        rows = (
            await self._session.execute(
                select(AgentTurn)
                .where(AgentTurn.stopped_at.is_(None))
                .order_by(AgentTurn.started_at)
            )
        ).scalars()
        return [
            TurnRecord(
                turn_id=row.id,
                topic_id=row.topic_id,
                continuation_id=row.continuation_id,
                author=row.author,
                content=row.content,
                is_resume=row.is_resume,
                resendable=row.resendable,
                started_at=_aware(row.started_at),
                delivered_at=(
                    None if row.delivered_at is None else _aware(row.delivered_at)
                ),
            )
            for row in rows
        ]


def _aware(value: datetime) -> datetime:
    """Postgres hands back tz-aware values; a driver that does not must not make
    every comparison in the sweep raise."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
