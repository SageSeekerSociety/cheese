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
        delivered_at: datetime | None = None,
    ) -> None:
        # `delivered_at` is for a turn that has no 投喂 phase to stamp later — it
        # is born delivered or it is born unclosable. Everything the platform
        # feeds leaves it None and stamps it when the transport accepts.
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
                delivered_at=delivered_at,
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

    async def mark_credits_refused(self, turn_id: uuid.UUID, at: datetime) -> bool:
        """Stamp that admission refused this turn for spent credits (#715).

        First-writer-wins: admission is asked again for the SAME refusal (the
        proxy caches a verdict for 30s, Claude Code retries ten times), and only
        the call that actually flips the column should trigger the one-time room
        notice — which is exactly what the returned bool tells the caller. A
        second call for an already-stamped turn returns False and changes
        nothing.
        """
        result = await self._session.execute(
            update(AgentTurn)
            .where(AgentTurn.id == turn_id, AgentTurn.credits_refused_at.is_(None))
            .values(credits_refused_at=at)
        )
        # UPDATE returns a CursorResult, which has rowcount at runtime.
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]

    async def credits_refused(self, turn_id: uuid.UUID) -> bool:
        """Was this turn ever stamped refused-for-credits? What the turn's own
        end (`StopFailure`) reads to decide whose wording the room gets."""
        value = (
            await self._session.execute(
                select(AgentTurn.credits_refused_at).where(AgentTurn.id == turn_id)
            )
        ).scalar_one_or_none()
        return value is not None

    async def open_turn_id_for_topic(self, topic_id: uuid.UUID) -> uuid.UUID | None:
        """The still-running turn at this PLACE, if there is one.

        Admission only ever has the place a caller claims to run in, never a
        turn id (a scoped token carries `t`, never `turn_id`) — this is how it
        finds the interval that place names, so a credits refusal can be
        stamped on the turn it actually refused.

        The room's OWN line, which is where every turn now runs; the rows a
        thread left behind before work stopped being a place are excluded by
        the same clause that used to select them.
        """
        stmt = (
            select(AgentTurn.id)
            .where(
                AgentTurn.topic_id == topic_id,
                AgentTurn.task_id.is_(None),
                AgentTurn.stopped_at.is_(None),
            )
            .order_by(AgentTurn.started_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def open_turn_author_for_topic(self, topic_id: uuid.UUID) -> str | None:
        """这一轮由谁的消息发起 —— 也就是「这一轮的结果由谁在等」。

        芝士在轮次中途提出待确认问题，本轮就停在那里等回答。等的不是房间里任意一个
        人，而是把这件事交给它的那个人，而那条消息的作者正是 `AgentTurn.author`。

        平台发起的轮次（resume、各类提醒）作者是 `system`，那种轮次里的提问指不到
        具体的人 —— 这里照样把 `system` 返回，由调用点决定它意味着什么，和
        `open_turn_id_for_topic` 一样只回答被问到的那件事。
        """
        stmt = (
            select(AgentTurn.author)
            .where(
                AgentTurn.topic_id == topic_id,
                AgentTurn.task_id.is_(None),
                AgentTurn.stopped_at.is_(None),
            )
            .order_by(AgentTurn.started_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def open_turn_authors_for_topics(
        self, topic_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        """{房间: 这一轮由谁发起}，一次查完 —— 跨项目的「待我处理」用。

        和 `open_turn_author_for_topic` 同一个判据，只是批量：那个列表要对几十个
        房间问同一件事，逐个问就是一个列表一次请求变成几十次。
        """
        if not topic_ids:
            return {}
        stmt = (
            select(AgentTurn.topic_id, AgentTurn.author)
            .where(
                AgentTurn.topic_id.in_(topic_ids),
                AgentTurn.task_id.is_(None),
                AgentTurn.stopped_at.is_(None),
            )
            .order_by(AgentTurn.topic_id, AgentTurn.started_at.desc())
            .distinct(AgentTurn.topic_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {topic_id: author for topic_id, author in rows}

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
                # The room's own line. A Stop is the room's session finishing,
                # and the intervals a thread left behind when work was still a
                # place are not this session's to close.
                AgentTurn.task_id.is_(None),
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
                # The PLACE this turn ran in — the thread when it had one. The
                # sweep re-addresses work by this id, and re-addressing a
                # thread's turn to its room would resume the wrong conversation.
                topic_id=row.task_id or row.topic_id,
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
